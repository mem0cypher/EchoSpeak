"""Canonical immutable media-asset catalog.

The catalog owns source-asset identity and provenance across Chat, Media, and
generation jobs. It is the sole source-file authority for retained assets.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

class MediaLibraryError(RuntimeError):
    pass


class MediaLibraryAsset(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    project_id: str
    session_id: str = ""
    document_id: str = ""
    name: str
    media_kind: Literal["image", "video", "audio", "caption", "unknown"] = "unknown"
    source_kind: Literal["imported", "generated", "rendered", "proxy"] = "imported"
    project_relative_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    immutable: bool = True
    status: Literal["ready", "failed", "cancelled"] = "ready"
    prompt: str = ""
    provider: str = ""
    model: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)
    job_id: str = ""
    execution_id: str = ""
    tool_run_id: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


def resolve_media_library_root(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        from config import DATA_DIR

        data_dir = Path(DATA_DIR)
    return (Path(data_dir).expanduser().resolve() / "media_library").resolve()


class MediaLibraryStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root).expanduser().resolve() if root else resolve_media_library_root()
        self.assets_root = self.root / "assets"
        self.corrupt_root = self.root / "corrupt-state"
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self.corrupt_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, asset_id: str) -> Path:
        value = str(asset_id or "").strip()
        if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
            raise MediaLibraryError("Invalid MediaAsset id")
        return self.assets_root / f"{value}.json"

    def _quarantine(self, path: Path, error: Exception) -> None:
        stamp = f"{int(time.time())}-{time.time_ns()}"
        target = self.corrupt_root / f"{path.stem}-{stamp}.json"
        try:
            shutil.copy2(path, target)
            target.with_suffix(".diagnostic.txt").write_text(
                f"Malformed MediaAsset record: {error}\nSource: {path}\n"
                "Recovery: inspect the quarantined JSON, repair it, and restore it under assets/<id>.json.\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _read(self, path: Path) -> MediaLibraryAsset:
        try:
            return MediaLibraryAsset.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            self._quarantine(path, exc)
            raise MediaLibraryError(f"Malformed MediaAsset record quarantined: {path.name}") from exc

    def register(self, asset: MediaLibraryAsset) -> MediaLibraryAsset:
        if not asset.immutable:
            raise MediaLibraryError("Canonical source assets must be immutable")
        if asset.status == "ready" and (not asset.project_relative_path or not asset.sha256):
            raise MediaLibraryError("Ready MediaAssets require a verified path and sha256")
        path = self._path(asset.id)
        with self._lock:
            if path.exists():
                current = self._read(path)
                stable = (current.project_id, current.project_relative_path, current.sha256)
                incoming = (asset.project_id, asset.project_relative_path, asset.sha256)
                if stable != incoming:
                    raise MediaLibraryError("MediaAsset identity already belongs to another source")
                return current
            asset.updated_at = time.time()
            tmp = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            tmp.write_text(json.dumps(asset.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            return asset

    def get(self, asset_id: str) -> Optional[MediaLibraryAsset]:
        path = self._path(asset_id)
        if not path.exists():
            return None
        with self._lock:
            return self._read(path)

    def list(self, *, project_id: str = "", session_id: str = "", limit: int = 200) -> list[MediaLibraryAsset]:
        rows: list[MediaLibraryAsset] = []
        with self._lock:
            paths = sorted(self.assets_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            for path in paths:
                try:
                    asset = self._read(path)
                except MediaLibraryError:
                    continue
                if project_id and asset.project_id != project_id:
                    continue
                if session_id and not project_id and asset.session_id != session_id:
                    continue
                rows.append(asset)
                if len(rows) >= max(1, min(int(limit or 200), 500)):
                    break
        return rows


_STORE: Optional[MediaLibraryStore] = None


def get_media_library_store() -> MediaLibraryStore:
    global _STORE
    if _STORE is None:
        _STORE = MediaLibraryStore()
    return _STORE
