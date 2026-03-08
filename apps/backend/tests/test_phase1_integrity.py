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


def test_heartbeat_discord_route_uses_shared_queue(monkeypatch):
    from agent import heartbeat
    import discord_bot

    calls = []

    def fake_queue(user_id, message):
        calls.append((user_id, message))
        return True

    monkeypatch.setattr(config, "discord_bot_owner_id", "999", raising=False)
    monkeypatch.setattr(config, "discord_bot_allowed_users", ["123"], raising=False)
    monkeypatch.setattr(discord_bot, "queue_discord_dm", fake_queue, raising=True)

    heartbeat._route_discord("hello from routine", label="Routine")

    assert calls == [("999", "🫀 **EchoSpeak Routine**\nhello from routine")]


def test_heartbeat_discord_route_falls_back_to_first_allowed_user(monkeypatch):
    from agent import heartbeat
    import discord_bot

    calls = []

    def fake_queue(user_id, message):
        calls.append((user_id, message))
        return True

    monkeypatch.setattr(config, "discord_bot_owner_id", "", raising=False)
    monkeypatch.setattr(config, "discord_bot_allowed_users", ["123"], raising=False)
    monkeypatch.setattr(discord_bot, "queue_discord_dm", fake_queue, raising=True)

    heartbeat._route_discord("hello from routine", label="Routine")

    assert calls == [("123", "🫀 **EchoSpeak Routine**\nhello from routine")]


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
