"""Canonical Connection catalog and lifecycle service.

ConnectionRegistry owns durable identity, scope, lifecycle, capability and
health. CredentialBroker owns secrets. Provider adapters and MCP own transport.
This service coordinates them without creating a second execution authority.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from agent.connections import (
    ConnectionAuthentication,
    ConnectionCapability,
    ConnectionCapabilityKind,
    ConnectionCapabilityRisk,
    ConnectionHealth,
    ConnectionKind,
    ConnectionLifecycle,
    ConnectionRecord,
    ConnectionRegistry,
    ConnectionRegistryError,
    ConnectionScope,
    get_connection_registry,
)
from agent.credential_broker import CredentialBroker, get_credential_broker


class ProviderCapabilityDescriptor(BaseModel):
    id: str
    name: str
    description: str = ""
    risk: ConnectionCapabilityRisk = ConnectionCapabilityRisk.READ
    permissions: list[str] = Field(default_factory=list)


class ConnectionProviderDescriptor(BaseModel):
    id: str
    name: str
    description: str
    kind: ConnectionKind
    authentication: Literal["none", "local_path", "static_credential", "oauth_pkce", "mcp_oauth"]
    rollout_state: Literal["available", "advanced", "requires_provider_adapter"]
    setup_fields: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[ProviderCapabilityDescriptor] = Field(default_factory=list)
    documentation_url: str = ""
    order: int = 100


class AuthorizationResult(BaseModel):
    transaction_id: str
    provider_id: str
    status: Literal[
        "connected",
        "probe_required",
        "authorization_required",
        "reconnect_required",
    ]
    connection_id: str = ""
    authorization_url: str = ""
    message: str = ""


def _provider_catalog() -> tuple[ConnectionProviderDescriptor, ...]:
    read = ConnectionCapabilityRisk.READ
    write = ConnectionCapabilityRisk.WRITE
    destructive = ConnectionCapabilityRisk.DESTRUCTIVE
    return (
        ConnectionProviderDescriptor(
            id="obsidian",
            name="Obsidian",
            description="Read and write a Project-scoped local vault.",
            kind=ConnectionKind.LOCAL_APPLICATION,
            authentication="local_path",
            rollout_state="available",
            order=10,
            setup_fields=[{"name": "vault_path", "label": "Vault folder", "type": "path", "secret": False}],
            capabilities=[
                ProviderCapabilityDescriptor(id="vault.read", name="Read notes", risk=read, permissions=["files.read"]),
                ProviderCapabilityDescriptor(id="vault.write", name="Create and update notes", risk=write, permissions=["files.write"]),
                ProviderCapabilityDescriptor(id="vault.delete", name="Delete notes", risk=destructive, permissions=["files.delete"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="notion",
            name="Notion",
            description="Search and update a Notion workspace.",
            kind=ConnectionKind.MCP_SERVER,
            authentication="mcp_oauth",
            rollout_state="requires_provider_adapter",
            order=20,
            documentation_url="https://developers.notion.com/docs/get-started-with-mcp",
            capabilities=[
                ProviderCapabilityDescriptor(id="notion.read", name="Search and read pages", risk=read, permissions=["notion.read"]),
                ProviderCapabilityDescriptor(id="notion.write", name="Create and update pages", risk=write, permissions=["notion.write"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="github",
            name="GitHub",
            description="Inspect repositories, issues, and pull requests; writes remain separately governed.",
            kind=ConnectionKind.API,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=30,
            documentation_url="https://docs.github.com/apps/oauth-apps",
            capabilities=[
                ProviderCapabilityDescriptor(id="github.read", name="Read repositories and issues", risk=read, permissions=["github.read"]),
                ProviderCapabilityDescriptor(id="github.write", name="Create issues and comments", risk=write, permissions=["github.write"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="google_calendar",
            name="Google Calendar",
            description="Read schedules first; calendar mutations use incremental authorization.",
            kind=ConnectionKind.CALENDAR,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=40,
            capabilities=[
                ProviderCapabilityDescriptor(id="calendar.read", name="Read events", risk=read, permissions=["calendar.read"]),
                ProviderCapabilityDescriptor(id="calendar.write", name="Create or update events", risk=write, permissions=["calendar.write"]),
                ProviderCapabilityDescriptor(id="calendar.delete", name="Delete events", risk=destructive, permissions=["calendar.delete"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="spotify",
            name="Spotify",
            description="Search music and control playback through desktop-safe PKCE authorization.",
            kind=ConnectionKind.API,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=50,
            capabilities=[
                ProviderCapabilityDescriptor(id="spotify.read", name="Search and inspect playback", risk=read, permissions=["spotify.read"]),
                ProviderCapabilityDescriptor(id="spotify.control", name="Control playback", risk=write, permissions=["spotify.control"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="home_assistant",
            name="Home Assistant",
            description="Inspect entities and govern device-control operations separately.",
            kind=ConnectionKind.API,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=60,
            capabilities=[
                ProviderCapabilityDescriptor(id="home_assistant.read", name="Read entity state", risk=read, permissions=["home_assistant.read"]),
                ProviderCapabilityDescriptor(id="home_assistant.control", name="Control devices", risk=write, permissions=["home_assistant.control"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="custom_mcp",
            name="Custom MCP server",
            description="Connect a reviewed stdio, Streamable HTTP, or legacy SSE MCP server.",
            kind=ConnectionKind.MCP_SERVER,
            authentication="static_credential",
            rollout_state="advanced",
            order=90,
            setup_fields=[
                {"name": "name", "label": "Name", "type": "text", "secret": False},
                {"name": "transport", "label": "Transport", "type": "select", "options": ["stdio", "streamable_http", "sse"], "secret": False},
                {"name": "command", "label": "Command", "type": "text", "secret": False},
                {"name": "url", "label": "Server URL", "type": "url", "secret": False},
                {"name": "headers", "label": "Authentication headers", "type": "secret_object", "secret": True},
                {"name": "env", "label": "Process environment", "type": "secret_object", "secret": True},
            ],
            capabilities=[],
        ),
        ConnectionProviderDescriptor(
            id="google_workspace",
            name="Gmail & Google Drive",
            description="Search mail and files with narrow Google Workspace scopes.",
            kind=ConnectionKind.API,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=100,
            capabilities=[
                ProviderCapabilityDescriptor(id="google_workspace.read", name="Read mail and files", risk=read, permissions=["google_workspace.read"]),
                ProviderCapabilityDescriptor(id="google_workspace.write", name="Send mail or change files", risk=write, permissions=["google_workspace.write"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="slack",
            name="Slack",
            description="Search workspace context and govern outgoing messages separately.",
            kind=ConnectionKind.API,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=110,
            capabilities=[
                ProviderCapabilityDescriptor(id="slack.read", name="Read workspace content", risk=read, permissions=["slack.read"]),
                ProviderCapabilityDescriptor(id="slack.write", name="Send messages", risk=write, permissions=["slack.write"]),
            ],
        ),
        ConnectionProviderDescriptor(
            id="microsoft_graph",
            name="Microsoft 365",
            description="Connect Outlook, Calendar, OneDrive, and Teams through Microsoft Graph.",
            kind=ConnectionKind.API,
            authentication="oauth_pkce",
            rollout_state="requires_provider_adapter",
            order=120,
            capabilities=[
                ProviderCapabilityDescriptor(id="microsoft_graph.read", name="Read Microsoft 365 data", risk=read, permissions=["microsoft_graph.read"]),
                ProviderCapabilityDescriptor(id="microsoft_graph.write", name="Send or update Microsoft 365 data", risk=write, permissions=["microsoft_graph.write"]),
            ],
        ),
    )


def _connection_capabilities(
    provider: ConnectionProviderDescriptor,
) -> list[ConnectionCapability]:
    return [
        ConnectionCapability(
            id=item.id,
            kind=(
                ConnectionCapabilityKind.RESOURCE
                if item.risk == ConnectionCapabilityRisk.READ
                else ConnectionCapabilityKind.TOOL
            ),
            name=item.name,
            description=item.description,
            requires_approval=item.risk != ConnectionCapabilityRisk.READ,
            risk=item.risk,
            permissions=list(item.permissions),
        )
        for item in provider.capabilities
    ]


class ConnectionLifecycleService:
    def __init__(
        self,
        registry: Optional[ConnectionRegistry] = None,
        broker: Optional[CredentialBroker] = None,
    ) -> None:
        self.registry = registry or get_connection_registry()
        self.broker = broker or get_credential_broker()
        self.providers = {item.id: item for item in _provider_catalog()}

    def _sync_provider_tools(self, provider_id: str) -> None:
        try:
            from dataclasses import replace
            from agent.tool_registry import ToolRegistry

            records = [
                record
                for record in self.registry.list_unscoped()
                if record.provider == provider_id
                and record.enabled
                and record.health not in {
                    ConnectionHealth.UNHEALTHY,
                    ConnectionHealth.BLOCKED,
                    ConnectionHealth.DISABLED,
                    ConnectionHealth.MISSING_CONFIGURATION,
                }
            ]
            available = bool(records)
            health = (
                "healthy"
                if any(record.health == ConnectionHealth.HEALTHY for record in records)
                else "degraded"
                if records
                else "unavailable"
            )
            for entry in list(ToolRegistry.get_all().values()):
                if entry.connection_id != f"provider:{provider_id}":
                    continue
                ToolRegistry._put(
                    replace(
                        entry,
                        available=available,
                        health=health,
                        unavailable_reason=(
                            "" if available else f"No active {provider_id} Connection is registered"
                        ),
                    )
                )
        except Exception:
            pass

    def migrate_legacy_settings(self, runtime_config: Any) -> list[str]:
        """Project configured legacy integrations into explicit records once.

        This compatibility bridge does not use ALLOW flags as proof of
        connectivity. Credentials are copied into DPAPI and the resulting
        records remain degraded until an explicit provider probe succeeds.
        """
        existing_providers = {
            item.provider
            for item in self.registry.list_unscoped()
            if item.source_ref.startswith("legacy-settings:")
        }
        specs = {
            "notion": {
                "secrets": {"token": getattr(runtime_config, "notion_token", "")},
                "metadata": {
                    "default_database_id": getattr(runtime_config, "notion_default_database_id", "")
                },
                "enabled": bool(getattr(runtime_config, "allow_notion", False)),
            },
            "github": {
                "secrets": {"token": getattr(runtime_config, "github_token", "")},
                "metadata": {"default_repo": getattr(runtime_config, "github_default_repo", "")},
                "enabled": bool(getattr(runtime_config, "allow_github", False)),
            },
            "google_calendar": {
                "secrets": {
                    "credentials_path": getattr(runtime_config, "google_calendar_credentials_path", ""),
                    "token_path": getattr(runtime_config, "google_calendar_token_path", ""),
                },
                "metadata": {
                    "default_timezone": getattr(runtime_config, "calendar_default_timezone", "")
                },
                "enabled": bool(getattr(runtime_config, "allow_calendar", False)),
            },
            "spotify": {
                "secrets": {
                    "client_id": getattr(runtime_config, "spotify_client_id", ""),
                    "client_secret": getattr(runtime_config, "spotify_client_secret", ""),
                    "redirect_uri": getattr(runtime_config, "spotify_redirect_uri", ""),
                    "token_path": getattr(runtime_config, "spotify_token_path", ""),
                },
                "metadata": {},
                "enabled": bool(getattr(runtime_config, "allow_spotify", False)),
            },
            "home_assistant": {
                "secrets": {"token": getattr(runtime_config, "home_assistant_token", "")},
                "metadata": {"url": getattr(runtime_config, "home_assistant_url", "")},
                "enabled": bool(getattr(runtime_config, "allow_home_assistant", False)),
            },
        }
        migrated: list[str] = []
        for provider_id, spec in specs.items():
            if provider_id in existing_providers:
                continue
            provider = self.providers[provider_id]
            secrets = {
                key: value
                for key, value in dict(spec["secrets"]).items()
                if str(value or "").strip()
            }
            if not secrets:
                continue
            enabled = bool(spec["enabled"])
            reference = self.broker.put(
                secrets,
                label=f"EchoSpeak legacy {provider.name} connection",
            )
            record = ConnectionRecord(
                id=f"legacy-{provider_id}",
                kind=provider.kind,
                display_name=f"{provider.name} (legacy setup)",
                provider=provider_id,
                source_ref=f"legacy-settings:{provider_id}",
                enabled=enabled,
                health=ConnectionHealth.UNKNOWN if enabled else ConnectionHealth.DISABLED,
                authentication=ConnectionAuthentication.CONFIGURED,
                lifecycle=ConnectionLifecycle.DEGRADED if enabled else ConnectionLifecycle.DISABLED,
                credential_refs=[reference],
                scope=ConnectionScope(allow_global=True),
                capabilities=_connection_capabilities(provider),
                metadata={
                    key: value
                    for key, value in dict(spec["metadata"]).items()
                    if str(value or "").strip()
                },
                provenance={
                    "owner": "ConnectionRegistry",
                    "migration": "legacy_settings_v1",
                },
            )
            self.registry.register(record)
            self._sync_provider_tools(provider_id)
            migrated.append(record.id)
        return migrated

    def catalog(
        self,
        *,
        project_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        existing = (
            {
                item.provider: item
                for item in self.registry.list(project_id=project_id, session_id=session_id)
            }
            if str(project_id or "").strip()
            else {}
        )
        rows = []
        for provider in sorted(self.providers.values(), key=lambda item: (item.order, item.name)):
            connection = existing.get(provider.id)
            rows.append(
                {
                    **provider.model_dump(mode="json"),
                    "connection": connection.model_dump(mode="json") if connection else None,
                }
            )
        return rows

    def begin(
        self,
        *,
        provider_id: str,
        project_id: str,
        session_id: str,
        display_name: str = "",
        configuration: Optional[dict[str, Any]] = None,
        credentials: Optional[dict[str, Any]] = None,
        allow_global: bool = False,
    ) -> AuthorizationResult:
        provider = self.providers.get(str(provider_id or "").strip())
        if provider is None:
            raise ConnectionRegistryError("Unknown Connection provider")
        transaction_id = uuid.uuid4().hex
        config = dict(configuration or {})
        secret_values = dict(credentials or {})

        if provider.rollout_state == "requires_provider_adapter":
            return AuthorizationResult(
                transaction_id=transaction_id,
                provider_id=provider.id,
                status="authorization_required",
                message=(
                    f"{provider.name} requires its provider OAuth adapter. "
                    "No connection was created and no credential was requested."
                ),
            )

        connection_id = str(config.get("connection_id") or f"{provider.id}-{uuid.uuid4().hex[:10]}")
        credential_refs: list[str] = []
        if secret_values:
            credential_refs.append(
                self.broker.put(
                    secret_values,
                    label=f"EchoSpeak {provider.name} connection",
                )
            )

        metadata = {
            key: value
            for key, value in config.items()
            if key not in {"headers", "env", "token", "password", "client_secret", "private_key"}
        }
        for key in ("url", "endpoint"):
            value = str(metadata.get(key) or "").strip()
            if not value:
                continue
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None:
                raise ConnectionRegistryError(
                    "Connection URL userinfo must be supplied through the credential broker"
                )
        if provider.id == "obsidian":
            vault = Path(str(config.get("vault_path") or "")).expanduser().resolve()
            if not vault.is_dir():
                raise ConnectionRegistryError("Obsidian vault folder does not exist")
            metadata["vault_path"] = str(vault)

        scope = ConnectionScope(
            allow_global=bool(allow_global),
            project_ids=[] if allow_global else [project_id],
            session_ids=[] if allow_global else [session_id],
            filesystem_roots=(
                [str(metadata["vault_path"])]
                if provider.id == "obsidian"
                else []
            ),
            network_hosts=[],
            permissions=[
                permission
                for capability in provider.capabilities
                for permission in capability.permissions
            ],
        )
        auth = (
            ConnectionAuthentication.NONE
            if provider.authentication in {"none", "local_path"}
            else ConnectionAuthentication.CONFIGURED
            if credential_refs
            else ConnectionAuthentication.REQUIRED
        )
        record = ConnectionRecord(
            id=connection_id,
            kind=provider.kind,
            display_name=str(display_name or provider.name),
            provider=provider.id,
            source_ref=f"connection-catalog:{provider.id}",
            enabled=True,
            health=(
                ConnectionHealth.HEALTHY
                if provider.authentication in {"none", "local_path"}
                else ConnectionHealth.UNKNOWN
            ),
            authentication=auth,
            lifecycle=(
                ConnectionLifecycle.CONNECTED
                if provider.authentication in {"none", "local_path"}
                else ConnectionLifecycle.DEGRADED
            ),
            credential_refs=credential_refs,
            scope=scope,
            capabilities=_connection_capabilities(provider),
            metadata=metadata,
            provenance={"owner": "ConnectionRegistry", "created_by": "connection_lifecycle"},
            last_checked_at=time.time() if provider.authentication in {"none", "local_path"} else None,
        )
        self.registry.register(record)
        self._sync_provider_tools(provider.id)
        return AuthorizationResult(
            transaction_id=transaction_id,
            provider_id=provider.id,
            status=(
                "connected"
                if record.lifecycle == ConnectionLifecycle.CONNECTED
                else "probe_required"
            ),
            connection_id=record.id,
            message=(
                "Connection established."
                if record.lifecycle == ConnectionLifecycle.CONNECTED
                else "Credentials are stored; probe the provider before exposing capabilities."
            ),
        )

    def set_capability(
        self,
        connection_id: str,
        capability_id: str,
        *,
        expected_revision: int,
        enabled: bool,
    ) -> ConnectionRecord:
        record = self.registry.get_unscoped(connection_id)
        if record is None:
            raise ConnectionRegistryError("Connection not found")
        capabilities = [
            capability.model_copy(update={"enabled": bool(enabled)})
            if capability.id == capability_id
            else capability
            for capability in record.capabilities
        ]
        if not any(item.id == capability_id for item in record.capabilities):
            raise ConnectionRegistryError("Connection capability not found")
        updated = self.registry.update(
            connection_id,
            expected_revision=expected_revision,
            capabilities=capabilities,
        )
        self._sync_provider_tools(updated.provider)
        return updated

    def disable(self, connection_id: str, *, expected_revision: int) -> ConnectionRecord:
        updated = self.registry.update(
            connection_id,
            expected_revision=expected_revision,
            enabled=False,
            health=ConnectionHealth.DISABLED,
            lifecycle=ConnectionLifecycle.DISABLED,
        )
        self._sync_provider_tools(updated.provider)
        return updated

    def reconnect(self, connection_id: str, *, expected_revision: int) -> ConnectionRecord:
        record = self.registry.get_unscoped(connection_id)
        if record is None:
            raise ConnectionRegistryError("Connection not found")
        auth = (
            ConnectionAuthentication.CONFIGURED
            if record.credential_refs
            else ConnectionAuthentication.NONE
            if record.authentication == ConnectionAuthentication.NONE
            else ConnectionAuthentication.REQUIRED
        )
        updated = self.registry.update(
            connection_id,
            expected_revision=expected_revision,
            enabled=True,
            health=ConnectionHealth.UNKNOWN,
            authentication=auth,
            lifecycle=ConnectionLifecycle.DEGRADED,
            errors=[],
        )
        self._sync_provider_tools(updated.provider)
        return updated

    def disconnect(self, connection_id: str, *, expected_revision: int) -> ConnectionRecord:
        removed = self.registry.remove(connection_id, expected_revision=expected_revision)
        for reference in removed.credential_refs:
            self.broker.delete(reference)
        try:
            from agent.tool_registry import ToolRegistry

            owner = f"connection:{removed.id}"
            for name, entry in list(ToolRegistry.get_all().items()):
                if entry.owner == owner:
                    ToolRegistry.remove_owned(name, owner)
        except Exception:
            pass
        self._sync_provider_tools(removed.provider)
        return removed

    def probe(self, connection_id: str, *, expected_revision: int) -> ConnectionRecord:
        record = self.registry.get_unscoped(connection_id)
        if record is None:
            raise ConnectionRegistryError("Connection not found")
        healthy = False
        error = ""
        if record.provider == "obsidian":
            path = Path(str(record.metadata.get("vault_path") or "")).expanduser()
            healthy = path.is_dir()
            error = "" if healthy else "Configured vault folder is unavailable"
        elif record.kind == ConnectionKind.MCP_SERVER:
            try:
                from agent.mcp_client import get_mcp_manager

                result = get_mcp_manager().probe(record.id)
                healthy = bool(result.get("ok"))
                error = str(result.get("error") or "")
            except Exception as exc:
                error = str(exc)
        else:
            error = "Provider-specific live probe is not implemented"
        updated = self.registry.update(
            connection_id,
            expected_revision=expected_revision,
            health=ConnectionHealth.HEALTHY if healthy else ConnectionHealth.DEGRADED,
            lifecycle=ConnectionLifecycle.CONNECTED if healthy else ConnectionLifecycle.DEGRADED,
            errors=[] if healthy else [error],
            last_checked_at=time.time(),
        )
        self._sync_provider_tools(updated.provider)
        return updated


_SERVICE: Optional[ConnectionLifecycleService] = None


def get_connection_lifecycle_service() -> ConnectionLifecycleService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ConnectionLifecycleService()
    return _SERVICE
