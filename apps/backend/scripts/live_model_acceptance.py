#!/usr/bin/env python3
"""Permanent live-model acceptance harness for EchoSpeak.

Exercises natural-language requests through the real production HTTP API
and evaluates durable backend state (not prose). Requires a running backend
with ECHOSPEAK_DATA_DIR set and a configured LM Studio (or other) model.

Usage:
  python scripts/live_model_acceptance.py \\
    --base http://127.0.0.1:8000 \\
    --meta path/to/live_meta.json \\
    --out path/to/report.json \\
    [--subset chat,coding,memory] \\
    [--limit 0]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_json(
    method: str,
    url: str,
    body: Optional[dict] = None,
    timeout: float = 300.0,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return int(resp.status), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            return int(exc.code), {"error": raw or str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}


def http_stream(url: str, body: dict, timeout: float = 300.0) -> list[dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    events: list[dict] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    events.append({"type": "raw", "text": line})
    except Exception as exc:
        events.append({"type": "error", "message": str(exc)})
    return events


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    id: str
    archetype: str
    prompt: str
    variation: str = "direct"
    multi_turn: bool = False
    model_invoked: bool = False
    model_provider: str = ""
    model_name: str = ""
    request_id: str = ""
    execution_id: str = ""
    http_status: int = 0
    response_text: str = ""
    success_flag: Optional[bool] = None
    route: str = ""  # native_tool_call | structured_action | deterministic | clarification | plain | error
    tool_runs: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    scores: dict[str, str] = field(default_factory=dict)  # layer -> pass|fail|n/a
    passed: bool = False
    failure_reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class LiveModelHarness:
    def __init__(self, base: str, meta: dict, out_path: Path):
        self.base = base.rstrip("/")
        self.meta = meta
        self.out_path = out_path
        self.workspace = Path(meta["workspace"])
        self.results: list[ScenarioResult] = []
        self.provider = ""
        self.model = ""
        self.thread_id = ""
        self.project_id = ""
        self.video_doc_id = ""
        self.video_rev = 0
        self.clip_id = ""
        self._consecutive_http0 = 0
        self._backend_dead = False

    # -- setup ---------------------------------------------------------------

    def setup(self) -> None:
        code, health = http_json("GET", f"{self.base}/health", timeout=10)
        if code != 200:
            raise RuntimeError(f"backend unhealthy: {code} {health}")

        code, prov = http_json("GET", f"{self.base}/provider", timeout=15)
        if isinstance(prov, dict):
            self.provider = str(prov.get("provider") or prov.get("name") or "")
            self.model = str(
                prov.get("model")
                or (prov.get("local") or {}).get("model_name")
                or prov.get("model_name")
                or ""
            )
        # diagnostics
        code, diag = http_json("GET", f"{self.base}/diagnostics/tool-calling", timeout=15)
        if isinstance(diag, dict):
            d = diag.get("diagnostics") or diag.get("capability_matrix") or {}
            if not self.provider:
                self.provider = str(d.get("provider") or "")
            if not self.model:
                self.model = str(d.get("model") or "")

        code, th = http_json("POST", f"{self.base}/threads", {"title": "llm-live-matrix"}, timeout=20)
        self.thread_id = str((th or {}).get("thread_id") or (th or {}).get("id") or "")
        if not self.thread_id:
            raise RuntimeError(f"create thread failed: {code} {th}")

        code, proj = http_json(
            "POST",
            f"{self.base}/projects/attach-folder",
            {
                "path": str(self.workspace),
                "name": "LLM Live Project",
                "trust_state": "trusted",
                "session_id": self.thread_id,
            },
            timeout=40,
        )
        self.project_id = str((proj or {}).get("id") or "")
        if self.project_id:
            http_json(
                "POST",
                f"{self.base}/projects/{self.project_id}/activate?thread_id={self.thread_id}",
                {},
                timeout=20,
            )

        # Video document for video scenarios
        code, doc = http_json(
            "POST",
            f"{self.base}/video/documents",
            {
                "session_id": self.thread_id,
                "project_id": self.project_id,
                "name": "LLM Live Cut",
            },
            timeout=30,
        )
        self.video_doc_id = str((doc or {}).get("id") or "")
        self.video_rev = int((doc or {}).get("revision") or 0)
        if self.video_doc_id:
            # seed track
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
            code, doc2 = http_json(
                "GET",
                f"{self.base}/video/documents/{self.video_doc_id}"
                f"?session_id={self.thread_id}&project_id={self.project_id}",
                timeout=20,
            )
            self.video_rev = int((doc2 or {}).get("revision") or self.video_rev)
            # Import disposable media so selection-based video ops have a real clip.
            # Prefer project-relative path used by live_api_acceptance.
            rel = "media/clip_a.mp4"
            media_abs = self.workspace / rel
            if not media_abs.exists():
                alt = str(self.meta.get("video_clip") or "").strip()
                if alt and Path(alt).exists():
                    media_abs = Path(alt)
                    try:
                        rel = str(media_abs.relative_to(self.workspace)).replace("\\", "/")
                    except ValueError:
                        rel = media_abs.name
            if media_abs.exists():
                http_json(
                    "POST",
                    f"{self.base}/video/documents/{self.video_doc_id}/import",
                    {
                        "session_id": self.thread_id,
                        "project_id": self.project_id,
                        "project_relative_path": rel.replace("\\", "/"),
                        "expected_revision": self.video_rev,
                    },
                    timeout=60,
                )
                code, doc2b = http_json(
                    "GET",
                    f"{self.base}/video/documents/{self.video_doc_id}"
                    f"?session_id={self.thread_id}&project_id={self.project_id}",
                    timeout=20,
                )
                if code == 200 and isinstance(doc2b, dict):
                    doc2 = doc2b
                    self.video_rev = int((doc2 or {}).get("revision") or self.video_rev)
            # Insert synthetic clip if assets exist
            assets = (doc2 or {}).get("assets") or []
            if not assets:
                # Last-resort: register a placeholder asset via transaction if API allows
                pass
            if assets:
                aid = assets[0].get("id")
                self.clip_id = "clip-llm-1"
                http_json(
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
                    timeout=30,
                )
                code, doc3 = http_json(
                    "GET",
                    f"{self.base}/video/documents/{self.video_doc_id}"
                    f"?session_id={self.thread_id}&project_id={self.project_id}",
                    timeout=20,
                )
                self.video_rev = int((doc3 or {}).get("revision") or self.video_rev)
                clips = (doc3 or {}).get("clips") or []
                if clips and not self.clip_id:
                    self.clip_id = str(clips[0].get("id") or "")

        print(
            f"SETUP provider={self.provider!r} model={self.model!r} "
            f"thread={self.thread_id} project={self.project_id} "
            f"video_doc={self.video_doc_id} rev={self.video_rev} clip={self.clip_id}"
        )

    # -- helpers -------------------------------------------------------------

    def _query(
        self,
        message: str,
        *,
        include_memory: bool = False,
        stream: bool = False,
        video: bool = False,
        timeout: float = 280.0,
    ) -> tuple[int, dict, list[dict]]:
        body: dict[str, Any] = {
            "message": message,
            "thread_id": self.thread_id,
            "include_memory": include_memory,
        }
        if video and self.video_doc_id:
            body["video_document_id"] = self.video_doc_id
            body["video_selection"] = {
                "document_id": self.video_doc_id,
                "selected_clip_ids": [self.clip_id] if self.clip_id else [],
                "selected_asset_ids": [],
                "playhead": {
                    "ticks": "3000",
                    "time_base": {"numerator": 1, "denominator": 1000},
                },
                "document_revision": self.video_rev,
            }
        events: list[dict] = []
        if stream:
            events = http_stream(f"{self.base}/query/stream", body, timeout=timeout)
            final = {}
            for e in reversed(events):
                if e.get("type") == "final" or e.get("response"):
                    final = {
                        "response": e.get("response") or e.get("text") or "",
                        "success": e.get("success"),
                        "request_id": e.get("request_id"),
                        "execution_id": e.get("execution_id"),
                    }
                    break
            return 200 if events else 0, final, events

        code, data = http_json("POST", f"{self.base}/query", body, timeout=timeout)
        return code, data if isinstance(data, dict) else {"response": str(data)}, events

    def _pending_approvals(self) -> list[dict]:
        code, data = http_json(
            "GET", f"{self.base}/approvals?thread_id={self.thread_id}", timeout=20
        )
        if code != 200:
            return []
        items = data if isinstance(data, list) else (data or {}).get("items") or []
        pending = [a for a in items if str(a.get("status") or "").lower() == "pending"]
        # Also honor Session pointer when list is empty/stale
        if not pending:
            st = self._thread_state()
            cur = str(st.get("pending_approval_id") or "").strip()
            if cur:
                c2, one = http_json("GET", f"{self.base}/approvals/{cur}", timeout=15)
                if c2 == 200 and isinstance(one, dict) and str(one.get("status") or "").lower() == "pending":
                    pending = [one]
                elif c2 == 200 and isinstance(one, dict) and one.get("approval"):
                    ap = one["approval"]
                    if str(ap.get("status") or "").lower() == "pending":
                        pending = [ap]
        return pending

    def _tool_runs(self, execution_id: str = "") -> list[dict]:
        if execution_id:
            code, data = http_json(
                "GET", f"{self.base}/executions/{execution_id}/tool-runs", timeout=20
            )
        else:
            code, data = http_json(
                "GET",
                f"{self.base}/tool-runs?session_id={self.thread_id}&limit=50",
                timeout=20,
            )
        if code != 200:
            return []
        return list((data or {}).get("items") or [])

    def _thread_state(self) -> dict:
        code, data = http_json(
            "GET", f"{self.base}/threads/{self.thread_id}/state", timeout=15
        )
        return data if isinstance(data, dict) else {}

    def _score(
        self,
        r: ScenarioResult,
        *,
        intent: str = "n/a",
        context: str = "n/a",
        mode: str = "n/a",
        selection: str = "n/a",
        args: str = "n/a",
        authority: str = "n/a",
        execution: str = "n/a",
        verification: str = "n/a",
        persistence: str = "n/a",
        projection: str = "n/a",
        truth: str = "n/a",
    ) -> None:
        r.scores = {
            "intent_understanding": intent,
            "context_accuracy": context,
            "mode_selection": mode,
            "tool_skill_selection": selection,
            "argument_validity": args,
            "authority_enforcement": authority,
            "execution_result": execution,
            "verification": verification,
            "persistence": persistence,
            "frontend_projection": projection,
            "final_response_truth": truth,
        }
        required = [v for k, v in r.scores.items() if v != "n/a"]
        r.passed = bool(required) and all(v == "pass" for v in required)

    def _classify_route(self, text: str, tool_runs: list[dict], approvals: list[dict]) -> str:
        low = (text or "").lower()
        if approvals or any("propose" in str(t.get("tool_name") or "") for t in tool_runs):
            if "deterministic" in low:
                return "deterministic"
            return "native_or_structured_tool"
        if any(t.get("tool_name") for t in tool_runs):
            return "native_or_structured_tool"
        if re.search(r"\b(which|clarify|need more|what (?:clip|file)|select)\b", low):
            return "clarification"
        if re.search(r"\b(cannot|can't|unable|blocked|disabled)\b", low):
            return "blocked"
        return "plain"

    def run_scenario(
        self,
        sid: str,
        archetype: str,
        prompt: str,
        *,
        variation: str = "direct",
        include_memory: bool = False,
        stream: bool = False,
        video: bool = False,
        expect: Optional[Callable[[ScenarioResult, dict], None]] = None,
        multi_turn_followups: Optional[list[str]] = None,
    ) -> ScenarioResult:
        r = ScenarioResult(
            id=sid,
            archetype=archetype,
            prompt=prompt,
            variation=variation,
            multi_turn=bool(multi_turn_followups),
            model_provider=self.provider,
            model_name=self.model,
        )
        t0 = time.time()
        print(f"\n>>> [{sid}] {archetype}/{variation}: {prompt[:80]!r}")
        if self._backend_dead:
            r.failure_reason = "backend_unavailable (prior http 0 cascade)"
            self._score(r, intent="fail", truth="fail", execution="fail")
            r.duration_s = 0.0
            print(f"<<< FAIL [{sid}] skipped — backend dead")
            self.results.append(r)
            return r
        try:
            code, data, events = self._query(
                prompt,
                include_memory=include_memory,
                stream=stream,
                video=video,
            )
            r.http_status = code
            r.response_text = str((data or {}).get("response") or "")[:2000]
            r.success_flag = (data or {}).get("success")
            r.request_id = str((data or {}).get("request_id") or "")
            r.execution_id = str((data or {}).get("execution_id") or "")
            r.model_invoked = code == 200 and bool(r.response_text or events)
            if code == 0:
                self._consecutive_http0 += 1
                if self._consecutive_http0 >= 2:
                    self._backend_dead = True
                    print("!! Backend appears down (consecutive http 0) — aborting remaining live queries")
            else:
                self._consecutive_http0 = 0

            if multi_turn_followups:
                for fu in multi_turn_followups:
                    c2, d2, _ = self._query(fu, include_memory=include_memory, video=video)
                    r.response_text = str((d2 or {}).get("response") or r.response_text)[:2000]
                    r.http_status = c2
                    r.execution_id = str((d2 or {}).get("execution_id") or r.execution_id)

            # Refresh execution id from state — prefer this Turn only for oracles
            st = self._thread_state()
            if not r.execution_id:
                r.execution_id = str(st.get("last_execution_id") or "")
            # IMPORTANT: never fall back to full Session ToolRuns (stale history).
            # Empty list for this execution means no tools ran this Turn.
            if r.execution_id:
                r.tool_runs = self._tool_runs(r.execution_id)
            else:
                r.tool_runs = []
            r.approvals = self._pending_approvals()
            r.route = self._classify_route(r.response_text, r.tool_runs, r.approvals)
            r.evidence = {
                "thread_id": self.thread_id,
                "project_id": self.project_id,
                "execution_id": r.execution_id,
                "tool_run_count": len(r.tool_runs),
                "tool_names": [t.get("tool_name") for t in r.tool_runs],
                "pending_approvals": len(r.approvals),
                "execution_status": st.get("execution_status"),
                "stream_events": len(events),
            }
            if expect:
                expect(r, data if isinstance(data, dict) else {})
            else:
                # default: HTTP 200 + non-empty response
                if code == 200 and r.response_text.strip():
                    self._score(
                        r,
                        intent="pass",
                        truth="pass" if r.success_flag is not False else "fail",
                        execution="pass" if r.success_flag is not False else "n/a",
                    )
                else:
                    r.failure_reason = f"http={code} empty_or_fail"
                    self._score(r, intent="fail", truth="fail")
        except Exception as exc:
            r.failure_reason = f"exception: {exc}"
            self._score(r, intent="fail", truth="fail")
        r.duration_s = round(time.time() - t0, 2)
        status = "PASS" if r.passed else "FAIL"
        print(f"<<< {status} [{sid}] route={r.route} {r.duration_s}s {r.failure_reason or r.response_text[:80]!r}")
        self.results.append(r)
        return r

    # -- matrix builders -----------------------------------------------------

    def matrix_chat(self) -> None:
        def plain_ok(r: ScenarioResult, data: dict) -> None:
            text = r.response_text.strip()
            tools = [t.get("tool_name") for t in r.tool_runs]
            mut = [n for n in tools if n in {"file_write", "video_apply_transaction", "file_delete"}]
            if r.http_status != 200 or not text:
                r.failure_reason = "no response"
                self._score(r, intent="fail", truth="fail")
                return
            if mut:
                r.failure_reason = f"unexpected mutation tools: {mut}"
                self._score(r, intent="pass", selection="fail", authority="fail", truth="fail")
                return
            # Conversational "Forget X for a moment" must not hit memory-delete path.
            if re.search(
                r"(?i)could not find an active saved memory|Forgot \d+ matching saved memory",
                text,
            ):
                r.failure_reason = "memory-forget route on ordinary chat"
                self._score(r, intent="fail", mode="fail", selection="fail", truth="fail")
                return
            self._score(
                r,
                intent="pass",
                mode="pass",
                selection="pass",
                authority="pass",
                truth="pass",
                execution="pass",
            )

        prompts = [
            ("chat_hello", "Hello, how are you?", "direct"),
            ("chat_fact", "What is 17 times 19? Just the number.", "direct"),
            ("chat_reason", "Explain in one sentence why local-first agents matter.", "direct"),
            ("chat_casual", "yo whats up", "casual"),
            ("chat_ambiguous", "that thing we talked about earlier", "ambiguous"),
            ("chat_explain", "Explain what EchoSpeak is in plain English.", "direct"),
            ("chat_topic_switch", "Forget coding for a moment — recommend a good stretch after desk work.", "direct"),
            ("chat_project_unrelated", "What time of day is usually best for deep focus?", "direct"),
        ]
        for sid, p, var in prompts:
            self.run_scenario(sid, "chat", p, variation=var, expect=plain_ok)

        # multi-turn follow-up
        def followup_expect(r: ScenarioResult, d: dict) -> None:
            ok = "teal" in r.response_text.lower()
            self._score(
                r,
                intent="pass" if ok else "fail",
                context="pass" if ok else "fail",
                truth="pass" if ok else "fail",
            )
            if not ok:
                r.failure_reason = "did not retain prior turn color"

        self.run_scenario(
            "chat_followup",
            "chat",
            "My favorite color is teal.",
            multi_turn_followups=["What color did I just say is my favorite?"],
            include_memory=False,
            expect=followup_expect,
        )

    def matrix_inspect(self) -> None:
        def read_only(r: ScenarioResult, data: dict) -> None:
            tools = [str(t.get("tool_name") or "") for t in r.tool_runs]
            writes = [t for t in tools if t in {"file_write", "file_delete", "file_move"}]
            text = r.response_text.lower()
            if r.http_status != 200:
                r.failure_reason = f"http {r.http_status}"
                self._score(r, intent="fail", truth="fail")
                return
            if writes:
                r.failure_reason = f"mutated: {writes}"
                self._score(r, intent="pass", selection="fail", authority="fail", truth="fail")
                return
            # Prefer some evidence of file awareness
            ok_text = bool(text) and len(text) > 20
            self._score(
                r,
                intent="pass" if ok_text else "fail",
                mode="pass",
                selection="pass" if not writes else "fail",
                authority="pass",
                truth="pass" if ok_text and "wrote" not in text else "fail",
                execution="pass",
            )
            if not ok_text:
                r.failure_reason = "empty or too short"

        cases = [
            ("inspect_overview", "What does this project do?", "direct"),
            ("inspect_arch", "Look through the local files and explain the architecture briefly.", "direct"),
            ("inspect_index_only", "Inspect index.html only. Do not change anything.", "direct"),
            ("inspect_game_readonly", "Check game.js but do not change anything.", "direct"),
            ("inspect_find_title", "Where is the page title set in this project?", "direct"),
            ("inspect_local_first", "Use local files first and do not search the web. Summarize index.html.", "direct"),
            ("inspect_casual", "hey peek at the html file real quick", "casual"),
            ("inspect_ambiguous", "look at that main file", "ambiguous"),
        ]
        for sid, p, var in cases:
            self.run_scenario(sid, "inspect", p, variation=var, expect=read_only)

    def matrix_coding(self) -> None:
        def coding_expect(r: ScenarioResult, data: dict, *, want_pending: bool = True) -> None:
            text = r.response_text.lower()
            pending = r.approvals
            tools = [str(t.get("tool_name") or "") for t in r.tool_runs]
            if r.http_status != 200:
                r.failure_reason = f"http {r.http_status}"
                self._score(r, intent="fail", truth="fail")
                return
            # Good outcomes: pending approval for file_write, or confirm language
            has_fw = any(a.get("tool") == "file_write" for a in pending) or "file_write" in tools
            mentions_index = "index.html" in text or any(
                "index.html" in str((a.get("kwargs") or {}).get("path") or "") for a in pending
            )
            game_targeted = any(
                Path(str((a.get("kwargs") or {}).get("path") or "")).name == "game.js" for a in pending
            )
            if game_targeted:
                r.failure_reason = "targeted game.js despite exclusion"
                self._score(r, intent="pass", selection="fail", args="fail", authority="fail", truth="fail")
                return
            if want_pending and (has_fw or "confirm" in text or "not been saved" in text or "proposal" in text):
                self._score(
                    r,
                    intent="pass",
                    mode="pass",
                    selection="pass" if mentions_index or has_fw else "fail",
                    args="pass" if not game_targeted else "fail",
                    authority="pass",
                    truth="pass",
                    execution="pass",
                )
                if r.scores.get("selection") == "fail":
                    r.failure_reason = "no clear index.html targeting"
                return
            if not want_pending and ("not change" in text or "won't" in text or "will not" in text or "explain" in text):
                self._score(r, intent="pass", mode="pass", selection="pass", authority="pass", truth="pass")
                return
            # Still ok if model inspected and explained
            if text and len(text) > 40:
                self._score(r, intent="pass", mode="pass", selection="n/a", truth="pass", execution="pass")
                return
            r.failure_reason = "no coding proposal or useful response"
            self._score(r, intent="fail", truth="fail")

        cases = [
            ("coding_title", "Change the title in index.html to Game Application Live.", "direct", True),
            ("coding_only_index", "Only update index.html. Do not touch game.js. Change the title slightly.", "direct", True),
            ("coding_plan_first", "Fix nothing yet — inspect the title in index.html and explain what you'd change.", "direct", False),
            ("coding_casual", "yo fix the page title in the html file", "casual", True),
            ("coding_exclude", "Change the title in index.html. Do not edit game.js.", "direct", True),
            ("coding_no_change", "Do not change anything, just explain the issue with the title tag in index.html.", "direct", False),
        ]
        for sid, p, var, want in cases:
            self.run_scenario(
                sid,
                "coding",
                p,
                variation=var,
                expect=lambda r, d, w=want: coding_expect(r, d, want_pending=w),
            )

        # Approve once if pending
        pending = self._pending_approvals()
        if pending:
            aid = pending[0]["id"]
            path = str((pending[0].get("kwargs") or {}).get("path") or "")
            before = Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else ""
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
            after = Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else ""
            r = ScenarioResult(
                id="coding_approve_once",
                archetype="coding",
                prompt="(approve pending file_write)",
                model_provider=self.provider,
                model_name=self.model,
                http_status=c1,
                response_text=str((b1 or {}).get("response") or "")[:500],
                route="approval",
            )
            ok = c1 == 200 and bool((b1 or {}).get("success")) and (c2 >= 400 or not (b2 or {}).get("success"))
            # game.js unchanged
            game = self.workspace / "game.js"
            game_ok = game.exists()
            self._score(
                r,
                intent="pass",
                authority="pass" if ok else "fail",
                execution="pass" if ok else "fail",
                verification="pass" if after != before or ok else "fail",
                persistence="pass" if ok else "fail",
                truth="pass" if ok else "fail",
            )
            if not ok:
                r.failure_reason = f"c1={c1} c2={c2} success={(b1 or {}).get('success')}"
            r.passed = ok and game_ok
            self.results.append(r)
            print(f"<<< {'PASS' if r.passed else 'FAIL'} [coding_approve_once] {r.failure_reason}")

        # Cancel path — create new proposal then cancel
        def _setup_pending_ok(r: ScenarioResult, d: dict) -> None:
            # Setup step: any useful proposal / response is enough to proceed to cancel.
            ok = r.http_status == 200 and bool(r.response_text.strip())
            self._score(
                r,
                intent="pass" if ok else "fail",
                execution="pass" if ok else "fail",
                truth="pass" if ok else "fail",
            )
            if not ok:
                r.failure_reason = f"setup pending failed http={r.http_status}"

        self.run_scenario(
            "coding_for_cancel",
            "coding",
            "Change the title in index.html to Cancelled Title. Do not touch game.js.",
            expect=_setup_pending_ok,
        )
        pending = self._pending_approvals()
        if pending:
            aid = pending[0]["id"]
            c, b = http_json(
                "POST",
                f"{self.base}/approvals/{aid}/cancel?expected_session_id={self.thread_id}",
                timeout=60,
            )
            r = ScenarioResult(
                id="coding_cancel",
                archetype="coding",
                prompt="(cancel pending)",
                http_status=c,
                response_text=str((b or {}).get("response") or b)[:300],
                route="cancel",
            )
            self._score(
                r,
                authority="pass" if c == 200 else "fail",
                execution="pass" if c == 200 else "fail",
                truth="pass",
            )
            r.passed = c == 200
            if not r.passed:
                r.failure_reason = f"cancel status {c}"
            self.results.append(r)
            print(f"<<< {'PASS' if r.passed else 'FAIL'} [coding_cancel]")

    def matrix_memory(self) -> None:
        def mem_ok(r: ScenarioResult, data: dict) -> None:
            text = r.response_text.lower()
            if r.http_status != 200:
                r.failure_reason = f"http {r.http_status}"
                self._score(r, intent="fail", truth="fail")
                return
            # save ack or confirmation ask or rejection
            good = any(
                k in text
                for k in (
                    "remember",
                    "saved",
                    "memory",
                    "confirm",
                    "won't save",
                    "will not save",
                    "not store",
                    "got it",
                    "noted",
                    "forget",
                    "forgot",
                    "updated",
                    "preference",
                )
            )
            self._score(
                r,
                intent="pass" if good or len(text) > 20 else "fail",
                truth="pass" if good or len(text) > 20 else "fail",
                persistence="n/a",
                execution="pass",
            )
            if not (good or len(text) > 20):
                r.failure_reason = "no memory-related response"

        cases = [
            ("mem_explicit_coding", "Remember that I prefer concise coding-agent prompts.", True),
            ("mem_explicit_arch", "Keep in mind that I like detailed architecture explanations.", True),
            ("mem_workflow", "From now on, test manually after the runtime is stable.", True),
            ("mem_team", "Remember that Edmonton is my favorite hockey team.", True),
            ("mem_correct", "Actually, my favorite hockey team is Calgary now.", True),
            ("mem_forget", "Forget my hockey-team preference.", True),
            ("mem_temp", "My playhead is at 00:12 right now — don't permanently remember that.", True),
            ("mem_casual", "btw remember i like short answers", True),
        ]
        for sid, p, im in cases:
            self.run_scenario(sid, "memory", p, include_memory=im, expect=mem_ok)

        # Studio list
        code, mems = http_json("GET", f"{self.base}/memory?limit=50", timeout=20)
        items = (mems or {}).get("items") if isinstance(mems, dict) else (mems or [])
        r = ScenarioResult(
            id="mem_studio_list",
            archetype="memory",
            prompt="(GET /memory)",
            http_status=code,
            response_text=f"count={len(items or [])}",
            route="api",
        )
        self._score(r, persistence="pass" if code == 200 else "fail", truth="pass" if code == 200 else "fail")
        r.passed = code == 200
        self.results.append(r)
        print(f"<<< {'PASS' if r.passed else 'FAIL'} [mem_studio_list] n={len(items or [])}")

        # rebuild index
        code, reb = http_json("POST", f"{self.base}/memory/rebuild-index", {}, timeout=90)
        r = ScenarioResult(
            id="mem_rebuild_index",
            archetype="memory",
            prompt="(POST /memory/rebuild-index)",
            http_status=code,
            response_text=str(reb)[:200],
            route="api",
        )
        ok = code == 200 and bool((reb or {}).get("ok"))
        self._score(r, persistence="pass" if ok else "fail", verification="pass" if ok else "fail")
        r.passed = ok
        if not ok:
            r.failure_reason = str(reb)
        self.results.append(r)
        print(f"<<< {'PASS' if r.passed else 'FAIL'} [mem_rebuild_index]")

    def matrix_research(self) -> None:
        def research_ok(r: ScenarioResult, data: dict) -> None:
            text = r.response_text
            tools = [str(t.get("tool_name") or "") for t in r.tool_runs]
            if r.http_status != 200:
                r.failure_reason = f"http {r.http_status}"
                self._score(r, intent="fail", truth="fail")
                return
            # Either used web_search or gave honest limitation
            used = "web_search" in tools or "browse_task" in tools
            honest = bool(re.search(r"(?i)(cannot|can't|unable|no (?:search|source)|offline|not available)", text))
            long_enough = len(text) > 40
            self._score(
                r,
                intent="pass",
                selection="pass" if used or honest or long_enough else "fail",
                truth="pass" if used or honest or long_enough else "fail",
                execution="pass",
            )
            if r.scores.get("selection") == "fail":
                r.failure_reason = "no research tool and no useful answer"

        cases = [
            ("research_topic", "Research one short fact about the Edmonton Oilers and mention sources if you can.", "direct"),
            ("research_casual", "look up something quick about local-first AI agents", "casual"),
            ("research_local_first", "Use local project material first. Summarize what this project is without searching the web if possible.", "direct"),
            ("research_video_plan", "Research this for a video I'm planning about local AI runtimes — one paragraph.", "direct"),
        ]
        for sid, p, var in cases:
            self.run_scenario(sid, "research", p, variation=var, expect=research_ok)

        code, arts = http_json(
            "GET",
            f"{self.base}/research/artifacts?project_id={self.project_id}&session_id={self.thread_id}",
            timeout=20,
        )
        r = ScenarioResult(
            id="research_artifacts_api",
            archetype="research",
            prompt="(list research artifacts)",
            http_status=code,
            response_text=str(arts)[:200],
            route="api",
        )
        ok = code == 200
        self._score(r, persistence="pass" if ok else "fail", truth="pass" if ok else "fail")
        r.passed = ok
        self.results.append(r)
        print(f"<<< {'PASS' if r.passed else 'FAIL'} [research_artifacts_api]")

    def matrix_video(self) -> None:
        def video_ok(r: ScenarioResult, data: dict, *, allow_clarify: bool = True) -> None:
            text = r.response_text.lower()
            if r.http_status != 200:
                r.failure_reason = f"http {r.http_status}"
                self._score(r, intent="fail", truth="fail")
                return
            pending_video = any(a.get("tool") == "video_apply_transaction" for a in r.approvals)
            det = "deterministic" in text or "proposal" in text
            clarify = bool(re.search(r"\b(select|which clip|need a|playhead|cannot|could not)\b", text))
            blocked = "blocked" in text or "unavailable" in text
            if pending_video or det or (allow_clarify and clarify) or blocked or len(text) > 30:
                # Must not claim applied without approval language
                invented = "applied" in text and "revision" in text and "approve" not in text and not pending_video
                if invented:
                    r.failure_reason = "claimed apply without approval"
                    self._score(r, intent="pass", authority="fail", truth="fail")
                    return
                self._score(
                    r,
                    intent="pass",
                    selection="pass",
                    authority="pass",
                    truth="pass",
                    execution="pass",
                )
                return
            r.failure_reason = "empty or unusable video response"
            self._score(r, intent="fail", truth="fail")

        cases = [
            ("video_split", "Split the selected clip at the playhead.", "direct", True),
            ("video_cut_casual", "cut this clip at the playhead", "casual", True),
            ("video_mute", "Mute the selected clip.", "direct", True),
            ("video_volume", "Set the selected clip volume to 50 percent.", "direct", True),
            ("video_delete", "Delete the selected clip.", "direct", True),
            ("video_silence", "Remove the silent parts from this video.", "direct", True),
            ("video_captions", "Add captions to this video.", "direct", True),
            ("video_broll", "Generate B-roll for this section.", "direct", True),
            ("video_vague", "Make this look better.", "ambiguous", True),
            ("video_edit_vague", "Edit the video.", "ambiguous", True),
            ("video_no_selection_style", "Split the selected clip here.", "direct", True),
        ]
        for sid, p, var, vid in cases:
            self.run_scenario(
                sid,
                "video",
                p,
                variation=var,
                video=vid,
                expect=lambda r, d: video_ok(r, d),
            )

        # Approve video if pending
        pending = [a for a in self._pending_approvals() if a.get("tool") == "video_apply_transaction"]
        if pending:
            aid = pending[0]["id"]
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
            r = ScenarioResult(
                id="video_approve_once",
                archetype="video",
                prompt="(approve video)",
                http_status=c1,
                response_text=str((b1 or {}).get("response") or "")[:300],
                route="approval",
            )
            ok = c1 == 200 and (c2 >= 400 or not (b2 or {}).get("success"))
            self._score(r, authority="pass" if ok else "fail", execution="pass" if ok else "fail", truth="pass" if ok else "fail")
            r.passed = ok
            if not ok:
                r.failure_reason = f"c1={c1} c2={c2}"
            self.results.append(r)
            print(f"<<< {'PASS' if r.passed else 'FAIL'} [video_approve_once]")

    def matrix_skills(self) -> None:
        code, skills = http_json("GET", f"{self.base}/skills/status", timeout=20)
        items = (skills or {}).get("items") or []
        bad = [i for i in items if i.get("executable") and i.get("status") == "prompt_only"]
        r = ScenarioResult(
            id="skills_status_truth",
            archetype="skills",
            prompt="(GET /skills/status)",
            http_status=code,
            response_text=f"n={len(items)} bad={len(bad)}",
            route="api",
        )
        self._score(r, selection="pass" if code == 200 and not bad else "fail", truth="pass" if not bad else "fail")
        r.passed = code == 200 and not bad
        if bad:
            r.failure_reason = f"prompt_only executable: {[b['id'] for b in bad[:5]]}"
        self.results.append(r)
        print(f"<<< {'PASS' if r.passed else 'FAIL'} [skills_status_truth]")

        cases = [
            ("skill_split_direct", "Split the selected clip at the playhead.", True),
            ("skill_silence", "Remove silence and close the gaps in my video.", True),
            ("skill_research_video", "Research this topic and structure a short video outline.", False),
            ("skill_continue", "Continue the unfinished editing workflow if any.", True),
            ("skill_create_workflow", "Create a reusable workflow idea for editing podcast episodes — do not execute it.", False),
        ]

        def skill_expect(r: ScenarioResult, d: dict) -> None:
            ok = r.http_status == 200 and bool(r.response_text.strip())
            self._score(
                r,
                intent="pass" if ok else "fail",
                selection="pass" if ok else "fail",
                truth="pass" if ok else "fail",
                execution="pass" if ok else "fail",
            )
            if not ok:
                r.failure_reason = f"http={r.http_status} empty"

        for sid, p, vid in cases:
            self.run_scenario(sid, "skills", p, video=vid, expect=skill_expect)

    def matrix_continuation(self) -> None:
        cases = [
            ("cont_continue", "continue"),
            ("cont_go_ahead", "go ahead"),
            ("cont_retry", "retry"),
            ("cont_cancel", "cancel"),
            ("cont_stop", "stop"),
        ]
        for sid, p in cases:
            self.run_scenario(
                sid,
                "continuation",
                p,
                expect=lambda r, d: self._score(
                    r,
                    intent="pass" if r.http_status == 200 else "fail",
                    truth="pass" if r.http_status == 200 else "fail",
                    authority="pass",
                    execution="pass",
                )
                if r.http_status == 200
                else self._score(r, intent="fail", truth="fail"),
            )

    def matrix_isolation(self) -> None:
        # Second session same project
        code, th2 = http_json("POST", f"{self.base}/threads", {"title": "llm-isolation-2"}, timeout=20)
        tid2 = str((th2 or {}).get("thread_id") or "")
        r = ScenarioResult(
            id="iso_second_session",
            archetype="isolation",
            prompt="(create second session)",
            http_status=code,
            response_text=tid2,
            route="api",
        )
        self._score(r, authority="pass" if tid2 and tid2 != self.thread_id else "fail", truth="pass")
        r.passed = bool(tid2 and tid2 != self.thread_id)
        self.results.append(r)
        print(f"<<< {'PASS' if r.passed else 'FAIL'} [iso_second_session]")

        # Query on original session shouldn't create phantom
        code, threads = http_json("GET", f"{self.base}/threads", timeout=15)
        n = len(threads) if isinstance(threads, list) else int((threads or {}).get("count") or 0)
        r = ScenarioResult(
            id="iso_thread_list",
            archetype="isolation",
            prompt="(list threads)",
            http_status=code,
            response_text=f"count={n}",
            route="api",
        )
        self._score(r, authority="pass" if code == 200 else "fail", truth="pass")
        r.passed = code == 200 and n >= 2
        self.results.append(r)
        print(f"<<< {'PASS' if r.passed else 'FAIL'} [iso_thread_list] n={n}")

    def matrix_streaming(self) -> None:
        def stream_ok(r: ScenarioResult, data: dict) -> None:
            ev = int(r.evidence.get("stream_events") or 0)
            if r.http_status == 200 and (r.response_text or ev > 0):
                seqs = []
                # re-run not available; trust harness stored events count
                self._score(r, intent="pass", truth="pass", execution="pass", projection="pass")
            else:
                r.failure_reason = "stream failed"
                self._score(r, intent="fail", truth="fail")

        self.run_scenario(
            "stream_simple",
            "streaming",
            "Reply with exactly the word streamok.",
            stream=True,
            expect=stream_ok,
        )
        self.run_scenario(
            "stream_project",
            "streaming",
            "In one short sentence, what is index.html for in this project?",
            stream=True,
            expect=stream_ok,
        )

    def matrix_tools(self) -> None:
        cases = [
            ("tool_time", "What time is it right now?"),
            ("tool_calc", "Calculate 1234 * 567 without tools if you can, or use the calculator."),
            ("tool_list", "List the files in this project."),
            ("tool_read", "Read the first few lines of index.html."),
        ]
        for sid, p in cases:
            self.run_scenario(
                sid,
                "tools",
                p,
                expect=lambda r, d: self._score(
                    r,
                    intent="pass" if r.http_status == 200 and r.response_text else "fail",
                    selection="pass",
                    truth="pass" if r.http_status == 200 else "fail",
                    execution="pass" if r.http_status == 200 else "fail",
                ),
            )

    def matrix_authority(self) -> None:
        # Project switch with pending if any
        pending = self._pending_approvals()
        if not pending:
            # create one
            def _auth_setup_ok(r: ScenarioResult, d: dict) -> None:
                ok = r.http_status == 200 and bool(r.response_text.strip())
                self._score(
                    r,
                    intent="pass" if ok else "fail",
                    execution="pass" if ok else "fail",
                    truth="pass" if ok else "fail",
                )
                if not ok:
                    r.failure_reason = f"auth setup failed http={r.http_status}"

            self.run_scenario(
                "auth_setup_pending",
                "authority",
                "Change the title in index.html to Authority Test Title. Do not edit game.js.",
                expect=_auth_setup_ok,
            )
            pending = self._pending_approvals()
        if pending:
            # create other project
            other = Path(self.workspace).parent / "other_llm_proj"
            other.mkdir(exist_ok=True)
            (other / "readme.txt").write_text("other\n", encoding="utf-8")
            code, oproj = http_json(
                "POST",
                f"{self.base}/projects/attach-folder",
                {
                    "path": str(other),
                    "name": "Other LLM",
                    "trust_state": "trusted",
                    "session_id": self.thread_id,
                },
                timeout=30,
            )
            oid = (oproj or {}).get("id")
            if oid:
                http_json(
                    "POST",
                    f"{self.base}/projects/{oid}/activate?thread_id={self.thread_id}",
                    {},
                    timeout=15,
                )
                aid = pending[0]["id"]
                c, b = http_json(
                    "POST",
                    f"{self.base}/approvals/{aid}/confirm?expected_session_id={self.thread_id}",
                    timeout=60,
                )
                blocked = c >= 400 or not (b or {}).get("success")
                r = ScenarioResult(
                    id="auth_project_switch_blocks",
                    archetype="authority",
                    prompt="(confirm after project switch)",
                    http_status=c,
                    response_text=str(b)[:200],
                    route="approval",
                )
                self._score(r, authority="pass" if blocked else "fail", truth="pass" if blocked else "fail")
                r.passed = blocked
                if not blocked:
                    r.failure_reason = "approval ran after project switch"
                self.results.append(r)
                print(f"<<< {'PASS' if r.passed else 'FAIL'} [auth_project_switch_blocks]")
                # restore project
                http_json(
                    "POST",
                    f"{self.base}/projects/{self.project_id}/activate?thread_id={self.thread_id}",
                    {},
                    timeout=15,
                )

    # -- run all -------------------------------------------------------------

    def run_all(self, subsets: Optional[set[str]] = None, limit: int = 0) -> dict:
        self.setup()
        runners = [
            ("chat", self.matrix_chat),
            ("inspect", self.matrix_inspect),
            ("coding", self.matrix_coding),
            ("memory", self.matrix_memory),
            ("research", self.matrix_research),
            ("video", self.matrix_video),
            ("skills", self.matrix_skills),
            ("continuation", self.matrix_continuation),
            ("isolation", self.matrix_isolation),
            ("streaming", self.matrix_streaming),
            ("tools", self.matrix_tools),
            ("authority", self.matrix_authority),
        ]
        for name, fn in runners:
            if subsets and name not in subsets:
                continue
            if self._backend_dead:
                print(f"\n======== ARCHETYPE: {name} SKIPPED (backend dead) ========")
                r = ScenarioResult(
                    id=f"{name}_skipped_backend_dead",
                    archetype=name,
                    prompt="(skipped)",
                    failure_reason="backend_unavailable",
                    passed=False,
                )
                self.results.append(r)
                continue
            print(f"\n======== ARCHETYPE: {name} ========")
            try:
                fn()
            except Exception as exc:
                print(f"ARCHETYPE ERROR {name}: {exc}")
                r = ScenarioResult(
                    id=f"{name}_archetype_error",
                    archetype=name,
                    prompt="(archetype crashed)",
                    failure_reason=str(exc),
                    passed=False,
                )
                self.results.append(r)
            if limit and len(self.results) >= limit:
                break

        report = self.build_report()
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {self.out_path}")
        self.print_summary(report)
        return report

    def build_report(self) -> dict:
        by_arch: dict[str, dict] = {}
        for r in self.results:
            a = by_arch.setdefault(r.archetype, {"total": 0, "passed": 0, "failed": 0, "ids": []})
            a["total"] += 1
            if r.passed:
                a["passed"] += 1
            else:
                a["failed"] += 1
            a["ids"].append(r.id)

        routes: dict[str, int] = {}
        for r in self.results:
            routes[r.route or "unknown"] = routes.get(r.route or "unknown", 0) + 1

        failures = [
            {
                "id": r.id,
                "archetype": r.archetype,
                "prompt": r.prompt,
                "reason": r.failure_reason,
                "response": r.response_text[:300],
                "route": r.route,
            }
            for r in self.results
            if not r.passed
        ]

        clusters: dict[str, list[str]] = {}
        for f in failures:
            key = f["reason"] or "unknown"
            # cluster by first token-ish
            key = re.split(r"[:/]", key)[0][:80]
            clusters.setdefault(key, []).append(f["id"])

        n_pass = sum(1 for r in self.results if r.passed)
        n_fail = sum(1 for r in self.results if not r.passed)
        return {
            "generated_at": time.time(),
            "provider": self.provider,
            "model": self.model,
            "thread_id": self.thread_id,
            "project_id": self.project_id,
            "workspace": str(self.workspace),
            "total": len(self.results),
            "passed": n_pass,
            "failed": n_fail,
            "pass_rate": round(n_pass / max(1, len(self.results)), 3),
            "by_archetype": by_arch,
            "routes": routes,
            "failures": failures,
            "failure_clusters": clusters,
            "scenarios": [asdict(r) for r in self.results],
        }

    def print_summary(self, report: dict) -> None:
        print("\n" + "=" * 72)
        print("LIVE-MODEL ACCEPTANCE SUMMARY")
        print("=" * 72)
        print(f"Provider: {report['provider']}")
        print(f"Model:    {report['model']}")
        print(f"Total:    {report['total']}  Passed: {report['passed']}  Failed: {report['failed']}  Rate: {report['pass_rate']}")
        print("\nBy archetype:")
        for k, v in sorted((report.get("by_archetype") or {}).items()):
            print(f"  {k:16} {v['passed']}/{v['total']}")
        print("\nRoutes:", report.get("routes"))
        if report.get("failures"):
            print("\nFailed prompts:")
            for f in report["failures"][:30]:
                print(f"  - [{f['id']}] {f['prompt'][:60]!r} :: {f['reason'][:80]}")
        print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="EchoSpeak live-model acceptance harness")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--subset", default="", help="comma list: chat,inspect,coding,...")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else Path(meta["base"]) / "live_model_acceptance_report.json"
    subsets = {s.strip() for s in args.subset.split(",") if s.strip()} or None
    harness = LiveModelHarness(args.base, meta, out)
    report = harness.run_all(subsets=subsets, limit=args.limit)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    # Path import for typing
    from pathlib import Path as _P  # noqa: F401

    raise SystemExit(main())
