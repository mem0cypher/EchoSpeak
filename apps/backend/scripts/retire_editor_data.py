#!/usr/bin/env python3
"""Export legacy Editor JSON and optionally register verified MediaAssets.

The source tree is always read-only. Media registration is explicit and uses
the caller-provided catalog root; no production singleton is selected.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.editor_retirement import retire_editor_data
from agent.media_library import MediaLibraryStore


def _project_roots(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        project_id, separator, root = str(value or "").partition("=")
        if not separator or not project_id.strip() or not root.strip():
            raise ValueError("--project-root must use PROJECT_ID=PATH")
        result[project_id.strip()] = Path(root).expanduser()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-destructively retire legacy EchoSpeak Editor data")
    parser.add_argument("--source", required=True, help="Legacy video_editor data root")
    parser.add_argument("--destination", required=True, help="Separate archive destination")
    parser.add_argument("--project-root", action="append", default=[], help="PROJECT_ID=PATH for asset verification")
    parser.add_argument("--register-media-root", default="", help="Explicit Media Library root; omit for archive-only")
    args = parser.parse_args()

    roots = _project_roots(args.project_root)
    media_root = Path(args.register_media_root).expanduser() if args.register_media_root else None
    store = MediaLibraryStore(media_root) if media_root is not None else None
    result = retire_editor_data(
        Path(args.source),
        Path(args.destination),
        project_roots=roots,
        media_library_store=store,
        media_library_root=media_root,
    )
    print(json.dumps({
        "manifest_path": str(result.manifest_path),
        "receipt_path": str(result.receipt_path) if result.receipt_path else "",
        "reused_export": result.reused_export,
        "inventory": result.manifest.get("inventory", {}),
        "media_import_count": len(result.manifest.get("media_import_plan") or []),
        "source_mutated": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
