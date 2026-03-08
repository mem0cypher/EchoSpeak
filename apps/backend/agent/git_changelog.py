"""
Git changelog watcher for EchoSpeak (v6.7.0).

Detects new git commits in the EchoSpeak repo and formats them into
tweet-ready update logs for the autonomous Twitter posting system and
Discord changelog announcements.

Design:
  - Reads git log from the repo root
  - Persists an acknowledged watermark (last handled commit SHA)
  - Stores a pending announcement payload until downstream delivery is handled
  - Formats commit summaries into concise update tweets (280 char limit)
  - Also reads CHANGES.md for richer version-level summaries
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# State persistence
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_CHANGELOG_STATE_PATH = _DATA_DIR / "git_changelog_state.json"

# Repo root (EchoSpeak project root)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_state() -> Dict[str, Any]:
    try:
        if _CHANGELOG_STATE_PATH.exists():
            state = json.loads(_CHANGELOG_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                state.setdefault("last_commit_sha", "")
                state.setdefault("last_check", "")
                state.setdefault("pending", None)
                return state
    except Exception:
        pass
    return {"last_commit_sha": "", "last_check": "", "pending": None}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        _CHANGELOG_STATE_PATH.write_text(
            json.dumps(state, default=str, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Failed to save git changelog state: {e}")


def get_recent_commits(since_sha: str = "", limit: int = 10) -> List[Dict[str, str]]:
    """Get recent git commits from the EchoSpeak repo.

    Args:
        since_sha: Only return commits after this SHA. If empty, returns the last `limit` commits.
        limit: Max number of commits to return.

    Returns:
        List of dicts with keys: sha, short_sha, message, author, date
    """
    try:
        cmd = [
            "git", "log",
            f"--max-count={limit}",
            "--format=%H|%h|%s|%an|%aI",
        ]
        if since_sha:
            cmd.append(f"{since_sha}..HEAD")

        result = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.debug(f"git log failed: {result.stderr[:200]}")
            return []

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) >= 5:
                commits.append({
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "message": parts[2],
                    "author": parts[3],
                    "date": parts[4],
                })
        return commits
    except Exception as e:
        logger.debug(f"git changelog: failed to read commits: {e}")
        return []


def get_current_head_sha() -> str:
    """Get the current HEAD commit SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def get_latest_changelog_section() -> str:
    """Read the first version section from CHANGES.md for richer context."""
    changes_path = _REPO_ROOT / "CHANGES.md"
    if not changes_path.exists():
        return ""
    try:
        text = changes_path.read_text(encoding="utf-8")
        lines = text.split("\n")
        section_lines: list[str] = []
        in_section = False
        for line in lines:
            if line.startswith("## ") and not in_section:
                in_section = True
                section_lines.append(line)
                continue
            if line.startswith("## ") and in_section:
                break  # Hit the next version section
            if in_section:
                section_lines.append(line)
        return "\n".join(section_lines).strip()[:1500]  # Cap at 1500 chars
    except Exception:
        return ""


def get_commit_diff_summary(since_sha: str = "", limit_chars: int = 2000) -> str:
    """Get a summary of actual code changes between commits.

    Returns ``git diff --stat`` (file-level overview) plus truncated unified
    diffs for code files so the caller knows *what* changed, not just the
    one-line commit messages.
    """
    try:
        # 1. File-level overview (--stat)
        stat_cmd = ["git", "diff", "--stat"]
        if since_sha:
            stat_cmd.append(f"{since_sha}..HEAD")
        stat_result = subprocess.run(
            stat_cmd, cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        stat_output = stat_result.stdout.strip() if stat_result.returncode == 0 else ""

        # 2. Actual unified diff for code files (truncated)
        diff_cmd = ["git", "diff", "--no-color", "-U2"]
        if since_sha:
            diff_cmd.append(f"{since_sha}..HEAD")
        diff_cmd.extend(["--", "*.py", "*.md", "*.ts", "*.tsx", "*.json"])
        diff_result = subprocess.run(
            diff_cmd, cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=15,
        )
        diff_output = ""
        if diff_result.returncode == 0 and diff_result.stdout.strip():
            diff_output = diff_result.stdout.strip()[:limit_chars]

        parts: List[str] = []
        if stat_output:
            parts.append(f"Files changed:\n{stat_output}")
        if diff_output:
            parts.append(f"Code changes (truncated):\n{diff_output}")
        return "\n\n".join(parts) if parts else ""
    except Exception as e:
        logger.debug(f"git diff summary failed: {e}")
        return ""


def _build_pending_payload(base_sha: str, head_sha: str) -> Optional[Dict[str, Any]]:
    commits = get_recent_commits(since_sha=base_sha, limit=10)
    if not commits:
        return None

    changelog_section = get_latest_changelog_section()
    return {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "latest_sha": head_sha[:8],
        "commit_count": len(commits),
        "commits": commits,
        "changelog_section": changelog_section,
        "discord_handled": False,
        "twitter_handled": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def check_for_new_commits() -> Optional[Dict[str, Any]]:
    """Check if there are new commits since the last check.

    Returns:
        Pending changelog payload if there is new work to announce,
        or None if nothing new.
    """
    state = _load_state()
    pending = state.get("pending")
    if isinstance(pending, dict) and pending.get("head_sha"):
        return pending

    last_sha = state.get("last_commit_sha", "")
    current_sha = get_current_head_sha()

    if not current_sha:
        return None

    # First run — just record current state, don't report
    if not last_sha:
        state["last_commit_sha"] = current_sha
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
        logger.info(f"Git changelog: initialized watermark at {current_sha[:8]}")
        return None

    # No new commits
    if current_sha == last_sha:
        return None

    payload = _build_pending_payload(last_sha, current_sha)
    if not payload:
        # SHA changed but no parseable commits — acknowledge anyway so we don't loop forever.
        state["last_commit_sha"] = current_sha
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
        return None

    state["pending"] = payload
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    logger.info(
        f"Git changelog: {payload.get('commit_count', 0)} new commit(s) detected since {last_sha[:8]}"
    )

    return payload


def update_pending_changelog(head_sha: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Update status fields on the pending changelog payload."""
    state = _load_state()
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return None

    pending_head = str(pending.get("head_sha") or "")
    if head_sha and pending_head and pending_head != head_sha:
        return pending

    for key, value in fields.items():
        pending[key] = value
    state["pending"] = pending
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return pending


def mark_changelog_announced(head_sha: str = "") -> bool:
    """Acknowledge the pending changelog and advance the watermark."""
    state = _load_state()
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return False

    pending_head = str(pending.get("head_sha") or "")
    if head_sha and pending_head and pending_head != head_sha:
        return False

    if pending_head:
        state["last_commit_sha"] = pending_head
    state["pending"] = None
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return True


def _extract_changelog_heading(changelog_section: str) -> str:
    for line in (changelog_section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
    return ""


def _extract_changelog_highlights(changelog_section: str, limit: int = 4) -> List[str]:
    highlights: List[str] = []
    for line in (changelog_section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            highlights.append(stripped)
        if len(highlights) >= limit:
            break
    return highlights


def format_update_tweet_prompt(changelog_data: Dict[str, Any]) -> str:
    """Format changelog data into a prompt for the agent to compose an update tweet.

    The agent will use its Soul personality to write the tweet in Echo's voice,
    not a dry changelog paste.  Includes actual code diffs so the agent can
    describe changes accurately instead of hallucinating details.
    """
    commits = changelog_data.get("commits", [])
    changelog_section = changelog_data.get("changelog_section", "")
    count = changelog_data.get("commit_count", 0)
    latest_sha = changelog_data.get("latest_sha", "")
    base_sha = changelog_data.get("base_sha", "")

    commit_summaries = "\n".join(
        f"- {c['short_sha']}: {c['message']}" for c in commits[:8]
    )

    grounded_context = ""
    try:
        from agent.update_context import get_update_context_service

        grounded_context = get_update_context_service().build_context_block(
            since_sha=base_sha,
            limit=max(1, min(int(count or len(commits) or 5), 8)),
            public=False,
            include_diff=True,
            max_diff_chars=2000,
            commits=commits,
            changelog_section=changelog_section,
            latest_sha=latest_sha,
            heading="Grounded EchoSpeak update context for this tweet",
        )
    except Exception:
        grounded_context = ""

    # Gather actual code diff context as a fallback if the shared service fails.
    diff_summary = ""
    if not grounded_context:
        diff_summary = get_commit_diff_summary(since_sha=base_sha, limit_chars=2000)

    prompt = (
        "You are Echo, posting an EchoSpeak development update to your Twitter/X account. "
        "You just pushed new code. Write a single tweet (max 280 chars) announcing the update. "
        "Be yourself — casual, direct, maybe a little proud of the work. "
        "Mention what changed in plain language, not git-speak. "
        "Don't use hashtags unless they're genuinely relevant. "
        "Don't say 'just pushed' every time — vary your phrasing.\n\n"
        f"Commit count: {count}\n"
        f"Latest: {latest_sha}\n\n"
        f"Recent commits:\n{commit_summaries}\n"
    )

    if grounded_context:
        prompt += f"\n{grounded_context}\n"
    elif diff_summary:
        prompt += f"\nActual code changes:\n{diff_summary}\n"

    if changelog_section:
        prompt += f"\nCHANGES.md context (use for richer detail if relevant):\n{changelog_section[:800]}\n"

    prompt += (
        "\nIMPORTANT: Only describe changes you can verify from the commit messages, "
        "code diffs, or CHANGES.md above. Do NOT invent or assume technical details "
        "that are not directly supported by this context. If you're unsure about a "
        "specific change, keep your description general rather than fabricating specifics. "
        "If you have tools available, you may use self_read or self_grep to verify details "
        "in the codebase before composing your tweet.\n\n"
        "Reply with ONLY the tweet text, nothing else."
    )

    return prompt


def format_discord_update_message(changelog_data: Dict[str, Any]) -> str:
    """Format changelog data into a Discord-friendly update announcement."""
    commits = changelog_data.get("commits", [])
    changelog_section = changelog_data.get("changelog_section", "")
    count = changelog_data.get("commit_count", 0)
    latest_sha = changelog_data.get("latest_sha", "")
    heading = _extract_changelog_heading(changelog_section)
    highlights = _extract_changelog_highlights(changelog_section)

    header = "🛠 **EchoSpeak Update**"
    if heading:
        header += f"\n**{heading}**"

    lines = [
        header,
        f"`{latest_sha}` • {count} commit{'s' if count != 1 else ''}",
    ]

    if highlights:
        lines.append("**Highlights**\n" + "\n".join(highlights[:4]))

    if commits:
        commit_lines = "\n".join(
            f"- `{c['short_sha']}` {c['message']}" for c in commits[:5]
        )
        lines.append("**Recent commits**\n" + commit_lines)

    lines.append("Built and shipped through EchoSpeak's git watcher.")
    return "\n\n".join(lines).strip()
