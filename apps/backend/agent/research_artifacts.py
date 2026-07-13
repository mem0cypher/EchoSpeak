"""Canonical research artifacts for skill handoff.

Research prose is not a durable artifact. Skills consume ResearchArtifact
records with Project/Session ownership and citations.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from config import DATA_DIR
except Exception:
    DATA_DIR = Path("data")

_ROOT = Path(DATA_DIR) / "research_artifacts"
_LOCK = threading.RLock()


class ResearchArtifact(BaseModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str = ""
    execution_id: str = ""
    tool_run_id: str = ""
    objective: str = ""
    query: str = ""
    summary: str = ""
    citations: List[Dict[str, str]] = Field(default_factory=list)
    source_urls: List[str] = Field(default_factory=list)
    status: str = "ready"  # pending | ready | failed | stale
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


def _path(artifact_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "", artifact_id)[:64] or "unknown"
    return _ROOT / f"{safe}.json"


def save_research_artifact(artifact: ResearchArtifact) -> ResearchArtifact:
    with _LOCK:
        _ROOT.mkdir(parents=True, exist_ok=True)
        artifact.updated_at = time.time()
        path = _path(artifact.id)
        path.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return artifact


def get_research_artifact(artifact_id: str) -> Optional[ResearchArtifact]:
    path = _path(artifact_id)
    if not path.exists():
        return None
    try:
        return ResearchArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
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
            try:
                art = ResearchArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if project_id and art.project_id and art.project_id != project_id:
                continue
            if session_id and art.session_id and art.session_id != session_id:
                if project_id and art.project_id == project_id:
                    pass  # Project-scoped list may include other sessions
                else:
                    continue
            rows.append(art)
            if len(rows) >= max(1, int(limit or 50)):
                break
    return rows


def find_compatible_research_artifact(
    *,
    project_id: str = "",
    session_id: str = "",
    objective: str = "",
    require_project: bool = True,
) -> Optional[ResearchArtifact]:
    """Lookup newest compatible artifact for skill consumption.

    Never reuses an artifact from another Project. Session mismatch is allowed
    only when Project matches and objective tokens overlap. Stale/failed
    artifacts are rejected.
    """
    if not _ROOT.exists():
        return None
    if require_project and not str(project_id or "").strip():
        return None
    objective_tokens = set(re.findall(r"[a-z0-9]{3,}", str(objective or "").casefold()))
    best: Optional[ResearchArtifact] = None
    best_score = -1.0
    with _LOCK:
        for path in sorted(_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                art = ResearchArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if art.status != "ready":
                continue
            # Hard Project isolation
            if project_id and art.project_id and art.project_id != project_id:
                continue
            if project_id and not art.project_id:
                continue
            if session_id and art.session_id and art.session_id != session_id:
                # Same Project only — require objective overlap
                if not project_id or art.project_id != project_id:
                    continue
            score = 0.0
            if project_id and art.project_id == project_id:
                score += 2.0
            if session_id and art.session_id == session_id:
                score += 1.5
            tokens = set(re.findall(r"[a-z0-9]{3,}", f"{art.objective} {art.query}".casefold()))
            overlap = len(objective_tokens & tokens) if objective_tokens else 0
            if objective_tokens:
                if overlap == 0 and art.session_id != session_id:
                    continue
                score += min(3.0, overlap * 0.5)
            if score > best_score:
                best_score = score
                best = art
    # Require meaningful match: project pin at minimum
    min_score = 2.0 if project_id else 1.0
    return best if best_score >= min_score else None


def consume_research_artifact_for_skill(
    artifact_id: str,
    *,
    project_id: str,
    session_id: str = "",
    skill_id: str = "",
    objective: str = "",
) -> Dict[str, Any]:
    """Validate ownership and return structured artifact for skill use (not prose)."""
    art = get_research_artifact(artifact_id)
    if art is None:
        return {"ok": False, "error_code": "not_found", "error": "Research artifact not found"}
    if art.status != "ready":
        return {"ok": False, "error_code": "stale_context", "error": f"Artifact status is {art.status}"}
    if project_id and art.project_id and art.project_id != project_id:
        return {
            "ok": False,
            "error_code": "permission_denied",
            "error": "Artifact belongs to a different Project",
        }
    if session_id and art.session_id and art.session_id != session_id:
        # Same Project + objective overlap still ok
        if not (project_id and art.project_id == project_id):
            return {
                "ok": False,
                "error_code": "stale_context",
                "error": "Artifact Session does not match and Project scope is insufficient",
            }
    if not art.citations and not art.source_urls:
        return {
            "ok": False,
            "error_code": "verification_failed",
            "error": "Artifact has no citations/sources; refuse uncited handoff",
        }
    return {
        "ok": True,
        "artifact": art.model_dump(mode="json"),
        "skill_id": skill_id,
        "objective": objective or art.objective,
        "citations": list(art.citations or []),
        "source_urls": list(art.source_urls or []),
        "summary": art.summary,
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
) -> ResearchArtifact:
    text = str(output or "").strip()
    urls = re.findall(r"https?://[^\s\]\)\"']+", text)
    citations: List[Dict[str, str]] = []
    for url in urls[:12]:
        citations.append({"url": url, "title": ""})
    summary = re.sub(r"\s+", " ", text)[:1600]
    status = "ready" if (summary or citations) else "failed"
    return ResearchArtifact(
        project_id=project_id,
        session_id=session_id,
        execution_id=execution_id,
        tool_run_id=tool_run_id,
        objective=objective or query,
        query=query,
        summary=summary,
        citations=citations,
        source_urls=urls[:12],
        status=status,
    )
