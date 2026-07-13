"""Self-edit/rollback must fail closed without inventing git history."""

from __future__ import annotations

import json
from pathlib import Path


def test_self_edit_disabled_returns_structured_permission(monkeypatch):
    from agent import tools as tools_mod
    from config import config

    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_self_modification", False)
    raw = tools_mod.self_edit.invoke(
        {
            "file_path": "apps/backend/SOUL.md",
            "old_content": "x",
            "new_content": "y",
        }
    )
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["error_code"] == "permission_denied"
    assert data.get("applied") is False


def test_self_rollback_disabled_structured(monkeypatch):
    from agent import tools as tools_mod
    from config import config

    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_self_modification", False)
    raw = tools_mod.self_rollback.invoke({"steps": 1})
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["error_code"] == "permission_denied"


def test_git_precondition_missing_repo(tmp_path, monkeypatch):
    from agent import tools as tools_mod
    from config import config

    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_self_modification", True)
    monkeypatch.setattr(tools_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    raw = tools_mod.self_edit.invoke(
        {
            "file_path": "x.txt",
            "old_content": "hello",
            "new_content": "world",
        }
    )
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["error_code"] in {"missing_precondition", "unavailable_adapter"}
    # File must not have been mutated
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hello"
