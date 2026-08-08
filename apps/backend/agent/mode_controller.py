"""Deterministic turn-mode controller.

This module is the runtime control plane for a user turn.  It classifies the
turn once, records the selected mode, and derives model/tool/verification policy
from that decision.  The language model can still generate prose, but it cannot
decide which operational lane it is in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, FrozenSet, Iterable, Optional

try:
    from agent.active_work import request_approves_plan, request_continues_project
except Exception:  # pragma: no cover - import-safe fallback for isolated tests
    request_approves_plan = None  # type: ignore
    request_continues_project = None  # type: ignore

try:
    from agent.intent_guard import is_explicit_new_project_request, is_information_request
except Exception:  # pragma: no cover
    def is_information_request(_: str) -> bool:
        return False

    def is_explicit_new_project_request(_: str) -> bool:
        return False

try:
    from agent.research import is_deep_research_intent
except Exception:  # pragma: no cover
    def is_deep_research_intent(_: str) -> bool:
        return False


class TurnMode(str, Enum):
    CHAT = "chat"
    TASK_RESEARCH = "task_research"
    CODING = "coding"


class CodingPhaseName(str, Enum):
    INSPECT = "inspect"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    CONFIRM = "confirm"
    SUMMARIZE = "summarize"


def is_search_retry_utterance(text: str) -> bool:
    """True when the user is asking to re-run a prior search/lookup.

    Must match natural phrasing such as \"try again with that search\" without
    requiring a bare \"try again\" full match.
    """
    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not low:
        return False
    return bool(
        re.search(
            r"(?i)\b("
            r"(?:try|retry)(?:\s+\w+){0,6}\s+(?:that\s+|the\s+|this\s+)?search|"
            r"(?:try|retry)\s+again(?:\s+with)?(?:\s+(?:that|the|this))?\s+search|"
            r"retry\s+(?:the\s+|that\s+|this\s+)?search|"
            r"search\s+(?:it|that|this)\s+again|"
            r"(?:do|run)\s+(?:that|the|this)\s+search\s+again|"
            r"look\s+(?:it|that)\s+up\s+again|"
            r"search\s+again"
            r")\b",
            low,
        )
    )


# Mode classification chooses capabilities and evidence policy, never a model.
# Blank ModeDecision model fields mean "use the exact Session model binding".

RESEARCH_TOOLS: FrozenSet[str] = frozenset(
    {
        "web_search",
        "safe_web_fetch",
        "weather_live",
        "sports_live",
        "get_system_time",
        "calculate",
        "youtube_transcript",
        "browse_task",
        "system_info",
    }
)

CODING_READ_TOOLS: FrozenSet[str] = frozenset(
    {
        "file_list",
        "file_read",
        "project_status",
        "project_update_context",
        "system_info",
    }
)

CODING_WRITE_TOOLS: FrozenSet[str] = frozenset(
    {
        "file_write",
        "file_mkdir",
        "file_move",
        "file_copy",
        "file_delete",
        "artifact_write",
        "notepad_write",
        "checkpoint_undo",
        "code_preview_start",
        "code_preview_stop",
    }
)

CODING_VERIFY_TOOLS: FrozenSet[str] = frozenset(
    {
        "terminal_run",
        "file_list",
        "file_read",
        "project_status",
        "system_info",
        "code_preview_start",
        "code_preview_stop",
    }
)


@dataclass(frozen=True)
class ModeDecision:
    mode: TurnMode
    confidence: float
    reason: str
    user_text: str
    coding_phase: Optional[CodingPhaseName] = None
    model_provider: str = ""
    model_name: str = ""
    allowed_tool_names: FrozenSet[str] = field(default_factory=frozenset)
    verification_required: bool = False
    evidence_required: bool = False
    can_switch_mid_turn: bool = False
    ambiguous: bool = False
    fallback_mode: TurnMode = TurnMode.CHAT
    objective: str = ""
    current_subject: str = ""
    active_project_path: str = ""
    continuation_context: str = ""
    required_capabilities: FrozenSet[str] = field(default_factory=frozenset)
    constraints: FrozenSet[str] = field(default_factory=frozenset)
    intent_relation: str = "new_objective"

    def with_allowed_tools(self, names: Iterable[str]) -> "ModeDecision":
        return replace(self, allowed_tool_names=frozenset(str(n) for n in names if str(n or "").strip()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "coding_phase": self.coding_phase.value if self.coding_phase else None,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "allowed_tool_names": sorted(self.allowed_tool_names),
            "verification_required": self.verification_required,
            "evidence_required": self.evidence_required,
            "can_switch_mid_turn": self.can_switch_mid_turn,
            "ambiguous": self.ambiguous,
            "fallback_mode": self.fallback_mode.value,
            "objective": self.objective,
            "current_subject": self.current_subject,
            "active_project_path": self.active_project_path,
            "continuation_context": self.continuation_context,
            "required_capabilities": sorted(self.required_capabilities),
            "constraints": sorted(self.constraints),
            "intent_relation": self.intent_relation,
        }


def normalize_user_text(user_input: str) -> str:
    raw = str(user_input or "").strip()
    if not raw:
        return ""
    marker = "user request:"
    low = raw.lower()
    idx = low.rfind(marker)
    if idx != -1:
        raw = raw[idx + len(marker) :].strip()
    live = raw.lower().find("live desktop context:")
    if live != -1:
        raw = raw[:live].strip()
    return re.sub(r"\s+", " ", raw).strip()


def _has_coding_operation(text: str) -> bool:
    low = text.lower()
    if not low:
        return False
    # Educational questions can mention code without asking Echo to operate on a
    # workspace. Keep those conversational unless a concrete local target exists.
    if re.match(r"^(?:could you |can you |please )?(?:explain|teach me|what (?:is|are)|why |how (?:do|does|can|should|would) )", low):
        if not re.search(r"\b(?:this|my|the|current|attached)\s+(?:repo|repository|codebase|project|file|folder|workspace)\b", low):
            return False
    if low in {"/undo", "undo", "undo last change", "rollback last change"}:
        return True
    if re.search(r"\b(?:pytest|npm|pnpm|yarn|vite|stack trace|traceback|build logs?)\b", low):
        return True
    if re.search(
        r"\b(?:fix|debug|test|compile|lint|typecheck|refactor|inspect|review|edit|update|implement)\b"
        r".{0,80}\b(?:repo|repository|codebase|bug|implementation|branch|pull request|diff|patch|code)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(read|open|edit|modify|update|write|create|delete|move|copy|rename|list)\s+"
        r"(?:the\s+)?(?:file|folder|directory|repo|code|source|project)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:write|build|create|make|implement)\b.{0,100}\b(?:javascript|typescript|python|html|css|demo|script|program)\b",
        low,
    ):
        return True
    # Feature work inside software and games is coding even when the request
    # uses domain nouns (score, enemies, health) that also occur in live-sports
    # questions.  Require an edit verb and a software/game entity, while
    # excluding the ordinary live-score phrasing, so this remains structural
    # rather than a title- or project-specific exception.
    if (
        re.search(r"\b(?:add|implement|change|update|fix|make|remove)\b", low)
        and re.search(
            r"\b(?:player|enemy|enemies|gameplay|collision|health|score|power[ -]?up|"
            r"pause button|level|mechanic|spawn|despawn|game object)\b",
            low,
        )
        and not re.search(
            r"\b(?:live score|what(?:'s| is) the score|score tonight|team score|"
            r"match score|fixture|standings?)\b",
            low,
        )
    ):
        return True
    if re.search(r"\b(?:create|make|write)\s+(?:me\s+)?(?:a|an)\s+(?:[a-z0-9_-]+\s+){0,3}(?:script|file)\b", low):
        return True
    if re.search(r"\b[a-z0-9_.-]+\.(py|ts|tsx|js|jsx|json|md|css|html|go|rs|java|cpp|c|cs|yaml|yml)\b", low):
        return True
    if re.search(
        r"\b("
        r"on my desktop|my desktop|look on (?:my )?desktop|list (?:my )?desktop|"
        r"read the files?|read (?:the )?project|open (?:the )?(?:project|folder|files?)|"
        r"list (?:the )?files?|inspect (?:the )?(?:project|folder|code|files?)|"
        r"look at (?:the )?(?:files?|code|project|folder)|"
        r"scan (?:the )?(?:folder|project|files?|directory|code)|"
        r"understand (?:the )?project|go through (?:the )?files?|"
        r"read every file|read all files|make (?:a )?plan|"
        r"get (?:this |the )?game playable|make (?:it |the game )?playable|"
        r"add a comment|implement (?:the |this )?(?:feature|fix|plan)"
        r")\b",
        low,
    ):
        return True
    if _local_project_state_intent(text) or _proposal_only_intent(text):
        return True
    return False


def _is_utility_tool_request(text: str) -> bool:
    """Clock/date/calc-only requests — not web research, no verification gate."""
    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not low:
        return False
    # Live research keywords win over pure arithmetic phrasing.
    if re.search(
        r"\b(search|look up|research|online|web|weather|score|news|price|fifa|match|"
        r"bitcoin|stock|headline|forecast)\b",
        low,
    ):
        return False
    if re.fullmatch(
        r"(?:please\s+)?(?:"
        r"what(?:'s| is)?\s+the\s+time(?:\s+is\s+it)?|"
        r"what\s+time\s+is\s+it|"
        r"current\s+time|"
        r"time\s+please|"
        r"what(?:'s| is)?\s+(?:the\s+)?date(?:\s+today)?|"
        r"what\s+day\s+is\s+it|"
        r"today(?:'s)?\s+date"
        r")[.!?]?",
        low,
    ):
        return True
    if re.search(r"\b(calculate|compute|solve|evaluate)\b", low) and re.search(r"\d", low):
        return True
    # Unit conversion is local calculator work, not web research.
    if re.search(
        r"\bconvert\b.{0,40}\b(?:celsius|fahrenheit|°?\s*c|°?\s*f|"
        r"kilometers?|miles?|kg|pounds?|lbs?)\b",
        low,
    ) and re.search(r"\d", low):
        return True
    # Arithmetic questions: "what is 17 * 19", "17 x 19?", "17 times 19"
    if re.search(
        r"(?:"
        r"what(?:'s|\s+is)\s+"
        r")?"
        r"[\d.]+\s*(?:[+\-*/^x×]|times|plus|minus|divided\s+by)\s*[\d.]+",
        low,
    ) and not re.search(r"\b(who|where|when|why|news|weather|score)\b", low):
        return True
    if re.fullmatch(r"[\d\s+\-*/^().%x×]+", low) and re.search(r"\d", low):
        return True
    return False


def _is_checkable_task(text: str) -> bool:
    low = text.lower()
    if not low:
        return False
    if _is_utility_tool_request(text):
        return False
    if re.search(r"\b(?:do not|don't|dont|without|why did you|why do you)\s+(?:use\s+)?(?:search|research|look up)\b", low):
        return False
    if is_deep_research_intent(text):
        return True
    explicit_lookup = bool(re.search(
        r"(?:^|[.!?]\s*|\bplease\s+)(?:search(?:\s+(?:the\s+)?web)?(?:\s+for)?|look up|research|find out|fact[- ]?check)\b",
        low,
    ))
    if re.search(
        r"\b("
        r"compare sources|evidence|citations?|"
        r"weather|forecast|score|scores|schedule|matches|fixtures|odds|"
        r"price|stock|bitcoin|exchange rate|news|headline"
        r")\b",
        low,
    ):
        return True
    if explicit_lookup:
        return True
    if re.search(
        r"\b(?:latest|current|today(?:'s)?|tonight|tomorrow|recently released)\b.{0,70}"
        r"\b(?:version|release|status|result|event|office|holder|documentation|guidance|availability|information)\b",
        low,
    ):
        return True
    if re.search(r"\b(?:today|tonight|tomorrow|later)\b", low) and re.search(
        r"\b(?:club|clubs|team|teams|match|matches|game|games|fixture|fixtures|pitch|play|playing)\b",
        low,
    ):
        return True
    if re.search(r"\d\s*[+\-*/^]\s*\d", low):
        return True
    if re.search(r"\bwhen\s+(?:does|do|is|are)\b.{0,80}\b(?:play|start|begin|air|release|launch)\b", low):
        return True
    if re.search(r"\bwho\s+(?:is|are)\s+(?:the\s+)?(?:president|prime minister|governor|mayor|ceo|chair|leader|coach|manager)\b", low):
        return True
    if re.search(r"\b(?:where (?:can|should) i (?:buy|go|stay|eat)|is .{1,60} (?:in stock))\b", low):
        return True
    # Pure arithmetic is utility (CHAT + calculate), never TASK_RESEARCH.
    # Previously `\d + \d` forced research and caused false web_search for "17 * 19".
    return False


def _explicit_research_step(text: str) -> bool:
    low = str(text or "").lower()
    return bool(re.search(
        r"\b(search (?:the )?web|look up(?: online)?|research|check (?:the )?(?:official |current |latest )?docs?|"
        r"official documentation|current documentation|find (?:the )?(?:latest|current) (?:guidance|docs?|information))\b",
        low,
    ))


def extract_turn_constraints(text: str) -> FrozenSet[str]:
    low = str(text or "").lower()
    constraints: set[str] = set()
    if re.search(r"\b(read[- ]?only|do not modify|don't modify|without modifying|no changes?)\b", low):
        constraints.add("read_only")
    if re.search(r"\b(wait for (?:my )?approval|do not (?:apply|implement|write) (?:until|without)|don't (?:apply|implement|write) (?:until|without))\b", low):
        constraints.add("wait_for_approval")
    if re.search(r"\b(propos(?:e|al) only|only propose|inspect.{0,80}propose|propose.{0,80}wait for approval)\b", low):
        constraints.add("proposal_only")
    if re.search(r"\b(primary sources? only|use (?:only )?primary sources?|official sources? only)\b", low):
        constraints.add("primary_sources")
    if re.search(r"\b(local[- ]first|use local (?:files|state|context) first|do not search the web|don't search the web)\b", low):
        constraints.add("local_first")
    if re.search(r"\b(no delet(?:e|ion)|(?:do not|don't|never)[^.!?]{0,40}\bdelet(?:e|ion))\b", low):
        constraints.add("no_delete")
    if re.search(r"\b(verify (?:the )?(?:result|changes)|verification required|run (?:the )?(?:tests|checks))\b", low):
        constraints.add("verification_required")
    return frozenset(constraints)


def _explicit_read_only_intent(text: str) -> bool:
    low = str(text or "").lower()
    if re.search(r"\b(do not modify|don't modify|read[- ]?only|without modifying|wait for (?:my )?approval|proposal only)\b", low):
        return True
    inspect = bool(re.search(r"\b(inspect|check|understand|explain|analy[sz]e|review|tell me|show me)\b", low))
    modify = bool(re.search(
        r"\b(implement|apply|edit|modify|change|write|delete|remove|rename|move|copy|"
        r"create|build|fix (?:it|the|this)|make (?:the|this) change)\b",
        low,
    ))
    return inspect and not modify


def _proposal_only_intent(text: str) -> bool:
    low = str(text or "").lower()
    return bool(
        re.search(r"\b(wait for (?:my )?approval|proposal only|only propose|do not implement|don't implement)\b", low)
        or (re.search(r"\bpropos(?:e|al)\b", low) and re.search(r"\binspect|analy[sz]e|review\b", low))
    )


def _local_project_state_intent(text: str) -> bool:
    low = str(text or "").lower()
    return bool(
        re.search(
            r"\b(current objective|active objective|unfinished work|remaining work|what(?:'s| is) left|"
            r"project status|project progress|active work|completed work|open steps?|project architecture)\b",
            low,
        )
    )


def _explicit_modification_intent(text: str) -> bool:
    low = str(text or "").lower()
    return bool(
        re.search(
            r"\b("
            r"implement|apply|edit|modify|change|write|delete|remove|rename|move|copy|create|build|scaffold|"
            r"add|insert|comment|annotate|patch|tweak|refactor|"
            r"update (?:the )?(?:code|file|project)|"
            r"fix (?:it|the|this|bug|issue)|"
            r"make (?:it |the |this |the game )?(?:playable|work|run)|"
            r"get (?:it |this |the )?(?:game )?(?:playable|working|running)|"
            r"make a plan"
            r")\b",
            low,
        )
        or re.search(r"\bmake\s+(?:me\s+)?(?:a|an)\s+(?:[a-z0-9_.-]+\s+){0,3}(?:file|script)\b", low)
        or re.search(r"\badd\s+(?:a\s+)?comment\b", low)
        or re.search(r"(?i)\bplayable\b", low)
    )


def _intent_relation(text: str, *, continues: bool, explicit_new: bool) -> str:
    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
    # Accept assistant-offered next actions (must resolve against pending_offered_action).
    # Includes "yes proceed with the changes" — not only single-word "yes".
    if re.fullmatch(
        r"(?:please\s+)?(?:"
        r"confirm|"
        r"y(?:es|eah|ep|up)?(?:\s+please)?|"
        r"ok(?:ay)?(?:\s+(?:please|do\s+(?:it|that)|go\s+ahead|sure))?|"
        r"sure(?:\s+(?:do\s+(?:it|that)|thing))?|"
        r"do\s+(?:it|that)|go\s+ahead|go\s+for\s+it|proceed|"
        r"look\s+it\s+up|search\s+it|please\s+do|"
        r"sounds\s+good|that\s+works|do\s+the\s+(?:search|research|lookup)|"
        r"y(?:es|eah|ep)?(?:\s*,?\s*)?(?:please\s+)?(?:proceed|confirm|do\s+it|go\s+ahead)"
        r"(?:\s+with\s+(?:the\s+)?(?:change|changes|edit|edits|update|plan|that|it))?|"
        r"proceed(?:\s+with\s+(?:the\s+)?(?:change|changes|edit|edits|update|plan|that|it))?|"
        r"yes\s+proceed(?:\s+with\s+(?:the\s+)?(?:change|changes|edit|edits|that|it))?"
        r")[.!?]?",
        low,
    ):
        return "confirm"
    if re.search(
        r"(?i)^\s*(?:yes|yeah|yep|ok|okay|sure)(?:\s*,|\s+please)?\s+"
        r"(?:proceed|confirm|do\s+it|go\s+ahead|apply|make\s+the\s+change)",
        low,
    ) and len(low.split()) <= 14:
        return "confirm"
    if re.fullmatch(
        r"(?:please\s+)?(?:"
        r"cancel|no(?:pe)?|n|stop|never\s+mind|nevermind|abort|dismiss|"
        r"no\s+thanks|no\s+thank\s+you|don'?t|dont|not\s+now|skip"
        r")[.!?]?",
        low,
    ):
        return "cancel"
    # Retry/try-again is always a continuation signal (coding or multi-task research).
    # Include "try again with that search", "retry the search", "search it again", etc.
    if re.fullmatch(r"(?:please )?(?:try|retry)(?: that| it| again)?", low) or re.fullmatch(
        r"(?:please )?try again[.!?]?", low
    ):
        return "retry"
    if is_search_retry_utterance(low):
        return "retry"
    if explicit_new:
        return "new_objective"
    # Explicit continue phrases resume unfinished work even without a coding ActiveWork project.
    if re.fullmatch(
        r"(?:please )?(?:continue|resume|keep going|go ahead|proceed|where were we|pick up where we stopped)[.!?]?",
        low,
    ):
        return "continue"
    if continues and re.fullmatch(
        r"(?:please )?(?:continue|resume|keep going|go ahead|proceed|where were we|pick up where we stopped)[.!?]?",
        low,
    ):
        return "continue"
    return "new_objective"


def _coding_phase_for(text: str, active_work: Any = None) -> CodingPhaseName:
    if str(text or "").strip().lower() in {"/undo", "undo", "undo last change", "rollback last change"}:
        return CodingPhaseName.IMPLEMENT
    if _proposal_only_intent(text):
        return CodingPhaseName.PLAN
    if _explicit_read_only_intent(text) or (_explicit_research_step(text) and not _explicit_modification_intent(text)):
        return CodingPhaseName.INSPECT
    if is_explicit_new_project_request(text):
        return CodingPhaseName.IMPLEMENT
    if _explicit_modification_intent(text):
        return CodingPhaseName.IMPLEMENT
    phase = str(getattr(active_work, "phase", "") or "").strip().lower()
    continues = False
    approves = False
    if active_work is not None and request_continues_project is not None:
        try:
            continues = bool(request_continues_project(text, active_work))
        except Exception:
            continues = False
    if request_approves_plan is not None:
        try:
            approves = bool(request_approves_plan(text))
        except Exception:
            approves = False
    relation = _intent_relation(text, continues=continues, explicit_new=False)
    if continues and approves:
        return CodingPhaseName.IMPLEMENT
    # Bare "yes" / "proceed with the changes" on an active project means implement,
    # not another inspect pass — otherwise confirmations stall as "What about it?".
    if relation == "confirm" and getattr(active_work, "project_path", ""):
        if phase == "verify":
            return CodingPhaseName.VERIFY
        if phase == "confirm":
            return CodingPhaseName.CONFIRM
        if phase == "summarize":
            return CodingPhaseName.SUMMARIZE
        if phase == "plan":
            return CodingPhaseName.IMPLEMENT
        return CodingPhaseName.IMPLEMENT
    if phase in {"implement", "verify", "confirm", "summarize"} and relation in {"continue", "confirm"}:
        if phase == "verify":
            return CodingPhaseName.VERIFY
        if phase == "confirm":
            return CodingPhaseName.CONFIRM
        if phase == "summarize":
            return CodingPhaseName.SUMMARIZE
        return CodingPhaseName.IMPLEMENT
    if phase == "plan" and relation == "continue":
        return CodingPhaseName.PLAN
    if getattr(active_work, "project_path", "") and relation == "continue":
        return CodingPhaseName.INSPECT
    return CodingPhaseName.INSPECT


def classify_turn_mode(user_input: str, *, source: str = "", active_work: Any = None) -> ModeDecision:
    """Classify a user turn once, deterministically."""
    text = normalize_user_text(user_input)
    low = text.lower()
    src = str(source or "").strip().lower()

    if not text:
        return ModeDecision(
            mode=TurnMode.CHAT,
            confidence=1.0,
            reason="empty input",
            user_text=text,
            model_provider="",
            model_name="",
            required_capabilities=frozenset({"chat"}),
        )

    continues_project = False
    if active_work is not None and request_continues_project is not None:
        try:
            continues_project = bool(request_continues_project(text, active_work))
        except Exception:
            continues_project = False

    explicit_new_project = is_explicit_new_project_request(text)
    coding_operation = _has_coding_operation(text)
    info_request = is_information_request(text)
    constraints = extract_turn_constraints(text)
    relation = _intent_relation(text, continues=continues_project, explicit_new=explicit_new_project)
    if continues_project and request_approves_plan is not None:
        try:
            if request_approves_plan(text):
                relation = "confirm"
        except Exception:
            pass
    active_project = bool(getattr(active_work, "project_path", ""))
    research_step = _explicit_research_step(text)
    local_state_request = _local_project_state_intent(text)
    proposal_only = _proposal_only_intent(text)
    read_only = _explicit_read_only_intent(text)
    modifies = _explicit_modification_intent(text)

    # Clock/date/calc utilities are CHAT — never TASK_RESEARCH / verification-gated.
    if _is_utility_tool_request(text):
        return ModeDecision(
            mode=TurnMode.CHAT,
            confidence=0.95,
            reason="utility tool request (clock/date/calc)",
            user_text=text,
            model_provider="",
            model_name="",
            verification_required=False,
            evidence_required=False,
            required_capabilities=frozenset({"chat"}),
            constraints=constraints,
            intent_relation=relation,
        )

    coding_research = bool(research_step and (active_project or coding_operation) and not modifies)
    coding_intent = bool(
        explicit_new_project
        or coding_operation
        or local_state_request
        or proposal_only
        or coding_research
        or (continues_project and (read_only or modifies or research_step or local_state_request))
        or (relation == "confirm" and active_project)
        or (relation == "continue" and active_project)
    )

    if coding_intent and not (info_request and not (coding_operation or local_state_request or coding_research)):
        phase = _coding_phase_for(text, active_work)
        compound_research = research_step
        return ModeDecision(
            mode=TurnMode.CODING,
            confidence=0.96 if (continues_project or explicit_new_project) else 0.86,
            reason=(
                "local project state inspection"
                if local_state_request
                else "read-only project research"
                if coding_research
                else "proposal-only project work"
                if proposal_only
                else "active project continuation"
                if relation in {"continue", "confirm"}
                else "coding operation or project creation"
            ),
            user_text=text,
            coding_phase=phase,
            model_provider="",
            model_name="",
            verification_required=True,
            evidence_required=compound_research,
            required_capabilities=frozenset({"coding", "research"} if compound_research else {"coding"}),
            constraints=constraints,
            intent_relation=relation,
        )

    # Referential search retry must be research, not tool-free chat.
    if relation == "retry" and is_search_retry_utterance(text):
        return ModeDecision(
            mode=TurnMode.TASK_RESEARCH,
            confidence=0.92,
            reason="referential search retry",
            user_text=text,
            model_provider="",
            model_name="",
            verification_required=True,
            evidence_required=True,
            required_capabilities=frozenset({"research"}),
            constraints=constraints,
            intent_relation="retry",
        )

    if _is_checkable_task(text):
        deep = is_deep_research_intent(text)
        return ModeDecision(
            mode=TurnMode.TASK_RESEARCH,
            confidence=0.94 if deep else 0.82,
            reason="deep research intent" if deep else "checkable task or live information request",
            user_text=text,
            model_provider="",
            model_name="",
            verification_required=True,
            evidence_required=True,
            required_capabilities=frozenset({"research"}),
            constraints=constraints,
            intent_relation=relation,
        )

    ambiguous = bool(re.search(r"\b(make|build|create|do it|start|continue|look into)\b", low))
    if src in {"routine", "heartbeat", "proactive", "cron"}:
        ambiguous = False
    return ModeDecision(
        mode=TurnMode.CHAT,
        confidence=0.72 if ambiguous else 0.9,
        reason="no operational or checkable intent",
        user_text=text,
        model_provider="",
        model_name="",
        ambiguous=ambiguous,
        required_capabilities=frozenset({"chat"}),
        constraints=constraints,
        intent_relation=relation,
    )


def allowed_tools_for_mode(decision: ModeDecision, available_tool_names: Iterable[str]) -> FrozenSet[str]:
    """Bind the least-privilege tool inventory for this classified turn.

    This is one input to authority, not the final authority owner: registration,
    Project scope, role policy, configuration, approval, and tool-boundary
    validation still apply. A mode can reduce capability but never grant a tool
    that is absent from the canonical registry inventory.
    """
    available = frozenset(str(n) for n in available_tool_names if str(n or "").strip())
    if not available:
        return frozenset()

    if decision.mode == TurnMode.CHAT:
        requested = (
            frozenset({"get_system_time"})
            if re.search(r"\b(?:time|date|day)\b", decision.user_text, flags=re.IGNORECASE)
            else frozenset({"calculate"})
            if _is_utility_tool_request(decision.user_text)
            else frozenset()
        )
    elif decision.mode == TurnMode.TASK_RESEARCH:
        requested = RESEARCH_TOOLS
    else:
        phase = decision.coding_phase or CodingPhaseName.INSPECT
        selected = set(CODING_READ_TOOLS)
        if "research" in decision.required_capabilities:
            selected.update(RESEARCH_TOOLS)
        if phase == CodingPhaseName.IMPLEMENT:
            selected.update(CODING_WRITE_TOOLS)
            selected.update(CODING_VERIFY_TOOLS)
            selected.update({"image_apply_operations", "image_export"})
        elif phase == CodingPhaseName.VERIFY:
            selected.update(CODING_VERIFY_TOOLS)
        requested = frozenset(selected)

    constraints = set(decision.constraints or frozenset())
    if "read_only" in constraints or "proposal_only" in constraints:
        requested = requested - CODING_WRITE_TOOLS - frozenset(
            {"terminal_run", "image_apply_operations", "image_export"}
        )
    if "no_delete" in constraints:
        requested = requested - frozenset({"file_delete"})
    if "local_first" in constraints and decision.mode != TurnMode.TASK_RESEARCH:
        requested = requested - frozenset({"web_search", "safe_web_fetch", "browse_task", "youtube_transcript"})
    return frozenset(available & requested)


def tool_allowed_by_mode(decision: Optional[ModeDecision], tool_name: str) -> bool:
    """Check the immutable per-turn capability inventory bound after routing."""
    if decision is None:
        return True
    name = str(tool_name or "").strip()
    if not name:
        return False
    return name in (decision.allowed_tool_names or frozenset())
