"""Bounded, evidence-first workspace for long-running web research."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Callable


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@dataclass
class ResearchRound:
    query: str
    evidence: str


@dataclass
class ResearchWorkspace:
    question: str
    max_raw_rounds: int = 3
    rounds: list[ResearchRound] = field(default_factory=list)
    central_report: str = ""

    def add_round(self, query: str, evidence: str) -> None:
        self.rounds.append(ResearchRound(_compact(query), str(evidence or "").strip()))

    def needs_compaction(self) -> bool:
        return len(self.rounds) > max(1, int(self.max_raw_rounds))

    def compaction_prompt(self) -> str:
        packets = "\n\n".join(
            f"Round {idx + 1} - query: {round.query}\n{round.evidence[:5000]}"
            for idx, round in enumerate(self.rounds)
        )
        previous = f"Existing central report:\n{self.central_report}\n\n" if self.central_report else ""
        return (
            "You are maintaining an evidence ledger for a deep-research task.\n"
            f"Original question: {self.question}\n\n{previous}"
            "Use ONLY the supplied evidence packets. Produce a compact central report with:\n"
            "- confirmed findings, each carrying its source title/URL when available;\n"
            "- unresolved questions or conflicts;\n"
            "- no guessed bridges, background assumptions, or unsupported facts.\n"
            "Keep it below 1,600 characters.\n\n"
            f"Evidence packets:\n{packets}"
        )

    def compact(self, summarize: Callable[[str], str]) -> str:
        if not self.needs_compaction():
            return self.central_report
        summary = _compact(summarize(self.compaction_prompt()))
        if summary:
            self.central_report = summary[:1600]
        self.rounds = self.rounds[-1:]
        return self.central_report

    def context_for_next_round(self, max_chars: int = 2400) -> str:
        parts = [f"Original question: {self.question}"]
        if self.central_report:
            parts.append(f"Central evidence report:\n{self.central_report}")
        if self.rounds:
            latest = self.rounds[-1]
            parts.append(f"Latest raw evidence ({latest.query}):\n{latest.evidence[:1000]}")
        return "\n\n".join(parts)[:max_chars]
