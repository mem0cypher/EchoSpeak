"""Secret-free Settings catalog projected from EchoSpeak's canonical owners.

This module is deliberately read-only.  It does not own provider selection,
credentials, Connections, tools, skills, or VoiceJobs; it turns those owners'
current records into one stable product-facing card contract.
"""

from __future__ import annotations

import importlib.util
import time
from typing import Any, Literal

from pydantic import BaseModel, Field


SettingsCatalogCategory = Literal[
    "models",
    "search_research",
    "voice_speech",
    "connections",
    "local_tools",
    "skills",
    "mcp",
]
SettingsCatalogStatus = Literal[
    "available",
    "needs_setup",
    "authorizing",
    "connected",
    "ready",
    "degraded",
    "reconnect_required",
    "disabled",
    "advanced_only",
]


class SettingsCatalogCapability(BaseModel):
    id: str
    label: str
    access: Literal["read", "write", "destructive", "runtime"] = "read"
    available: bool = True
    enabled: bool = True
    approval_required: bool = False


class SettingsCatalogAction(BaseModel):
    kind: Literal["connect", "configure", "manage", "advanced", "inspect", "none"]
    label: str
    enabled: bool = True


class SettingsCatalogCard(BaseModel):
    id: str
    category: SettingsCatalogCategory
    name: str
    description: str
    status: SettingsCatalogStatus
    status_label: str
    locality: Literal["local", "cloud", "hybrid", "varies"]
    cost_class: Literal["local", "free", "usage_based", "varies", "included"]
    data_path: str
    connected: bool = False
    ready: bool = False
    enabled: bool = True
    selected: bool = False
    capabilities: list[SettingsCatalogCapability] = Field(default_factory=list)
    last_checked_at: float | None = None
    scope_label: str = "Application"
    detail: str = ""
    issue: str = ""
    primary_action: SettingsCatalogAction
    secondary_action: SettingsCatalogAction | None = None


class SettingsCatalogProjection(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: float = Field(default_factory=time.time)
    cards: list[SettingsCatalogCard]
    owners: dict[str, str]


def _status_label(status: str) -> str:
    return str(status or "available").replace("_", " ").title()


def _capability(
    identifier: str,
    label: str,
    *,
    access: str = "read",
    available: bool = True,
    enabled: bool = True,
    approval_required: bool = False,
) -> SettingsCatalogCapability:
    return SettingsCatalogCapability(
        id=identifier,
        label=label,
        access=access,
        available=available,
        enabled=enabled,
        approval_required=approval_required,
    )


def _model_cards(runtime_config: Any, provider_info: dict[str, Any]) -> list[SettingsCatalogCard]:
    from agent.model_runtime import list_available_providers

    selected_provider = str(provider_info.get("provider") or "")
    selected_model = str(provider_info.get("model") or "")
    selected_ready = bool(provider_info.get("ready"))
    readiness_message = str(provider_info.get("readiness_message") or "").strip()
    cards: list[SettingsCatalogCard] = []
    for provider in list_available_providers():
        provider_id = str(provider.get("id") or "")
        local = bool(provider.get("local"))
        selected = provider_id == selected_provider
        credential_configured = (
            bool(str(getattr(runtime_config.openai, "api_key", "") or "").strip())
            if provider_id == "openai"
            else bool(str(getattr(runtime_config.gemini, "api_key", "") or "").strip())
            if provider_id == "gemini"
            else selected and bool(selected_model)
        )
        if selected and selected_ready:
            status: SettingsCatalogStatus = "ready"
        elif selected and credential_configured:
            status = "degraded"
        elif credential_configured:
            status = "connected"
        else:
            status = "available" if local else "needs_setup"
        issue = readiness_message if selected and not selected_ready else ""
        cards.append(
            SettingsCatalogCard(
                id=f"model:{provider_id}",
                category="models",
                name=str(provider.get("name") or provider_id),
                description=str(provider.get("description") or "Model provider"),
                status=status,
                status_label=_status_label(status),
                locality="local" if local else "cloud",
                cost_class="local" if local else "usage_based",
                data_path="Stays on this device" if local else "Sent to the selected provider",
                connected=credential_configured,
                ready=selected and selected_ready,
                selected=selected,
                capabilities=[
                    _capability("conversation", "Conversation", access="runtime"),
                    _capability("structured_turns", "Structured turn understanding", access="runtime"),
                    _capability("streaming", "Streaming responses", access="runtime"),
                ],
                last_checked_at=time.time() if selected else None,
                scope_label="Current Session" if selected else "Available to Sessions",
                detail=(
                    f"Selected model: {selected_model}" if selected and selected_model
                    else "Select and configure this provider for an individual Session."
                ),
                issue=issue,
                primary_action=SettingsCatalogAction(
                    kind="none",
                    label="Manage" if selected else "Configure",
                ),
            )
        )
    return cards


def _search_cards(runtime_config: Any) -> list[SettingsCatalogCard]:
    ddg_installed = bool(
        importlib.util.find_spec("ddgs") or importlib.util.find_spec("duckduckgo_search")
    )
    configured = str(getattr(runtime_config, "web_search_provider", "auto") or "auto").lower()
    brave_connected = bool(str(getattr(runtime_config, "brave_search_api_key", "") or "").strip())
    searxng_connected = bool(str(getattr(runtime_config, "searxng_base_url", "") or "").strip())
    sports_connected = bool(str(getattr(runtime_config, "odds_api_key", "") or "").strip())
    sports_enabled = bool(getattr(runtime_config, "sports_live_enabled", False))
    specs = [
        (
            "duckduckgo", "DuckDuckGo", "General web discovery without an account.", ddg_installed,
            configured in {"auto", "duckduckgo", "ddg"}, "cloud", "free", "Queries are sent to DuckDuckGo",
        ),
        (
            "brave", "Brave Search", "Independent search index for current web research.", brave_connected,
            configured == "brave", "cloud", "usage_based", "Queries are sent to Brave Search",
        ),
        (
            "searxng", "SearXNG", "Self-hosted or private metasearch endpoint.", searxng_connected,
            configured == "searxng", "hybrid", "varies", "Queries use your configured SearXNG service",
        ),
    ]
    cards: list[SettingsCatalogCard] = []
    for provider_id, name, description, connected, selected, locality, cost, data_path in specs:
        status: SettingsCatalogStatus = "ready" if connected and selected else "connected" if connected else "needs_setup"
        if provider_id == "duckduckgo" and not connected:
            status = "degraded"
        cards.append(
            SettingsCatalogCard(
                id=f"search:{provider_id}",
                category="search_research",
                name=name,
                description=description,
                status=status,
                status_label=_status_label(status),
                locality=locality,
                cost_class=cost,
                data_path=data_path,
                connected=connected,
                ready=connected and selected,
                selected=selected,
                capabilities=[
                    _capability("search.discovery", "Search discovery"),
                    _capability("search.sources", "Source provenance"),
                ],
                detail="The TaskRun scheduler chooses one provider-attributable acquisition per attempt.",
                issue="Search package is not installed." if provider_id == "duckduckgo" and not connected else "",
                primary_action=SettingsCatalogAction(kind="configure", label="Configure"),
            )
        )
    sports_status: SettingsCatalogStatus = (
        "disabled" if not sports_enabled else "connected" if sports_connected else "needs_setup"
    )
    cards.append(
        SettingsCatalogCard(
            id="search:structured-sports",
            category="search_research",
            name="Structured sports data",
            description="Current schedules, scores, and odds through the configured structured provider.",
            status=sports_status,
            status_label=_status_label(sports_status),
            locality="cloud",
            cost_class="usage_based",
            data_path="Sports lookups are sent to the configured provider",
            connected=sports_connected,
            ready=sports_connected and sports_enabled,
            enabled=sports_enabled,
            capabilities=[_capability("sports.live", "Live sports data")],
            detail="Structured data is preferred when it can cover the requested fields.",
            primary_action=SettingsCatalogAction(kind="configure", label="Configure"),
        )
    )
    return cards


def _voice_cards(runtime_config: Any) -> list[SettingsCatalogCard]:
    from agent.voice_runtime import default_voice_provider, voice_provider_statuses

    presentation = {
        "windows-sapi": ("Windows Speech", "Local dictation and speech playback using installed Windows voices and language packs."),
        "faster-whisper-local": ("Faster Whisper", "Local speech recognition with an explicitly selected model."),
        "whisper-cpp-local": ("Whisper.cpp", "Local speech recognition through a reviewed native runtime."),
        "piper-local": ("Piper", "Local text-to-speech with an explicitly selected voice model."),
        "personaplex": ("PersonaPlex", "Experimental full-duplex model retained as a disabled compatibility option."),
        "openai-audio": ("OpenAI Audio", "Cloud speech and realtime audio through explicit upload and cost approval."),
    }
    cards: list[SettingsCatalogCard] = []
    try:
        providers = voice_provider_statuses()
    except Exception:
        return [
            SettingsCatalogCard(
                id="voice:runtime",
                category="voice_speech",
                name="Voice runtime",
                description="Local and cloud speech capability discovery.",
                status="degraded",
                status_label="Degraded",
                locality="varies",
                cost_class="varies",
                data_path="No audio was sent while reading this status",
                issue="Voice capability status could not be read. Use Advanced for bounded diagnostics.",
                primary_action=SettingsCatalogAction(kind="advanced", label="Advanced"),
            )
        ]
    for provider in providers:
        cloud_selected = (
            provider.locality == "cloud"
            and str(getattr(runtime_config, "voice_cloud_provider", "") or "").strip() == provider.id
        )
        selected_operations = {
            operation
            for operation in provider.operations
            if default_voice_provider(operation) == provider.id
        }
        all_operations_ready = bool(provider.operations) and all(
            provider.operation_readiness.get(operation, False)
            for operation in provider.operations
        )
        if provider.execution_ready and all_operations_ready:
            status: SettingsCatalogStatus = "ready"
        elif provider.execution_ready:
            status = "degraded"
        elif cloud_selected and not provider.detected:
            status = "reconnect_required"
        elif cloud_selected:
            status = "degraded"
        elif provider.locality == "cloud" and provider.detected:
            status = "connected"
        elif provider.configured:
            status = "degraded"
        elif provider.detected:
            status = "available"
        else:
            status = "needs_setup"
        name, description = presentation.get(
            provider.id,
            (provider.id.replace("-", " ").title(), "Voice and speech provider"),
        )
        cards.append(
            SettingsCatalogCard(
                id=f"voice:{provider.id}",
                category="voice_speech",
                name=name,
                description=description,
                status=status,
                status_label=_status_label(status),
                locality=provider.locality,
                cost_class="local" if provider.locality == "local" else "usage_based",
                data_path=(
                    "Audio stays on this device"
                    if provider.locality == "local"
                    else "Audio may be sent only after explicit opt-in and approval"
                    if cloud_selected
                    else "Audio stays on this device until cloud audio is explicitly enabled"
                ),
                connected=provider.detected if provider.locality == "cloud" else provider.configured,
                ready=provider.execution_ready,
                selected=bool(selected_operations) or cloud_selected,
                capabilities=[
                    _capability(
                        f"voice.{operation}",
                        operation.replace("_", " ").title(),
                        access="runtime",
                        available=provider.operation_readiness.get(operation, False),
                    )
                    for operation in provider.operations
                ],
                last_checked_at=time.time(),
                detail=(
                    "Cloud audio is explicitly opted in, but no audio will leave this device until its governed adapter and approval boundary are ready."
                    if cloud_selected
                    else "Selected for " + ", ".join(sorted(item.replace("_", " ") for item in selected_operations)) + "."
                    if selected_operations
                    else "Streaming and interruption are supported by this adapter."
                    if provider.supports_streaming and provider.supports_barge_in
                    else "Voice transport and agent speech jobs share the governed VoiceJob record boundary."
                ),
                issue=(
                    "Some speech operations are available, while others still need local setup."
                    if provider.execution_ready and not all_operations_ready
                    else "Detected, but the governed execution adapter is not ready."
                    if provider.detected and not provider.execution_ready
                    else "Not available on this device."
                    if not provider.detected
                    else ""
                ),
                primary_action=SettingsCatalogAction(kind="configure", label="Configure"),
            )
        )
    return cards


def _connection_cards(connection_catalog: list[dict[str, Any]]) -> list[SettingsCatalogCard]:
    cards: list[SettingsCatalogCard] = []
    for provider in connection_catalog:
        if str(provider.get("id") or "") == "custom_mcp":
            continue
        connection = provider.get("connection") or {}
        lifecycle = str(connection.get("lifecycle") or "")
        health = str(connection.get("health") or "")
        rollout = str(provider.get("rollout_state") or "available")
        if lifecycle == "connected" and health == "healthy":
            status: SettingsCatalogStatus = "ready"
        elif lifecycle == "authorizing":
            status = "authorizing"
        elif lifecycle == "reconnect_required":
            status = "reconnect_required"
        elif lifecycle == "disabled":
            status = "disabled"
        elif connection:
            status = "connected" if connection.get("credential_configured") else "degraded"
        elif rollout in {"advanced", "requires_provider_adapter"}:
            status = "advanced_only"
        else:
            status = "available"
        capabilities = connection.get("capabilities") or provider.get("capabilities") or []
        scope = connection.get("scope") or {}
        cards.append(
            SettingsCatalogCard(
                id=f"connection:{provider.get('id')}",
                category="connections",
                name=str(provider.get("name") or provider.get("id") or "Connection"),
                description=str(provider.get("description") or "External capability"),
                status=status,
                status_label=_status_label(status),
                locality="local" if str(provider.get("kind")) == "local_application" else "cloud",
                cost_class="varies",
                data_path="Project-scoped local data" if str(provider.get("kind")) == "local_application" else "Data is exchanged with this service",
                connected=bool(connection),
                ready=status == "ready",
                enabled=bool(connection.get("enabled", True)) if connection else True,
                capabilities=[
                    _capability(
                        str(item.get("id") or "capability"),
                        str(item.get("name") or item.get("id") or "Capability"),
                        access=str(item.get("risk") or "read"),
                        available=bool(item.get("available", True)),
                        enabled=bool(item.get("enabled", True)),
                        approval_required=bool(item.get("requires_approval")),
                    )
                    for item in capabilities
                ],
                last_checked_at=connection.get("last_checked_at"),
                scope_label="All Projects" if scope.get("allow_global") else "Active Project and Session",
                detail="Authentication is configured; readiness still requires a successful capability probe." if connection and status != "ready" else "",
                issue=(
                    "The most recent connection check did not succeed. Use Advanced for bounded diagnostics."
                    if connection.get("errors")
                    else ""
                ),
                primary_action=(
                    SettingsCatalogAction(kind="none", label="Manage", enabled=False)
                    if connection
                    else SettingsCatalogAction(
                        kind="connect",
                        label="Connect",
                        enabled=rollout == "available",
                    )
                ),
                secondary_action=SettingsCatalogAction(kind="advanced", label="Advanced"),
            )
        )
    return cards


def _tool_cards(runtime_config: Any) -> list[SettingsCatalogCard]:
    from agent.tool_registry import ToolRegistry

    enabled_names = {
        str(getattr(func, "name", "") or getattr(func, "__name__", ""))
        for func in ToolRegistry.get_config_filtered_funcs(runtime_config)
    }
    cards: list[SettingsCatalogCard] = []
    for entry in sorted(ToolRegistry.get_all().values(), key=lambda item: (item.category, item.name)):
        if entry.origin == "mcp":
            continue
        ready = entry.available and entry.name in enabled_names
        status: SettingsCatalogStatus = "ready" if ready else "disabled" if entry.available else "degraded"
        cards.append(
            SettingsCatalogCard(
                id=f"tool:{entry.name}",
                category="local_tools",
                name=entry.name.replace("_", " ").title(),
                description=entry.description,
                status=status,
                status_label=_status_label(status),
                locality="local",
                cost_class="included",
                data_path="Runs inside the governed EchoSpeak runtime",
                connected=True,
                ready=ready,
                enabled=ready,
                capabilities=[
                    _capability(
                        entry.name,
                        entry.category.replace("_", " ").title(),
                        access="destructive" if entry.risk_level == "destructive" else "write" if entry.is_action else "read",
                        available=entry.available,
                        approval_required=bool(entry.approval_required or entry.is_action),
                    )
                ],
                scope_label="Current runtime inventory",
                detail=f"Owned by {entry.owner}." if entry.owner else "",
                issue="This tool is not currently available in the active runtime." if entry.unavailable_reason else "",
                primary_action=SettingsCatalogAction(kind="none", label="Inspect"),
                secondary_action=SettingsCatalogAction(kind="advanced", label="Advanced"),
            )
        )
    return cards


def _skill_cards() -> list[SettingsCatalogCard]:
    from agent.skill_status_audit import audit_all_skills

    cards: list[SettingsCatalogCard] = []
    try:
        skills = audit_all_skills(
            available_capabilities={"approvals", "research"},
            available_artifacts=set(),
        )
    except Exception:
        return [
            SettingsCatalogCard(
                id="skill:registry",
                category="skills",
                name="Skill registry",
                description="Reviewed workflow packages available to Echo.",
                status="degraded",
                status_label="Degraded",
                locality="local",
                cost_class="included",
                data_path="Skill packages stay on this device",
                issue="Skill status could not be read. Use Advanced for bounded diagnostics.",
                primary_action=SettingsCatalogAction(kind="advanced", label="Advanced"),
            )
        ]
    for skill in skills:
        raw_status = str(skill.get("status") or "invalid")
        status: SettingsCatalogStatus = (
            "ready" if raw_status == "executable"
            else "disabled" if raw_status == "disabled"
            else "degraded" if raw_status.startswith("blocked")
            else "advanced_only"
        )
        required_tools = [str(item) for item in skill.get("required_tools") or []]
        cards.append(
            SettingsCatalogCard(
                id=f"skill:{skill.get('id')}",
                category="skills",
                name=str(skill.get("name") or skill.get("id") or "Skill"),
                description="A reviewed EchoSpeak workflow package.",
                status=status,
                status_label=_status_label(status),
                locality="local",
                cost_class="included",
                data_path="Skill instructions stay in the local package registry",
                connected=True,
                ready=status == "ready",
                enabled=status != "disabled",
                capabilities=[_capability(f"tool:{tool}", tool.replace("_", " ").title(), access="runtime") for tool in required_tools],
                scope_label="Runtime skill registry",
                detail=f"Version {skill.get('version') or 'unknown'} · {skill.get('disposition') or raw_status}",
                issue="One or more required capabilities are unavailable." if skill.get("reasons") else "",
                primary_action=SettingsCatalogAction(kind="none", label="Inspect"),
                secondary_action=SettingsCatalogAction(kind="advanced", label="Advanced"),
            )
        )
    return cards


def _mcp_cards(connection_catalog: list[dict[str, Any]]) -> list[SettingsCatalogCard]:
    from agent.tool_registry import ToolRegistry

    provider = next((item for item in connection_catalog if str(item.get("id")) == "custom_mcp"), {})
    connection = provider.get("connection") or {}
    mcp_tools = [entry for entry in ToolRegistry.get_all().values() if entry.origin == "mcp"]
    connected = bool(connection)
    ready = connected and str(connection.get("health") or "") == "healthy"
    status: SettingsCatalogStatus = "ready" if ready else "connected" if connected else "advanced_only"
    return [
        SettingsCatalogCard(
            id="mcp:servers",
            category="mcp",
            name="MCP servers",
            description="Reviewed MCP transports and the tools discovered from them.",
            status=status,
            status_label=_status_label(status),
            locality="varies",
            cost_class="varies",
            data_path="Depends on each configured MCP server",
            connected=connected,
            ready=ready,
            capabilities=[
                _capability(
                    f"mcp:{entry.name}",
                    entry.name.replace("_", " ").title(),
                    access="write" if entry.is_action else "read",
                    available=entry.available,
                    approval_required=bool(entry.approval_required or entry.is_action),
                )
                for entry in sorted(mcp_tools, key=lambda item: item.name)
            ],
            last_checked_at=connection.get("last_checked_at"),
            scope_label="Active Project and Session",
            detail=f"{len(mcp_tools)} discovered tool{'s' if len(mcp_tools) != 1 else ''} in the current inventory.",
            issue=(
                "The most recent MCP check did not succeed. Use Advanced for bounded diagnostics."
                if connection.get("errors")
                else ""
            ),
            primary_action=SettingsCatalogAction(kind="advanced", label="Advanced"),
        )
    ]


def build_settings_catalog(
    *,
    runtime_config: Any,
    provider_info: dict[str, Any],
    connection_catalog: list[dict[str, Any]],
) -> SettingsCatalogProjection:
    """Build one bounded projection without copying secret or transport fields."""

    cards = [
        *_model_cards(runtime_config, provider_info),
        *_search_cards(runtime_config),
        *_voice_cards(runtime_config),
        *_connection_cards(connection_catalog),
        *_tool_cards(runtime_config),
        *_skill_cards(),
        *_mcp_cards(connection_catalog),
    ]
    return SettingsCatalogProjection(
        cards=cards,
        owners={
            "models": "SessionModelBinding and model_runtime",
            "search_research": "CanonicalSemanticRuntime capability snapshot",
            "voice_speech": "VoiceJobStore and voice_runtime",
            "connections": "ConnectionRegistry and CredentialBroker",
            "local_tools": "ToolRegistry",
            "skills": "SkillsRegistry",
            "mcp": "ConnectionRegistry, MCP manager, and ToolRegistry",
        },
    )
