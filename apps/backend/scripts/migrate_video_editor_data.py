#!/usr/bin/env python3
"""Explicit one-time import of legacy Video Editor data into ECHOSPEAK_DATA_DIR.

Usage:
  python scripts/migrate_video_editor_data.py --dry-run
  python scripts/migrate_video_editor_data.py --execute
  python scripts/migrate_video_editor_data.py --execute --allow-overwrite  # after backup
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python scripts/migrate_video_editor_data.py` from apps/backend
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate legacy video_editor store data")
    ap.add_argument("--source", default="", help="Legacy source root (default: apps/backend/data/video_editor)")
    ap.add_argument("--destination", default="", help="Destination video_editor root under DATA_DIR")
    ap.add_argument("--dry-run", action="store_true", help="Plan only (default if --execute not set)")
    ap.add_argument("--execute", action="store_true", help="Perform verified copy; source left untouched")
    ap.add_argument("--allow-overwrite", action="store_true", help="Overwrite differing destination files")
    ap.add_argument("--detect-only", action="store_true", help="Print detection JSON and exit")
    args = ap.parse_args()

    from agent.video_editor.migrate import detect_legacy_video_data, migrate_legacy_video_data

    src = Path(args.source) if args.source else None
    dest = Path(args.destination) if args.destination else None
    detection = detect_legacy_video_data(source=src, destination=dest)
    if args.detect_only:
        print(json.dumps(detection, indent=2))
        return 0

    dry_run = not args.execute
    if dry_run and not args.dry_run:
        # Default to dry-run for safety when neither flag is set
        dry_run = True
    report = migrate_legacy_video_data(
        source=src,
        destination=dest,
        dry_run=dry_run,
        allow_overwrite=bool(args.allow_overwrite),
    )
    print(json.dumps({"detection": detection, "report": report.as_dict()}, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
