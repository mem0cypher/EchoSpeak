"""Focused tests: open-application allowlist normalization + Studio memory list."""
from __future__ import annotations

import asyncio

import pytest


def test_normalize_open_application_allowlist_spaces_hyphens_and_legacy_string():
    from config import _normalize_open_application_allowlist

    assert _normalize_open_application_allowlist("notepad, calc") == ["notepad", "calc"]
    assert _normalize_open_application_allowlist("Visual Studio Code\nGoogle-Chrome") == [
        "visual studio code",
        "google-chrome",
    ]
    assert _normalize_open_application_allowlist(["Notepad", "notepad", "paint"]) == ["notepad", "paint"]
    assert _normalize_open_application_allowlist(None) == []


def test_apply_overrides_migrates_string_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_DATA_DIR", str(tmp_path / "d"))
    from config import Config

    cfg = Config()
    cfg.apply_overrides({"open_application_allowlist": "notepad,visual studio code"})
    assert cfg.open_application_allowlist == ["notepad", "visual studio code"]
    cfg.apply_overrides({"open_application_allowlist": ["Paint", "calc"]})
    assert cfg.open_application_allowlist == ["paint", "calc"]


def test_memory_list_does_not_require_bound_project(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSPEAK_DATA_DIR", str(tmp_path / "mem"))
    from api import server as server_mod

    class _State:
        active_project_id = ""
        project_path = ""

    class _Store:
        def get_thread_state(self, _tid):
            return _State()

    class _Mem:
        use_faiss = False

        def list_items(self, **_kwargs):
            return []

        def count_items(self, **_kwargs):
            return 0

    class _Agent:
        memory = _Mem()

    monkeypatch.setattr(server_mod, "get_state_store", lambda: _Store())
    monkeypatch.setattr(server_mod, "get_agent", lambda _tid=None: _Agent())

    result = asyncio.run(server_mod.list_memory(offset=0, limit=20, thread_id="s1", project_id=""))
    assert result.items == []
    assert result.count == 0
    assert result.use_faiss is False


def test_memory_list_skips_corrupt_rows_without_erasing(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSPEAK_DATA_DIR", str(tmp_path / "mem2"))
    from api import server as server_mod

    class _State:
        active_project_id = ""
        project_path = ""

    class _Store:
        def get_thread_state(self, _tid):
            return _State()

    class _Mem:
        use_faiss = False

        def list_items(self, **_kwargs):
            return [
                {"id": "", "text": "bad"},  # missing id → skipped
                {
                    "id": "ok1",
                    "text": "hello",
                    "timestamp": "t",
                    "metadata": {"scope": "account", "type": "note"},
                },
            ]

        def count_items(self, **_kwargs):
            return 1

    class _Agent:
        memory = _Mem()

    monkeypatch.setattr(server_mod, "get_state_store", lambda: _Store())
    monkeypatch.setattr(server_mod, "get_agent", lambda _tid=None: _Agent())

    result = asyncio.run(server_mod.list_memory(offset=0, limit=20, thread_id="s1", project_id=""))
    assert len(result.items) == 1
    assert result.items[0].id == "ok1"
    assert result.items[0].text == "hello"


def test_memory_read_scope_mismatch_is_409_not_500(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSPEAK_DATA_DIR", str(tmp_path / "mem3"))
    from api import server as server_mod
    from fastapi import HTTPException

    class _State:
        active_project_id = "proj-a"
        project_path = "/tmp/a"

    class _Store:
        def get_thread_state(self, _tid):
            return _State()

    monkeypatch.setattr(server_mod, "get_state_store", lambda: _Store())
    with pytest.raises(HTTPException) as ei:
        server_mod._resolve_memory_read_scope("s1", "proj-b")
    assert ei.value.status_code == 409
