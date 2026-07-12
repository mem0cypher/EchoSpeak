"""Context budgeting helpers for preserving model reasoning headroom."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional


_UNTRUSTED_INSTRUCTION_LINE = re.compile(
    r"(?i)^\s*(?:"
    r"ignore (?:all |any )?(?:previous|prior|system|developer) instructions?|"
    r"disregard (?:all |any )?(?:previous|prior|system|developer) instructions?|"
    r"(?:system|developer|assistant)\s*(?:message|prompt)?\s*:|"
    r"you are now\b|act as\b|"
    r"(?:call|invoke|execute|run) (?:the )?(?:tool|command|shell)\b|"
    r"reveal (?:the )?(?:system prompt|secret|credentials?|tokens?)\b"
    r")"
)


def sanitize_untrusted_context(text: str) -> str:
    """Redact instruction-shaped lines from files, pages, memories, and tool output.

    This is a deterministic boundary, not a semantic prompt-injection detector.
    User requests and explicitly pinned/profile memory are intentionally handled
    elsewhere and are not passed through this function.
    """
    lines: List[str] = []
    for line in str(text or "").splitlines():
        if _UNTRUSTED_INSTRUCTION_LINE.search(line):
            lines.append("[potential instruction from untrusted content redacted]")
        else:
            lines.append(line)
    return "\n".join(lines).strip()


def estimate_tokens(text: str) -> int:
    """Cheap cross-provider token estimate: roughly 4 chars per token."""
    s = str(text or "")
    if not s:
        return 0
    return max(1, (len(s) + 3) // 4)


def compress_text(text: str, max_chars: int, *, label: str = "content") -> str:
    """Deterministic compress: keep head + tail when over budget.

    Used for summarize/compact pressure stages so large tool dumps and history
    actually shrink instead of only being logged as over-budget.
    """
    raw = str(text or "").strip()
    if max_chars <= 0:
        return ""
    if len(raw) <= max_chars:
        return raw
    if max_chars < 80:
        return raw[:max_chars].rstrip() + "…"
    # Keep ~60% head / 30% tail with a marker in the middle.
    head_budget = max(40, int(max_chars * 0.60))
    tail_budget = max(20, max_chars - head_budget - 48)
    head = raw[:head_budget].rstrip()
    tail = raw[-tail_budget:].lstrip()
    return f"{head}\n...[{label} compressed for context headroom]...\n{tail}"


@dataclass
class ContextBlock:
    name: str
    text: str
    priority: int
    header: str = ""
    min_chars: int = 0
    protected: bool = False


@dataclass
class ContextBudgetReport:
    enabled: bool
    max_tokens: int
    reserve_tokens: int
    injectable_tokens: int
    used_tokens: int
    kept_blocks: List[str]
    trimmed_blocks: List[str]
    stage: str = "none"
    usage_ratio: float = 0.0
    protected_blocks: List[str] | None = None
    compressed_blocks: List[str] | None = None


class ContextBudgetManager:
    """Trim injectable context blocks by priority while preserving reserved headroom."""

    def __init__(self, *, context_window: int, reserve_tokens: int, enabled: bool = True):
        self.context_window = max(0, int(context_window or 0))
        self.reserve_tokens = max(0, int(reserve_tokens or 0))
        self.enabled = bool(enabled)

    @property
    def injectable_tokens(self) -> int:
        if self.context_window <= 0:
            return 0
        return max(0, self.context_window - self.reserve_tokens)

    def fit_blocks(self, blocks: Iterable[ContextBlock], *, overhead_tokens: int = 0) -> tuple[str, ContextBudgetReport]:
        material = [b for b in blocks if str(b.text or "").strip()]
        if not self.enabled or self.injectable_tokens <= 0:
            text = "\n\n".join(self._render_block(b) for b in material if b.text.strip()).strip()
            used = estimate_tokens(text)
            ratio = self._usage_ratio(used + max(0, int(overhead_tokens or 0)))
            return text, ContextBudgetReport(
                False,
                self.context_window,
                self.reserve_tokens,
                0,
                used,
                [b.name for b in material],
                [],
                self._stage_for_ratio(ratio),
                ratio,
                [b.name for b in material if b.protected],
                [],
            )

        budget = max(0, self.injectable_tokens - max(0, int(overhead_tokens or 0)))
        # Pre-pass: if unrestricted size would hit summarize/compact, compress non-protected first.
        unrestricted = sum(estimate_tokens(self._render_block(b)) for b in material) + max(0, int(overhead_tokens or 0))
        projected_stage = self._stage_for_ratio(self._usage_ratio(unrestricted))
        compressed_names: List[str] = []
        working: List[ContextBlock] = []
        for block in material:
            text = str(block.text or "").strip()
            if block.protected or projected_stage == "none":
                working.append(block)
                continue
            max_chars = self._stage_char_cap(projected_stage, block)
            if max_chars and len(text) > max_chars:
                compressed = compress_text(text, max_chars, label=block.name)
                working.append(
                    ContextBlock(
                        name=block.name,
                        text=compressed,
                        priority=block.priority,
                        header=block.header,
                        min_chars=block.min_chars,
                        protected=block.protected,
                    )
                )
                compressed_names.append(block.name)
            else:
                working.append(block)

        used = 0
        rendered: List[str] = []
        kept: List[str] = []
        trimmed: List[str] = []

        protected = [b for b in working if b.protected]
        normal = [b for b in working if not b.protected]
        ordered = sorted(protected, key=lambda b: b.priority) + sorted(normal, key=lambda b: b.priority)

        for block in ordered:
            raw_text = str(block.text or "").strip()
            rendered_block = self._render_block(block)
            need = estimate_tokens(rendered_block)
            if used + need <= budget:
                rendered.append(rendered_block)
                kept.append(block.name)
                used += need
                continue

            remaining_tokens = max(0, budget - used)
            remaining_chars = remaining_tokens * 4
            if block.protected:
                # Protected still ships, but compact-stage may hard-clip extreme overflow.
                if projected_stage == "compact" and need > remaining_tokens and remaining_chars >= 120:
                    header = str(block.header or block.name).strip()
                    body_budget = max(80, remaining_chars - len(header) - 20)
                    clipped = compress_text(raw_text, body_budget, label=block.name)
                    rendered.append(f"{header}:\n{clipped}" if header else clipped)
                    kept.append(block.name)
                    compressed_names.append(block.name)
                    used = budget
                else:
                    rendered.append(rendered_block)
                    kept.append(block.name)
                    used += need
                continue

            if remaining_chars >= max(80, int(block.min_chars or 0)):
                header = str(block.header or block.name).strip()
                body_budget = max(0, remaining_chars - len(header) - 20)
                clipped = compress_text(raw_text, body_budget, label=block.name) if body_budget < len(raw_text) else raw_text
                if clipped:
                    if body_budget < len(raw_text) and "...[" not in clipped:
                        clipped += "\n...[trimmed for context headroom]"
                    rendered.append(f"{header}:\n{clipped}" if header else clipped)
                    kept.append(block.name)
                    if body_budget < len(raw_text):
                        compressed_names.append(block.name)
                    used = budget
                else:
                    trimmed.append(block.name)
            else:
                trimmed.append(block.name)
            break

        # Any lower-priority blocks after the first overflow are trimmed.
        overflow_started = False
        for block in ordered:
            if block.name in kept or block.name in trimmed:
                if block.name in trimmed:
                    overflow_started = True
                continue
            if overflow_started or used >= budget:
                trimmed.append(block.name)

        ratio = self._usage_ratio(used + max(0, int(overhead_tokens or 0)))
        stage = self._stage_for_ratio(ratio)
        # If we already compressed due to projected pressure, report at least that stage.
        if projected_stage in {"summarize", "compact"} and stage == "none":
            stage = projected_stage
        elif projected_stage == "compact" and stage in {"none", "soft_trim", "summarize"}:
            stage = "compact"
        elif projected_stage == "summarize" and stage in {"none", "soft_trim"}:
            stage = "summarize"

        return "\n\n".join(rendered).strip(), ContextBudgetReport(
            True,
            self.context_window,
            self.reserve_tokens,
            budget,
            used,
            kept,
            trimmed,
            stage,
            ratio,
            [b.name for b in material if b.protected],
            list(dict.fromkeys(compressed_names)),
        )

    def fit_text(self, text: str, *, overhead_tokens: int = 0, label: str = "blob") -> tuple[str, ContextBudgetReport]:
        """Budget a single free-form blob (tool reinjection, Stage 5 history, etc.)."""
        block = ContextBlock(label, str(text or ""), priority=5, header="", min_chars=120, protected=False)
        return self.fit_blocks([block], overhead_tokens=overhead_tokens)

    def _stage_char_cap(self, stage: str, block: ContextBlock) -> Optional[int]:
        """Max body chars for non-protected blocks under pressure stages."""
        if stage == "compact":
            return max(160, int(block.min_chars or 0) or 200)
        if stage == "summarize":
            return max(400, int(block.min_chars or 0) or 500)
        if stage == "soft_trim":
            return max(1200, int(block.min_chars or 0) or 1200)
        return None

    def _render_block(self, block: ContextBlock) -> str:
        text = str(block.text or "").strip()
        header = str(block.header or block.name).strip()
        if not header:
            return text
        return f"{header}:\n{text}"

    def pressure_stage(self, blocks: Iterable[ContextBlock], *, overhead_tokens: int = 0) -> str:
        material = [b for b in blocks if str(b.text or "").strip()]
        used = sum(estimate_tokens(self._render_block(b)) for b in material) + max(0, int(overhead_tokens or 0))
        return self._stage_for_ratio(self._usage_ratio(used))

    def _usage_ratio(self, used_tokens: int) -> float:
        if self.context_window <= 0:
            return 0.0
        return max(0.0, min(2.0, float(used_tokens) / float(self.context_window)))

    def _stage_for_ratio(self, ratio: float) -> str:
        if ratio >= 0.85:
            return "compact"
        if ratio >= 0.65:
            return "summarize"
        if ratio >= 0.50:
            return "soft_trim"
        return "none"
