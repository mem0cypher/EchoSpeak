"""
GitHub tools — issues, PRs, comments, and repo info.

Requires:
  pip install PyGithub
  ALLOW_GITHUB=true
  GITHUB_TOKEN=ghp_...
"""

from __future__ import annotations

from typing import Optional, List

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

def _get_github_client():
    """Build and return an authenticated PyGithub client."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "allow_github", False):
        raise RuntimeError("GitHub integration is disabled. Set ALLOW_GITHUB=true in .env")

    token = getattr(config, "github_token", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set in .env")

    from github import Github
    return Github(token)


def _resolve_repo(repo_str: Optional[str] = None):
    """Resolve a repo from user input or config default."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    repo_name = (repo_str or "").strip() or getattr(config, "github_default_repo", "")
    if not repo_name:
        raise RuntimeError("No repo specified and GITHUB_DEFAULT_REPO not set")

    g = _get_github_client()
    return g.get_repo(repo_name)


# ── Schemas ─────────────────────────────────────────────────────────

class GHListIssuesArgs(BaseModel):
    repo: Optional[str] = Field(default=None, description="Repo in 'owner/name' format. Uses default if empty.")
    state: str = Field(default="open", description="Filter: 'open', 'closed', or 'all'")
    labels: Optional[List[str]] = Field(default=None, description="Filter by label names")
    limit: int = Field(default=15, description="Max results")


class GHGetIssueArgs(BaseModel):
    number: int = Field(description="Issue number")
    repo: Optional[str] = Field(default=None, description="Repo in 'owner/name' format")


class GHCreateIssueArgs(BaseModel):
    title: str = Field(description="Issue title")
    body: Optional[str] = Field(default=None, description="Issue body / description")
    labels: Optional[List[str]] = Field(default=None, description="Labels to apply")
    repo: Optional[str] = Field(default=None, description="Repo in 'owner/name' format")


class GHListPRsArgs(BaseModel):
    repo: Optional[str] = Field(default=None, description="Repo in 'owner/name' format")
    state: str = Field(default="open", description="Filter: 'open', 'closed', or 'all'")
    limit: int = Field(default=15, description="Max results")


class GHGetPRArgs(BaseModel):
    number: int = Field(description="PR number")
    repo: Optional[str] = Field(default=None, description="Repo in 'owner/name' format")


class GHCommentIssueArgs(BaseModel):
    number: int = Field(description="Issue or PR number")
    comment: str = Field(description="Comment body text")
    repo: Optional[str] = Field(default=None, description="Repo in 'owner/name' format")


# ── github_list_issues ──────────────────────────────────────────────

@ToolRegistry.register(
    name="github_list_issues",
    description="List GitHub issues from a repository. Filter by state, labels.",
    category="github",
    risk_level="safe",
)
@tool(args_schema=GHListIssuesArgs)
def github_list_issues(
    repo: Optional[str] = None,
    state: str = "open",
    labels: Optional[List[str]] = None,
    limit: int = 15,
) -> str:
    """List issues from a GitHub repo."""
    try:
        r = _resolve_repo(repo)
        kwargs: dict = {"state": state}
        if labels:
            kwargs["labels"] = [r.get_label(l) for l in labels]

        issues = r.get_issues(**kwargs)
        items = []
        count = 0
        for issue in issues:
            if issue.pull_request:
                continue  # Skip PRs
            items.append(issue)
            count += 1
            if count >= limit:
                break

        if not items:
            return f"📋 No {state} issues in **{r.full_name}**."

        lines = [f"📋 **{r.full_name}** — {state} issues ({len(items)})\n"]
        for issue in items:
            label_str = ", ".join(f"`{l.name}`" for l in issue.labels) if issue.labels else ""
            assignee = f" → @{issue.assignee.login}" if issue.assignee else ""
            lines.append(
                f"• **#{issue.number}** {issue.title}{assignee}\n"
                f"  {label_str} | {issue.comments} comments | {issue.created_at.strftime('%b %d')}"
            )

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"github_list_issues failed: {exc}")
        return f"❌ Failed to list issues: {exc}"


# ── github_get_issue ────────────────────────────────────────────────

@ToolRegistry.register(
    name="github_get_issue",
    description="Get details of a specific GitHub issue by number.",
    category="github",
    risk_level="safe",
)
@tool(args_schema=GHGetIssueArgs)
def github_get_issue(number: int, repo: Optional[str] = None) -> str:
    """Get a GitHub issue by number."""
    try:
        r = _resolve_repo(repo)
        issue = r.get_issue(number=number)

        label_str = ", ".join(f"`{l.name}`" for l in issue.labels) if issue.labels else "none"
        assignee = f"@{issue.assignee.login}" if issue.assignee else "unassigned"
        body = (issue.body or "")[:500]
        if len(issue.body or "") > 500:
            body += "…"

        lines = [
            f"📌 **#{issue.number}: {issue.title}**",
            f"State: {issue.state} | Labels: {label_str} | Assignee: {assignee}",
            f"Created: {issue.created_at.strftime('%b %d, %Y')} | Comments: {issue.comments}",
            f"URL: {issue.html_url}",
            "",
            body,
        ]

        # Show last 3 comments
        if issue.comments > 0:
            lines.append("\n**Recent comments:**")
            comments = list(issue.get_comments())[-3:]
            for c in comments:
                c_body = (c.body or "")[:200]
                lines.append(f"  💬 @{c.user.login} ({c.created_at.strftime('%b %d')}): {c_body}")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"github_get_issue failed: {exc}")
        return f"❌ Failed to get issue #{number}: {exc}"


# ── github_create_issue ─────────────────────────────────────────────

@ToolRegistry.register(
    name="github_create_issue",
    description="Create a new GitHub issue with title, body, and optional labels.",
    category="github",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=GHCreateIssueArgs)
def github_create_issue(
    title: str,
    body: Optional[str] = None,
    labels: Optional[List[str]] = None,
    repo: Optional[str] = None,
) -> str:
    """Create a GitHub issue."""
    try:
        r = _resolve_repo(repo)
        kwargs: dict = {"title": title.strip()}
        if body:
            kwargs["body"] = body.strip()
        if labels:
            kwargs["labels"] = labels

        issue = r.create_issue(**kwargs)
        return (
            f"✅ Issue created: **#{issue.number}: {issue.title}**\n"
            f"- URL: {issue.html_url}\n"
            f"- Repo: {r.full_name}"
        )
    except Exception as exc:
        logger.error(f"github_create_issue failed: {exc}")
        return f"❌ Failed to create issue: {exc}"


# ── github_list_prs ─────────────────────────────────────────────────

@ToolRegistry.register(
    name="github_list_prs",
    description="List pull requests from a GitHub repository.",
    category="github",
    risk_level="safe",
)
@tool(args_schema=GHListPRsArgs)
def github_list_prs(
    repo: Optional[str] = None,
    state: str = "open",
    limit: int = 15,
) -> str:
    """List PRs from a GitHub repo."""
    try:
        r = _resolve_repo(repo)
        pulls = r.get_pulls(state=state, sort="updated", direction="desc")

        items = list(pulls[:limit])
        if not items:
            return f"🔀 No {state} PRs in **{r.full_name}**."

        lines = [f"🔀 **{r.full_name}** — {state} PRs ({len(items)})\n"]
        for pr in items:
            review_status = "✅ approved" if pr.mergeable else "🔄 pending"
            lines.append(
                f"• **#{pr.number}** {pr.title}\n"
                f"  {pr.user.login} → `{pr.base.ref}` | +{pr.additions}/-{pr.deletions} | {pr.comments} comments"
            )

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"github_list_prs failed: {exc}")
        return f"❌ Failed to list PRs: {exc}"


# ── github_get_pr ───────────────────────────────────────────────────

@ToolRegistry.register(
    name="github_get_pr",
    description="Get details of a specific pull request by number.",
    category="github",
    risk_level="safe",
)
@tool(args_schema=GHGetPRArgs)
def github_get_pr(number: int, repo: Optional[str] = None) -> str:
    """Get a GitHub PR by number."""
    try:
        r = _resolve_repo(repo)
        pr = r.get_pull(number=number)

        body = (pr.body or "")[:500]
        if len(pr.body or "") > 500:
            body += "…"

        merge_status = "✅ Merged" if pr.merged else ("🟢 Mergeable" if pr.mergeable else "🔴 Conflicts")

        lines = [
            f"🔀 **#{pr.number}: {pr.title}**",
            f"Author: @{pr.user.login} | State: {pr.state} | {merge_status}",
            f"Branch: `{pr.head.ref}` → `{pr.base.ref}`",
            f"Changes: +{pr.additions}/-{pr.deletions} in {pr.changed_files} files",
            f"Created: {pr.created_at.strftime('%b %d, %Y')} | Comments: {pr.comments}",
            f"URL: {pr.html_url}",
            "",
            body,
        ]

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"github_get_pr failed: {exc}")
        return f"❌ Failed to get PR #{number}: {exc}"


# ── github_comment_issue ────────────────────────────────────────────

@ToolRegistry.register(
    name="github_comment_issue",
    description="Add a comment to a GitHub issue or PR by number.",
    category="github",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=GHCommentIssueArgs)
def github_comment_issue(
    number: int,
    comment: str,
    repo: Optional[str] = None,
) -> str:
    """Comment on a GitHub issue or PR."""
    try:
        r = _resolve_repo(repo)
        issue = r.get_issue(number=number)
        c = issue.create_comment(body=comment.strip())
        return (
            f"✅ Comment added to **#{number}**\n"
            f"- URL: {c.html_url}"
        )
    except Exception as exc:
        logger.error(f"github_comment_issue failed: {exc}")
        return f"❌ Failed to comment on #{number}: {exc}"
