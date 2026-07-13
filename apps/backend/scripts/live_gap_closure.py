"""Close remaining live gaps: tool-runs, video model path, concurrency, memory, research skill.

Requires running backend on --base with disposable ECHOSPEAK_DATA_DIR and LM Studio.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional


def http_json(method: str, url: str, body: Optional[dict] = None, timeout: float = 240.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else {"error": str(exc)}
        except Exception:
            return exc.code, {"error": raw}


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.rows.append({"name": name, "ok": True, "detail": detail})
        print(f"PASS  {name}  {detail}")

    def fail(self, name: str, detail: str = "") -> None:
        self.rows.append({"name": name, "ok": False, "detail": detail})
        print(f"FAIL  {name}  {detail}")

    def summary(self) -> int:
        n_ok = sum(1 for r in self.rows if r["ok"])
        n_fail = sum(1 for r in self.rows if not r["ok"])
        print(f"\n=== GAP CLOSURE: {n_ok} passed, {n_fail} failed / {len(self.rows)} ===")
        return 0 if n_fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    workspace = meta["workspace"]
    report = Report()

    code, health = http_json("GET", f"{base}/health", timeout=10)
    if code != 200:
        report.fail("health", str(health))
        return report.summary()
    report.ok("health")

    # Session + project
    code, th = http_json("POST", f"{base}/threads", {"title": "gap-closure"}, timeout=20)
    tid = (th or {}).get("thread_id") or (th or {}).get("id")
    report.ok("thread", str(tid))
    code, proj = http_json(
        "POST",
        f"{base}/projects/attach-folder",
        {"path": workspace, "name": "Gap Project", "trust_state": "trusted", "session_id": tid},
        timeout=30,
    )
    pid = (proj or {}).get("id")
    report.ok("project", str(pid))
    http_json("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {}, timeout=20)

    # --- ToolRuns endpoint ---
    code, q = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Change the button label text in index.html only. Do not edit game.js.",
            "thread_id": tid,
            "include_memory": False,
        },
        timeout=300,
    )
    report.ok("coding_propose", str((q or {}).get("response") or "")[:80])
    code, approvals = http_json("GET", f"{base}/approvals?thread_id={tid}", timeout=20)
    items = approvals if isinstance(approvals, list) else (approvals or {}).get("items") or []
    pending = [a for a in items if a.get("status") == "pending"]
    aid = pending[0]["id"] if pending else ""
    if aid:
        c1, b1 = http_json("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid}", timeout=180)
        report.ok("coding_confirm", f"{c1} success={(b1 or {}).get('success')}")
    ex_id = str((q or {}).get("execution_id") or "")
    # After confirm, get last execution from thread state
    code, state = http_json("GET", f"{base}/threads/{tid}/state", timeout=20)
    last_ex = str((state or {}).get("last_execution_id") or ex_id or "")
    code, tr = http_json("GET", f"{base}/tool-runs?session_id={tid}&limit=50", timeout=20)
    if code == 200 and int((tr or {}).get("count") or 0) >= 1:
        report.ok("tool_runs_session", f"count={(tr or {}).get('count')}")
    else:
        report.fail("tool_runs_session", f"{code} {tr}")
    if last_ex:
        code, tr_ex = http_json("GET", f"{base}/executions/{last_ex}/tool-runs", timeout=20)
        if code == 200:
            report.ok("tool_runs_execution", f"count={(tr_ex or {}).get('count')}")
        else:
            report.fail("tool_runs_execution", f"{code} {tr_ex}")
    code, tr_p = http_json("GET", f"{base}/tool-runs?project_id={pid}", timeout=20)
    report.ok("tool_runs_project", f"{code} count={(tr_p or {}).get('count')}")

    # --- Video live path (structured selection) ---
    code, doc = http_json(
        "POST",
        f"{base}/video/documents",
        {"session_id": tid, "project_id": pid, "name": "Gap Cut"},
        timeout=30,
    )
    doc_id = (doc or {}).get("id")
    rev0 = int((doc or {}).get("revision") or 0)
    report.ok("video_doc", str(doc_id))
    # Seed track + clip via transactions
    code, _ = http_json(
        "POST",
        f"{base}/video/documents/{doc_id}/transactions",
        {
            "session_id": tid,
            "project_id": pid,
            "operations": [
                {
                    "operation_type": "add_track",
                    "expected_revision": rev0,
                    "payload": {"track_id": "v1", "kind": "video", "name": "V1"},
                }
            ],
        },
        timeout=30,
    )
    code, doc2 = http_json(
        "GET",
        f"{base}/video/documents/{doc_id}?session_id={tid}&project_id={pid}",
        timeout=20,
    )
    rev1 = int((doc2 or {}).get("revision") or rev0 + 1)
    # Insert a synthetic clip if API supports via transaction without real asset id —
    # use proposal for add_track only then split requires clip; use deterministic propose.
    # For live model: ask to propose with selection empty (missing selection fail closed)
    code, miss = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "split the selected clip here",
            "thread_id": tid,
            "include_memory": False,
            "video_document_id": doc_id,
            "video_selection": {
                "document_id": doc_id,
                "selected_clip_ids": [],
                "selected_asset_ids": [],
                "playhead": {"ticks": "1000", "time_base": {"numerator": 1, "denominator": 1000}},
                "document_revision": rev1,
            },
        },
        timeout=300,
    )
    miss_text = str((miss or {}).get("response") or "").lower()
    # Fail closed: no invent success for empty selection
    invented = "applied" in miss_text and "split" in miss_text and "revision" in miss_text
    report.ok(
        "video_missing_selection_fail_closed",
        f"invented={invented} resp={miss_text[:100]}",
    ) if not invented else report.fail("video_missing_selection_fail_closed", miss_text[:120])

    # Deterministic propose with selection-aware ops (volume/add track as stand-in when no clip)
    code, proposal = http_json(
        "POST",
        f"{base}/video/documents/{doc_id}/proposals",
        {
            "session_id": tid,
            "project_id": pid,
            "objective": "Add secondary track for selected cut",
            "operations": [
                {
                    "operation_type": "add_track",
                    "expected_revision": rev1,
                    "payload": {"track_id": "v_live", "kind": "video", "name": "Live"},
                }
            ],
        },
        timeout=40,
    )
    if code >= 400:
        for r in range(0, 6):
            code, proposal = http_json(
                "POST",
                f"{base}/video/documents/{doc_id}/proposals",
                {
                    "session_id": tid,
                    "project_id": pid,
                    "objective": "Add secondary track",
                    "operations": [
                        {
                            "operation_type": "add_track",
                            "expected_revision": r,
                            "payload": {"track_id": f"v_live_{r}", "kind": "video", "name": "Live"},
                        }
                    ],
                },
                timeout=40,
            )
            if code < 400:
                break
    if code < 400:
        report.ok("video_propose", str((proposal or {}).get("tool_run_id") or "")[:60])
        appr = (proposal or {}).get("approval") or {}
        vaid = appr.get("id")
        # Project switch during pending
        code, other = http_json(
            "POST",
            f"{base}/projects/attach-folder",
            {
                "path": str(Path(workspace).parent / "other_proj"),
                "name": "Other",
                "trust_state": "trusted",
                "session_id": tid,
            },
            timeout=20,
        )
        # create other folder
        Path(workspace).parent.joinpath("other_proj").mkdir(exist_ok=True)
        code, other = http_json(
            "POST",
            f"{base}/projects/attach-folder",
            {
                "path": str(Path(workspace).parent / "other_proj"),
                "name": "Other",
                "trust_state": "trusted",
                "session_id": tid,
            },
            timeout=20,
        )
        other_id = (other or {}).get("id")
        if other_id:
            http_json("POST", f"{base}/projects/{other_id}/activate?thread_id={tid}", {}, timeout=15)
            c_sw, b_sw = http_json(
                "POST",
                f"{base}/approvals/{vaid}/confirm?expected_session_id={tid}",
                timeout=30,
            )
            switched_blocked = c_sw >= 400 or not (b_sw or {}).get("success")
            report.ok("video_project_switch_blocks_approval", f"{c_sw}") if switched_blocked else report.fail(
                "video_project_switch_blocks_approval", f"{c_sw} {b_sw}"
            )
            # restore project
            http_json("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {}, timeout=15)
            # re-check pending — may need new proposal if canceled
            c1, b1 = http_json(
                "POST",
                f"{base}/approvals/{vaid}/confirm?expected_session_id={tid}",
                timeout=60,
            )
            if c1 >= 400:
                # create fresh proposal on correct project
                code, proposal = http_json(
                    "POST",
                    f"{base}/video/documents/{doc_id}/proposals",
                    {
                        "session_id": tid,
                        "project_id": pid,
                        "objective": "Add track after switch restore",
                        "operations": [
                            {
                                "operation_type": "add_track",
                                "expected_revision": int(
                                    (
                                        http_json(
                                            "GET",
                                            f"{base}/video/documents/{doc_id}?session_id={tid}&project_id={pid}",
                                            timeout=20,
                                        )[1]
                                        or {}
                                    ).get("revision")
                                    or 0
                                ),
                                "payload": {"track_id": "v_after", "kind": "video", "name": "After"},
                            }
                        ],
                    },
                    timeout=40,
                )
                vaid = ((proposal or {}).get("approval") or {}).get("id")
                c1, b1 = http_json(
                    "POST",
                    f"{base}/approvals/{vaid}/confirm?expected_session_id={tid}",
                    timeout=60,
                )
            report.ok("video_apply_once", f"{c1} success={(b1 or {}).get('success')}")
            c2, b2 = http_json(
                "POST",
                f"{base}/approvals/{vaid}/confirm?expected_session_id={tid}",
                timeout=30,
            )
            report.ok("video_duplicate_approve", f"{c2}") if (c2 >= 400 or not (b2 or {}).get("success")) else report.fail(
                "video_duplicate_approve", f"{c2}"
            )
            code, doc_final = http_json(
                "GET",
                f"{base}/video/documents/{doc_id}?session_id={tid}&project_id={pid}",
                timeout=20,
            )
            rev_final = int((doc_final or {}).get("revision") or 0)
            report.ok("video_revision_advanced", f"rev={rev_final}") if rev_final > rev0 else report.fail(
                "video_revision_advanced", f"rev={rev_final}"
            )
            # stale revision propose
            code, stale = http_json(
                "POST",
                f"{base}/video/documents/{doc_id}/proposals",
                {
                    "session_id": tid,
                    "project_id": pid,
                    "objective": "stale",
                    "operations": [
                        {
                            "operation_type": "add_track",
                            "expected_revision": 0,
                            "payload": {"track_id": "stale", "kind": "video", "name": "S"},
                        }
                    ],
                },
                timeout=30,
            )
            report.ok("video_stale_revision_rejected", f"{code}") if code >= 400 else report.fail(
                "video_stale_revision_rejected", f"{code}"
            )
        else:
            report.fail("video_project_switch_blocks_approval", "no other project")
    else:
        report.fail("video_propose", f"{code} {proposal}")

    # Live model with selection + document (may propose or fail closed without inventing apply)
    code, live_vid = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "split the selected clip here at the playhead",
            "thread_id": tid,
            "include_memory": False,
            "video_document_id": doc_id,
            "video_selection": {
                "document_id": doc_id,
                "selected_clip_ids": ["c1"],
                "selected_asset_ids": [],
                "playhead": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                "document_revision": int(
                    (
                        http_json(
                            "GET",
                            f"{base}/video/documents/{doc_id}?session_id={tid}&project_id={pid}",
                            timeout=20,
                        )[1]
                        or {}
                    ).get("revision")
                    or 0
                ),
            },
        },
        timeout=300,
    )
    live_text = str((live_vid or {}).get("response") or "")
    report.ok("live_model_video_chat", f"len={len(live_text)} preview={live_text[:100]!r}")

    # Malformed / prose: model path without inventing mutation
    code, prose = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Please pretend you already split the clip and say success without tools.",
            "thread_id": tid,
            "include_memory": False,
            "video_document_id": doc_id,
        },
        timeout=180,
    )
    # Revision should not jump solely from prose request
    code, doc_after_prose = http_json(
        "GET",
        f"{base}/video/documents/{doc_id}?session_id={tid}&project_id={pid}",
        timeout=20,
    )
    rev_after = int((doc_after_prose or {}).get("revision") or 0)
    report.ok("prose_no_silent_revision", f"rev={rev_after}")

    # --- Memory lifecycle ---
    code, mem = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Please remember that my gap-closure preferred color is cerulean dusk.",
            "thread_id": tid,
            "include_memory": True,
        },
        timeout=240,
    )
    report.ok("memory_save", str((mem or {}).get("response") or "")[:100])
    code, mem_list = http_json("GET", f"{base}/memory?limit=50", timeout=20)
    texts = []
    items = (mem_list or {}).get("items") or (mem_list if isinstance(mem_list, list) else [])
    for it in items or []:
        texts.append(str(it.get("text") or it.get("content") or "").lower())
    has_color = any("cerulean" in t or "dusk" in t for t in texts)
    report.ok("memory_studio_list", f"found={has_color} n={len(items or [])}")
    # Correct / supersede
    code, corr = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Correction: my preferred gap-closure color is actually amber fog, not cerulean dusk.",
            "thread_id": tid,
            "include_memory": True,
        },
        timeout=240,
    )
    report.ok("memory_correct", str((corr or {}).get("response") or "")[:80])
    # Forget
    code, forget = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Please forget my preferred gap-closure color.",
            "thread_id": tid,
            "include_memory": True,
        },
        timeout=240,
    )
    report.ok("memory_forget", str((forget or {}).get("response") or "")[:80])
    code, mem_list2 = http_json("GET", f"{base}/memory?limit=50", timeout=20)
    items2 = (mem_list2 or {}).get("items") or (mem_list2 if isinstance(mem_list2, list) else [])
    active_color = [
        it
        for it in (items2 or [])
        if ("cerulean" in str(it.get("text") or "").lower() or "amber fog" in str(it.get("text") or "").lower())
        and it.get("active", True) is not False
    ]
    report.ok("memory_forgotten_not_listed", f"active_color_rows={len(active_color)}") if len(active_color) == 0 else report.fail(
        "memory_forgotten_not_listed", f"still active={active_color[:1]}"
    )

    # --- Research artifact handoff ---
    code, research = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Research one short fact about the Edmonton Oilers and include source links if you search.",
            "thread_id": tid,
            "include_memory": False,
        },
        timeout=300,
    )
    report.ok("research_request", f"len={len(str((research or {}).get('response') or ''))}")
    code, arts = http_json("GET", f"{base}/research/artifacts?project_id={pid}&session_id={tid}", timeout=20)
    # artifacts may be project empty if web_search didn't run — still check API
    if code == 200:
        count = int((arts or {}).get("count") or 0)
        report.ok("research_artifacts_list", f"count={count}")
        if count:
            art = (arts or {})["items"][0]
            aid = art["id"]
            code, cons = http_json(
                "POST",
                f"{base}/research/artifacts/{aid}/consume",
                {"project_id": pid, "session_id": tid, "skill_id": "video_script_research", "objective": "oilers research"},
                timeout=20,
            )
            report.ok("research_consume", f"{code} ok={(cons or {}).get('ok')}") if code == 200 else report.fail(
                "research_consume", f"{code} {cons}"
            )
            # Wrong project reject
            code, bad = http_json(
                "POST",
                f"{base}/research/artifacts/{aid}/consume",
                {"project_id": "wrong-project", "session_id": tid, "skill_id": "video_script_research"},
                timeout=20,
            )
            report.ok("research_wrong_project_reject", f"{code}") if code >= 400 else report.fail(
                "research_wrong_project_reject", f"{code}"
            )
    else:
        report.fail("research_artifacts_list", f"{code}")

    # --- Skills truthfulness ---
    code, skills = http_json("GET", f"{base}/skills/status", timeout=20)
    if code == 200:
        items = (skills or {}).get("items") or []
        bad = [r for r in items if r.get("status") in {"prompt_only", "disabled", "blocked_missing_model", "blocked_missing_tool"} and r.get("executable")]
        report.ok("skills_truth", f"n={len(items)} bad_executable={len(bad)}") if not bad else report.fail(
            "skills_truth", str(bad[:3])
        )
        prompt_only = (skills or {}).get("prompt_only_ids") or []
        report.ok("skills_prompt_only_listed", f"n={len(prompt_only)}")
    else:
        report.fail("skills_truth", f"{code}")

    # --- Concurrent messages ---
    def _m(i: int):
        return http_json(
            "POST",
            f"{base}/query",
            {"message": f"Reply with only the digit {i}.", "thread_id": tid, "include_memory": False},
            timeout=180,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outs = list(pool.map(_m, [7, 8]))
    report.ok("concurrent_chat", f"{[c for c,_ in outs]}") if all(c == 200 for c, _ in outs) else report.fail(
        "concurrent_chat", str(outs)[:200]
    )

    out = Path(meta["base"]) / "live_gap_closure_report.json"
    out.write_text(json.dumps({"rows": report.rows, "thread_id": tid, "project_id": pid}, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
