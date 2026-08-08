"""Versioned canonical research artifacts and exact-scope access helpers.

The canonical semantic runtime and research runtime own planning, acquisition,
and completion. SearchGrounder is a bounded compatibility acquisition adapter;
this module owns only durable research records and their migration/access
contract.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

try:
    from config import DATA_DIR
except Exception:
    DATA_DIR = Path("data")

_ROOT = Path(DATA_DIR) / "research_artifacts"
_LOCK = threading.RLock()
CURRENT_RESEARCH_SCHEMA_VERSION = 3


ResearchMode = Literal[
    "quick_lookup",
    "standard_research",
    "deep_research",
    "local_private",
    "live_structured",
]
ResearchStatus = Literal["pending", "ready", "failed", "stale"]
BranchStatus = Literal["pending", "active", "completed", "blocked", "failed", "cancelled"]
VerificationStatus = Literal["pending", "passed", "failed", "inconclusive", "skipped"]


class ResearchBudget(BaseModel):
    max_time_seconds: float = Field(default=120.0, ge=0)
    max_sources: int = Field(default=12, ge=0)
    max_branches: int = Field(default=4, ge=0)
    max_queries: int = Field(default=12, ge=0)
    max_retries: int = Field(default=2, ge=0)
    max_context_tokens: int = Field(default=12000, ge=0)
    max_model_calls: int = Field(default=8, ge=0)
    max_external_calls: int = Field(default=16, ge=0)


class ResearchPlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    mode: ResearchMode = "quick_lookup"
    question: str = ""
    resolved_scope: str = ""
    resolved_entities: List[str] = Field(default_factory=list)
    branch_ids: List[str] = Field(default_factory=list)
    stop_conditions: List[str] = Field(default_factory=list)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ResearchBranch(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_branch_id: str = ""
    question: str = ""
    objective: str = ""
    status: BranchStatus = "pending"
    query_count: int = Field(default=0, ge=0)
    source_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    error: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ResearchSource(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str = ""
    provider: str = ""
    source_identifier: str = ""
    title: str = ""
    url: str = ""
    source_type: str = "web"
    authority: str = "unknown"
    published_at: Optional[float] = None
    retrieved_at: float = Field(default_factory=time.time)
    freshness: str = "unknown"
    checksum: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str = ""
    source_id: str
    content: str
    locator: str = ""
    extracted_at: float = Field(default_factory=time.time)
    effective_at: Optional[float] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    exact: bool = False
    provenance: Dict[str, Any] = Field(default_factory=dict)
    requirement_id: str = ""
    attempt_id: str = ""
    covered_fields: List[str] = Field(default_factory=list)
    unavailable_fields: List[str] = Field(default_factory=list)


class ClaimRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str = ""
    text: str
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: Literal["proposed", "supported", "disputed", "rejected"] = "proposed"
    contradiction_ids: List[str] = Field(default_factory=list)


class ContradictionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    description: str = ""
    status: Literal["open", "resolved", "accepted_uncertainty"] = "open"
    resolution: str = ""


class CoverageGap(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str = ""
    question: str = ""
    reason: str = ""
    severity: Literal["low", "medium", "high"] = "medium"
    status: Literal["open", "resolved", "accepted"] = "open"


class VerificationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    method: str = ""
    status: VerificationStatus = "pending"
    summary: str = ""
    verified_at: Optional[float] = None
    model_provider: str = ""
    model_id: str = ""


class ResearchArtifact(BaseModel):
    schema_version: int = CURRENT_RESEARCH_SCHEMA_VERSION
    migrated_from_schema: Optional[int] = None
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str = ""
    execution_id: str = ""
    tool_run_id: str = ""
    requirement_id: str = ""
    attempt_id: str = ""
    objective: str = ""
    query: str = ""
    resolved_scope: str = ""
    model_provider: str = ""
    model_id: str = ""
    plan: Optional[ResearchPlan] = None
    branches: List[ResearchBranch] = Field(default_factory=list)
    sources: List[ResearchSource] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    claims: List[ClaimRecord] = Field(default_factory=list)
    contradictions: List[ContradictionRecord] = Field(default_factory=list)
    coverage_gaps: List[CoverageGap] = Field(default_factory=list)
    verification: List[VerificationRecord] = Field(default_factory=list)
    summary: str = ""
    citations: List[Dict[str, str]] = Field(default_factory=list)
    source_urls: List[str] = Field(default_factory=list)
    status: ResearchStatus = "ready"
    outcome: str = ""
    execution_status: str = ""
    result_state: str = ""
    provider: str = ""
    observed_at: Optional[float] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ResearchArtifactStoreError(RuntimeError):
    """Canonical research state could not be safely read or written."""


class ResearchArtifactAccessError(PermissionError):
    """A caller attempted to access an artifact outside its exact scope."""


def _path(artifact_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "", artifact_id)[:64] or "unknown"
    return _ROOT / f"{safe}.json"


def _quarantine_and_raise(path: Path, error: BaseException) -> None:
    quarantine = _ROOT / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    copied = quarantine / path.name
    recovery = quarantine / "RECOVERY.txt"
    note = "quarantine unavailable"
    try:
        quarantine.mkdir(parents=True, exist_ok=False)
        shutil.copy2(path, copied)
        recovery.write_text(
            "EchoSpeak research-artifact recovery\n\n"
            f"Authoritative file: {path}\n"
            f"Quarantine copy: {copied}\n"
            f"Parse/schema error: {error}\n\n"
            "Manual recovery:\n"
            "1. Keep the backend stopped.\n"
            "2. Repair the authoritative JSON or restore a known-good copy.\n"
            "3. Preserve this quarantine directory until recovery is verified.\n"
            "4. Restart one backend instance and re-open the artifact.\n",
            encoding="utf-8",
        )
        note = f"quarantine copy: {copied}; recovery guide: {recovery}"
    except Exception as quarantine_error:
        note = f"quarantine failed: {quarantine_error}"
    raise ResearchArtifactStoreError(
        f"Authoritative research artifact is unreadable: {path}. "
        f"The original was not overwritten; {note}. ({error})"
    ) from error


def _migrate_payload_in_memory(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("research artifact root must be an object")
    version = int(payload.get("schema_version") or 1)
    if version > CURRENT_RESEARCH_SCHEMA_VERSION:
        raise ValueError(f"unsupported future research schema version: {version}")
    if version == CURRENT_RESEARCH_SCHEMA_VERSION:
        return dict(payload)
    if version not in {1, 2}:
        raise ValueError(f"unsupported research schema version: {version}")

    migrated = dict(payload)
    migrated["schema_version"] = CURRENT_RESEARCH_SCHEMA_VERSION
    migrated["migrated_from_schema"] = version
    migrated.setdefault("requirement_id", "")
    migrated.setdefault("attempt_id", "")
    migrated.setdefault("resolved_scope", "")
    migrated.setdefault("model_provider", "")
    migrated.setdefault("model_id", "")
    migrated.setdefault("plan", None)
    for key in (
        "branches",
        "sources",
        "evidence",
        "claims",
        "contradictions",
        "coverage_gaps",
        "verification",
    ):
        migrated.setdefault(key, [])
    migrated.setdefault("outcome", "")
    return migrated


def _read_artifact(path: Path) -> ResearchArtifact:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        migrated = _migrate_payload_in_memory(payload)
        return ResearchArtifact.model_validate(migrated)
    except ResearchArtifactStoreError:
        raise
    except Exception as exc:
        _quarantine_and_raise(path, exc)
    raise AssertionError("unreachable")


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception as exc:
        raise ResearchArtifactStoreError(f"Failed to atomically save research artifact {path}: {exc}") from exc
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def save_research_artifact(artifact: ResearchArtifact) -> ResearchArtifact:
    with _LOCK:
        stored = artifact.model_copy(
            deep=True,
            update={"schema_version": CURRENT_RESEARCH_SCHEMA_VERSION, "updated_at": time.time()},
        )
        _atomic_write(_path(stored.id), stored.model_dump(mode="json"))
        return stored


def get_research_artifact(artifact_id: str) -> Optional[ResearchArtifact]:
    path = _path(artifact_id)
    if not path.exists():
        return None
    with _LOCK:
        return _read_artifact(path)


def artifact_has_exact_scope(artifact: ResearchArtifact, *, project_id: str, session_id: str) -> bool:
    session = str(session_id or "").strip()
    if not session:
        return False
    return artifact.project_id == str(project_id or "").strip() and artifact.session_id == session


def require_exact_artifact_scope(
    artifact: ResearchArtifact,
    *,
    project_id: str,
    session_id: str,
) -> ResearchArtifact:
    if not str(session_id or "").strip():
        raise ResearchArtifactAccessError("Session identity is required")
    if not artifact_has_exact_scope(artifact, project_id=project_id, session_id=session_id):
        raise ResearchArtifactAccessError("Research artifact does not belong to the exact Project/Session scope")
    return artifact


def get_research_artifact_for_scope(
    artifact_id: str,
    *,
    project_id: str,
    session_id: str,
) -> Optional[ResearchArtifact]:
    artifact = get_research_artifact(artifact_id)
    if artifact is None:
        return None
    try:
        return require_exact_artifact_scope(artifact, project_id=project_id, session_id=session_id)
    except ResearchArtifactAccessError:
        return None


def list_research_artifacts(
    *,
    project_id: str = "",
    session_id: str = "",
    limit: int = 50,
) -> List[ResearchArtifact]:
    if not _ROOT.exists():
        return []
    rows: List[ResearchArtifact] = []
    with _LOCK:
        for path in sorted(_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            artifact = _read_artifact(path)
            if project_id and artifact.project_id != project_id:
                continue
            if session_id and artifact.session_id != session_id:
                continue
            rows.append(artifact)
            if len(rows) >= max(1, int(limit or 50)):
                break
    return rows


def list_research_artifacts_for_scope(
    *, project_id: str, session_id: str, limit: int = 50
) -> List[ResearchArtifact]:
    if not str(session_id or "").strip():
        raise ResearchArtifactAccessError("Session identity is required")
    return list_research_artifacts(project_id=project_id, session_id=session_id, limit=limit)


def find_compatible_research_artifact(
    *,
    project_id: str = "",
    session_id: str = "",
    objective: str = "",
    require_project: bool = True,
) -> Optional[ResearchArtifact]:
    """Find a reusable artifact inside one exact Project/Session boundary."""
    if not _ROOT.exists():
        return None
    project = str(project_id or "").strip()
    session = str(session_id or "").strip()
    if (require_project and not project) or not session:
        return None
    objective_tokens = set(re.findall(r"[a-z0-9]{3,}", str(objective or "").casefold()))
    best: Optional[ResearchArtifact] = None
    best_score = -1.0
    with _LOCK:
        for path in sorted(_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            artifact = _read_artifact(path)
            if artifact.status != "ready":
                continue
            if project and artifact.project_id != project:
                continue
            if artifact.session_id != session:
                continue
            if require_project and not artifact.project_id:
                continue
            score = 0.0
            if project and artifact.project_id == project:
                score += 2.0
            if artifact.session_id == session:
                score += 1.5
            tokens = set(re.findall(r"[a-z0-9]{3,}", f"{artifact.objective} {artifact.query}".casefold()))
            overlap = len(objective_tokens & tokens) if objective_tokens else 0
            if objective_tokens:
                if overlap == 0:
                    continue
                score += min(3.0, overlap * 0.5)
            if score > best_score:
                best_score = score
                best = artifact
    minimum = 2.0 if project else 1.0
    return best if best_score >= minimum else None


def consume_research_artifact_for_skill(
    artifact_id: str,
    *,
    project_id: str,
    session_id: str = "",
    skill_id: str = "",
    objective: str = "",
) -> Dict[str, Any]:
    artifact = get_research_artifact(artifact_id)
    if artifact is None:
        return {"ok": False, "error_code": "not_found", "error": "Research artifact not found"}
    if artifact.status != "ready":
        return {"ok": False, "error_code": "stale_context", "error": f"Artifact status is {artifact.status}"}
    project = str(project_id or "").strip()
    if not project or artifact.project_id != project:
        return {"ok": False, "error_code": "permission_denied", "error": "Exact Project scope is required"}
    if session_id and artifact.session_id != str(session_id).strip():
        return {"ok": False, "error_code": "stale_context", "error": "Artifact Session does not match"}
    if not artifact.citations and not artifact.source_urls and not artifact.sources:
        return {
            "ok": False,
            "error_code": "verification_failed",
            "error": "Artifact has no citations/sources; refuse uncited handoff",
        }
    return {
        "ok": True,
        "artifact": artifact.model_dump(mode="json"),
        "skill_id": skill_id,
        "objective": objective or artifact.objective,
        "citations": list(artifact.citations or []),
        "source_urls": list(artifact.source_urls or []),
        "summary": artifact.summary,
        "prose_fallback": False,
    }


def build_research_artifact_from_tool_output(
    *,
    output: str,
    query: str,
    project_id: str = "",
    session_id: str = "",
    execution_id: str = "",
    tool_run_id: str = "",
    objective: str = "",
    model_provider: str = "",
    model_id: str = "",
    execution_status: str = "",
    result_state: str = "",
    provider: str = "",
    observed_at: Optional[float] = None,
    confidence: Optional[float] = None,
    requirement_id: str = "",
    attempt_id: str = "",
    evidence_id: str = "",
    covered_fields: Optional[List[str]] = None,
    unavailable_fields: Optional[List[str]] = None,
    verified: bool = False,
) -> ResearchArtifact:
    """Compatibility builder for the existing SearchGrounder ToolRun path."""
    text = str(output or "").strip()
    urls = list(dict.fromkeys(re.findall(r"https?://[^\s\]\)\"']+", text)))[:12]
    citations = [{"url": url, "title": ""} for url in urls]
    sources = [
        ResearchSource(
            provider="web_search",
            source_identifier=url,
            url=url,
            source_type="web",
            provenance={"tool_run_id": tool_run_id},
        )
        for url in urls
    ]
    if not sources and tool_run_id:
        sources = [ResearchSource(
            provider=provider or "runtime_tool",
            source_identifier=tool_run_id,
            source_type="tool",
            retrieved_at=observed_at or time.time(),
            provenance={"tool_run_id": tool_run_id},
        )]
    evidence = []
    if text and sources:
        evidence = [EvidenceRecord(
            id=evidence_id or str(uuid.uuid4()),
            source_id=sources[0].id,
            content=re.sub(r"\s+", " ", text)[:12000],
            exact=bool(verified and result_state == "data_found"),
            confidence=float(confidence if confidence is not None else 0.5),
            requirement_id=requirement_id,
            attempt_id=attempt_id,
            covered_fields=list(covered_fields or []),
            unavailable_fields=list(unavailable_fields or []),
            provenance={"tool_run_id": tool_run_id},
        )]
    summary = re.sub(r"\s+", " ", text)[:1600]
    usable = bool(verified and str(result_state or "data_found") == "data_found")
    status: ResearchStatus = "ready" if usable and (summary or citations) else "failed"
    return ResearchArtifact(
        project_id=project_id,
        session_id=session_id,
        execution_id=execution_id,
        tool_run_id=tool_run_id,
        requirement_id=requirement_id,
        attempt_id=attempt_id,
        objective=objective or query,
        query=query,
        model_provider=model_provider,
        model_id=model_id,
        summary=summary,
        citations=citations,
        source_urls=urls,
        sources=sources,
        evidence=evidence,
        status=status,
        execution_status=execution_status,
        result_state=result_state,
        provider=provider,
        observed_at=observed_at,
        confidence=confidence,
    )
