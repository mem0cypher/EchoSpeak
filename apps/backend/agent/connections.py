"""Typed, secret-free Connection registry and capability projections.

Low-level provider, MCP, plugin, and local-application clients keep ownership
of their transport processes.  This registry owns the approved capability and
scope records that Tasks and Routines may reference.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

try:
    from config import DATA_DIR
except Exception:  # pragma: no cover - import fallback for isolated tooling
    DATA_DIR = Path("data")


CONNECTION_REGISTRY_SCHEMA_VERSION = 1


class ConnectionRegistryError(RuntimeError):
    pass


class ConnectionStateError(ConnectionRegistryError):
    pass


class ConnectionConflictError(ConnectionRegistryError):
    pass


class ConnectionScopeError(ConnectionRegistryError):
    pass


class ConnectionKind(str, Enum):
    PLUGIN = "plugin"
    MCP_SERVER = "mcp_server"
    API = "api"
    WEBSITE = "website"
    CALENDAR = "calendar"
    PROVIDER = "provider"
    LOCAL_APPLICATION = "local_application"
    CREATIVE_TOOL = "creative_tool"
    OTHER = "other"


class ConnectionCapabilityKind(str, Enum):
    TOOL = "tool"
    RESOURCE = "resource"
    EVENT = "event"
    PROVIDER = "provider"
    LOCAL_ACTION = "local_action"


class ConnectionHealth(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    BLOCKED = "blocked"
    DISABLED = "disabled"
    MISSING_CONFIGURATION = "missing_configuration"


class ConnectionAuthentication(str, Enum):
    NONE = "none"
    REQUIRED = "required"
    CONFIGURED = "configured"
    EXPIRED = "expired"
    ERROR = "error"


_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(secret|password|passwd|token|api[_-]?key|authorization|cookie|private[_-]?key|client[_-]?secret)(?:$|[_-])"
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)\s*[=:]\s*[^\s,;]+"
)
_BEARER_TEXT = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+={0,2}")
_UNRESTRICTED_TOOL_NAMES = frozenset(
    {"shell", "terminal", "exec", "command", "run_command", "terminal_run", "unrestricted_shell"}
)


def _validate_secret_free_mapping(value: Any, *, path: str = "metadata") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValueError(f"Connection {path} cannot contain secret field {key!r}")
            _validate_secret_free_mapping(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_secret_free_mapping(child, path=f"{path}[{index}]")
    elif isinstance(value, str) and _redact_text(value) != value:
        raise ValueError(f"Connection {path} cannot contain secret-like values")


def _redact_text(value: str) -> str:
    text = _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}=[REDACTED]", str(value or ""))
    return _BEARER_TEXT.sub("Bearer [REDACTED]", text)


def _safe_projection(value: Any) -> Any:
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for key, child in value.items():
            projected[str(key)] = "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _safe_projection(child)
        return projected
    if isinstance(value, list):
        return [_safe_projection(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class ConnectionScope(BaseModel):
    """Explicit authority boundary for one Connection."""

    allow_global: bool = False
    project_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    filesystem_roots: list[str] = Field(default_factory=list)
    network_hosts: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("project_ids", "session_ids", "filesystem_roots", "network_hosts", "permissions")
    @classmethod
    def normalize_unique_values(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(str(item or "").strip() for item in values if str(item or "").strip()))
        if any(item == "*" for item in cleaned):
            raise ValueError("wildcard Connection scopes are not allowed")
        return cleaned

    @model_validator(mode="after")
    def validate_authority(self) -> "ConnectionScope":
        if not self.allow_global and not self.project_ids:
            raise ValueError("Connection scope requires explicit project_ids or allow_global")
        return self

    def allows(self, project_id: str, session_id: str = "") -> bool:
        project_allowed = self.allow_global or str(project_id or "") in self.project_ids
        session_allowed = not self.session_ids or str(session_id or "") in self.session_ids
        return project_allowed and session_allowed


class ConnectionCapability(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    kind: ConnectionCapabilityKind
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    available: bool = True
    requires_approval: bool = False
    tool_names: list[str] = Field(default_factory=list)
    resource_types: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_names", "resource_types", "event_types", "permissions")
    @classmethod
    def normalize_capability_lists(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item or "").strip() for item in values if str(item or "").strip()))

    @model_validator(mode="after")
    def validate_capability(self) -> "ConnectionCapability":
        unsafe = {name.lower() for name in self.tool_names} & _UNRESTRICTED_TOOL_NAMES
        if unsafe:
            raise ValueError(f"Connection capabilities cannot expose unrestricted shell tools: {sorted(unsafe)}")
        _validate_secret_free_mapping(self.metadata, path="capability.metadata")
        return self


class ConnectionRecord(BaseModel):
    schema_version: Literal[1] = CONNECTION_REGISTRY_SCHEMA_VERSION
    id: str = Field(min_length=1, max_length=200)
    kind: ConnectionKind
    display_name: str = Field(min_length=1, max_length=200)
    provider: str = ""
    source_ref: str = Field(default="", max_length=500)
    enabled: bool = True
    health: ConnectionHealth = ConnectionHealth.UNKNOWN
    authentication: ConnectionAuthentication = ConnectionAuthentication.NONE
    scope: ConnectionScope
    capabilities: list[ConnectionCapability] = Field(default_factory=list)
    active_job_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_checked_at: Optional[float] = None
    last_used_at: Optional[float] = None
    revision: int = Field(default=1, ge=1)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @field_validator("active_job_ids")
    @classmethod
    def normalize_jobs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item or "").strip() for item in values if str(item or "").strip()))

    @field_validator("errors")
    @classmethod
    def bound_errors(cls, values: list[str]) -> list[str]:
        return [_redact_text(str(item or "")[:2000]) for item in values[-20:]]

    @field_validator("provider", "source_ref")
    @classmethod
    def reject_secret_bearing_refs(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if _redact_text(cleaned) != cleaned:
            raise ValueError("Connection references cannot contain credentials")
        return cleaned

    @model_validator(mode="after")
    def validate_record(self) -> "ConnectionRecord":
        capability_ids = [capability.id for capability in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("Connection capability ids must be unique")
        _validate_secret_free_mapping(self.provenance, path="provenance")
        _validate_secret_free_mapping(self.metadata, path="metadata")
        if not self.enabled and self.health != ConnectionHealth.DISABLED:
            raise ValueError("disabled Connections must report disabled health")
        return self


class ConnectionReference(BaseModel):
    connection_id: str = Field(min_length=1)
    capability_ids: list[str] = Field(default_factory=list)


class ConnectionProjection(BaseModel):
    schema_version: Literal[1] = CONNECTION_REGISTRY_SCHEMA_VERSION
    id: str
    kind: ConnectionKind
    display_name: str
    provider: str = ""
    source_ref: str = ""
    enabled: bool
    health: ConnectionHealth
    authentication: ConnectionAuthentication
    scope: dict[str, Any]
    capabilities: list[dict[str, Any]]
    active_job_ids: list[str]
    errors: list[str]
    last_checked_at: Optional[float]
    last_used_at: Optional[float]
    revision: int
    updated_at: float


class _ConnectionEnvelope(BaseModel):
    schema_version: Literal[1] = CONNECTION_REGISTRY_SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    connections: dict[str, ConnectionRecord] = Field(default_factory=dict)


def resolve_connection_registry_path(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir if data_dir is not None else DATA_DIR).expanduser().resolve()
    return root / "connections" / "registry.json"


class ConnectionRegistry:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or resolve_connection_registry_path()).expanduser().resolve()
        self._lock = threading.RLock()
        self._envelope = _ConnectionEnvelope()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                raise ValueError("authoritative Connection registry is empty")
            envelope = _ConnectionEnvelope.model_validate(json.loads(raw))
            for key, record in envelope.connections.items():
                if key != record.id:
                    raise ValueError(f"Connection map key {key!r} does not match record id {record.id!r}")
            self._envelope = envelope
        except Exception as exc:
            self._fail_corrupt(exc)

    def _fail_corrupt(self, error: Exception) -> None:
        quarantine = self.path.parent / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            quarantine.mkdir(parents=True, exist_ok=False)
            copy = quarantine / self.path.name
            shutil.copy2(self.path, copy)
            guide = quarantine / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak Connection registry recovery\n\n"
                f"Authoritative file: {self.path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep the backend stopped. Repair the authoritative JSON or restore a reviewed backup, "
                "then restart one backend instance. The original file was not changed.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:  # pragma: no cover
            note = f"quarantine failed: {quarantine_error}"
        raise ConnectionStateError(
            f"Connection registry is unreadable at {self.path}; the authoritative file was not "
            f"overwritten; {note}. ({error})"
        ) from error

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(self._envelope.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except Exception as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConnectionStateError(f"Failed to persist Connection registry: {exc}") from exc

    @staticmethod
    def _copy(record: ConnectionRecord) -> ConnectionRecord:
        return record.model_copy(deep=True)

    def register(self, record: ConnectionRecord | dict[str, Any]) -> ConnectionRecord:
        candidate = ConnectionRecord.model_validate(record)
        with self._lock:
            existing = self._envelope.connections.get(candidate.id)
            if existing is not None:
                if existing.model_dump(mode="json") != candidate.model_dump(mode="json"):
                    raise ConnectionConflictError("Connection id is already registered")
                return self._copy(existing)
            self._envelope.connections[candidate.id] = candidate
            self._envelope.revision += 1
            self._persist()
            return self._copy(candidate)

    def update(
        self,
        connection_id: str,
        *,
        expected_revision: int,
        health: Optional[ConnectionHealth | str] = None,
        authentication: Optional[ConnectionAuthentication | str] = None,
        enabled: Optional[bool] = None,
        active_job_ids: Optional[list[str]] = None,
        errors: Optional[list[str]] = None,
        last_checked_at: Optional[float] = None,
        last_used_at: Optional[float] = None,
    ) -> ConnectionRecord:
        with self._lock:
            record = self._envelope.connections.get(str(connection_id or ""))
            if record is None:
                raise ConnectionRegistryError("Connection not found")
            if record.revision != int(expected_revision):
                raise ConnectionConflictError(
                    f"Connection revision changed (expected {expected_revision}, current {record.revision})"
                )
            changes: dict[str, Any] = {}
            if health is not None:
                changes["health"] = ConnectionHealth(health)
            if authentication is not None:
                changes["authentication"] = ConnectionAuthentication(authentication)
            if enabled is not None:
                changes["enabled"] = bool(enabled)
                if not enabled:
                    changes["health"] = ConnectionHealth.DISABLED
            if active_job_ids is not None:
                changes["active_job_ids"] = list(active_job_ids)
            if errors is not None:
                changes["errors"] = list(errors)
            if last_checked_at is not None:
                changes["last_checked_at"] = float(last_checked_at)
            if last_used_at is not None:
                changes["last_used_at"] = float(last_used_at)
            changes["revision"] = record.revision + 1
            changes["updated_at"] = time.time()
            updated = ConnectionRecord.model_validate(record.model_copy(update=changes).model_dump())
            self._envelope.connections[record.id] = updated
            self._envelope.revision += 1
            self._persist()
            return self._copy(updated)

    def get(self, connection_id: str, *, project_id: str, session_id: str = "") -> Optional[ConnectionRecord]:
        with self._lock:
            record = self._envelope.connections.get(str(connection_id or ""))
            if record is None or not record.scope.allows(project_id, session_id):
                return None
            return self._copy(record)

    def list(self, *, project_id: str, session_id: str = "") -> list[ConnectionProjection]:
        if not str(project_id or "").strip():
            raise ConnectionScopeError("Project scope is required to list Connections")
        with self._lock:
            records = [
                self._copy(record)
                for record in self._envelope.connections.values()
                if record.scope.allows(project_id, session_id)
            ]
        return [self._project(record) for record in sorted(records, key=lambda item: item.display_name.lower())]

    def resolve_references(
        self,
        references: list[ConnectionReference | dict[str, Any]],
        *,
        project_id: str,
        session_id: str,
    ) -> list[ConnectionProjection]:
        resolved: list[ConnectionProjection] = []
        with self._lock:
            for raw in references:
                reference = ConnectionReference.model_validate(raw)
                record = self._envelope.connections.get(reference.connection_id)
                if record is None:
                    raise ConnectionRegistryError(f"Connection {reference.connection_id!r} is not registered")
                if not record.scope.allows(project_id, session_id):
                    raise ConnectionScopeError(
                        f"Connection {reference.connection_id!r} is outside the requested Project/Session scope"
                    )
                if not record.enabled or record.health in {
                    ConnectionHealth.DISABLED,
                    ConnectionHealth.BLOCKED,
                    ConnectionHealth.UNHEALTHY,
                    ConnectionHealth.MISSING_CONFIGURATION,
                }:
                    raise ConnectionRegistryError(
                        f"Connection {reference.connection_id!r} is not executable ({record.health.value})"
                    )
                available = {
                    capability.id
                    for capability in record.capabilities
                    if capability.enabled and capability.available
                }
                requested = set(reference.capability_ids)
                if not requested:
                    raise ConnectionRegistryError(
                        f"Connection {reference.connection_id!r} requires explicit capability ids"
                    )
                missing = requested - available
                if missing:
                    raise ConnectionRegistryError(
                        f"Connection {reference.connection_id!r} lacks approved capabilities: {sorted(missing)}"
                    )
                projection = self._project(record)
                projection.capabilities = [
                    capability
                    for capability in projection.capabilities
                    if str(capability.get("id") or "") in requested
                ]
                resolved.append(projection)
        return resolved

    @staticmethod
    def _project(record: ConnectionRecord) -> ConnectionProjection:
        scope = {
            "allow_global": record.scope.allow_global,
            "project_ids": list(record.scope.project_ids),
            "session_ids": list(record.scope.session_ids),
            "filesystem_roots": list(record.scope.filesystem_roots),
            "network_hosts": list(record.scope.network_hosts),
            "permissions": list(record.scope.permissions),
        }
        capabilities = [
            _safe_projection(capability.model_dump(mode="json"))
            for capability in record.capabilities
        ]
        return ConnectionProjection(
            id=record.id,
            kind=record.kind,
            display_name=record.display_name,
            provider=record.provider,
            source_ref=record.source_ref,
            enabled=record.enabled,
            health=record.health,
            authentication=record.authentication,
            scope=_safe_projection(scope),
            capabilities=capabilities,
            active_job_ids=list(record.active_job_ids),
            errors=[_redact_text(error) for error in record.errors],
            last_checked_at=record.last_checked_at,
            last_used_at=record.last_used_at,
            revision=record.revision,
            updated_at=record.updated_at,
        )


_REGISTRY: Optional[ConnectionRegistry] = None


def get_connection_registry() -> ConnectionRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ConnectionRegistry()
    return _REGISTRY
