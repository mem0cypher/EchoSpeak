#!/usr/bin/env python3
"""Focused live-model acceptance for governed coding / research / video / stability.

Does not re-run the full 70-scenario matrix. Uses real process_query + LM Studio.
Outcomes are explicit (not merged into a single pass rate):
  completed_successfully | clarified_correctly | blocked_honestly |
  failed_safely | failed_incorrectly
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Reuse HTTP helpers from the permanent harness
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_model_acceptance import LiveModelHarness, http_json  # noqa: E402


OUTCOMES = (
    "completed_successfully",
    "clarified_correctly",
    "blocked_honestly",
    "failed_safely",
    "failed_incorrectly",
)


@dataclass
class GovernedResult:
    id: str
    path: str  # coding | research | video | stability
    prompt: str
    outcome: str = "failed_incorrectly"
    http_status: int = 0
    response_text: str = ""
    execution_id: str = ""
    route: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, str] = field(default_factory=dict)
    failure_reason: str = ""
    duration_s: float = 0.0
    model_provider: str = ""
    model_name: str = ""


class GovernedPathsHarness(LiveModelHarness):
    def __init__(self, base: str, meta: dict, out_path: Path):
        super().__init__(base, meta, out_path)
        self.g_results: list[GovernedResult] = []

    def _score_layers(self, **kwargs: str) -> dict[str, str]:
        keys = (
            "user_task_completion",
            "correct_clarification",
            "correct_blocking",
            "architectural_truthfulness",
            "tool_skill_selection",
            "argument_validity",
            "approval_creation",
            "verified_mutation",
            "final_response_truthfulness",
        )
        return {k: kwargs.get(k, "n/a") for k in keys}

    def _append(self, r: GovernedResult) -> None:
        status = r.outcome
        print(
            f"<<< {status} [{r.id}] {r.duration_s}s "
            f"{(r.failure_reason or r.response_text)[:90]!r}"
        )
        self.g_results.append(r)

    def _query_bundle(self, prompt: str, *, video: bool = False, include_memory: bool = False):
        t0 = time.time()
        code, data, events = self._query(
            prompt, include_memory=include_memory, video=video, stream=False
        )
        st = self._thread_state()
        eid = str((data or {}).get("execution_id") or st.get("last_execution_id") or "")
        tools = self._tool_runs(eid) if eid else []
        approvals = self._pending_approvals()
        text = str((data or {}).get("response") or "")
        route = self._classify_route(text, tools, approvals)
        return {
            "code": code,
            "data": data if isinstance(data, dict) else {},
            "text": text,
            "eid": eid,
            "tools": tools,
            "approvals": approvals,
            "route": route,
            "duration": round(time.time() - t0, 2),
            "events": events,
        }

    # -- coding --------------------------------------------------------------

    def path_coding(self) -> None:
        print("\n======== PATH: coding approval lifecycle ========")
        # Reset index title
        idx = self.workspace / "index.html"
        baseline = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "  <meta charset=\"utf-8\">\n  <title>My Awesome Game</title>\n"
            "</head>\n<body>\n  <main id=\"app\">Start Game</main>\n"
            "  <script src=\"game.js\"></script>\n</body>\n</html>\n"
        )
        idx.write_text(baseline, encoding="utf-8")
        game_before = (self.workspace / "game.js").read_bytes() if (self.workspace / "game.js").exists() else b""

        def eval_proposal(sid: str, prompt: str, *, require_index: bool = True) -> GovernedResult:
            b = self._query_bundle(prompt)
            r = GovernedResult(
                id=sid,
                path="coding",
                prompt=prompt,
                http_status=b["code"],
                response_text=b["text"][:2000],
                execution_id=b["eid"],
                route=b["route"],
                duration_s=b["duration"],
                model_provider=self.provider,
                model_name=self.model,
            )
            pending_fw = [a for a in b["approvals"] if a.get("tool") == "file_write"]
            text_l = b["text"].lower()
            invented = bool(
                re.search(
                    r"(?i)\b(i have updated|i updated|successfully updated|changes made to|"
                    r"the title has been changed|modified `?index)\b",
                    b["text"],
                )
            ) and not pending_fw and "not been saved" not in text_l and "prepared an edit" not in text_l
            clarified = bool(
                re.search(r"(?i)\b(which file|need a path|which html|clarify|what should the)\b", text_l)
            )
            blocked = bool(re.search(r"(?i)\b(blocked|cannot|can't|will not claim|did not create)\b", text_l))
            path_ok = any(
                Path(str((a.get("kwargs") or {}).get("path") or "")).name == "index.html"
                for a in pending_fw
            ) if require_index else True
            game_hit = any(
                Path(str((a.get("kwargs") or {}).get("path") or "")).name == "game.js"
                for a in pending_fw
            )
            r.evidence = {
                "pending_file_write": len(pending_fw),
                "paths": [str((a.get("kwargs") or {}).get("path") or "") for a in pending_fw],
                "tool_names": [t.get("tool_name") for t in b["tools"]],
                "invented_success": invented,
            }
            if b["code"] != 200:
                r.outcome = "failed_safely" if b["code"] == 0 else "failed_incorrectly"
                r.failure_reason = f"http {b['code']}"
                r.scores = self._score_layers(architectural_truthfulness="fail", final_response_truthfulness="fail")
                return r
            if game_hit:
                r.outcome = "failed_incorrectly"
                r.failure_reason = "targeted game.js"
                r.scores = self._score_layers(
                    argument_validity="fail",
                    approval_creation="fail",
                    architectural_truthfulness="fail",
                )
                return r
            if invented:
                r.outcome = "failed_incorrectly"
                r.failure_reason = "prose claimed mutation without durable approval"
                r.scores = self._score_layers(
                    architectural_truthfulness="fail",
                    approval_creation="fail",
                    final_response_truthfulness="fail",
                    user_task_completion="fail",
                )
                return r
            if pending_fw and path_ok:
                r.outcome = "completed_successfully"  # proposal stage complete
                r.scores = self._score_layers(
                    user_task_completion="n/a",
                    architectural_truthfulness="pass",
                    tool_skill_selection="pass",
                    argument_validity="pass",
                    approval_creation="pass",
                    final_response_truthfulness="pass",
                )
                return r
            if clarified:
                r.outcome = "clarified_correctly"
                r.scores = self._score_layers(
                    correct_clarification="pass",
                    architectural_truthfulness="pass",
                    final_response_truthfulness="pass",
                )
                return r
            if blocked or "prepared an edit" in text_l or "not been saved" in text_l:
                # honest incomplete without inventing
                r.outcome = "blocked_honestly" if blocked else "failed_safely"
                r.scores = self._score_layers(
                    correct_blocking="pass" if blocked else "n/a",
                    architectural_truthfulness="pass",
                    approval_creation="fail",
                    final_response_truthfulness="pass",
                )
                if not pending_fw:
                    r.failure_reason = "no durable pending approval"
                return r
            r.outcome = "failed_incorrectly"
            r.failure_reason = "no proposal, clarification, or honest block"
            r.scores = self._score_layers(
                approval_creation="fail",
                architectural_truthfulness="fail",
                final_response_truthfulness="fail",
            )
            return r

        cases = [
            ("coding_direct_title", "Change the title in index.html to Game Application Governed."),
            ("coding_exclude", "Only update index.html. Do not touch game.js. Change the title to Governed Only."),
            ("coding_casual", "yo change the page title in index.html to Casual Title Please"),
            ("coding_explain_only", "Do not change anything, just explain the title tag in index.html."),
        ]
        for sid, prompt in cases:
            print(f"\n>>> [{sid}] {prompt[:70]!r}")
            if sid == "coding_explain_only":
                b = self._query_bundle(prompt)
                r = GovernedResult(
                    id=sid,
                    path="coding",
                    prompt=prompt,
                    http_status=b["code"],
                    response_text=b["text"][:2000],
                    execution_id=b["eid"],
                    route=b["route"],
                    duration_s=b["duration"],
                    model_provider=self.provider,
                    model_name=self.model,
                )
                pending = [a for a in b["approvals"] if a.get("tool") == "file_write"]
                if pending:
                    r.outcome = "failed_incorrectly"
                    r.failure_reason = "created write approval on explain-only"
                elif b["code"] == 200 and b["text"].strip():
                    r.outcome = "completed_successfully"
                    r.scores = self._score_layers(
                        user_task_completion="pass",
                        architectural_truthfulness="pass",
                        final_response_truthfulness="pass",
                    )
                else:
                    r.outcome = "failed_incorrectly"
                self._append(r)
            else:
                self._append(eval_proposal(sid, prompt))

        # Full lifecycle: force a clear proposal then confirm once + duplicate
        print("\n>>> [coding_lifecycle] full proposal → confirm → ToolRun")
        t0 = time.time()
        b = self._query_bundle(
            "Change the title in index.html to Lifecycle Verified Title. Do not edit game.js."
        )
        pending = [a for a in self._pending_approvals() if a.get("tool") == "file_write"]
        r = GovernedResult(
            id="coding_lifecycle",
            path="coding",
            prompt="Change the title in index.html to Lifecycle Verified Title. Do not edit game.js.",
            http_status=b["code"],
            response_text=b["text"][:1500],
            execution_id=b["eid"],
            route=b["route"],
            model_provider=self.provider,
            model_name=self.model,
        )
        if not pending:
            r.outcome = "failed_incorrectly"
            r.failure_reason = "no pending file_write for lifecycle"
            r.duration_s = round(time.time() - t0, 2)
            r.scores = self._score_layers(approval_creation="fail", verified_mutation="fail")
            self._append(r)
        else:
            aid = pending[0]["id"]
            path = Path(str((pending[0].get("kwargs") or {}).get("path") or ""))
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            c1, b1 = http_json(
                "POST",
                f"{self.base}/approvals/{aid}/confirm?expected_session_id={self.thread_id}",
                timeout=180,
            )
            c2, b2 = http_json(
                "POST",
                f"{self.base}/approvals/{aid}/confirm?expected_session_id={self.thread_id}",
                timeout=60,
            )
            after = path.read_text(encoding="utf-8") if path.exists() else ""
            game_ok = (self.workspace / "game.js").read_bytes() == game_before if game_before else True
            # ToolRuns for confirm may be on new execution — query session tools
            code, tr = http_json(
                "GET",
                f"{self.base}/tool-runs?session_id={self.thread_id}&limit=30",
                timeout=20,
            )
            items = list((tr or {}).get("items") or []) if code == 200 else []
            writes = [t for t in items if t.get("tool_name") == "file_write"]
            ok = (
                c1 == 200
                and bool((b1 or {}).get("success"))
                and (c2 >= 400 or not (b2 or {}).get("success"))
                and "Lifecycle Verified Title" in after
                and after != before
                and game_ok
            )
            r.evidence = {
                "confirm_c1": c1,
                "confirm_c2": c2,
                "file_write_toolruns": len(writes),
                "title_in_file": "Lifecycle Verified Title" in after,
            }
            r.duration_s = round(time.time() - t0, 2)
            if ok:
                r.outcome = "completed_successfully"
                r.scores = self._score_layers(
                    user_task_completion="pass",
                    architectural_truthfulness="pass",
                    approval_creation="pass",
                    verified_mutation="pass",
                    final_response_truthfulness="pass",
                    argument_validity="pass",
                )
            else:
                r.outcome = "failed_incorrectly"
                r.failure_reason = f"lifecycle failed c1={c1} c2={c2} title={('Lifecycle Verified Title' in after)}"
                r.scores = self._score_layers(
                    verified_mutation="fail",
                    approval_creation="pass" if pending else "fail",
                )
            self._append(r)

        # Cancel path
        print("\n>>> [coding_cancel] propose then cancel")
        b = self._query_bundle(
            "Change the title in index.html to Should Be Cancelled. Do not edit game.js."
        )
        pending = [a for a in self._pending_approvals() if a.get("tool") == "file_write"]
        r = GovernedResult(
            id="coding_cancel",
            path="coding",
            prompt="Change the title in index.html to Should Be Cancelled. Do not edit game.js.",
            response_text=b["text"][:800],
            http_status=b["code"],
            model_provider=self.provider,
            model_name=self.model,
        )
        if not pending:
            r.outcome = "failed_safely"
            r.failure_reason = "no pending to cancel"
            self._append(r)
        else:
            aid = pending[0]["id"]
            path = Path(str((pending[0].get("kwargs") or {}).get("path") or ""))
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            c, _ = http_json(
                "POST",
                f"{self.base}/approvals/{aid}/cancel?expected_session_id={self.thread_id}",
                timeout=60,
            )
            after = path.read_text(encoding="utf-8") if path.exists() else ""
            if c == 200 and after == before:
                r.outcome = "completed_successfully"
                r.scores = self._score_layers(
                    user_task_completion="pass",
                    architectural_truthfulness="pass",
                    verified_mutation="pass",
                )
            else:
                r.outcome = "failed_incorrectly"
                r.failure_reason = f"cancel c={c} file_changed={after != before}"
            self._append(r)

    # -- research ------------------------------------------------------------

    def path_research(self) -> None:
        print("\n======== PATH: research execution truth ========")

        def eval_research(sid: str, prompt: str, *, local_first: bool = False) -> None:
            print(f"\n>>> [{sid}] {prompt[:70]!r}")
            b = self._query_bundle(prompt)
            r = GovernedResult(
                id=sid,
                path="research",
                prompt=prompt,
                http_status=b["code"],
                response_text=b["text"][:2000],
                execution_id=b["eid"],
                route=b["route"],
                duration_s=b["duration"],
                model_provider=self.provider,
                model_name=self.model,
            )
            tools = [str(t.get("tool_name") or "") for t in b["tools"]]
            used_research = any(t in {"web_search", "browse_task", "sports_live"} for t in tools)
            honest_block = bool(
                re.search(
                    r"(?i)(could not complete verified public research|no successful web_search|"
                    r"cannot verify|unsupported summary|not available)",
                    b["text"],
                )
            )
            # Artifacts
            code, arts = http_json(
                "GET",
                f"{self.base}/research/artifacts?project_id={self.project_id}&session_id={self.thread_id}",
                timeout=20,
            )
            items = []
            if code == 200:
                items = (arts or {}).get("items") or (arts if isinstance(arts, list) else []) or []
            r.evidence = {
                "tools": tools,
                "used_research": used_research,
                "artifact_count": len(items),
                "honest_block": honest_block,
            }
            if b["code"] != 200:
                r.outcome = "failed_safely"
                r.failure_reason = f"http {b['code']}"
                self._append(r)
                return
            if local_first:
                # Must not invent public research completion
                if used_research:
                    r.outcome = "completed_successfully"
                elif b["text"].strip() and not re.search(r"(?i)\baccording to (cnn|bbc|reuters)\b", b["text"]):
                    r.outcome = "completed_successfully"
                    r.scores = self._score_layers(
                        user_task_completion="pass",
                        architectural_truthfulness="pass",
                        final_response_truthfulness="pass",
                    )
                else:
                    r.outcome = "failed_incorrectly"
                self._append(r)
                return
            parse_fail = bool(
                re.search(
                    r"(?i)(could not safely parse|tool-call-shaped|no successful web_search|"
                    r"unsupported summary|could not complete verified public research)",
                    b["text"],
                )
            )
            fluent_without_tool = (
                not used_research
                and not honest_block
                and not parse_fail
                and len(b["text"].strip()) > 40
                and not re.search(r"(?i)\b(cannot|can't|unable|failed|blocked)\b", b["text"])
            )
            if used_research:
                r.outcome = "completed_successfully"
                r.scores = self._score_layers(
                    user_task_completion="pass",
                    tool_skill_selection="pass",
                    architectural_truthfulness="pass",
                    final_response_truthfulness="pass",
                )
            elif honest_block or parse_fail:
                r.outcome = "blocked_honestly"
                r.scores = self._score_layers(
                    correct_blocking="pass",
                    architectural_truthfulness="pass",
                    final_response_truthfulness="pass",
                    user_task_completion="fail",
                )
            elif fluent_without_tool:
                # Fluent answer without tool = incorrect architecture
                r.outcome = "failed_incorrectly"
                r.failure_reason = "research prose without research ToolRun"
                r.scores = self._score_layers(
                    architectural_truthfulness="fail",
                    tool_skill_selection="fail",
                    final_response_truthfulness="fail",
                    user_task_completion="fail",
                )
            else:
                r.outcome = "failed_safely"
                r.failure_reason = "research incomplete without invented facts"
                r.scores = self._score_layers(
                    architectural_truthfulness="pass",
                    final_response_truthfulness="pass",
                    user_task_completion="fail",
                )
            self._append(r)

        eval_research(
            "research_public",
            "Research one short fact about the Edmonton Oilers and mention sources if you can.",
        )
        eval_research(
            "research_lookup",
            "Look up the latest public information about local-first AI agents — one paragraph with sources.",
        )
        eval_research(
            "research_local_first",
            "Use local project material first and do not search the web. Summarize what this project is.",
            local_first=True,
        )

    # -- video ---------------------------------------------------------------

    def _seed_video_with_clip(self) -> bool:
        """Create document, import real media, insert clip, set selection state."""
        code, doc = http_json(
            "POST",
            f"{self.base}/video/documents",
            {
                "session_id": self.thread_id,
                "project_id": self.project_id,
                "name": "Governed Live Cut",
            },
            timeout=30,
        )
        self.video_doc_id = str((doc or {}).get("id") or "")
        self.video_rev = int((doc or {}).get("revision") or 0)
        if not self.video_doc_id:
            print(f"video doc create failed: {code} {doc}")
            return False
        # Prefer assets/import (canonical)
        rel = "media/clip_a.mp4"
        media = self.workspace / rel
        if not media.exists() or media.stat().st_size < 1000:
            print(f"media missing or tiny: {media}")
            return False
        code, imp = http_json(
            "POST",
            f"{self.base}/video/documents/{self.video_doc_id}/assets/import",
            {
                "session_id": self.thread_id,
                "project_id": self.project_id,
                "project_relative_path": rel,
            },
            timeout=60,
        )
        if code >= 400:
            # fallback legacy /import
            code, imp = http_json(
                "POST",
                f"{self.base}/video/documents/{self.video_doc_id}/import",
                {
                    "session_id": self.thread_id,
                    "project_id": self.project_id,
                    "project_relative_path": rel,
                },
                timeout=60,
            )
        print(f"video import status={code}")
        code, doc2 = http_json(
            "GET",
            f"{self.base}/video/documents/{self.video_doc_id}"
            f"?session_id={self.thread_id}&project_id={self.project_id}",
            timeout=20,
        )
        self.video_rev = int((doc2 or {}).get("revision") or self.video_rev)
        assets = (doc2 or {}).get("assets") or []
        if not assets:
            print("no assets after import")
            return False
        aid = assets[0].get("id")
        self.clip_id = "clip-gov-1"
        # Sequential txs: same expected_revision on multi-op batches can fail silently.
        http_json(
            "POST",
            f"{self.base}/video/documents/{self.video_doc_id}/transactions",
            {
                "session_id": self.thread_id,
                "project_id": self.project_id,
                "operations": [
                    {
                        "operation_type": "add_track",
                        "expected_revision": self.video_rev,
                        "payload": {"track_id": "v1", "kind": "video", "name": "V1"},
                    }
                ],
            },
            timeout=30,
        )
        code, doc3 = http_json(
            "GET",
            f"{self.base}/video/documents/{self.video_doc_id}"
            f"?session_id={self.thread_id}&project_id={self.project_id}",
            timeout=20,
        )
        self.video_rev = int((doc3 or {}).get("revision") or self.video_rev + 1)
        code, tx = http_json(
            "POST",
            f"{self.base}/video/documents/{self.video_doc_id}/transactions",
            {
                "session_id": self.thread_id,
                "project_id": self.project_id,
                "operations": [
                    {
                        "operation_type": "insert_clip",
                        "expected_revision": self.video_rev,
                        "payload": {
                            "track_id": "v1",
                            "clip_id": self.clip_id,
                            "asset_id": aid,
                            "timeline_start": {
                                "ticks": "0",
                                "time_base": {"numerator": 1, "denominator": 1000},
                            },
                            "duration": {
                                "ticks": "6000",
                                "time_base": {"numerator": 1, "denominator": 1000},
                            },
                        },
                    }
                ],
            },
            timeout=40,
        )
        print(f"insert_clip status={code} body={str(tx)[:160]}")
        code, docf = http_json(
            "GET",
            f"{self.base}/video/documents/{self.video_doc_id}"
            f"?session_id={self.thread_id}&project_id={self.project_id}",
            timeout=20,
        )
        self.video_rev = int((docf or {}).get("revision") or self.video_rev)
        clips = (docf or {}).get("clips") or []
        tracks = (docf or {}).get("tracks") or []
        # clips may be nested in tracks
        if not clips and tracks:
            for t in tracks:
                clips.extend(t.get("clips") or [])
        if clips:
            self.clip_id = str(clips[0].get("id") or self.clip_id)
        print(
            f"video seed doc={self.video_doc_id} rev={self.video_rev} "
            f"clip={self.clip_id} assets={len(assets)} clips={len(clips)} tx={code}"
        )
        return bool(self.clip_id and self.video_doc_id)

    def path_video(self) -> None:
        print("\n======== PATH: full live video mutation ========")
        seeded = self._seed_video_with_clip()
        r_seed = GovernedResult(
            id="video_seed",
            path="video",
            prompt="(seed synthetic timeline + selected clip)",
            outcome="completed_successfully" if seeded else "failed_incorrectly",
            failure_reason="" if seeded else "seed failed",
            model_provider=self.provider,
            model_name=self.model,
            evidence={"clip_id": self.clip_id, "doc": self.video_doc_id, "rev": self.video_rev},
        )
        self._append(r_seed)
        if not seeded:
            return

        def eval_video_op(sid: str, prompt: str, *, expect_proposal: bool = True) -> None:
            print(f"\n>>> [{sid}] {prompt[:70]!r}")
            b = self._query_bundle(prompt, video=True)
            r = GovernedResult(
                id=sid,
                path="video",
                prompt=prompt,
                http_status=b["code"],
                response_text=b["text"][:2000],
                execution_id=b["eid"],
                route=b["route"],
                duration_s=b["duration"],
                model_provider=self.provider,
                model_name=self.model,
            )
            pending_v = [a for a in b["approvals"] if a.get("tool") == "video_apply_transaction"]
            tools = [str(t.get("tool_name") or "") for t in b["tools"]]
            text_l = b["text"].lower()
            missing_sel = bool(re.search(r"(?i)(need a selected clip|select a clip|which clip)", text_l))
            blocked = bool(re.search(r"(?i)(blocked|unavailable|can't do that|cannot)", text_l))
            clarified = bool(re.search(r"(?i)(which|what do you want|clarify|section)", text_l))
            invented = bool(
                re.search(r"(?i)(applied|revision\s+\d+).{0,40}(split|mute|deleted)", text_l)
            ) and not pending_v
            r.evidence = {
                "pending_video": len(pending_v),
                "tools": tools,
                "clip_id": self.clip_id,
                "rev": self.video_rev,
            }
            if invented:
                r.outcome = "failed_incorrectly"
                r.failure_reason = "claimed video mutation without approval"
            elif pending_v:
                r.outcome = "completed_successfully"  # proposal stage
                r.scores = self._score_layers(
                    approval_creation="pass",
                    architectural_truthfulness="pass",
                    tool_skill_selection="pass",
                    final_response_truthfulness="pass",
                )
            elif expect_proposal and missing_sel:
                # With seeded selection this is incorrect
                r.outcome = "failed_incorrectly"
                r.failure_reason = "missing-selection despite seeded clip"
            elif blocked:
                r.outcome = "blocked_honestly"
                r.scores = self._score_layers(correct_blocking="pass", architectural_truthfulness="pass")
            elif clarified:
                r.outcome = "clarified_correctly"
                r.scores = self._score_layers(correct_clarification="pass", architectural_truthfulness="pass")
            elif b["code"] == 200 and b["text"].strip():
                r.outcome = "failed_safely"
                r.failure_reason = "response without proposal"
            else:
                r.outcome = "failed_incorrectly"
            self._append(r)

        eval_video_op("video_split_selected", "Split the selected clip at the playhead.")
        eval_video_op("video_mute_selected", "Mute the selected clip.")
        eval_video_op("video_volume_selected", "Set the selected clip volume to 50 percent.")
        eval_video_op("video_delete_selected", "Delete the selected clip.")
        eval_video_op("video_vague", "Make this look better.", expect_proposal=False)

        # Missing selection (no video context) — cancel leftover pendings first so
        # session-wide pending list does not pollute this Turn's oracle.
        for a in list(self._pending_approvals()):
            try:
                http_json(
                    "POST",
                    f"{self.base}/approvals/{a['id']}/cancel?expected_session_id={self.thread_id}",
                    timeout=30,
                )
            except Exception:
                pass
        print("\n>>> [video_no_selection] split without video payload")
        b = self._query_bundle("Split the selected clip at the playhead.", video=False)
        r = GovernedResult(
            id="video_no_selection",
            path="video",
            prompt="Split the selected clip at the playhead. (no selection payload)",
            http_status=b["code"],
            response_text=b["text"][:1000],
            duration_s=b["duration"],
            model_provider=self.provider,
            model_name=self.model,
        )
        # Only count approvals created this execution (if any)
        pending_this = []
        if b["eid"]:
            # Approvals lack execution link sometimes — require fail-closed text and no success claim
            pass
        text_l = b["text"].lower()
        invented = bool(re.search(r"(?i)(prepared a proposal|applied|revision)", text_l)) and not re.search(
            r"(?i)(need a selected|select a clip|which clip)", text_l
        )
        if invented:
            r.outcome = "failed_incorrectly"
            r.failure_reason = "proposed without selection"
        elif re.search(r"(?i)(select|which clip|need a selected|no clip)", text_l):
            r.outcome = "clarified_correctly"
            r.scores = self._score_layers(
                correct_clarification="pass",
                architectural_truthfulness="pass",
            )
        else:
            r.outcome = "failed_safely"
            r.failure_reason = "no clear selection fail-closed message"
        self._append(r)

        # Fresh single proposal then approve once (avoid multi-pending race)
        print("\n>>> [video_fresh_split_then_approve]")
        for a in list(self._pending_approvals()):
            try:
                http_json(
                    "POST",
                    f"{self.base}/approvals/{a['id']}/cancel?expected_session_id={self.thread_id}",
                    timeout=30,
                )
            except Exception:
                pass
        # refresh doc rev
        code, doc0 = http_json(
            "GET",
            f"{self.base}/video/documents/{self.video_doc_id}"
            f"?session_id={self.thread_id}&project_id={self.project_id}",
            timeout=20,
        )
        self.video_rev = int((doc0 or {}).get("revision") or self.video_rev)
        b = self._query_bundle("Split the selected clip at the playhead.", video=True)
        pending = [a for a in self._pending_approvals() if a.get("tool") == "video_apply_transaction"]
        # Prefer Session's current pending_approval_id (authority pointer).
        if pending:
            st = self._thread_state()
            cur = str(st.get("pending_approval_id") or "").strip()
            if cur and any(str(a.get("id")) == cur for a in pending):
                aid = cur
            else:
                pending = sorted(
                    pending, key=lambda a: str(a.get("created_at") or a.get("id") or "")
                )
                aid = pending[-1]["id"]
            rev_before = self.video_rev
            c1, b1 = http_json(
                "POST",
                f"{self.base}/approvals/{aid}/confirm?expected_session_id={self.thread_id}",
                timeout=90,
            )
            c2, b2 = http_json(
                "POST",
                f"{self.base}/approvals/{aid}/confirm?expected_session_id={self.thread_id}",
                timeout=30,
            )
            code, doc = http_json(
                "GET",
                f"{self.base}/video/documents/{self.video_doc_id}"
                f"?session_id={self.thread_id}&project_id={self.project_id}",
                timeout=20,
            )
            rev_after = int((doc or {}).get("revision") or 0)
            clips = list((doc or {}).get("clips") or [])
            if not clips:
                for t in (doc or {}).get("tracks") or []:
                    clips.extend(t.get("clips") or [])
            r = GovernedResult(
                id="video_approve_once",
                path="video",
                prompt="(fresh split proposal + confirm once + duplicate)",
                http_status=c1,
                response_text=str(b1)[:500],
                model_provider=self.provider,
                model_name=self.model,
                evidence={
                    "c1": c1,
                    "c2": c2,
                    "rev_before": rev_before,
                    "rev_after": rev_after,
                    "clip_count": len(clips),
                    "proposal_text": b["text"][:200],
                },
            )
            ok = (
                c1 == 200
                and bool((b1 or {}).get("success") is not False)
                and (c2 >= 400 or not (b2 or {}).get("success"))
                and rev_after > rev_before
            )
            r.outcome = "completed_successfully" if ok else "failed_incorrectly"
            if not ok:
                r.failure_reason = f"c1={c1} c2={c2} rev {rev_before}->{rev_after} detail={str(b1)[:120]}"
            else:
                r.scores = self._score_layers(
                    user_task_completion="pass",
                    verified_mutation="pass",
                    architectural_truthfulness="pass",
                    approval_creation="pass",
                )
                self.video_rev = rev_after
            self._append(r)
        else:
            r = GovernedResult(
                id="video_approve_once",
                path="video",
                prompt="(fresh split proposal + confirm)",
                outcome="failed_incorrectly",
                failure_reason="no pending after fresh split",
                response_text=b["text"][:400],
                model_provider=self.provider,
                model_name=self.model,
            )
            self._append(r)

    # -- stability -----------------------------------------------------------

    def path_stability(self) -> None:
        print("\n======== PATH: long-run stability ========")
        # repeated short calls
        ok_n = 0
        fail_n = 0
        for i in range(5):
            b = self._query_bundle(f"Reply with exactly the word stab{i}.")
            if b["code"] == 200 and b["text"].strip():
                ok_n += 1
            else:
                fail_n += 1
        r = GovernedResult(
            id="stability_repeat_5",
            path="stability",
            prompt="(5 repeated short queries)",
            outcome="completed_successfully" if fail_n == 0 else "failed_safely",
            evidence={"ok": ok_n, "fail": fail_n},
            failure_reason="" if fail_n == 0 else f"{fail_n} failed",
            model_provider=self.provider,
            model_name=self.model,
        )
        self._append(r)

        # concurrent-ish sequential burst while checking health
        health_ok = 0
        for _ in range(3):
            c, h = http_json("GET", f"{self.base}/health", timeout=5)
            if c == 200:
                health_ok += 1
            time.sleep(0.3)
        r = GovernedResult(
            id="stability_health_between",
            path="stability",
            prompt="(health between calls)",
            outcome="completed_successfully" if health_ok == 3 else "failed_safely",
            evidence={"health_ok": health_ok},
            model_provider=self.provider,
            model_name=self.model,
        )
        self._append(r)

        # empty-ish / malformed-friendly short prompt
        b = self._query_bundle("???")
        r = GovernedResult(
            id="stability_ambiguous_punct",
            path="stability",
            prompt="???",
            http_status=b["code"],
            response_text=b["text"][:500],
            duration_s=b["duration"],
            model_provider=self.provider,
            model_name=self.model,
        )
        if b["code"] == 200:
            r.outcome = "clarified_correctly" if b["text"].strip() else "failed_safely"
        else:
            r.outcome = "failed_safely"
        self._append(r)

        # stream cancel-ish: open stream simple
        t0 = time.time()
        events = self._query("Say only the word streamgov.", stream=True)[2]
        r = GovernedResult(
            id="stability_stream",
            path="stability",
            prompt="Say only the word streamgov.",
            outcome="completed_successfully" if events else "failed_safely",
            evidence={"events": len(events)},
            duration_s=round(time.time() - t0, 2),
            model_provider=self.provider,
            model_name=self.model,
        )
        self._append(r)

    def build_report(self) -> dict:
        by_outcome: dict[str, int] = {o: 0 for o in OUTCOMES}
        by_path: dict[str, dict] = {}
        layer_stats: dict[str, dict[str, int]] = {}
        for r in self.g_results:
            by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
            p = by_path.setdefault(
                r.path, {o: 0 for o in OUTCOMES} | {"total": 0}
            )
            p["total"] += 1
            p[r.outcome] = p.get(r.outcome, 0) + 1
            for k, v in (r.scores or {}).items():
                slot = layer_stats.setdefault(k, {"pass": 0, "fail": 0, "n/a": 0})
                slot[v if v in slot else "n/a"] = slot.get(v if v in slot else "n/a", 0) + 1

        def rate(num: int, den: int) -> float:
            return round(num / den, 3) if den else 0.0

        coding = [r for r in self.g_results if r.path == "coding"]
        research = [r for r in self.g_results if r.path == "research"]
        video = [r for r in self.g_results if r.path == "video"]
        # Mutation completion: lifecycle completed_successfully only
        coding_mutations = [
            r for r in coding if r.id in {"coding_lifecycle"} and r.outcome == "completed_successfully"
        ]
        video_mutations = [
            r for r in video if r.id == "video_approve_once" and r.outcome == "completed_successfully"
        ]
        research_tools = [
            r
            for r in research
            if r.outcome == "completed_successfully" and (r.evidence or {}).get("used_research")
        ]

        return {
            "generated_at": time.time(),
            "provider": self.provider,
            "model": self.model,
            "thread_id": self.thread_id,
            "project_id": self.project_id,
            "video_doc_id": self.video_doc_id,
            "video_clip_id": self.clip_id,
            "total": len(self.g_results),
            "by_outcome": by_outcome,
            "by_path": by_path,
            "layer_stats": layer_stats,
            "rates": {
                "coding_proposal_or_honest": rate(
                    sum(
                        1
                        for r in coding
                        if r.outcome
                        in {
                            "completed_successfully",
                            "clarified_correctly",
                            "blocked_honestly",
                        }
                        or (r.evidence or {}).get("pending_file_write")
                    ),
                    max(1, len([c for c in coding if c.id != "coding_explain_only"])),
                ),
                "coding_verified_mutation": rate(len(coding_mutations), 1),
                "research_toolrun_or_honest_block": rate(
                    sum(
                        1
                        for r in research
                        if r.outcome in {"completed_successfully", "blocked_honestly"}
                        or (r.evidence or {}).get("used_research")
                        or (r.evidence or {}).get("honest_block")
                    ),
                    max(1, len(research)),
                ),
                "research_with_toolrun": rate(len(research_tools), max(1, len([r for r in research if r.id != "research_local_first"]))),
                "video_proposal_with_selection": rate(
                    sum(
                        1
                        for r in video
                        if r.id.startswith("video_")
                        and r.id not in {"video_seed", "video_no_selection", "video_approve_once", "video_vague"}
                        and r.outcome == "completed_successfully"
                    ),
                    4,
                ),
                "video_verified_mutation": rate(len(video_mutations), 1),
                "clarified_correctly": rate(by_outcome.get("clarified_correctly", 0), max(1, len(self.g_results))),
                "blocked_honestly": rate(by_outcome.get("blocked_honestly", 0), max(1, len(self.g_results))),
                "failed_incorrectly": rate(by_outcome.get("failed_incorrectly", 0), max(1, len(self.g_results))),
            },
            "scenarios": [asdict(r) for r in self.g_results],
        }

    def run(self, paths: Optional[set[str]] = None) -> dict:
        self.setup()
        # re-seed video with real clip for this harness
        paths = paths or {"coding", "research", "video", "stability"}
        if "coding" in paths:
            self.path_coding()
        if "research" in paths:
            self.path_research()
        if "video" in paths:
            self.path_video()
        if "stability" in paths:
            self.path_stability()
        report = self.build_report()
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("\n" + "=" * 72)
        print("GOVERNED PATHS REPORT")
        print("=" * 72)
        print(f"Provider: {report['provider']}  Model: {report['model']}")
        print(f"Total: {report['total']}")
        print("By outcome:", report["by_outcome"])
        print("Rates:", json.dumps(report["rates"], indent=2))
        print(f"Wrote {self.out_path}")
        return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--paths", default="coding,research,video,stability")
    args = ap.parse_args()
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    harness = GovernedPathsHarness(args.base, meta, Path(args.out))
    report = harness.run(set(p.strip() for p in args.paths.split(",") if p.strip()))
    # Exit non-zero if any failed_incorrectly
    bad = report["by_outcome"].get("failed_incorrectly", 0)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
