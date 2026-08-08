"""Non-executable package manifests for EchoSpeak extensions.

A package describes installable components. A Connection describes configured
transport/authentication. A ToolRegistry entry describes executable authority.
Those concepts intentionally do not inherit authority from one another.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.connections import ConnectionKind


PACKAGE_MANIFEST_SCHEMA_VERSION = 1
_PACKAGE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_UNRESTRICTED_CAPABILITIES = frozenset({
    "shell", "exec", "unrestricted_shell", "host_terminal", "bypass_approval",
})


class PackageComponentKind(str, Enum):
    SKILL = "skill"
    TOOL_PROVIDER = "tool_provider"
    MODEL_ADAPTER = "model_adapter"
    MEDIA_PROVIDER = "media_provider"
    UI_EXTENSION = "ui_extension"
    LEGACY_PIPELINE_HOOK = "legacy_pipeline_hook"


class PackageComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    kind: PackageComponentKind
    entrypoint: str
    declared_tool_names: list[str] = Field(default_factory=list)
    declared_capabilities: list[str] = Field(default_factory=list)
    compatibility_only: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_component(self) -> "PackageComponent":
        if not _PACKAGE_ID.fullmatch(str(self.component_id or "")):
            raise ValueError("Package component_id must be a canonical lowercase identifier")
        entrypoint = str(self.entrypoint or "").replace("\\", "/").strip()
        if (
            not entrypoint
            or entrypoint.startswith("/")
            or re.match(r"^[A-Za-z]:", entrypoint)
            or ".." in Path(entrypoint).parts
        ):
            raise ValueError("Package component entrypoint must be a relative path without traversal")
        self.entrypoint = entrypoint
        self.declared_tool_names = _bounded_unique(self.declared_tool_names, limit=128)
        self.declared_capabilities = _bounded_unique(self.declared_capabilities, limit=128)
        unsafe = set(self.declared_capabilities) & _UNRESTRICTED_CAPABILITIES
        if unsafe:
            raise ValueError(f"Package components cannot declare unrestricted authority: {sorted(unsafe)}")
        if self.kind == PackageComponentKind.LEGACY_PIPELINE_HOOK and not self.compatibility_only:
            raise ValueError("legacy pipeline hooks must be marked compatibility_only")
        _reject_secret_like_metadata(self.metadata)
        return self


class PackageConnectionRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    kind: ConnectionKind
    provider: str = ""
    capability_ids: list[str] = Field(default_factory=list)
    optional: bool = False

    @model_validator(mode="after")
    def separate_package_from_connection(self) -> "PackageConnectionRequirement":
        if self.kind == ConnectionKind.PLUGIN:
            raise ValueError("plugin is a legacy Connection kind; packages must request a transport/auth Connection")
        if not _PACKAGE_ID.fullmatch(str(self.requirement_id or "")):
            raise ValueError("Package connection requirement_id is invalid")
        self.provider = str(self.provider or "").strip()[:160]
        self.capability_ids = _bounded_unique(self.capability_ids, limit=128)
        return self


class PackageManifest(BaseModel):
    """One auditable package declaration; never an executable dispatch object."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = PACKAGE_MANIFEST_SCHEMA_VERSION
    package_id: str
    version: str
    display_name: str
    description: str = ""
    publisher: str = "local"
    components: list[PackageComponent]
    connection_requirements: list[PackageConnectionRequirement] = Field(default_factory=list)
    requested_permissions: list[str] = Field(default_factory=list)
    compatible_echospeak_versions: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_future_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and int(value.get("schema_version") or 1) > PACKAGE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported future PackageManifest schema version")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> "PackageManifest":
        self.schema_version = PACKAGE_MANIFEST_SCHEMA_VERSION
        if not _PACKAGE_ID.fullmatch(str(self.package_id or "")):
            raise ValueError("package_id must be a canonical lowercase identifier")
        if not _SEMVER.fullmatch(str(self.version or "")):
            raise ValueError("Package version must use semantic versioning")
        self.display_name = re.sub(r"\s+", " ", str(self.display_name or "")).strip()[:200]
        if not self.display_name:
            raise ValueError("Package display_name is required")
        self.description = str(self.description or "").strip()[:2000]
        self.publisher = str(self.publisher or "local").strip()[:200]
        if not self.components:
            raise ValueError("Package must declare at least one component")
        component_ids = [item.component_id for item in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("Package component ids must be unique")
        requirement_ids = [item.requirement_id for item in self.connection_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Package connection requirement ids must be unique")
        self.requested_permissions = _bounded_unique(self.requested_permissions, limit=128)
        unsafe = set(self.requested_permissions) & _UNRESTRICTED_CAPABILITIES
        if unsafe:
            raise ValueError(f"Package cannot request unrestricted authority: {sorted(unsafe)}")
        self.compatible_echospeak_versions = _bounded_unique(
            self.compatible_echospeak_versions, limit=32
        )
        _reject_secret_like_metadata(self.provenance)
        return self


def load_package_manifest(path: Path) -> PackageManifest:
    """Strictly load a package manifest; importing code is a separate governed step."""

    raw = Path(path).read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError(f"Package manifest is empty: {path}")
    return PackageManifest.model_validate(json.loads(raw))


def project_legacy_skill_package(
    *,
    skill_id: str,
    display_name: str,
    has_tools: bool,
    has_pipeline_hook: bool,
) -> PackageManifest:
    """Create a read-only compatibility manifest for existing skill folders."""

    components = [PackageComponent(
        component_id="skill",
        kind=PackageComponentKind.SKILL,
        entrypoint="SKILL.md",
    )]
    if has_tools:
        components.append(PackageComponent(
            component_id="tools",
            kind=PackageComponentKind.TOOL_PROVIDER,
            entrypoint="tools.py",
        ))
    if has_pipeline_hook:
        components.append(PackageComponent(
            component_id="legacy-hook",
            kind=PackageComponentKind.LEGACY_PIPELINE_HOOK,
            entrypoint="plugin.py",
            compatibility_only=True,
        ))
    return PackageManifest(
        package_id=f"legacy.{skill_id.casefold().replace('_', '-')}",
        version="0.0.0+legacy",
        display_name=display_name or skill_id,
        components=components,
        provenance={"projection": "legacy_skill_folder", "skill_id": skill_id},
    )


def _bounded_unique(values: Iterable[Any], *, limit: int) -> list[str]:
    return list(dict.fromkeys(
        str(item or "").strip()[:200]
        for item in values
        if str(item or "").strip()
    ))[:limit]


def _reject_secret_like_metadata(value: Any, *, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key or "").casefold().replace("-", "_")
            if any(token in normalized for token in ("secret", "password", "token", "api_key", "private_key")):
                raise ValueError(f"Package {path} cannot contain credentials")
            _reject_secret_like_metadata(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_like_metadata(child, path=f"{path}[{index}]")
