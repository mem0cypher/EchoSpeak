"""Wire video domain into normal EchoSpeak chat Turns.

Not a separate video chat endpoint. Extends the production Turn path when a
Session has an active VideoDocument or the user request is clearly video work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.skill_contract import SkillManifest, SkillSelectionResult
from agent.skill_selection import select_composition, select_skill
from agent.video_editor.context import build_editor_context, editor_context_for_prompt
from agent.video_editor.models import EditorSelectionContext, VideoEditorContext
from agent.video_editor.store import VideoEditorStore, VideoStoreError, get_video_editor_store
from agent.video_editor.tool_catalog import list_video_tool_names


VIDEO_TOOL_PREFIX = "video_"

_VIDEO_INTENT = re.compile(
    r"\b("
    r"video|timeline|clip|clips|footage|playhead|media bin|rough cut|"
    r"b-?roll|caption|captions|subtitle|subtitles|silence|reframe|"
    r"highlight reel|export (?:the )?video|render (?:the )?video|"
    r"edit(?:ing)? (?:this |the |my )?video|split (?:the )?clip|"
    r"trim (?:the )?clip|insert (?:a )?clip|generate (?:some )?b-?roll|"
    r"podcast episode|multicam|sequence"
    r")\b",
    re.I,
)


@dataclass
class VideoTurnPackage:
    """Everything the normal Turn needs for video work."""

    active: bool = False
    reason: str = ""
    project_id: str = ""
    session_id: str = ""
    document_id: str = ""
    context: Optional[VideoEditorContext] = None
    context_prompt_block: str = ""
    allowed_video_tools: list[str] = field(default_factory=list)
    skill_selection: Optional[SkillSelectionResult] = None
    composition: list[SkillSelectionResult] = field(default_factory=list)
    skill_manifests: list[SkillManifest] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    direct_tool: str = ""  # e.g. video_propose_operations when deterministic routing applies


def is_video_edit_intent(user_text: str) -> bool:
    text = str(user_text or "").strip()
    if not text:
        return False
    return bool(_VIDEO_INTENT.search(text))


def session_has_video_document(
    project_id: str,
    *,
    store: Optional[VideoEditorStore] = None,
    document_id: str = "",
) -> tuple[bool, str]:
    project_id = str(project_id or "").strip()
    if not project_id:
        return False, ""
    video_store = store or get_video_editor_store()
    try:
        if document_id:
            video_store.get_document(project_id, document_id)
            return True, document_id
        docs = video_store.list_documents(project_id)
        if docs:
            return True, docs[0].id
    except VideoStoreError:
        return False, ""
    except Exception:
        return False, ""
    return False, ""


def filter_tools_for_turn(
    all_tool_names: set[str] | frozenset[str] | list[str],
    *,
    video_turn: bool,
) -> frozenset[str]:
    """Non-video turns must not receive video tools."""
    names = {str(n) for n in all_tool_names if str(n or "").strip()}
    video_tools = {n for n in names if n.startswith(VIDEO_TOOL_PREFIX)}
    if video_turn:
        return frozenset(names)
    return frozenset(names - video_tools)


def _is_utility_only(user_text: str) -> bool:
    low = re.sub(r"\s+", " ", str(user_text or "").strip().lower())
    if not low:
        return True
    if re.fullmatch(
        r"(?:please\s+)?(?:"
        r"what(?:'s| is)?\s+the\s+time(?:\s+is\s+it)?|"
        r"what\s+time\s+is\s+it|current\s+time|"
        r"what(?:'s| is)?\s+(?:the\s+)?date(?:\s+today)?|"
        r"hi|hello|hey|thanks|thank you|ok|okay"
        r")[.!?]?",
        low,
    ):
        return True
    return False


def build_video_turn_package(
    session_id: str,
    project_id: str,
    user_text: str,
    *,
    document_id: str = "",
    selection: Optional[EditorSelectionContext] = None,
    thread_state: Any = None,
    config: Any = None,
    store: Optional[VideoEditorStore] = None,
    skill_manifests: Optional[list[SkillManifest]] = None,
    explicit_skill_id: str = "",
    prior_unfinished_skill_id: str = "",
) -> VideoTurnPackage:
    """Decide if this Turn is video-relevant and assemble context + selection."""
    session_id = str(session_id or "").strip()
    project_id = str(project_id or "").strip()
    intent = is_video_edit_intent(user_text)
    has_doc, resolved_doc = session_has_video_document(
        project_id, store=store, document_id=document_id
    )
    continue_lang = bool(re.search(r"\b(continue|resume|yes|proceed|retry|do it|apply)\b", user_text or "", re.I))
    # Document alone is not enough for utility small-talk — avoid prompt pollution.
    video_relevant = bool(intent or (has_doc and continue_lang) or (has_doc and intent))
    if has_doc and not intent and not continue_lang and not _is_utility_only(user_text):
        # Follow-ups about "the cut" / "this timeline" without keywords still need context
        # when a document exists and the message is operational (not pure chat).
        if re.search(r"\b(edit|cut|clip|track|export|render|silence|caption|b-?roll)\b", user_text or "", re.I):
            video_relevant = True
        elif len(str(user_text or "").split()) >= 4 and has_doc:
            # Short operational phrasing with an open document — allow tools, light activation.
            video_relevant = True

    if not video_relevant:
        return VideoTurnPackage(active=False, reason="no_video_intent_or_document")

    # Intent without Project cannot load editor authority.
    if not project_id:
        return VideoTurnPackage(
            active=bool(intent),
            reason="video_intent_without_project",
            session_id=session_id,
            missing_requirements=["project:none_attached"],
        )

    video_store = store or get_video_editor_store()
    try:
        context = build_editor_context(
            session_id=session_id,
            project_id=project_id,
            document_id=resolved_doc or document_id,
            selection=selection,
            store=video_store,
            thread_state=thread_state,
            config=config,
        )
    except Exception as exc:
        return VideoTurnPackage(
            active=True,
            reason=f"context_failed:{exc}",
            project_id=project_id,
            session_id=session_id,
            missing_requirements=[f"context:{exc}"],
        )

    tools = list_video_tool_names()
    # Capability tokens available now
    caps = set()
    if context.capabilities.deterministic_editing:
        caps.update({"deterministic_editing", "timeline_mutation", "agent_proposals", "approvals"})
    if context.capabilities.research:
        caps.add("research")
    if context.capabilities.media_probe:
        caps.add("media_probe")
    if context.capabilities.analysis:
        caps.add("analysis")
    if context.capabilities.transcription:
        caps.add("transcription")
    if context.capabilities.generative_video:
        caps.add("generative_video")
    for mc in context.capabilities.model_capabilities:
        if mc.available:
            caps.add(mc.capability)

    perms = set()
    if context.authority.system_actions:
        perms.add("system_actions")
    if context.authority.video_agent_edits:
        perms.add("video_agent_edits")

    arts: set[str] = set()
    for plan in context.pending_plans:
        if plan.skill_id:
            arts.add(f"plan:{plan.skill_id}")
    # Structured artifacts on the document head (kinds skills can require).
    try:
        if context.document_id:
            doc = (store or get_video_editor_store()).get_document(project_id, context.document_id)
            for raw in getattr(doc, "artifacts", None) or []:
                kind = str((raw or {}).get("kind") or "").strip().lower()
                validity = str((raw or {}).get("validity") or "valid").strip().lower()
                if kind and validity == "valid":
                    arts.add(kind)
                    if kind == "silence":
                        arts.add("silence_detection")
                    if kind == "transcript":
                        arts.add("transcription")
                    if kind == "scene":
                        arts.add("scene_detection")
    except Exception:
        pass

    manifests = list(skill_manifests or [])
    video_manifests = [m for m in manifests if "video" in (m.supported_modes or []) or m.id.startswith("video_")]
    if not video_manifests:
        video_manifests = manifests

    selection_result = select_skill(
        user_text=user_text,
        manifests=video_manifests,
        available_tools=set(tools),
        available_capabilities=caps,
        available_artifacts=arts,
        permissions=perms,
        explicit_skill_id=explicit_skill_id,
        prior_unfinished_skill_id=prior_unfinished_skill_id,
        domain_hint="video",
        allow_stale_prior=bool(re.search(r"\b(continue|resume|yes|proceed|retry)\b", user_text or "", re.I)),
    )
    composition = select_composition(
        user_text=user_text,
        manifests=video_manifests,
        available_tools=set(tools),
        available_capabilities=caps,
        available_artifacts=arts,
        permissions=perms,
    )

    prompt_block = ""
    if intent or has_doc:
        prompt_block = (
            "Video editor authority (structured — do not invent IDs or revisions):\n"
            + editor_context_for_prompt(context)
        )
        if selection_result is not None:
            prompt_block += (
                f"\n\nSkill selection outcome: {selection_result.outcome.value}"
                f" skill_id={selection_result.skill_id or '(none)'}"
                f" direct_tool={selection_result.direct_tool or '(none)'}"
                f" reason={selection_result.reason}"
            )
            if selection_result.missing_requirements:
                prompt_block += f"\nMissing requirements: {', '.join(selection_result.missing_requirements)}"
        prompt_block += (
            "\nRules: propose structured video tools only; never rewrite timeline JSON; "
            "never emit FFmpeg/shell; apply only after approval; blocked jobs are not completion."
        )

    return VideoTurnPackage(
        active=True,
        reason="video_intent" if intent else "active_video_document",
        project_id=project_id,
        session_id=session_id,
        document_id=context.document_id,
        context=context,
        context_prompt_block=prompt_block,
        allowed_video_tools=tools if (intent or has_doc) else [],
        skill_selection=selection_result,
        composition=composition,
        skill_manifests=video_manifests,
        missing_requirements=list(selection_result.missing_requirements if selection_result else []),
    )
