"""Durable, fail-closed video document/revision/job owner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agent.projects import ProjectManager, get_project_manager
from agent.video_editor.models import (
    EditOperation,
    GeneratedCandidate,
    MediaAsset,
    VideoCreativeMemory,
    VideoEditPlan,
    VideoEditTransaction,
    VideoJob,
    VideoProjectDocument,
    VideoRevision,
)
from agent.video_editor.operations import operation_preview, stage_transaction


class VideoStoreError(RuntimeError):
    pass


class VideoStoreCorruption(VideoStoreError):
    pass


class VideoStorePersistenceError(VideoStoreError):
    """Structured failure when the configured data root is invalid or unwritable."""

    def __init__(self, message: str, *, code: str = "persistence_failed", root: str = ""):
        super().__init__(message)
        self.code = code
        self.root = root


def resolve_video_data_dir(*, data_dir: Optional[Path] = None) -> Path:
    """Resolve the canonical Video Editor data root.

    Always under the configured EchoSpeak data root (``ECHOSPEAK_DATA_DIR`` /
    ``config.DATA_DIR``), never a module-relative repository path.
    """
    if data_dir is not None:
        base = Path(data_dir).expanduser().resolve()
    else:
        try:
            from config import DATA_DIR

            base = Path(DATA_DIR).expanduser().resolve()
        except Exception as exc:  # pragma: no cover - config must load
            raise VideoStorePersistenceError(
                f"persistence_failed: cannot resolve configured data root ({exc})",
                code="persistence_failed",
            ) from exc
    return (base / "video_editor").resolve()


def legacy_repo_video_data_dir() -> Path:
    """Historical hard-coded path under apps/backend/data/video_editor (read-only detection)."""
    return (Path(__file__).resolve().parents[2] / "data" / "video_editor").resolve()


def _assert_data_root_usable(root: Path) -> None:
    """Fail closed if the video data root cannot be created or written."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".write_probe.{os.getpid()}.{time.time_ns()}"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        raise VideoStorePersistenceError(
            f"persistence_failed: video data root is not usable: {root} ({exc})",
            code="persistence_failed",
            root=str(root),
        ) from exc


class VideoEditorStore:
    def __init__(self, root: Optional[Path] = None, project_manager: Optional[ProjectManager] = None):
        resolved = Path(root).expanduser().resolve() if root is not None else resolve_video_data_dir()
        _assert_data_root_usable(resolved)
        self.root = resolved
        self.projects_root = self.root / "projects"
        self.corrupt_root = self.root / "corrupt-state"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.corrupt_root.mkdir(parents=True, exist_ok=True)
        self._project_manager = project_manager
        self._lock = threading.RLock()

    @property
    def project_manager(self) -> ProjectManager:
        return self._project_manager or get_project_manager()

    def _project(self, project_id: str):
        project = self.project_manager.get_project(str(project_id or "").strip())
        if project is None or project.archived:
            raise VideoStoreError("Project does not exist or is archived")
        root = Path(str(project.workspace_root or "")).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise VideoStoreError("Project root does not exist or is not a directory")
        return project, root

    def _project_dir(self, project_id: str) -> Path:
        return self.projects_root / self._safe_id(project_id, "Project")

    @staticmethod
    def _safe_id(value: str, label: str) -> str:
        key = str(value or "").strip()
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        if not key or any(ch not in allowed for ch in key):
            raise VideoStoreError(f"invalid {label} id")
        return key

    def _document_path(self, project_id: str, document_id: str) -> Path:
        key = self._safe_id(document_id, "video document")
        folder = (self._project_dir(project_id) / "documents").resolve()
        path = (folder / f"{key}.json").resolve()
        try:
            path.relative_to(folder)
        except ValueError as exc:
            raise VideoStoreError("video document path escaped its authority directory") from exc
        return path

    def _revision_path(self, project_id: str, document_id: str, revision_id: str) -> Path:
        document_key = self._safe_id(document_id, "video document")
        revision_key = self._safe_id(revision_id, "video revision")
        folder = (self._project_dir(project_id) / "revisions" / document_key).resolve()
        path = (folder / f"{revision_key}.json").resolve()
        try:
            path.relative_to(folder)
        except ValueError as exc:
            raise VideoStoreError("video revision path escaped its authority directory") from exc
        return path

    @staticmethod
    def _canonical(payload: Any) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")

    @classmethod
    def operation_hash(cls, operations: list[EditOperation]) -> str:
        return hashlib.sha256(cls._canonical([op.model_dump(mode="json") for op in operations])).hexdigest()

    def _write_json(self, path: Path, payload: Any) -> None:
        """Atomic write with Windows-safe retries (AV/indexers can hold targets briefly)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
        last_exc: Exception | None = None
        for attempt in range(6):
            temp = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{attempt}.tmp"
            )
            try:
                with temp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, path)
                return
            except PermissionError as exc:
                last_exc = exc
                try:
                    temp.unlink(missing_ok=True)
                except Exception:
                    pass
                time.sleep(0.02 * (attempt + 1))
            except Exception as exc:
                last_exc = exc
                try:
                    temp.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except Exception:
                    pass
        if last_exc is not None:
            raise last_exc

    def _quarantine(self, path: Path, error: Exception) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unique = f"{time.time_ns()}-{threading.get_ident()}"
        target = self.corrupt_root / (
            f"{stamp}-{path.stem}-{hashlib.sha256((str(path) + unique).encode()).hexdigest()[:12]}"
        )
        target.mkdir(parents=True, exist_ok=False)
        if path.exists():
            shutil.copy2(path, target / path.name)
        recovery = (
            "EchoSpeak video state failed closed. The authoritative file was not overwritten.\n"
            f"Source: {path}\nError: {error}\n"
            "Manual recovery: inspect the copied JSON, repair or restore it, then replace the original while EchoSpeak is stopped.\n"
        )
        self._write_json(target / "diagnostic.json", {"source": str(path), "error": str(error), "created_at": time.time()})
        (target / "RECOVERY.txt").write_text(recovery, encoding="utf-8", newline="\n")
        return target

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("authoritative JSON root must be an object")
            return payload
        except Exception as exc:
            quarantine = self._quarantine(path, exc)
            raise VideoStoreCorruption(
                f"Malformed authoritative video state at {path}. A recovery copy and diagnostic are at {quarantine}."
            ) from exc

    def _read_document(
        self,
        path: Path,
        *,
        project_id: str = "",
        document_id: str = "",
    ) -> VideoProjectDocument:
        payload = self._read_json(path)
        try:
            document = VideoProjectDocument.model_validate(payload)
            if project_id and document.project_id != project_id:
                raise ValueError("video document Project identity does not match its authority path")
            if document_id and document.id != document_id:
                raise ValueError("video document identity does not match its authority path")
            return document
        except Exception as exc:
            quarantine = self._quarantine(path, exc)
            raise VideoStoreCorruption(
                f"Invalid authoritative video schema at {path}. A recovery copy and diagnostic are at {quarantine}."
            ) from exc

    def _save_document(self, document: VideoProjectDocument) -> None:
        self._write_json(self._document_path(document.project_id, document.id), document.model_dump(mode="json"))

    def _save_snapshot(self, document: VideoProjectDocument, revision: VideoRevision) -> None:
        payload = document.model_dump(mode="json")
        digest = hashlib.sha256(self._canonical(payload)).hexdigest()
        revision.snapshot_sha256 = digest
        self._write_json(
            self._revision_path(document.project_id, document.id, revision.id),
            {"revision": revision.model_dump(mode="json"), "document": payload},
        )

    def _load_snapshot(self, project_id: str, document_id: str, revision_id: str) -> VideoProjectDocument:
        path = self._revision_path(project_id, document_id, revision_id)
        payload = self._read_json(path)
        try:
            revision_payload = payload.get("revision")
            document_payload = payload.get("document")
            if not isinstance(revision_payload, dict) or not isinstance(document_payload, dict):
                raise ValueError("revision snapshot requires revision and document objects")
            revision = VideoRevision.model_validate(revision_payload)
            document = VideoProjectDocument.model_validate(document_payload)
            digest = hashlib.sha256(self._canonical(document_payload)).hexdigest()
            if revision.id != revision_id or revision.project_id != project_id or revision.document_id != document_id:
                raise ValueError("revision wrapper identity does not match its authority path")
            if document.project_id != project_id or document.id != document_id:
                raise ValueError("snapshot document identity does not match its authority path")
            if not revision.snapshot_sha256 or digest != revision.snapshot_sha256:
                raise ValueError("revision snapshot digest does not match authoritative content")
            return document
        except Exception as exc:
            quarantine = self._quarantine(path, exc)
            raise VideoStoreCorruption(
                f"Invalid authoritative revision snapshot at {path}. A recovery copy and diagnostic are at {quarantine}."
            ) from exc

    def create_document(self, project_id: str, name: str) -> VideoProjectDocument:
        self._project(project_id)
        with self._lock:
            document = VideoProjectDocument(project_id=project_id, name=str(name or "Untitled Video").strip() or "Untitled Video")
            revision = VideoRevision(
                project_id=project_id,
                document_id=document.id,
                revision_number=0,
                source="create",
            )
            document.head_revision_id = revision.id
            document.revisions = [revision]
            self._save_snapshot(document, revision)
            document.revisions[0] = revision
            self._save_document(document)
            return document.model_copy(deep=True)

    def list_documents(self, project_id: str, *, include_archived: bool = False) -> list[VideoProjectDocument]:
        self._project(project_id)
        folder = self._project_dir(project_id) / "documents"
        if not folder.exists():
            return []
        documents: list[VideoProjectDocument] = []
        with self._lock:
            for path in sorted(folder.glob("*.json")):
                document = self._read_document(path, project_id=project_id, document_id=path.stem)
                if include_archived or not document.archived:
                    documents.append(document)
        return documents

    def get_document(self, project_id: str, document_id: str) -> VideoProjectDocument:
        self._project(project_id)
        path = self._document_path(project_id, document_id)
        if not path.exists():
            raise VideoStoreError("Video document not found")
        with self._lock:
            document = self._read_document(path, project_id=project_id, document_id=document_id)
        return document

    def archive_document(self, project_id: str, document_id: str) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            document.archived = True
            document.updated_at = time.time()
            self._save_document(document)
            return document

    def add_asset(self, project_id: str, document_id: str, asset: MediaAsset) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            if asset.project_id != project_id or asset.document_id != document_id:
                raise VideoStoreError("Asset ownership does not match the video document")
            if any(item.id == asset.id or item.sha256 == asset.sha256 for item in document.assets):
                return document
            document.assets.append(asset)
            document.updated_at = time.time()
            self._save_document(document)
            return document

    def prepare_transaction(
        self,
        project_id: str,
        document_id: str,
        session_id: str,
        operations: list[EditOperation],
        *,
        source: str,
    ) -> tuple[VideoEditTransaction, list[dict[str, Any]]]:
        with self._lock:
            document = self.get_document(project_id, document_id)
            if not operations:
                raise VideoStoreError("A transaction requires at least one operation")
            if any(op.expected_revision != document.revision for op in operations):
                raise VideoStoreError(f"Stale document revision; current revision is {document.revision}")
            try:
                stage_transaction(document, operations)
            except Exception as stage_exc:
                msg = str(stage_exc)
                lower = msg.lower()
                if "clip not found" in lower or "clip_id" in lower:
                    raise VideoStoreError(
                        f"verification_failed: target clip missing before apply ({msg})"
                    ) from stage_exc
                if "track" in lower and ("not found" in lower or "missing" in lower):
                    raise VideoStoreError(f"missing_track: {msg}") from stage_exc
                raise VideoStoreError(f"validation_failed: {msg}") from stage_exc
            transaction = VideoEditTransaction(
                project_id=project_id,
                document_id=document_id,
                session_id=session_id,
                expected_revision=document.revision,
                operation_hash=self.operation_hash(operations),
                operations=operations,
                source=source,
                status="pending_approval" if source == "agent" else "prepared",
            )
            document.transactions.append(transaction)
            document.updated_at = time.time()
            self._save_document(document)
            return transaction, [operation_preview(operation) for operation in operations]

    def apply_transaction(
        self,
        transaction: VideoEditTransaction,
        *,
        allow_idempotent: bool = False,
    ) -> VideoProjectDocument:
        """Apply a prepared transaction and advance revision exactly once.

        If the transaction was already applied, raises unless ``allow_idempotent``
        is True (callers must treat that as ``no_change``, not a new mutation).
        """
        with self._lock:
            document = self.get_document(transaction.project_id, transaction.document_id)
            existing = next((item for item in document.transactions if item.id == transaction.id), None)
            if existing is not None and existing.status == "applied":
                if allow_idempotent:
                    # Explicit no-change path — revision must NOT appear advanced.
                    return document
                raise VideoStoreError(
                    "Transaction already applied; refusing to report a new mutation success "
                    f"(revision remains {document.revision})"
                )
            if document.revision != transaction.expected_revision:
                raise VideoStoreError(
                    f"Document changed after the transaction was prepared (expected {transaction.expected_revision}, current {document.revision})"
                )
            if transaction.operation_hash != self.operation_hash(transaction.operations):
                raise VideoStoreError("Transaction operation identity changed")
            # Pre-validate clip targets that must already exist on the *current*
            # document. Do NOT pre-check insert_clip tracks here: the same batch
            # may include add_track that creates the track (stage_transaction
            # applies ops in order). Track/clip presence for inserts is verified
            # on the staged document after apply.
            from agent.video_editor.clips import clip_exists

            _CLIP_MUTATIONS = frozenset({
                "split_clip",
                "trim_clip",
                "move_clip",
                "delete_clip",
                "set_clip_volume",
                "set_clip_opacity",
                "set_clip_transform",
                "set_clip_speed",
                "set_clip_enabled",
            })
            for op in transaction.operations:
                op_type = str(getattr(op.operation_type, "value", op.operation_type) or "")
                payload = dict(op.payload or {})
                if op_type in _CLIP_MUTATIONS:
                    cid = str(payload.get("clip_id") or "").strip()
                    if not cid or not clip_exists(document, cid):
                        raise VideoStoreError(
                            f"verification_failed: target clip missing before apply ({cid or 'empty'})"
                        )
            before_rev = int(document.revision)
            try:
                staged = stage_transaction(document, transaction.operations)
            except Exception as stage_exc:
                msg = str(stage_exc)
                if "track" in msg.lower() and "not found" in msg.lower():
                    raise VideoStoreError(f"missing_track: {msg}") from stage_exc
                raise VideoStoreError(f"persistence_failed: {msg}") from stage_exc
            # Post-stage verification: inserts must land a durable clip on tracks.
            for op in transaction.operations:
                op_type = str(getattr(op.operation_type, "value", op.operation_type) or "")
                payload = dict(op.payload or {})
                if op_type == "insert_clip":
                    track_id = str(payload.get("track_id") or "").strip()
                    if not any(t.id == track_id for t in staged.timeline.tracks):
                        raise VideoStoreError(f"missing_track: {track_id}")
                    cid = str(payload.get("clip_id") or "").strip()
                    if not cid:
                        raise VideoStoreError(
                            "verification_failed: insert_clip requires a stable clip_id"
                        )
                    if not clip_exists(staged, cid):
                        raise VideoStoreError(
                            f"verification_failed: insert did not leave clip {cid} on the timeline"
                        )
            parent_revision_id = document.head_revision_id
            new_revision_number = document.revision + 1
            revision = VideoRevision(
                project_id=document.project_id,
                document_id=document.id,
                revision_number=new_revision_number,
                parent_revision_id=parent_revision_id,
                transaction_id=transaction.id,
                operation_ids=[item.id for item in transaction.operations],
                source=transaction.source,
            )
            staged.revision = new_revision_number
            staged.head_revision_id = revision.id
            staged.revisions = [*document.revisions, revision]
            staged.undo_revision_ids = [*document.undo_revision_ids, parent_revision_id] if parent_revision_id else list(document.undo_revision_ids)
            staged.redo_revision_ids = []
            applied = transaction.model_copy(update={"status": "applied", "resulting_revision_id": revision.id, "applied_at": time.time()})
            staged.transactions = [applied if item.id == transaction.id else item for item in document.transactions]
            if not any(item.id == applied.id for item in staged.transactions):
                staged.transactions.append(applied)
            staged.updated_at = time.time()
            # Immutable snapshot first; authoritative head/document is promoted last.
            self._save_snapshot(staged, revision)
            staged.revisions[-1] = revision
            self._save_document(staged)
            result = staged.model_copy(deep=True)
            if int(result.revision) != before_rev + 1:
                raise VideoStoreError(
                    f"verification_failed: expected revision {before_rev + 1}, got {result.revision}"
                )
            return result

    def get_transaction(self, project_id: str, document_id: str, transaction_id: str) -> VideoEditTransaction:
        document = self.get_document(project_id, document_id)
        transaction = next((item for item in document.transactions if item.id == transaction_id), None)
        if transaction is None:
            raise VideoStoreError("Video transaction not found")
        return transaction

    def update_transaction(self, project_id: str, document_id: str, transaction: VideoEditTransaction) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            document.transactions = [transaction if item.id == transaction.id else item for item in document.transactions]
            if not any(item.id == transaction.id for item in document.transactions):
                document.transactions.append(transaction)
            document.updated_at = time.time()
            self._save_document(document)
            return document

    def add_plan(self, project_id: str, document_id: str, plan: VideoEditPlan) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            document.plans.append(plan)
            document.updated_at = time.time()
            self._save_document(document)
            return document

    def update_plan(self, project_id: str, document_id: str, plan: VideoEditPlan) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            document.plans = [plan if item.id == plan.id else item for item in document.plans]
            if not any(item.id == plan.id for item in document.plans):
                raise VideoStoreError("Video edit plan not found")
            document.updated_at = time.time()
            self._save_document(document)
            return document

    def _restore_revision(self, project_id: str, document_id: str, *, redo: bool) -> VideoProjectDocument:
        with self._lock:
            current = self.get_document(project_id, document_id)
            source_stack = list(current.redo_revision_ids if redo else current.undo_revision_ids)
            if not source_stack:
                raise VideoStoreError("Nothing to redo" if redo else "Nothing to undo")
            target_revision_id = source_stack.pop()
            target = self._load_snapshot(project_id, document_id, target_revision_id)
            revision = VideoRevision(
                project_id=project_id,
                document_id=document_id,
                revision_number=current.revision + 1,
                parent_revision_id=current.head_revision_id,
                source="redo" if redo else "undo",
            )
            restored = target.model_copy(deep=True)
            restored.revision = current.revision + 1
            restored.head_revision_id = revision.id
            restored.revisions = [*current.revisions, revision]
            # Revisions own timeline state. Media identity, user-facing
            # document metadata, and asynchronous lifecycle collections remain
            # current so undo/redo cannot make later imports or jobs disappear.
            restored.name = current.name
            restored.archived = current.archived
            restored.assets = list(current.assets)
            restored.generated_assets = list(current.generated_assets)
            restored.transactions = list(current.transactions)
            restored.plans = list(current.plans)
            restored.jobs = list(current.jobs)
            restored.candidates = list(current.candidates)
            restored.created_at = current.created_at
            if redo:
                restored.redo_revision_ids = source_stack
                restored.undo_revision_ids = [*current.undo_revision_ids, current.head_revision_id]
            else:
                restored.undo_revision_ids = source_stack
                restored.redo_revision_ids = [*current.redo_revision_ids, current.head_revision_id]
            restored.updated_at = time.time()
            self._save_snapshot(restored, revision)
            restored.revisions[-1] = revision
            self._save_document(restored)
            return restored

    def undo(self, project_id: str, document_id: str) -> VideoProjectDocument:
        return self._restore_revision(project_id, document_id, redo=False)

    def redo(self, project_id: str, document_id: str) -> VideoProjectDocument:
        return self._restore_revision(project_id, document_id, redo=True)

    def create_job(
        self,
        project_id: str,
        document_id: str,
        job: VideoJob,
    ) -> tuple[VideoProjectDocument, VideoJob]:
        with self._lock:
            document = self.get_document(project_id, document_id)
            if job.project_id != project_id or job.document_id != document_id:
                raise VideoStoreError("Job ownership does not match the video document")
            available_assets = {item.id: item for item in [*document.assets, *document.generated_assets]}
            missing = [asset_id for asset_id in job.input_asset_ids if asset_id not in available_assets]
            if missing:
                raise VideoStoreError(f"Job input asset not found: {missing[0]}")
            expected_hashes = [available_assets[asset_id].sha256 for asset_id in job.input_asset_ids]
            job = job.model_copy(update={"input_hashes": expected_hashes})
            existing = next(
                (item for item in document.jobs if item.idempotency_key == job.idempotency_key),
                None,
            )
            if existing is not None:
                identity_fields = (
                    "project_id",
                    "document_id",
                    "session_id",
                    "kind",
                    "adapter_id",
                    "capability",
                    "input_asset_ids",
                    "input_hashes",
                    "parameters",
                    "expected_revision",
                )
                if any(getattr(existing, field) != getattr(job, field) for field in identity_fields):
                    raise VideoStoreError("Job idempotency key is already bound to different inputs")
                return document, existing
            document.jobs.append(job)
            document.updated_at = time.time()
            self._save_document(document)
            return document, job

    def add_candidate(self, project_id: str, document_id: str, candidate: GeneratedCandidate) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            if not any(item.id == candidate.id for item in document.candidates):
                document.candidates.append(candidate)
                document.updated_at = time.time()
                self._save_document(document)
            return document

    def get_job(self, project_id: str, document_id: str, job_id: str) -> VideoJob:
        document = self.get_document(project_id, document_id)
        job = next((item for item in document.jobs if item.id == job_id), None)
        if job is None:
            raise VideoStoreError("Video job not found")
        return job

    def update_job(self, project_id: str, document_id: str, job_id: str, **updates: Any) -> VideoJob:
        with self._lock:
            document = self.get_document(project_id, document_id)
            job = next((item for item in document.jobs if item.id == job_id), None)
            if job is None:
                raise VideoStoreError("Video job not found")
            # Never allow identity fields to be rewritten in place.
            forbidden = {
                "id",
                "project_id",
                "document_id",
                "idempotency_key",
                "kind",
                "input_asset_ids",
                "input_hashes",
            }
            safe = {key: value for key, value in updates.items() if key not in forbidden}
            if "status" in safe and job.status == "completed" and safe["status"] != "completed":
                raise VideoStoreError("Completed jobs are immutable terminal truth")
            # Generation/render/export completion requires real outputs or artifacts.
            if safe.get("status") == "completed":
                kind_value = job.kind.value if hasattr(job.kind, "value") else str(job.kind)
                outputs = safe.get("outputs", job.outputs)
                artifacts = safe.get("artifact_ids", job.artifact_ids)
                if kind_value in {"generation", "render", "export", "proxy", "preview"} and not outputs and not artifacts:
                    raise VideoStoreError("Cannot mark job completed without outputs or artifacts")
            updated = job.model_copy(update={**safe, "updated_at": time.time()})
            document.jobs = [updated if item.id == job_id else item for item in document.jobs]
            document.updated_at = time.time()
            self._save_document(document)
            return updated

    def update_creative_memory(
        self,
        project_id: str,
        document_id: str,
        memory: VideoCreativeMemory,
    ) -> VideoProjectDocument:
        with self._lock:
            document = self.get_document(project_id, document_id)
            if memory.project_id and memory.project_id != project_id:
                raise VideoStoreError("Creative memory Project identity does not match document")
            document.creative_memory = memory.model_copy(update={"project_id": project_id})
            document.updated_at = time.time()
            self._save_document(document)
            return document

    def add_artifact(self, project_id: str, document_id: str, artifact: dict[str, Any]) -> VideoProjectDocument:
        """Persist a structured skill artifact on the video document (not prose)."""
        with self._lock:
            document = self.get_document(project_id, document_id)
            art_id = str((artifact or {}).get("id") or "").strip()
            if not art_id:
                raise VideoStoreError("Artifact requires a stable id")
            existing = [a for a in document.artifacts if str(a.get("id") or "") != art_id]
            existing.append(dict(artifact))
            document.artifacts = existing
            document.updated_at = time.time()
            self._save_document(document)
            return document


_STORE: Optional[VideoEditorStore] = None
_STORE_LOCK = threading.Lock()
_STORE_ROOT: Optional[Path] = None


def reset_video_editor_store() -> None:
    """Drop the process-local singleton (tests / rebind after DATA_DIR change)."""
    global _STORE, _STORE_ROOT
    with _STORE_LOCK:
        _STORE = None
        _STORE_ROOT = None


def get_video_editor_store() -> VideoEditorStore:
    """Return the process VideoEditorStore bound to the *current* DATA_DIR.

    Does not capture a path at import time. If the configured data root changes
    (e.g. tests set ECHOSPEAK_DATA_DIR before first access, or reset), the
    singleton is recreated so roots stay isolated.

    Tests that inject a store must also monkeypatch ``resolve_video_data_dir``
    (or set ``_STORE_ROOT`` equal to that store's root *and* make resolve return
    the same path) so the singleton is not replaced mid-test.
    """
    global _STORE, _STORE_ROOT
    target = resolve_video_data_dir()
    with _STORE_LOCK:
        if (
            _STORE is not None
            and _STORE_ROOT is not None
            and Path(_STORE_ROOT).resolve() == target
            and Path(getattr(_STORE, "root", target)).resolve() == target
        ):
            return _STORE
        # Allow explicit test injection: store root equals resolve target even if
        # _STORE_ROOT was set in the same assignment.
        if _STORE is not None and Path(getattr(_STORE, "root", target)).resolve() == target:
            _STORE_ROOT = target
            return _STORE
        _STORE = VideoEditorStore(root=target)
        _STORE_ROOT = target
        return _STORE


# Deprecated alias: never write here; kept only for migration detection imports.
VIDEO_DATA_DIR = legacy_repo_video_data_dir()
