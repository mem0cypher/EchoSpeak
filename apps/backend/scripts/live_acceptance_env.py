"""Create a disposable EchoSpeak data + project tree for live acceptance."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "coding_project"


def build_env(base: Path | None = None) -> dict:
    base = Path(base or tempfile.mkdtemp(prefix="echospeak-live-"))
    data = base / "data"
    projects = base / "projects"
    workspace = base / "workspace" / "live_coding_project"
    video_media = workspace / "media"
    data.mkdir(parents=True, exist_ok=True)
    projects.mkdir(parents=True, exist_ok=True)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(FIXTURE, workspace)
    video_media.mkdir(exist_ok=True)
    # Tiny synthetic "clip" marker (not a real MP4 decode; domain uses metadata)
    (video_media / "clip_a.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

    # Seed settings for LM Studio + system actions (no production data bleed)
    prod_settings = ROOT / "data" / "settings.json"
    settings: dict = {}
    if prod_settings.exists():
        try:
            settings = json.loads(prod_settings.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    settings.update(
        {
            "use_local_models": True,
            "local": {
                "provider": "lmstudio",
                "model_name": "google/gemma-4-e2b",
                "base_url": "http://localhost:1234",
            },
            "enable_system_actions": True,
            "allow_file_write": True,
            "allow_terminal_commands": False,
            "allow_self_modification": False,
            "api_auth_enabled": False,
            "api_auth_localhost_bypass": True,
            "file_tool_root": str(base / "workspace"),
            "skills_dir": str(ROOT / "skills"),
            "artifacts_dir": str(data / "artifacts"),
            "heartbeat_enabled": False,
            "cron_enabled": False,
            "multi_agent_enabled": False,
            "disable_native_tool_calling": False,
            "lmstudio_tool_calling": True,
        }
    )
    (data / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    (base / "README_LIVE.txt").write_text(
        f"Disposable live acceptance root\nworkspace={workspace}\ndata={data}\n",
        encoding="utf-8",
    )
    meta = {
        "base": str(base),
        "data": str(data),
        "projects_dir": str(projects),
        "workspace": str(workspace),
        "video_clip": str(video_media / "clip_a.mp4"),
    }
    (base / "live_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta))
    return meta


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    build_env(target)
