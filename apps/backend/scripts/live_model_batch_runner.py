#!/usr/bin/env python3
"""Run live-model acceptance in archetype batches and merge reports.

Restarts are not automated here — if the backend dies mid-batch, re-run
the failed batch after bringing the server back up.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


BATCHES = [
    "chat,inspect",
    "coding",
    "memory,research",
    "video,skills",
    "continuation,isolation,streaming,tools,authority",
]


def health_ok(base: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_health(base: str, seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if health_ok(base):
            return True
        time.sleep(1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--batches",
        default=";".join(BATCHES),
        help="Semicolon-separated subset lists (each list is comma-separated archetypes)",
    )
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parent / "live_model_acceptance.py"
    backend_dir = Path(__file__).resolve().parents[1]

    if not wait_health(args.base, 90):
        print("backend unhealthy; abort")
        return 2

    merged_scenarios: list[dict] = []
    provider = ""
    model = ""
    batch_summaries: list[dict] = []

    # Support both ';' (preferred) and accidental ','-only defaults.
    raw_batches = args.batches
    if ";" in raw_batches:
        batch_list = [b.strip() for b in raw_batches.split(";") if b.strip()]
    else:
        # Fall back to predefined batch groups when a flat comma list is passed.
        batch_list = list(BATCHES)

    for i, batch in enumerate(batch_list):
        batch = batch.strip()
        if not batch:
            continue
        if not health_ok(args.base, 8):
            print(f"backend down before batch {batch}; stop")
            break
        partial = out.parent / f"live_model_batch_{i}.json"
        print(f"\n#### BATCH {i}: {batch} ####")
        cmd = [
            sys.executable,
            "-u",
            str(script),
            "--base",
            args.base,
            "--meta",
            args.meta,
            "--out",
            str(partial),
            "--subset",
            batch,
        ]
        proc = subprocess.run(cmd, cwd=str(backend_dir))
        if partial.exists():
            data = json.loads(partial.read_text(encoding="utf-8"))
            provider = data.get("provider") or provider
            model = data.get("model") or model
            merged_scenarios.extend(data.get("scenarios") or [])
            batch_summaries.append(
                {
                    "batch": batch,
                    "passed": data.get("passed"),
                    "failed": data.get("failed"),
                    "total": data.get("total"),
                    "exit": proc.returncode,
                }
            )
            print(f"batch result: {data.get('passed')}/{data.get('total')}")
        else:
            batch_summaries.append({"batch": batch, "error": "no report", "exit": proc.returncode})
            print(f"batch missing report exit={proc.returncode}")

        # brief cool-down between batches
        time.sleep(2)
        if not health_ok(args.base, 5):
            print("backend died after batch; remaining batches skipped")
            break

    # merge
    by_arch: dict[str, dict] = {}
    routes: dict[str, int] = {}
    failures: list[dict] = []
    for s in merged_scenarios:
        a = s.get("archetype") or "unknown"
        slot = by_arch.setdefault(a, {"total": 0, "passed": 0, "failed": 0, "ids": []})
        slot["total"] += 1
        if s.get("passed"):
            slot["passed"] += 1
        else:
            slot["failed"] += 1
            failures.append(
                {
                    "id": s.get("id"),
                    "archetype": a,
                    "prompt": s.get("prompt"),
                    "reason": s.get("failure_reason"),
                    "response": (s.get("response_text") or "")[:300],
                    "route": s.get("route"),
                }
            )
        slot["ids"].append(s.get("id"))
        r = s.get("route") or "unknown"
        routes[r] = routes.get(r, 0) + 1

    n_pass = sum(1 for s in merged_scenarios if s.get("passed"))
    n_fail = sum(1 for s in merged_scenarios if not s.get("passed"))
    clusters: dict[str, list[str]] = {}
    for f in failures:
        key = (f.get("reason") or "unknown").split(":")[0][:80]
        clusters.setdefault(key, []).append(f.get("id") or "")

    report = {
        "generated_at": time.time(),
        "provider": provider,
        "model": model,
        "total": len(merged_scenarios),
        "passed": n_pass,
        "failed": n_fail,
        "pass_rate": round(n_pass / max(1, len(merged_scenarios)), 3),
        "by_archetype": by_arch,
        "routes": routes,
        "failures": failures,
        "failure_clusters": clusters,
        "batches": batch_summaries,
        "scenarios": merged_scenarios,
    }
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nMerged report -> {out}")
    print(f"Total {report['total']}  Passed {n_pass}  Failed {n_fail}  Rate {report['pass_rate']}")
    for k, v in sorted(by_arch.items()):
        print(f"  {k:16} {v['passed']}/{v['total']}")
    return 0 if n_fail == 0 and n_pass > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
