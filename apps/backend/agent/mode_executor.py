"""Mode-specific executor contracts.

`mode_controller.classify_turn_mode()` remains the source of truth.  This module
describes what each selected mode may do, how it should fail, and how logs should
be scoped.  The current agent loop consumes these contracts through tool masks;
future loop extraction should preserve the same profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agent.mode_controller import CodingPhaseName, ModeDecision, TurnMode


@dataclass(frozen=True)
class ModeExecutionProfile:
    mode: TurnMode
    executor_name: str
    behavior: str
    failure_policy: str
    log_scope: str
    may_search: bool = False
    may_read_files: bool = False
    may_write_files: bool = False
    may_create_projects: bool = False
    requires_evidence: bool = False
    requires_verification: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "executor_name": self.executor_name,
            "behavior": self.behavior,
            "failure_policy": self.failure_policy,
            "log_scope": self.log_scope,
            "may_search": self.may_search,
            "may_read_files": self.may_read_files,
            "may_write_files": self.may_write_files,
            "may_create_projects": self.may_create_projects,
            "requires_evidence": self.requires_evidence,
            "requires_verification": self.requires_verification,
        }


CHAT_PROFILE = ModeExecutionProfile(
    mode=TurnMode.CHAT,
    executor_name="chat_executor",
    behavior="Prefer a direct conversational answer without operational tools.",
    failure_policy="Return a concise conversational fallback when no tool is needed.",
    log_scope="mode.chat",
)

RESEARCH_PROFILE = ModeExecutionProfile(
    mode=TurnMode.TASK_RESEARCH,
    executor_name="research_executor",
    behavior="Evidence-gathering answer using read-only research tools.",
    failure_policy="Report uncertainty and missing evidence; do not invent facts or write files.",
    log_scope="mode.research",
    may_search=True,
    requires_evidence=True,
    requires_verification=True,
)

CODING_PROFILES: dict[CodingPhaseName, ModeExecutionProfile] = {
    CodingPhaseName.INSPECT: ModeExecutionProfile(
        mode=TurnMode.CODING,
        executor_name="coding_inspect_executor",
        behavior="Read project context and inspect relevant files only.",
        failure_policy="Ask for the missing project/path context; do not scaffold or write.",
        log_scope="mode.coding.inspect",
        may_read_files=True,
        requires_verification=True,
    ),
    CodingPhaseName.PLAN: ModeExecutionProfile(
        mode=TurnMode.CODING,
        executor_name="coding_plan_executor",
        behavior="Persist active project plan state and produce a plan. Read-only tools only.",
        failure_policy="Keep the project in plan phase and wait for approval before writing.",
        log_scope="mode.coding.plan",
        may_read_files=True,
        may_create_projects=True,
        requires_verification=True,
    ),
    CodingPhaseName.IMPLEMENT: ModeExecutionProfile(
        mode=TurnMode.CODING,
        executor_name="coding_implement_executor",
        behavior="Apply approved changes, then verify with project-local checks.",
        failure_policy="Stop on failed writes/tests and report the concrete blocker.",
        log_scope="mode.coding.implement",
        may_read_files=True,
        may_write_files=True,
        may_create_projects=True,
        requires_verification=True,
    ),
    CodingPhaseName.VERIFY: ModeExecutionProfile(
        mode=TurnMode.CODING,
        executor_name="coding_verify_executor",
        behavior="Run verification and inspect outputs without additional writes.",
        failure_policy="Report failing checks with command/output evidence.",
        log_scope="mode.coding.verify",
        may_read_files=True,
        requires_verification=True,
    ),
    CodingPhaseName.CONFIRM: ModeExecutionProfile(
        mode=TurnMode.CODING,
        executor_name="coding_confirm_executor",
        behavior="Handle confirmation state with read-only context.",
        failure_policy="Do not execute action tools without explicit confirmation.",
        log_scope="mode.coding.confirm",
        may_read_files=True,
        requires_verification=True,
    ),
    CodingPhaseName.SUMMARIZE: ModeExecutionProfile(
        mode=TurnMode.CODING,
        executor_name="coding_summarize_executor",
        behavior="Summarize completed code work from project state and verification output.",
        failure_policy="If verification is missing, say so instead of implying success.",
        log_scope="mode.coding.summarize",
        may_read_files=True,
        requires_verification=True,
    ),
}


def execution_profile_for(decision: ModeDecision) -> ModeExecutionProfile:
    if decision.mode == TurnMode.TASK_RESEARCH:
        return RESEARCH_PROFILE
    if decision.mode == TurnMode.CODING:
        phase = decision.coding_phase or CodingPhaseName.INSPECT
        profile = CODING_PROFILES.get(phase, CODING_PROFILES[CodingPhaseName.INSPECT])
        if "research" in decision.required_capabilities:
            return replace(
                profile,
                behavior=profile.behavior + " May gather read-only web evidence before coding actions.",
                may_search=True,
                requires_evidence=True,
            )
        return profile
    return CHAT_PROFILE
