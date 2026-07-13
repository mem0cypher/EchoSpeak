"""Explicit, fail-closed migration of legacy Video Editor store data.

Legacy path (repository):
  apps/backend/data/video_editor/

Canonical path:
  {ECHOSPEAK_DATA_DIR|config.DATA_DIR}/video_editor/

Never silently moves or deletes the source. Source remains untouched until the
operator runs an explicit import and verification succeeds. Destination
collisions fail closed. An audit record is written under the destination root.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.video_editor.store import (
    VideoStoreError,
    VideoStorePersistenceError,
    legacy_repo_video_data_dir,
    resolve_video_data_dir,
)


@dataclass
class MigrationReport:
    ok: bool = False
    dry_run: bool = True
    source: str = ""
    destination: str = ""
    files_scanned: int = 0
    files_copied: int = 0
    collisions: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    audit_path: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "source": self.source,
            "destination": self.destination,
            "files_scanned": self.files_scanned,
            "files_copied": self.files_copied,
            "collisions": list(self.collisions),
            "verified": list(self.verified),
            "errors": list(self.errors),
            "audit_path": self.audit_path,
            "message": self.message,
        }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_legacy_video_data(
    *,
    source: Optional[Path] = None,
    destination: Optional[Path] = None,
) -> dict[str, Any]:
    """Detect whether legacy repository video data exists and needs migration."""
    src = Path(source).expanduser().resolve() if source else legacy_repo_video_data_dir()
    dest = Path(destination).expanduser().resolve() if destination else resolve_video_data_dir()
    src_exists = src.is_dir()
    src_files = list(src.rglob("*")) if src_exists else []
    src_file_count = sum(1 for p in src_files if p.is_file())
    dest_exists = dest.is_dir()
    dest_files = list(dest.rglob("*")) if dest_exists else []
    dest_file_count = sum(1 for p in dest_files if p.is_file() and p.name != "migration-audit.json")
    # Only treat as "legacy needs attention" when source is the repo path and has data.
    is_legacy_location = False
    try:
        is_legacy_location = src.resolve() == legacy_repo_video_data_dir()
    except Exception:
        is_legacy_location = False
    needs_migration = bool(
        is_legacy_location
        and src_file_count > 0
        and src.resolve() != dest.resolve()
        and dest_file_count == 0
    )
    return {
        "source": str(src),
        "destination": str(dest),
        "source_exists": src_exists,
        "source_file_count": src_file_count,
        "destination_exists": dest_exists,
        "destination_file_count": dest_file_count,
        "is_legacy_location": is_legacy_location,
        "needs_migration": needs_migration,
        "same_path": src.resolve() == dest.resolve() if src_exists else False,
    }


def migrate_legacy_video_data(
    *,
    source: Optional[Path] = None,
    destination: Optional[Path] = None,
    dry_run: bool = True,
    allow_overwrite: bool = False,
) -> MigrationReport:
    """Copy verified legacy video state into the configured data root.

    - Never deletes or renames the source.
    - Fails on destination collisions unless allow_overwrite=True.
    - Verifies every copied file by SHA-256.
    - Writes migration-audit.json under the destination root on success (or dry-run plan).
    """
    report = MigrationReport(dry_run=dry_run)
    src = Path(source).expanduser().resolve() if source else legacy_repo_video_data_dir()
    dest = Path(destination).expanduser().resolve() if destination else resolve_video_data_dir()
    report.source = str(src)
    report.destination = str(dest)

    if not src.is_dir():
        report.message = "No legacy source directory present"
        report.ok = True
        return report
    if src.resolve() == dest.resolve():
        report.message = "Source and destination are the same path; nothing to migrate"
        report.ok = True
        return report

    files = [p for p in sorted(src.rglob("*")) if p.is_file()]
    report.files_scanned = len(files)
    if not files:
        report.message = "Legacy source is empty"
        report.ok = True
        return report

    # Collision scan (relative paths)
    planned: list[tuple[Path, Path, str]] = []
    for src_file in files:
        rel = src_file.relative_to(src)
        dest_file = dest / rel
        digest = _sha256_file(src_file)
        if dest_file.exists():
            try:
                existing = _sha256_file(dest_file)
            except Exception as exc:
                report.errors.append(f"cannot read destination {dest_file}: {exc}")
                report.message = "persistence_failed: cannot inspect destination collision"
                return report
            if existing != digest and not allow_overwrite:
                report.collisions.append(str(rel).replace("\\", "/"))
            elif existing == digest:
                # Identical — treat as already present, still verify
                planned.append((src_file, dest_file, digest))
                continue
            else:
                planned.append((src_file, dest_file, digest))
        else:
            planned.append((src_file, dest_file, digest))

    if report.collisions:
        report.message = (
            f"migration_collision: {len(report.collisions)} destination file(s) differ from source; "
            "refusing to import. Resolve collisions or re-run with allow_overwrite after backup."
        )
        report.ok = False
        return report

    if dry_run:
        report.files_copied = 0
        report.verified = [str(rel) for _, dest_file, _ in [
            (s, d, h) for s, d, h in planned
        ]]
        # Use relative paths for verified list
        report.verified = []
        for s, d, h in planned:
            report.verified.append(str(s.relative_to(src)).replace("\\", "/"))
        report.message = f"dry_run: would copy {len(planned)} file(s); source left untouched"
        report.ok = True
        return report

    try:
        dest.mkdir(parents=True, exist_ok=True)
        probe = dest / f".migrate_probe.{time.time_ns()}"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        raise VideoStorePersistenceError(
            f"persistence_failed: destination not writable: {dest} ({exc})",
            root=str(dest),
        ) from exc

    for src_file, dest_file, expected_digest in planned:
        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            # Copy to temp then replace (Windows-safe)
            tmp = dest_file.with_name(f".{dest_file.name}.{time.time_ns()}.tmp")
            shutil.copy2(src_file, tmp)
            # Verify temp before promote
            got = _sha256_file(tmp)
            if got != expected_digest:
                tmp.unlink(missing_ok=True)
                report.errors.append(f"verification_failed: temp digest mismatch for {src_file.name}")
                report.message = "verification_failed during copy"
                report.ok = False
                return report
            tmp.replace(dest_file)
            # Final verify
            final = _sha256_file(dest_file)
            if final != expected_digest:
                report.errors.append(f"verification_failed: dest digest mismatch for {dest_file}")
                report.message = "verification_failed after promote"
                report.ok = False
                return report
            rel = str(src_file.relative_to(src)).replace("\\", "/")
            report.verified.append(rel)
            report.files_copied += 1
        except Exception as exc:
            report.errors.append(f"{src_file}: {exc}")
            report.message = f"persistence_failed: {exc}"
            report.ok = False
            return report

    audit = {
        "version": 1,
        "migrated_at": time.time(),
        "source": str(src),
        "destination": str(dest),
        "files_copied": report.files_copied,
        "verified": list(report.verified),
        "source_untouched": True,
        "allow_overwrite": allow_overwrite,
        "note": "Legacy source was not deleted or moved. Safe to remove manually after backup.",
    }
    audit_path = dest / "migration-audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report.audit_path = str(audit_path)
    report.message = (
        f"Migrated {report.files_copied} file(s) into {dest}. "
        "Legacy source left untouched for rollback."
    )
    report.ok = True
    return report
