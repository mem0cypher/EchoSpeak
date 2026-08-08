import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import server as server_mod
from api.server import app
from config import ModelProvider, config, get_llm_config


def _routes_for(path: str, method: str):
    matches = []
    for route in app.router.routes:
        route_path = getattr(route, "path", None)
        route_methods = getattr(route, "methods", None) or set()
        if route_path == path and method in route_methods:
            matches.append(route)
    return matches


def test_query_route_is_defined_once():
    assert len(_routes_for("/query", "POST")) == 1


def test_health_route_exists():
    assert len(_routes_for("/health", "GET")) == 1


def test_get_llm_config_honors_default_cloud_provider(monkeypatch):
    monkeypatch.setattr(config, "use_local_models", False, raising=False)
    monkeypatch.setattr(config, "default_cloud_provider", ModelProvider.GEMINI.value, raising=False)

    assert get_llm_config() is config.gemini


def test_provider_readiness_reports_lmstudio_unreachable(monkeypatch):
    monkeypatch.setattr(config.local, "base_url", "http://localhost:1234", raising=False)

    def fake_urlopen(req, timeout=0):
        raise server_mod.URLError("connection refused")

    monkeypatch.setattr(server_mod, "urlopen", fake_urlopen, raising=True)

    readiness = server_mod._check_provider_readiness(ModelProvider.LM_STUDIO)

    assert readiness["ok"] is False
    assert readiness["provider"] == "lmstudio"
    assert "LM Studio" in readiness["message"]
    assert "localhost:1234" in readiness["detail"]


def test_provider_readiness_accepts_openai_when_key_exists(monkeypatch):
    monkeypatch.setattr(config.openai, "api_key", "sk-test", raising=False)

    readiness = server_mod._check_provider_readiness(ModelProvider.OPENAI)

    assert readiness == {"ok": True, "provider": "openai", "message": "", "detail": ""}


def test_mcp_trust_summary_does_not_claim_missing_client_available():
    summary = server_mod._mcp_trust_summary(
        {"filesystem": {"transport": "stdio", "trust": "trusted"}},
        mcp_client_present=False,
        mcp_tool_count=2,
    )

    assert summary["mcp_configured_count"] == 1
    assert summary["mcp_client_present"] is False
    assert summary["mcp_available"] is False
    assert summary["mcp_available_tool_count"] == 0
    assert summary["mcp_status"] == "client_missing"
    assert summary["warnings"]


def test_api_auth_requires_key_when_enabled_for_nonlocal_host(monkeypatch):
    monkeypatch.setattr(config, "api_auth_enabled", True, raising=False)
    monkeypatch.setattr(config, "api_auth_key", "secret-key", raising=False)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", False, raising=False)

    assert server_mod._api_auth_ok({}, "192.168.1.20") is False
    assert server_mod._api_auth_ok({"x-echospeak-key": "secret-key"}, "192.168.1.20") is True


def test_api_auth_localhost_bypass(monkeypatch):
    monkeypatch.setattr(config, "api_auth_enabled", True, raising=False)
    monkeypatch.setattr(config, "api_auth_key", "secret-key", raising=False)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", True, raising=False)

    assert server_mod._api_auth_ok({}, "127.0.0.1") is True


def test_memory_doctor_flags_duplicates_and_conversation_dominance(monkeypatch):
    duplicate_text = "Ty prefers clean transparent reasoning traces."

    class FakeMemory:
        use_faiss = True
        memory_count = 4
        _profile = {"user_name": "Ty"}

        def list_items(self, offset=0, limit=300, thread_id=None, project_id="", include_global=False):
            return [
                {"id": "1", "text": duplicate_text, "timestamp": "1", "metadata": {"type": "conversation"}},
                {"id": "2", "text": duplicate_text, "timestamp": "2", "metadata": {"type": "conversation"}},
                {"id": "3", "text": "Project note", "timestamp": "3", "metadata": {"type": "project", "pinned": True}},
                {"id": "4", "text": "Untyped note", "timestamp": "4", "metadata": {}},
            ]

        def count_items(self, **_kwargs):
            return 4

    class FakeAgent:
        memory = FakeMemory()

    monkeypatch.setattr(config, "memory_auto_store_conversations", True, raising=False)

    report = server_mod._build_memory_doctor_report(FakeAgent(), thread_id=None, project_id="project-a", max_scan=10)

    assert report.ok is False
    assert report.type_counts["conversation"] == 2
    assert report.pinned_count == 1
    assert report.missing_type_count == 1
    assert report.duplicate_groups[0]["count"] == 2
    assert any("auto-store" in warning for warning in report.warnings)


def test_broadcast_discord_event_uses_gateway_loop(monkeypatch):
    captured = {}

    class StubLoop:
        def is_running(self):
            return True

    def fake_run_coroutine_threadsafe(coro, loop):
        captured["loop"] = loop
        try:
            coro.close()
        except Exception:
            pass

        class StubFuture:
            pass

        return StubFuture()

    loop = StubLoop()
    monkeypatch.setattr(server_mod, "_gateway_loop", loop, raising=False)
    monkeypatch.setattr(server_mod.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe, raising=True)

    server_mod.broadcast_discord_event({"type": "discord_activity", "tool": "discord_read_channel"})

    assert captured.get("loop") is loop


def test_sanitize_incoming_settings_ignores_redacted_secret_placeholders():
    out = server_mod._sanitize_incoming_settings(
        {
            "allow_discord_bot": True,
            "discord_bot_token": "***",
            "tavily_api_key": "***",
            "gemini": {"api_key": "***", "model": "gemini-3.1-flash-lite-preview"},
        }
    )

    assert out.get("allow_discord_bot") is True
    assert "discord_bot_token" not in out
    assert "tavily_api_key" not in out
    assert out.get("gemini") == {"model": "gemini-3.1-flash-lite-preview"}


def test_reconcile_discord_bot_runtime_starts_when_enabled(monkeypatch):
    import discord_bot

    calls = []

    class StartedBot:
        def __init__(self):
            self._task = "discord-task"

    async def fake_start_discord_bot(token, process_query_func, agent_name="EchoSpeak"):
        calls.append((token, agent_name, callable(process_query_func)))
        return StartedBot()

    async def fake_stop_discord_bot():
        raise AssertionError("stop_discord_bot should not be called")

    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        try:
            coro.close()
        except Exception:
            pass

        class StubTask:
            pass

        return StubTask()

    monkeypatch.setattr(config, "allow_discord_bot", True, raising=False)
    monkeypatch.setattr(config, "discord_bot_token", "x" * 60, raising=False)
    monkeypatch.setattr(server_mod, "_discord_bot_token_value", "", raising=False)
    monkeypatch.setattr(server_mod, "_discord_bot_task", None, raising=False)
    monkeypatch.setattr(discord_bot, "get_bot", lambda: None, raising=True)
    monkeypatch.setattr(discord_bot, "start_discord_bot", fake_start_discord_bot, raising=True)
    monkeypatch.setattr(discord_bot, "stop_discord_bot", fake_stop_discord_bot, raising=True)
    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task, raising=True)

    asyncio.run(server_mod._reconcile_discord_bot_runtime())

    assert calls == [("x" * 60, "EchoSpeak", True)]
    assert server_mod._discord_bot_task == "discord-task"
    assert server_mod._discord_bot_token_value == "x" * 60
    assert len(scheduled) == 1


def test_put_settings_persists_incomplete_draft_and_returns_issues(monkeypatch):
    class StubRequest:
        async def json(self):
            return {"allow_discord_bot": True}

    saved = {"payload": {}}

    def fake_write(payload):
        saved["payload"] = payload

    async def fake_reconcile():
        return None

    monkeypatch.setattr(server_mod, "_read_runtime_settings", lambda: dict(saved["payload"]), raising=True)
    monkeypatch.setattr(server_mod, "write_runtime_override_payload", fake_write, raising=True)
    monkeypatch.setattr(server_mod, "_reconcile_discord_bot_runtime", fake_reconcile, raising=True)
    monkeypatch.setattr(server_mod, "_validate_settings_effective", lambda effective: [{"key": "discord_bot_token", "message": "missing", "severity": "error"}], raising=True)
    monkeypatch.setattr(config, "reload", lambda: None, raising=False)
    monkeypatch.setattr(config, "to_public_dict", lambda: {"allow_discord_bot": True}, raising=False)

    resp = asyncio.run(server_mod.put_settings(StubRequest()))

    assert saved["payload"] == {"allow_discord_bot": True}
    assert resp.overrides == {"allow_discord_bot": True}
    assert resp.issues == [{"key": "discord_bot_token", "message": "missing", "severity": "error"}]


def test_heartbeat_discord_route_cannot_use_shared_queue(monkeypatch):
    from agent import heartbeat
    import discord_bot

    calls = []

    def fake_queue(user_id, message):
        calls.append((user_id, message))
        return True

    monkeypatch.setattr(config, "discord_bot_owner_id", "999", raising=False)
    monkeypatch.setattr(config, "discord_bot_allowed_users", ["123"], raising=False)
    monkeypatch.setattr(discord_bot, "queue_discord_dm", fake_queue, raising=True)

    heartbeat.route_message("hello from routine", ["discord"], label="Routine")

    assert calls == []


def test_heartbeat_discord_route_cannot_fall_back_to_allowed_user(monkeypatch):
    from agent import heartbeat
    import discord_bot

    calls = []

    def fake_queue(user_id, message):
        calls.append((user_id, message))
        return True

    monkeypatch.setattr(config, "discord_bot_owner_id", "", raising=False)
    monkeypatch.setattr(config, "discord_bot_allowed_users", ["123"], raising=False)
    monkeypatch.setattr(discord_bot, "queue_discord_dm", fake_queue, raising=True)

    heartbeat.route_message("hello from routine", ["discord"], label="Routine")

    assert calls == []


def test_notify_owner_security_event_queues_owner_dm(monkeypatch):
    import threading
    from agent import security
    import discord_bot

    calls = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    def fake_queue(user_id, message):
        calls.append((user_id, message))
        return True

    monkeypatch.setattr(config, "discord_bot_owner_id", "999", raising=False)
    monkeypatch.setattr(threading, "Thread", ImmediateThread, raising=True)
    monkeypatch.setattr(discord_bot, "queue_discord_dm", fake_queue, raising=True)

    security.notify_owner_security_event(
        {
            "event_type": "prompt_injection_detected",
            "severity": "high",
            "username": "tester",
            "user_id": "111",
            "role": "public",
            "details": {"blocked": True},
            "timestamp": "now",
        }
    )

    assert calls
    assert calls[0][0] == "999"
    assert "Security Alert" in calls[0][1]

def test_memory_compact_route_exists():
    assert len(_routes_for("/memory/compact", "POST")) == 1


def test_terminal_denylist_blocks_destructive_tokens(monkeypatch):
    from agent.tools import _terminal_command_denied

    monkeypatch.setattr(
        config,
        "terminal_command_denylist",
        ["rm", "del", "format", "shutdown", "reg", "powershell"],
        raising=False,
    )
    denied = _terminal_command_denied("rm -rf /")
    assert denied is not None
    assert "denylist" in denied.lower() or "blocked" in denied.lower()


def test_terminal_denylist_allows_harmless_echo(monkeypatch):
    from agent.tools import _terminal_command_denied

    monkeypatch.setattr(config, "terminal_command_denylist", ["rm", "del", "format"], raising=False)
    assert _terminal_command_denied("echo hello") is None


def test_memory_compact_accepts_query_params(monkeypatch):
    class FakeMemory:
        memory_count = 0

        def list_items(self, offset=0, limit=250, **_kwargs):
            return []

    class FakeAgent:
        memory = FakeMemory()

    monkeypatch.setattr(server_mod, "get_agent", lambda thread_id=None: FakeAgent(), raising=True)
    monkeypatch.setattr(server_mod, "_require_automation_project_scope", lambda session_id, project_id: project_id, raising=True)

    resp = asyncio.run(server_mod.compact_memory(request=None, thread_id="t1", project_id="project-a", similarity=0.94, max_scan=50))
    assert resp["success"] is True
    assert resp["deleted"] == 0
