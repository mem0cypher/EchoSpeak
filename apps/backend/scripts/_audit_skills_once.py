"""One-shot skill executable status dump for production-closure report."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skill_status_audit import audit_all_skills
from collections import Counter

rows = audit_all_skills(
    available_capabilities={"approvals", "research"},
    available_artifacts=set(),
)
print("STATUS_COUNTS", dict(Counter(r["status"] for r in rows)))
for r in rows:
    print(
        f"{r['status']:28} exec={str(r['executable']):5} {r['id']} "
        f"reasons={r['reasons'][:3]}"
    )
