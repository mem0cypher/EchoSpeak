from __future__ import annotations

import pytest

from agent.extension_packages import (
    PackageComponent,
    PackageComponentKind,
    PackageConnectionRequirement,
    PackageManifest,
    project_legacy_skill_package,
)


def test_package_and_connection_are_separate_contracts() -> None:
    manifest = PackageManifest(
        package_id="echo.calendar-tools",
        version="1.2.0",
        display_name="Calendar tools",
        components=[PackageComponent(
            component_id="tools",
            kind=PackageComponentKind.TOOL_PROVIDER,
            entrypoint="provider/tools.py",
            declared_tool_names=["calendar_list"],
        )],
        connection_requirements=[PackageConnectionRequirement(
            requirement_id="calendar-auth",
            kind="calendar",
            provider="google",
            capability_ids=["calendar.read"],
        )],
    )
    assert manifest.connection_requirements[0].kind.value == "calendar"
    assert manifest.components[0].kind == PackageComponentKind.TOOL_PROVIDER


def test_legacy_plugin_connection_cannot_be_requested_by_new_package() -> None:
    with pytest.raises(ValueError, match="legacy Connection kind"):
        PackageConnectionRequirement(
            requirement_id="bad",
            kind="plugin",
        )


def test_package_entrypoints_and_authority_are_bounded() -> None:
    with pytest.raises(ValueError, match="relative path"):
        PackageComponent(
            component_id="bad",
            kind=PackageComponentKind.TOOL_PROVIDER,
            entrypoint="../../outside.py",
        )
    with pytest.raises(ValueError, match="unrestricted authority"):
        PackageComponent(
            component_id="bad",
            kind=PackageComponentKind.TOOL_PROVIDER,
            entrypoint="tools.py",
            declared_capabilities=["unrestricted_shell"],
        )


def test_legacy_skill_projection_is_diagnostic_only() -> None:
    manifest = project_legacy_skill_package(
        skill_id="system_monitor",
        display_name="System Monitor",
        has_tools=False,
        has_pipeline_hook=True,
    )
    hook = next(
        item for item in manifest.components
        if item.kind == PackageComponentKind.LEGACY_PIPELINE_HOOK
    )
    assert hook.compatibility_only is True
