"""Identity-safe optional Obsidian projection for canonical EchoSpeak memory.

The memory store remains authoritative. Markdown files are a human-editable
projection with explicit conflict proposals; a watcher never overwrites either
side silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


SyncActionKind = Literal[
    "export_new", "export_update", "import_new", "import_update",
    "note_deleted", "memory_deleted", "conflict",
]


class ObsidianManifestEntry(BaseModel):
    memory_id: str
    note_path: str
    memory_version: int
    memory_checksum: str
    note_checksum: str
    last_synced_at: float = Field(default_factory=time.time)


class ObsidianManifest(BaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    session_id: str
    revision: int = 1
    entries: dict[str, ObsidianManifestEntry] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=time.time)


class ObsidianSyncAction(BaseModel):
    id: str = ""
    kind: SyncActionKind
    memory_id: str = ""
    note_path: str
    reason: str
    memory_text: str = ""
    note_text: str = ""
    memory_checksum: str = ""
    note_checksum: str = ""

    @model_validator(mode="after")
    def stable_identity(self) -> "ObsidianSyncAction":
        if not self.id:
            payload = "\0".join([
                self.kind,
                self.memory_id,
                self.note_path,
                self.memory_checksum,
                self.note_checksum,
            ])
            self.id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self


class ObsidianSyncPlan(BaseModel):
    project_id: str
    session_id: str
    vault_root: str
    actions: list[ObsidianSyncAction] = Field(default_factory=list)
    generated_at: float = Field(default_factory=time.time)


def _checksum(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    if not cleaned:
        raise ValueError("Obsidian scope segment is empty")
    return cleaned[:100]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


class ObsidianMemorySync:
    def __init__(self, vault_root: Path, *, folder_name: str = "EchoSpeak Memory") -> None:
        self.vault_root = Path(vault_root).expanduser().resolve(strict=False)
        self.folder_name = _safe_segment(folder_name)

    def _scope_root(self, project_id: str, session_id: str) -> Path:
        root = self.vault_root / self.folder_name / _safe_segment(project_id) / _safe_segment(session_id)
        resolved = root.resolve(strict=False)
        try:
            resolved.relative_to(self.vault_root)
        except ValueError as exc:
            raise ValueError("Obsidian scope escapes the configured vault") from exc
        return resolved

    def _manifest_path(self, project_id: str, session_id: str) -> Path:
        return self._scope_root(project_id, session_id) / ".echospeak-memory.json"

    def _load_manifest(self, project_id: str, session_id: str) -> ObsidianManifest:
        path = self._manifest_path(project_id, session_id)
        if not path.exists():
            return ObsidianManifest(project_id=project_id, session_id=session_id)
        try:
            manifest = ObsidianManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if manifest.project_id != project_id or manifest.session_id != session_id:
                raise ValueError("manifest scope does not match requested scope")
            return manifest
        except Exception as exc:
            quarantine = path.parent / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
            note = "quarantine unavailable"
            try:
                quarantine.mkdir(parents=True, exist_ok=False)
                copy = quarantine / path.name
                shutil.copy2(path, copy)
                guide = quarantine / "RECOVERY.txt"
                guide.write_text(
                    "EchoSpeak Obsidian sync recovery\n\n"
                    f"Authoritative manifest: {path}\nQuarantine copy: {copy}\nError: {exc}\n\n"
                    "Keep sync disabled, repair or restore the manifest, review note identities, then retry. "
                    "No notes or memories were changed.\n",
                    encoding="utf-8",
                )
                note = f"quarantine copy: {copy}; recovery guide: {guide}"
            except Exception as quarantine_exc:
                note = f"quarantine failed: {quarantine_exc}"
            raise RuntimeError(f"Obsidian sync manifest is unreadable; {note}. ({exc})") from exc

    @staticmethod
    def _render_note(record: dict[str, Any], project_id: str, session_id: str) -> str:
        metadata = dict(record.get("metadata") or {})
        memory_id = str(record.get("id") or "").strip()
        text = str(record.get("text") or "").strip()
        if not memory_id or not text:
            raise ValueError("Memory record requires id and text")
        lines = [
            "---",
            f"echospeak_id: {memory_id}",
            f"project_id: {project_id}",
            f"session_id: {session_id}",
            f"memory_type: {metadata.get('type') or record.get('memory_type') or 'note'}",
            f"scope: {metadata.get('scope') or record.get('scope') or 'project'}",
            f"version: {int(metadata.get('version') or record.get('version') or 1)}",
            f"memory_checksum: {metadata.get('checksum') or record.get('checksum') or _checksum(text)}",
            "---",
            "",
            text,
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_note(path: Path) -> tuple[dict[str, str], str]:
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            raise ValueError("managed note is missing EchoSpeak front matter")
        end = raw.find("\n---\n", 4)
        if end < 0:
            raise ValueError("managed note front matter is unterminated")
        fields: dict[str, str] = {}
        for line in raw[4:end].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
        body = raw[end + 5 :].strip()
        return fields, body

    def plan(
        self,
        records: list[dict[str, Any]],
        *,
        project_id: str,
        session_id: str,
    ) -> ObsidianSyncPlan:
        scope_root = self._scope_root(project_id, session_id)
        manifest = self._load_manifest(project_id, session_id)
        active = {str(row.get("id") or ""): row for row in records if str(row.get("id") or "")}
        actions: list[ObsidianSyncAction] = []

        for memory_id, record in active.items():
            entry = manifest.entries.get(memory_id)
            note_path = scope_root / (entry.note_path if entry else f"{_safe_segment(memory_id)}.md")
            expected = self._render_note(record, project_id, session_id)
            memory_text = str(record.get("text") or "").strip()
            metadata = dict(record.get("metadata") or {})
            memory_checksum = str(metadata.get("checksum") or _checksum(memory_text))
            if not note_path.exists():
                actions.append(ObsidianSyncAction(
                    kind="export_new" if entry is None else "note_deleted",
                    memory_id=memory_id,
                    note_path=str(note_path),
                    reason="No managed note exists" if entry is None else "Managed note was deleted",
                    memory_text=expected,
                    memory_checksum=memory_checksum,
                ))
                continue
            try:
                fields, note_text = self._parse_note(note_path)
            except Exception as exc:
                actions.append(ObsidianSyncAction(
                    kind="conflict", memory_id=memory_id, note_path=str(note_path),
                    reason=str(exc), memory_text=expected, memory_checksum=memory_checksum,
                ))
                continue
            if fields.get("echospeak_id") != memory_id or fields.get("project_id") != project_id or fields.get("session_id") != session_id:
                actions.append(ObsidianSyncAction(
                    kind="conflict", memory_id=memory_id, note_path=str(note_path),
                    reason="Managed note identity or scope does not match", memory_text=expected,
                    note_text=note_text, memory_checksum=memory_checksum, note_checksum=_checksum(note_text),
                ))
                continue
            note_checksum = _checksum(note_text)
            if entry is None:
                actions.append(ObsidianSyncAction(
                    kind="conflict", memory_id=memory_id, note_path=str(note_path),
                    reason="Note exists without a manifest identity", memory_text=expected,
                    note_text=note_text, memory_checksum=memory_checksum, note_checksum=note_checksum,
                ))
                continue
            memory_changed = entry.memory_checksum != memory_checksum
            note_changed = entry.note_checksum != note_checksum
            if memory_changed and note_changed:
                kind: SyncActionKind = "conflict"
                reason = "Memory and note both changed since the last sync"
            elif memory_changed:
                kind = "export_update"
                reason = "Canonical memory changed"
            elif note_changed:
                kind = "import_update"
                reason = "Obsidian note changed"
            else:
                continue
            actions.append(ObsidianSyncAction(
                kind=kind, memory_id=memory_id, note_path=str(note_path), reason=reason,
                memory_text=expected, note_text=note_text,
                memory_checksum=memory_checksum, note_checksum=note_checksum,
            ))

        for memory_id, entry in manifest.entries.items():
            if memory_id not in active:
                actions.append(ObsidianSyncAction(
                    kind="memory_deleted",
                    memory_id=memory_id,
                    note_path=str(scope_root / entry.note_path),
                    reason="Canonical memory is no longer active",
                ))

        if scope_root.exists():
            known_paths = {str((scope_root / entry.note_path).resolve(strict=False)) for entry in manifest.entries.values()}
            for path in sorted(scope_root.glob("*.md")):
                if str(path.resolve(strict=False)) in known_paths:
                    continue
                try:
                    fields, body = self._parse_note(path)
                    if fields.get("project_id") != project_id or fields.get("session_id") != session_id:
                        raise ValueError("note scope does not match this Project/Session")
                    if fields.get("echospeak_id") not in {"new", ""}:
                        raise ValueError("untracked note claims an existing EchoSpeak identity")
                    actions.append(ObsidianSyncAction(
                        kind="import_new", note_path=str(path), reason="New managed note",
                        note_text=body, note_checksum=_checksum(body),
                    ))
                except Exception as exc:
                    actions.append(ObsidianSyncAction(
                        kind="conflict", note_path=str(path), reason=str(exc),
                    ))

        return ObsidianSyncPlan(
            project_id=project_id,
            session_id=session_id,
            vault_root=str(self.vault_root),
            actions=actions,
        )

    def apply_exports(self, plan: ObsidianSyncPlan, action_ids: list[str]) -> ObsidianManifest:
        """Apply only explicitly selected canonical-to-note actions."""
        manifest = self._load_manifest(plan.project_id, plan.session_id)
        scope_root = self._scope_root(plan.project_id, plan.session_id)
        selected = set(action_ids)
        for action in plan.actions:
            if action.id not in selected:
                continue
            if action.kind not in {"export_new", "export_update"}:
                raise ValueError(f"Action {action.id} is not a safe export")
            path = Path(action.note_path).resolve(strict=False)
            path.relative_to(scope_root)
            _atomic_write(path, action.memory_text)
            _fields, body = self._parse_note(path)
            manifest.entries[action.memory_id] = ObsidianManifestEntry(
                memory_id=action.memory_id,
                note_path=str(path.relative_to(scope_root)),
                memory_version=int(_fields.get("version") or 1),
                memory_checksum=action.memory_checksum,
                note_checksum=_checksum(body),
            )
        manifest.revision += 1
        manifest.updated_at = time.time()
        _atomic_write(
            self._manifest_path(plan.project_id, plan.session_id),
            manifest.model_dump_json(indent=2) + "\n",
        )
        return manifest

    def apply_imports(
        self,
        plan: ObsidianSyncPlan,
        action_ids: list[str],
        *,
        memory: Any,
        project_path: str,
    ) -> ObsidianManifest:
        """Apply explicitly selected note edits through the canonical store."""
        manifest = self._load_manifest(plan.project_id, plan.session_id)
        scope_root = self._scope_root(plan.project_id, plan.session_id)
        selected = set(action_ids)
        for action in plan.actions:
            if action.id not in selected:
                continue
            if action.kind not in {"import_new", "import_update"}:
                raise ValueError(f"Action {action.id} is not an import proposal")
            path = Path(action.note_path).resolve(strict=False)
            path.relative_to(scope_root)
            fields, body = self._parse_note(path)
            if not body:
                raise ValueError("Empty notes cannot become durable memory")
            if action.kind == "import_update":
                memory_id = action.memory_id
                if not memory.update_item(
                    memory_id,
                    text=body,
                    project_id=plan.project_id,
                    thread_id=plan.session_id,
                    include_global=False,
                ):
                    raise RuntimeError("Canonical memory changed or left scope before import")
            else:
                memory_id = memory.add_memory_item(
                    body,
                    memory_type=str(fields.get("memory_type") or "note"),
                    thread_id=plan.session_id,
                    source="obsidian",
                    project_path=project_path,
                    project_id=plan.project_id,
                    scope="project",
                    source_item_id=str(path.relative_to(scope_root)),
                    semantic_key=f"obsidian:{path.relative_to(scope_root).as_posix().casefold()}",
                )
                if not memory_id:
                    raise RuntimeError("Obsidian note was rejected by canonical memory policy")
            rows = memory.list_items(
                offset=0,
                limit=1000,
                thread_id=plan.session_id,
                project_id=plan.project_id,
                project_path=project_path,
                include_global=False,
            )
            record = next((row for row in rows if str(row.get("id") or "") == memory_id), None)
            if record is None:
                raise RuntimeError("Imported memory could not be re-read from canonical state")
            rendered = self._render_note(record, plan.project_id, plan.session_id)
            _atomic_write(path, rendered)
            note_fields, note_body = self._parse_note(path)
            metadata = dict(record.get("metadata") or {})
            memory_checksum = str(metadata.get("checksum") or _checksum(str(record.get("text") or "")))
            manifest.entries[memory_id] = ObsidianManifestEntry(
                memory_id=memory_id,
                note_path=str(path.relative_to(scope_root)),
                memory_version=int(note_fields.get("version") or metadata.get("version") or 1),
                memory_checksum=memory_checksum,
                note_checksum=_checksum(note_body),
            )
        manifest.revision += 1
        manifest.updated_at = time.time()
        _atomic_write(
            self._manifest_path(plan.project_id, plan.session_id),
            manifest.model_dump_json(indent=2) + "\n",
        )
        return manifest
