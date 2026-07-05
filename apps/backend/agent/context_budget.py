"""Context budgeting helpers for preserving model reasoning headroom."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


def estimate_tokens(text: str) -> int:
    """Cheap cross-provider token estimate: roughly 4 chars per token."""
    s = str(text or "")
    if not s:
        return 0
    return max(1, (len(s) + 3) // 4)


@dataclass
class ContextBlock:
    name: str
    text: str
    priority: int
    header: str = ""
    min_chars: int = 0


@dataclass
class ContextBudgetReport:
    enabled: bool
    max_tokens: int
    reserve_tokens: int
    injectable_tokens: int
    used_tokens: int
    kept_blocks: List[str]
    trimmed_blocks: List[str]


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
            return text, ContextBudgetReport(False, self.context_window, self.reserve_tokens, 0, used, [b.name for b in material], [])

        budget = max(0, self.injectable_tokens - max(0, int(overhead_tokens or 0)))
        used = 0
        rendered: List[str] = []
        kept: List[str] = []
        trimmed: List[str] = []

        for block in sorted(material, key=lambda b: b.priority):
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
            if remaining_chars >= max(80, int(block.min_chars or 0)):
                header = str(block.header or block.name).strip()
                body_budget = max(0, remaining_chars - len(header) - 20)
                clipped = raw_text[:body_budget].rstrip()
                if clipped:
                    clipped += "\n...[trimmed for context headroom]"
                    rendered.append(f"{header}:\n{clipped}" if header else clipped)
                    kept.append(block.name)
                    used = budget
                else:
                    trimmed.append(block.name)
            else:
                trimmed.append(block.name)
            break

        # Any lower-priority blocks after the first overflow are trimmed.
        overflow_started = False
        for block in sorted(material, key=lambda b: b.priority):
            if block.name in kept or block.name in trimmed:
                if block.name in trimmed:
                    overflow_started = True
                continue
            if overflow_started or used >= budget:
                trimmed.append(block.name)

        return "\n\n".join(rendered).strip(), ContextBudgetReport(
            True,
            self.context_window,
            self.reserve_tokens,
            budget,
            used,
            kept,
            trimmed,
        )

    def _render_block(self, block: ContextBlock) -> str:
        text = str(block.text or "").strip()
        header = str(block.header or block.name).strip()
        if not header:
            return text
        return f"{header}:\n{text}"

