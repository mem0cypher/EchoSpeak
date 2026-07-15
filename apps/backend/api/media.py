"""Read-only Media workspace projection over canonical immutable MediaAssets."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from agent.media_library import MediaLibraryError, get_media_library_store
from agent.projects import get_project_manager
from agent.state import get_state_store
from agent.threads import get_thread_manager


router = APIRouter(prefix="/media", tags=["media"])


def _scope(session_id: str, project_id: str = ""):
    session_key = str(session_id or "").strip()
    if not session_key or get_thread_manager().get_thread(session_key) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    state = get_state_store().get_thread_state(session_key)
    if project_id and state.active_project_id != project_id:
        raise HTTPException(status_code=409, detail="Media Project does not match the active Session Project")
    return state


@router.get("/assets")
async def list_media_assets(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
):
    _scope(session_id, project_id)
    items = get_media_library_store().list(project_id=project_id, session_id=session_id, limit=limit)
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.get("/assets/{asset_id}/content")
async def media_asset_content(asset_id: str, session_id: str = Query(...)):
    state = _scope(session_id)
    try:
        asset = get_media_library_store().get(asset_id)
    except MediaLibraryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="MediaAsset not found")
    if asset.session_id != session_id and asset.project_id != state.active_project_id:
        raise HTTPException(status_code=403, detail="MediaAsset belongs to another Session or Project")
    project = get_project_manager().get_project(asset.project_id)
    if project is None or project.archived:
        raise HTTPException(status_code=404, detail="MediaAsset Project is unavailable")
    try:
        root = Path(project.workspace_root).expanduser().resolve(strict=True)
        target = (root / asset.project_relative_path).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="MediaAsset source file is missing") from exc
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="MediaAsset path escapes its Project") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="MediaAsset source file is missing")
    digest_builder = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest_builder.update(chunk)
    digest = digest_builder.hexdigest()
    if digest != asset.sha256:
        raise HTTPException(status_code=409, detail="MediaAsset source hash changed; immutable source is stale")
    return FileResponse(target, filename=asset.name)
