"""EchoSpeak's authoritative structured video-editor domain."""

from agent.video_editor.models import VideoEditorContext, VideoProjectDocument
from agent.video_editor.store import VideoEditorStore, get_video_editor_store

# Import tools so ToolRegistry entries are available at process start.
from agent.video_editor import tools as _video_tools  # noqa: F401
from agent.video_editor.skills import ensure_builtin_video_skills

ensure_builtin_video_skills()

__all__ = [
    "VideoProjectDocument",
    "VideoEditorContext",
    "VideoEditorStore",
    "get_video_editor_store",
]
