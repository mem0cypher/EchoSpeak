#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/apps/backend"
WEB_DIR="$ROOT_DIR/apps/web"
TUI_DIR="$ROOT_DIR/apps/tui"

print_step() {
  printf '\n==> %s\n' "$1"
}

require_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 1
  fi
}

print_step "Audit active code and docs for removed voice/search surfaces"
ROOT_DIR="$ROOT_DIR" python - <<'PY'
from pathlib import Path
import os
import re
import sys

root = Path(os.environ["ROOT_DIR"])
checks = {
    r"\blive_web_search\b": "deprecated live_web_search reference",
    r"\bSEARXNG\b|\bSearxNG\b": "deprecated SearxNG reference",
    r"\bDuckDuckGo\b|\bddgs\b": "deprecated DuckDuckGo reference",
    r"\bScrapling\b|\bWEB_SEARCH_USE_SCRAPLING\b": "deprecated Scrapling reference",
    r"\bUSE_POCKET_TTS\b|\bPOCKET_TTS_": "deprecated Pocket-TTS config reference",
    r"\bLOCAL_STT_": "deprecated local STT config reference",
    r"\bopenwakeword\b": "deprecated wake word reference",
    r"\bfaster-whisper\b": "deprecated local STT dependency reference",
    r"\bpyttsx3\b": "deprecated local TTS dependency reference",
    r"/tts\b": "deprecated backend /tts endpoint reference",
    r"/stt\b": "deprecated backend /stt endpoint reference",
}

scan_paths = [
    root / "apps/backend",
    root / "apps/web/src",
    root / "apps/tui",
    root / "docs",
    root / "ARCHITECTURE.md",
    root / "AUDIT.md",
    root / "apps/backend/TEST_RUNDOWN.md",
]

exclude_parts = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache"}
allowed_hits = {
    root / "apps/backend/io_module/pocket_tts_engine.py": {"Pocket-TTS has been removed. Use browser speech playback instead."},
    root / "apps/backend/io_module/stt_engine.py": {"Local STT has been removed. Use browser speech recognition instead."},
    root / "apps/backend/io_module/wake_listener.py": {"Wake listener voice activation has been removed. Use browser speech controls instead."},
}

failures = []
for scan_path in scan_paths:
    paths = [scan_path] if scan_path.is_file() else [p for p in scan_path.rglob('*') if p.is_file()]
    for path in paths:
        if any(part in exclude_parts for part in path.parts):
            continue
        if path.suffix.lower() not in {'.py', '.ts', '.tsx', '.go', '.md', '.txt', '.json'}:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        for pattern, label in checks.items():
            for match in re.finditer(pattern, text):
                line = text.count('\n', 0, match.start()) + 1
                hit_line = text.splitlines()[line - 1] if text.splitlines() else ''
                allowed = False
                allowed_line_set = allowed_hits.get(path)
                if allowed_line_set and hit_line.strip() in allowed_line_set:
                    allowed = True
                if not allowed:
                    failures.append(f"{path.relative_to(root)}:{line}: {label}: {hit_line.strip()}")

if failures:
    print("Audit failed. Remaining deprecated references found:")
    for item in failures:
        print(f"- {item}")
    sys.exit(1)

print("Audit passed: no deprecated active voice/search references were found.")
PY

print_step "Run backend cleanup-focused regression tests"
require_file "$BACKEND_DIR/.venv/bin/pytest"
(
  cd "$BACKEND_DIR"
  env PYTHONPATH=. .venv/bin/pytest \
    tests/test_router.py \
    tests/test_phase2_research.py \
    tests/test_echospeak.py::TestTtsSelection::test_browser_voice_uses_full_response
)

print_step "Run web cleanup-focused verification"
(
  cd "$WEB_DIR"
  npm run typecheck
  npm run test:run -- src/features/research/buildResearchRun.test.ts
  npm run build
)

print_step "Run Go TUI build verification"
(
  cd "$TUI_DIR"
  go build ./...
)

print_step "v6.6.0 cleanup regression script completed successfully"
