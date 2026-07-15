from __future__ import annotations

from pathlib import Path

from api.server import app


def test_retired_editor_routes_and_duplicate_search_modules_are_absent() -> None:
    paths = {route.path for route in app.routes}
    assert not any(path.startswith("/video") for path in paths)
    assert not any(path.startswith("/image-editor") for path in paths)

    backend = Path(__file__).resolve().parents[1]
    for relative in (
        "agent/deep_search_workflow.py",
        "agent/evidence_store.py",
        "agent/search_plan.py",
        "agent/search_provider.py",
        "api/video_editor.py",
    ):
        assert not (backend / relative).exists()


def test_web_navigation_has_no_retired_editor_surface() -> None:
    web = Path(__file__).resolve().parents[2] / "web" / "src"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (web / "index.tsx", web / "components" / "ProjectSidebar.tsx")
    )
    assert '"/app/video"' not in source
    assert '"/app/editor"' not in source
    assert "HybridEditor" not in source
    assert not list((web / "features" / "video-editor").glob("*.ts*"))
    assert not list((web / "features" / "image-editor").glob("*.ts*"))
