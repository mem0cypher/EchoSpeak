"""Bounded model conformance scenarios for an exact loaded model."""
from __future__ import annotations

import ast
import hashlib
import json
import operator
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from agent.identity import compile_echo_identity
from agent.model_adapters import AssembledModelResponse, ModelFamilyAdapter, get_family_adapter
from agent.model_contracts import (
    AgentDecision,
    DecisionKind,
    ToolDefinition,
    ToolOutcome,
    ToolUsePolicy,
)
from agent.model_control_plane import ModelExecutionControlPlane, ModelTurnEnvelopeCompiler


@dataclass(frozen=True)
class ConformanceScenario:
    id: str
    prompt: str
    tools: tuple[ToolDefinition, ...]
    executor: Callable[[str, dict[str, Any]], tuple[bool, str]]
    expected_tools: tuple[str, ...] = ()
    expected_answer_token: str = ""
    expect_blocked: bool = False


@dataclass
class ConformanceCaseResult:
    scenario_id: str
    passed: bool
    decision: str
    tool_names: list[str] = field(default_factory=list)
    reason: str = ""
    duration_ms: float = 0.0


@dataclass
class ModelConformanceReport:
    provider: str
    model_id: str
    family: str
    template: str
    adapter_version: str
    capabilities: dict[str, Any]
    cases: list[ConformanceCaseResult]
    recommended_max_exposed_tools: int
    created_at: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(case.passed for case in self.cases)

    def as_dict(self) -> dict[str, Any]:
        durations = sorted(float(case.duration_ms) for case in self.cases)
        passed_count = sum(1 for case in self.cases if case.passed)
        p50 = durations[len(durations) // 2] if durations else 0.0
        p95_index = min(
            len(durations) - 1,
            max(0, int(round((len(durations) - 1) * 0.95))),
        ) if durations else 0
        return {
            **self.__dict__,
            "passed": self.passed,
            "metrics": {
                "case_count": len(self.cases),
                "passed_count": passed_count,
                "pass_rate": (
                    round(passed_count / len(self.cases), 4)
                    if self.cases
                    else 0.0
                ),
                "p50_duration_ms": p50,
                "p95_duration_ms": durations[p95_index] if durations else 0.0,
                "sequential_tool_followup": next(
                    (
                        case.passed for case in self.cases
                        if case.scenario_id == "two_sequential_tools"
                    ),
                    False,
                ),
                "truthful_failure": next(
                    (
                        case.passed for case in self.cases
                        if case.scenario_id == "truthful_failed_tool"
                    ),
                    False,
                ),
            },
            "cases": [case.__dict__ for case in self.cases],
        }


class OpenAICompatibleStreamingTransport:
    def __init__(self, *, base_url: str, model_id: str, api_key: str = "not-needed", timeout: float = 180.0) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.model_id = model_id
        self.api_key = api_key
        self.timeout = timeout

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
        adapter: ModelFamilyAdapter,
        on_event=None,
        cancel=None,
    ) -> AssembledModelResponse:
        url = self.base_url
        if not url.endswith("/v1"):
            url += "/v1"
        events: list[dict[str, Any]] = []
        payload = {
            "model": self.model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": 0.0,
            "stream": True,
            # LM Studio family conformance needs room for models (notably
            # Gemma) that emit a separated reasoning channel before the call.
            "max_tokens": 512,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        with httpx.stream(
            "POST",
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if cancel and cancel():
                    return AssembledModelResponse(finish_reason="cancelled")
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                event = json.loads(body)
                events.append(event)
                if on_event:
                    choice = (event.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    calls = delta.get("tool_calls") or []
                    on_event({
                        "type": "stream_delta",
                        "content_chars": len(str(delta.get("content") or "")),
                        "argument_chars": sum(
                            len(str((item.get("function") or {}).get("arguments") or ""))
                            for item in calls if isinstance(item, dict)
                        ),
                        "tool": ",".join(
                            str((item.get("function") or {}).get("name") or "")
                            for item in calls if isinstance(item, dict)
                        ),
                    })
        return adapter.parse_stream(events)


def default_live_scenarios() -> list[ConformanceScenario]:
    calculate = ToolDefinition(
        name="calculate",
        description="Evaluate a basic arithmetic expression.",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )
    combine = ToolDefinition(
        name="combine_values",
        description="Combine two named values in a deterministic order.",
        parameters={
            "type": "object",
            "properties": {
                "left": {"type": "string"},
                "right": {"type": "string"},
            },
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    )
    lookup = ToolDefinition(
        name="lookup_number",
        description="Return a verified number for a supplied key.",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
    )
    failed = ToolDefinition(
        name="always_fails",
        description="A deterministic tool used to verify truthful failure handling.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(name: str, args: dict[str, Any]) -> tuple[bool, str]:
        if name == "calculate":
            expression = str(args.get("expression") or "")
            try:
                value = _safe_arithmetic(expression)
            except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                return False, "invalid expression"
            return True, str(value)
        if name == "combine_values":
            return True, f"{args.get('left', '')}|{args.get('right', '')}"
        if name == "lookup_number":
            return True, "7"
        return False, "deterministic failure"

    return [
        ConformanceScenario(
            id="single_calculator",
            prompt="Use the calculate tool to compute 17 * 19, then answer with the verified result.",
            tools=(calculate,), executor=execute, expected_tools=("calculate",), expected_answer_token="323",
        ),
        ConformanceScenario(
            id="multiple_arguments",
            prompt="Use combine_values with left='alpha' and right='beta', then report its verified result.",
            tools=(combine,), executor=execute, expected_tools=("combine_values",), expected_answer_token="alpha|beta",
        ),
        ConformanceScenario(
            id="returned_outcome",
            prompt="Use lookup_number with key='proof'. Answer with the exact verified number it returns.",
            tools=(lookup,), executor=execute, expected_tools=("lookup_number",), expected_answer_token="7",
        ),
        ConformanceScenario(
            id="two_sequential_tools",
            prompt=(
                "First call lookup_number with key='proof'. Then call calculate using the returned number times 6. "
                "Do not calculate before reading the first ToolOutcome; answer with the final verified result."
            ),
            tools=(lookup, calculate), executor=execute,
            expected_tools=("lookup_number", "calculate"), expected_answer_token="42",
        ),
        ConformanceScenario(
            id="truthful_failed_tool",
            prompt="Call always_fails once. Do not claim success or invent a result; block truthfully after its ToolOutcome.",
            tools=(failed,), executor=execute, expected_tools=("always_fails",), expect_blocked=True,
        ),
    ]


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_arithmetic(expression: str) -> int | float:
    """Evaluate the conformance fixture's tiny arithmetic grammar without eval."""
    if not expression or len(expression) > 128:
        raise ValueError("invalid expression")
    root = ast.parse(expression, mode="eval")

    def visit(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            return _BINARY_OPERATORS[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(node.op)](visit(node.operand))
        raise ValueError("invalid expression")

    value = visit(root)
    if abs(float(value)) > 1e100:
        raise OverflowError("result too large")
    return value


def run_live_conformance(
    *, provider: str, model_id: str, transport: Any,
    scenarios: Optional[list[ConformanceScenario]] = None,
) -> ModelConformanceReport:
    adapter = get_family_adapter(model_id, provider)
    compiler = ModelTurnEnvelopeCompiler()
    results: list[ConformanceCaseResult] = []
    for scenario in scenarios or default_live_scenarios():
        execution_id = str(uuid.uuid4())
        session_id = f"conformance-{uuid.uuid4()}"
        observed_tools: list[str] = []

        def envelope_factory(outcomes: list[ToolOutcome]):
            return compiler.compile(
                project_id="",
                session_id=session_id,
                turn_id=execution_id,
                execution_id=execution_id,
                request_id=execution_id,
                provider=provider,
                model_id=model_id,
                assistant_identity=compile_echo_identity("", provider=provider, model_id=model_id),
                objective=scenario.prompt,
                task_status="in_progress",
                current_plan_step=None,
                collected_inputs={},
                missing_inputs=[],
                latest_user_relation="new_work",
                latest_user_message=scenario.prompt,
                allowed_tools=scenario.tools,
                tool_use_policy=ToolUsePolicy.REQUIRED,
                relevant_memory=[],
                approval=None,
                tool_outcomes=outcomes,
                constraints=["deterministic disposable conformance tools only"],
            )

        def execute_tool(name: str, args: dict[str, Any]) -> ToolOutcome:
            observed_tools.append(name)
            success, output = scenario.executor(name, args)
            return ToolOutcome(
                tool_name=name,
                execution_id=execution_id,
                turn_id=execution_id,
                session_id=session_id,
                success=success,
                status="success" if success else "tool_failure",
                output=output if success else "",
                error_message="" if success else output,
                verification={"conformance_runtime": True, "observed_at": time.time()},
            )

        started = time.perf_counter()
        reason = ""
        try:
            decision, _trace = ModelExecutionControlPlane(max_loops=5).run(
                envelope_factory=envelope_factory,
                transport=transport,
                execute_tool=execute_tool,
            )
            expected_prefix = list(scenario.expected_tools)
            tools_ok = observed_tools[:len(expected_prefix)] == expected_prefix
            if scenario.expect_blocked:
                passed = tools_ok and decision.kind == DecisionKind.BLOCK
            else:
                passed = (
                    tools_ok
                    and decision.kind == DecisionKind.ANSWER
                    and scenario.expected_answer_token.casefold() in decision.message.casefold()
                )
            if not tools_ok:
                reason = f"expected tool sequence {expected_prefix}, observed {observed_tools}"
            elif not passed:
                reason = f"terminal decision={decision.kind.value} did not satisfy scenario"
        except Exception as exc:
            decision = AgentDecision(kind=DecisionKind.BLOCK, message=str(exc), reason_code=exc.__class__.__name__)
            passed = False
            reason = str(exc)
        results.append(ConformanceCaseResult(
            scenario_id=scenario.id,
            passed=passed,
            decision=decision.kind.value,
            tool_names=observed_tools,
            reason=reason,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
        ))
    passed_count = sum(1 for item in results if item.passed)
    maximum = 2 if passed_count == len(results) else 1 if passed_count >= 3 else 0
    return ModelConformanceReport(
        provider=provider,
        model_id=model_id,
        family=adapter.family.value,
        template=adapter.template,
        adapter_version=adapter.version,
        capabilities=adapter.capabilities.__dict__,
        cases=results,
        recommended_max_exposed_tools=maximum,
    )


def canonical_conformance_report_path(
    provider: str, model_id: str, *, root: Optional[Path] = None
) -> Path:
    if root is None:
        from config import DATA_DIR

        root = DATA_DIR / "model_conformance"
    identity = hashlib.sha256(
        f"{str(provider or '').strip().casefold()}:{str(model_id or '').strip()}".encode(
            "utf-8"
        )
    ).hexdigest()
    return Path(root) / f"{identity}.json"


def write_conformance_report(
    report: ModelConformanceReport, path: Optional[Path] = None
) -> Path:
    path = path or canonical_conformance_report_path(
        report.provider, report.model_id
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp.replace(path)
    return path


__all__ = [
    "ConformanceCaseResult",
    "ConformanceScenario",
    "canonical_conformance_report_path",
    "ModelConformanceReport",
    "OpenAICompatibleStreamingTransport",
    "default_live_scenarios",
    "run_live_conformance",
    "write_conformance_report",
]
