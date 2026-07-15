from pathlib import Path
import importlib.util

from agent import git_changelog, heartbeat, security
from api import server
from config import DATA_DIR
import twitter_bot


_ENTRY_PATH = Path(__file__).resolve().parents[2] / "desktop" / "backend" / "echospeak_backend.py"
_ENTRY_SPEC = importlib.util.spec_from_file_location("echospeak_desktop_entry", _ENTRY_PATH)
assert _ENTRY_SPEC and _ENTRY_SPEC.loader
desktop_entry = importlib.util.module_from_spec(_ENTRY_SPEC)
_ENTRY_SPEC.loader.exec_module(desktop_entry)


def test_desktop_websocket_subprotocol_extracts_ephemeral_key():
    headers = {"sec-websocket-protocol": "echospeak, echospeak-auth-launch-secret"}
    assert server._extract_api_auth_key_from_headers(headers) == "launch-secret"


def test_desktop_loopback_auth_is_required_when_bypass_is_disabled(monkeypatch):
    monkeypatch.setattr(server.config, "api_auth_enabled", True)
    monkeypatch.setattr(server.config, "api_auth_localhost_bypass", False)
    monkeypatch.setattr(server.config, "api_auth_key", "launch-secret")

    assert server._api_auth_required_for_host("127.0.0.1") is True
    assert server._api_auth_ok({"x-echospeak-key": "launch-secret"}, "127.0.0.1") is True
    assert server._api_auth_ok({"x-echospeak-key": "wrong"}, "127.0.0.1") is False


def test_desktop_mutable_state_uses_the_configured_data_root():
    root = Path(DATA_DIR).resolve()
    owned_paths = (
        server._TODO_FILE,
        server._AVATAR_CONFIG_FILE,
        heartbeat._DATA_DIR,
        git_changelog._CHANGELOG_STATE_PATH,
        security.AUDIT_LOG_PATH,
        twitter_bot._TWITTER_STATE_PATH,
        twitter_bot._AUTO_TWEET_STATE_PATH,
    )
    for path in owned_paths:
        assert Path(path).resolve().is_relative_to(root)


def test_desktop_seeds_mutable_soul_into_app_data(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUL_PATH", raising=False)
    desktop_entry._seed_mutable_defaults(tmp_path)

    expected = (tmp_path / "SOUL.md").resolve()
    assert expected.read_text(encoding="utf-8") == (
        Path(__file__).resolve().parents[1] / "SOUL.md"
    ).read_text(encoding="utf-8")
    assert Path(desktop_entry.os.environ["SOUL_PATH"]).resolve() == expected
