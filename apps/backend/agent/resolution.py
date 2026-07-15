"""One bounded advisory Echo pass. Runtime authority remains outside this module."""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field


class ResolutionRecommendation(str, Enum):
    PROCEED = "proceed"
    CLARIFY = "clarify"
    BLOCK = "block"


class ResolutionAdvice(BaseModel):
    recommended_mode: Literal["chat", "task_research", "coding"]
    interpreted_objective: str
    subject: str = ""
    project_id: str = ""
    session_id: str
    required_capabilities: list[str] = Field(default_factory=list)
    recommended_skills: list[str] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    operation_intent: Literal["inspect", "propose", "implement", "answer"] = "answer"
    risk_level: Literal["low", "moderate", "high", "destructive"] = "low"
    ambiguities: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    clarification: str = ""
    exclusions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    recommendation: ResolutionRecommendation = ResolutionRecommendation.PROCEED


class ResolutionEnvelope(BaseModel):
    ran: bool = False
    triggers: list[str] = Field(default_factory=list)
    advice: Optional[ResolutionAdvice] = None
    parse_error: str = ""

    def redacted_projection(self) -> dict[str, Any]:
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return re.sub(
                    r"(?i)(api[_ -]?key|password|token|secret|credential)\s*[:=]\s*\S+",
                    r"\1=[redacted]",
                    value,
                )[:2000]
            if isinstance(value, list):
                return [redact(item) for item in value[:100]]
            if isinstance(value, dict):
                return {str(key): redact(item) for key, item in list(value.items())[:100]}
            return value
        return {
            "ran": self.ran,
            "triggers": list(self.triggers),
            "advice": redact(self.advice.model_dump(mode="json")) if self.advice else None,
            "parse_error": redact(self.parse_error[:240]),
            "raw_response_omitted": True,
        }


_HIGH_RISK = re.compile(r"\b(delete|remove|overwrite|send|publish|deploy|install|execute|run command|move|rename)\b", re.I)
_AMBIGUOUS_REF = re.compile(r"\b(that|this|it|the previous one|continue that|do it)\b", re.I)
_TRIVIAL = re.compile(r"^(?:hi|hello|hey|thanks|thank you|\d+\s*[+*/-]\s*\d+)[.!?\s]*$", re.I)


class EchoResolutionEngine:
    """Pure bounded adviser: no stores, tool handles, approvals, or callbacks."""

    def triggers_for(self, *, user_text: str, mode_decision: Any, project_id: str, pending_action: bool) -> list[str]:
        text = str(user_text or "").strip()
        if not text or _TRIVIAL.fullmatch(text):
            return []
        triggers: list[str] = []
        if float(getattr(mode_decision, "confidence", 1.0) or 0.0) < 0.80:
            triggers.append("low_confidence")
        if bool(getattr(mode_decision, "ambiguous", False)):
            triggers.append("ambiguous_intent")
        if _AMBIGUOUS_REF.search(text) and not str(getattr(mode_decision, "current_subject", "") or ""):
            triggers.append("unresolved_reference")
        if _HIGH_RISK.search(text):
            triggers.append("high_risk_operation")
        constraints = {str(item).lower() for item in (getattr(mode_decision, "constraints", None) or [])}
        if any("read" in item and "only" in item for item in constraints) and re.search(r"\b(change|edit|write|delete)\b", text, re.I):
            triggers.append("constraint_conflict")
        mode = str(getattr(getattr(mode_decision, "mode", None), "value", getattr(mode_decision, "mode", "")))
        if mode == "coding" and not project_id:
            triggers.append("missing_project")
        if pending_action and str(getattr(mode_decision, "intent_relation", "")) == "new_objective":
            triggers.append("pending_action_conflict")
        return list(dict.fromkeys(triggers))

    def resolve(
        self,
        *,
        user_text: str,
        mode_decision: Any,
        project_id: str,
        session_id: str,
        available_tools: set[str],
        available_skills: set[str],
        pending_action: bool = False,
        adviser: Optional[Callable[[dict[str, Any]], str | dict[str, Any]]] = None,
    ) -> ResolutionEnvelope:
        triggers = self.triggers_for(
            user_text=user_text,
            mode_decision=mode_decision,
            project_id=project_id,
            pending_action=pending_action,
        )
        if not triggers:
            return ResolutionEnvelope(ran=False, triggers=[])
        mode = str(getattr(getattr(mode_decision, "mode", None), "value", getattr(mode_decision, "mode", "chat")))
        base = ResolutionAdvice(
            recommended_mode=mode if mode in {"chat", "task_research", "coding"} else "chat",
            interpreted_objective=str(getattr(mode_decision, "objective", "") or user_text)[:1000],
            subject=str(getattr(mode_decision, "current_subject", "") or "")[:500],
            project_id=project_id,
            session_id=session_id,
            required_capabilities=sorted(getattr(mode_decision, "required_capabilities", None) or []),
            recommended_tools=sorted(set(getattr(mode_decision, "allowed_tool_names", None) or []) & available_tools),
            confidence=float(getattr(mode_decision, "confidence", 0.5) or 0.5),
            ambiguities=list(triggers),
            recommendation=(
                ResolutionRecommendation.CLARIFY
                if {"missing_project", "constraint_conflict", "unresolved_reference"} & set(triggers)
                else ResolutionRecommendation.PROCEED
            ),
        )
        if adviser is None:
            return ResolutionEnvelope(ran=True, triggers=triggers, advice=base)
        request = {
            "objective": base.interpreted_objective,
            "mode": base.recommended_mode,
            "project_id": project_id,
            "session_id": session_id,
            "triggers": triggers,
            "constraints": sorted(getattr(mode_decision, "constraints", None) or []),
            "available_tools": sorted(available_tools),
            "available_skills": sorted(available_skills),
        }
        try:
            raw = adviser(request)
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
            advice = ResolutionAdvice.model_validate(payload)
            # Advisory output can narrow inventory, never expand authority or scope.
            if advice.project_id != project_id or advice.session_id != session_id:
                raise ValueError("resolution attempted to change authoritative scope")
            advice.recommended_tools = sorted(set(advice.recommended_tools) & available_tools)
            advice.recommended_skills = sorted(set(advice.recommended_skills) & available_skills)
            return ResolutionEnvelope(ran=True, triggers=triggers, advice=advice)
        except Exception as exc:
            base.recommendation = (
                ResolutionRecommendation.CLARIFY
                if {"missing_project", "constraint_conflict", "unresolved_reference", "high_risk_operation"} & set(triggers)
                else ResolutionRecommendation.PROCEED
            )
            return ResolutionEnvelope(ran=True, triggers=triggers, advice=base, parse_error=str(exc))
