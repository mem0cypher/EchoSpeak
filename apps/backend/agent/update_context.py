from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import DiscordUserRole
from agent.tool_registry import PipelinePlugin, PluginRegistry


@dataclass
class UpdateContextSnapshot:
    latest_sha: str = ""
    commit_count: int = 0
    commits: List[Dict[str, str]] = field(default_factory=list)
    changelog_section: str = ""
    diff_summary: str = ""


class UpdateContextService:
    """Shared, grounded update-awareness service for EchoSpeak.

    This is the single source of truth for "what changed?" style answers.
    It uses deterministic repo context (git log, diff summary, CHANGES.md)
    rather than relying on memory or model guesses.
    """

    _LITERAL_UPDATE_TERMS = (
        "what changed",
        "what's changed",
        "whats changed",
        "what changed recently",
        "what did you change",
        "what have you changed",
        "what's new",
        "whats new",
        "what's new with echospeak",
        "whats new with echospeak",
        "what's new with echo",
        "whats new with echo",
        "new updates",
        "recent updates",
        "latest updates",
        "development update",
        "dev update",
        "changelog",
        "what have you been working on",
        "what did you build",
        "what did you ship",
        "what got updated",
        "recent commits",
    )

    def is_update_intent(self, text: str) -> bool:
        low = str(text or "").strip().lower()
        if not low:
            return False
        if any(term in low for term in self._LITERAL_UPDATE_TERMS):
            return True
        if re.search(r"\b(?:update|updates|updated|change|changes|changed|changelog|ship|shipped|build|built|release|released)\b", low):
            if re.search(r"\b(?:echo|echospeak|yourself|you|your code|repo|project|work|recent|latest|new|lately)\b", low):
                return True
        if re.search(r"\b(?:anything|something)\s+new\b", low):
            return True
        if re.search(r"\b(?:recent|latest|new)\s+(?:work|changes|updates|commits|builds|fixes)\b", low):
            return True
        return False

    def build_snapshot(
        self,
        *,
        since_sha: str = "",
        limit: int = 5,
        public: bool = False,
        include_diff: bool = True,
        max_diff_chars: int = 1400,
        commits: Optional[List[Dict[str, str]]] = None,
        changelog_section: Optional[str] = None,
        latest_sha: str = "",
    ) -> UpdateContextSnapshot:
        from agent.git_changelog import (
            get_commit_diff_summary,
            get_current_head_sha,
            get_latest_changelog_section,
            get_recent_commits,
        )

        commit_list = [dict(c or {}) for c in (commits or get_recent_commits(since_sha=since_sha, limit=max(1, int(limit or 5))))]
        latest = str(latest_sha or "").strip()
        if not latest:
            latest = str((commit_list[0].get("short_sha") if commit_list else "") or "").strip()
        if not latest:
            latest = str(get_current_head_sha() or "")[:8]
        changelog_text = changelog_section if changelog_section is not None else get_latest_changelog_section()
        diff_summary = ""
        if include_diff and not public:
            diff_summary = get_commit_diff_summary(
                since_sha=since_sha,
                limit_chars=max(400, int(max_diff_chars or 1400)),
            )
        return UpdateContextSnapshot(
            latest_sha=latest,
            commit_count=len(commit_list),
            commits=commit_list,
            changelog_section=str(changelog_text or ""),
            diff_summary=str(diff_summary or ""),
        )

    def _extract_changelog_highlights(self, changelog_section: str, limit: int = 4) -> List[str]:
        lines = str(changelog_section or "").splitlines()
        highlights: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("## "):
                continue
            if stripped.startswith(("- ", "* ")):
                stripped = stripped[2:].strip()
            if len(stripped) < 4:
                continue
            highlights.append(stripped)
            if len(highlights) >= max(1, int(limit or 4)):
                break
        return highlights

    def render_context_block(
        self,
        snapshot: UpdateContextSnapshot,
        *,
        public: bool = False,
        heading: str = "Grounded EchoSpeak update context",
    ) -> str:
        if not snapshot.commits and not snapshot.changelog_section and not snapshot.diff_summary:
            return ""

        parts: List[str] = [
            f"{heading} (repo-backed; use this instead of guessing):"
        ]

        if snapshot.latest_sha:
            parts.append(f"Latest commit: {snapshot.latest_sha}")

        if snapshot.commits:
            commit_lines = "\n".join(
                f"- {str(c.get('short_sha') or '')}: {str(c.get('message') or '').strip()}"
                for c in snapshot.commits[:8]
                if str(c.get("message") or "").strip()
            )
            if commit_lines:
                parts.append(f"Recent commits:\n{commit_lines}")

        if public:
            highlights = self._extract_changelog_highlights(snapshot.changelog_section, limit=4)
            if highlights:
                parts.append("Recent changelog highlights:\n" + "\n".join(f"- {h}" for h in highlights))
            parts.append(
                "Public-safety note: keep descriptions high-level. Do not expose private memories, secrets, raw file contents, or internal-only details."
            )
        else:
            if snapshot.diff_summary:
                parts.append(f"Actual code changes:\n{snapshot.diff_summary}")
            if snapshot.changelog_section:
                parts.append(f"CHANGES.md context:\n{snapshot.changelog_section[:900]}")
            parts.append(
                "Accuracy note: only describe changes directly supported by this repo context. Do not invent technical details."
            )

        return "\n\n".join(p for p in parts if str(p or "").strip())

    def build_context_block(
        self,
        *,
        since_sha: str = "",
        limit: int = 5,
        public: bool = False,
        include_diff: bool = True,
        max_diff_chars: int = 1400,
        commits: Optional[List[Dict[str, str]]] = None,
        changelog_section: Optional[str] = None,
        latest_sha: str = "",
        heading: str = "Grounded EchoSpeak update context",
    ) -> str:
        snapshot = self.build_snapshot(
            since_sha=since_sha,
            limit=limit,
            public=public,
            include_diff=include_diff,
            max_diff_chars=max_diff_chars,
            commits=commits,
            changelog_section=changelog_section,
            latest_sha=latest_sha,
        )
        return self.render_context_block(snapshot, public=public, heading=heading)


_UPDATE_CONTEXT_SERVICE = UpdateContextService()


def get_update_context_service() -> UpdateContextService:
    return _UPDATE_CONTEXT_SERVICE


class UpdateContextPlugin(PipelinePlugin):
    def __init__(self, service: Optional[UpdateContextService] = None):
        self._service = service or get_update_context_service()

    def on_context(self, user_input: str, context: Any, **kwargs) -> Any:
        query = str(getattr(context, "extracted_input", "") or user_input or "").strip()
        if not self._service.is_update_intent(query):
            return None
        agent = kwargs.get("agent")
        source = str(kwargs.get("source") or getattr(agent, "_current_source", "") or "").strip().lower()
        public = self._is_public_request(agent, source)
        block = self._service.build_context_block(
            public=public,
            include_diff=not public,
            max_diff_chars=1600,
            limit=6,
        )
        if not block:
            return None
        existing = str(getattr(context, "context", "") or "")
        if block not in existing:
            setattr(context, "context", f"{block}\n\n{existing}" if existing else block)
        setattr(context, "update_context", block)
        setattr(context, "update_intent", True)
        return context

    def _is_public_request(self, agent: Any, source: str) -> bool:
        if source in {"twitter", "twitch"}:
            return True
        role = getattr(agent, "_current_user_role", DiscordUserRole.OWNER)
        return role != DiscordUserRole.OWNER


def ensure_update_context_plugin_registered() -> None:
    PluginRegistry.register(UpdateContextPlugin())
