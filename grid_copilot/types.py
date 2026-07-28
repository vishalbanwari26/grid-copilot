"""Core domain types shared across the pipeline.

These dataclasses are the vocabulary every stage speaks: the ingest layer emits
`Reading`s, the detector emits an `Anomaly`, the investigation tools produce
`Evidence`, the agent forms a `Hypothesis`, and the run ends in an
`IncidentReport`. Keeping them here (and dependency-free) means no module has to
import another just to agree on a shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Reading:
    """One timestamped sample from one asset, across all of its signals."""

    ts: datetime
    asset: str
    values: dict[str, float]


@dataclass
class Anomaly:
    """A detector's claim that something is off, plus the window it saw.

    `true_cause` is populated only for synthetic/labeled data; it is the ground
    truth the eval harness scores the agent's hypothesis against. It is never
    shown to the agent.
    """

    asset: str
    ts: datetime
    signal: str  # the signal whose value tripped the detector
    score: float  # magnitude of the deviation (e.g. |z-score|)
    detector: str
    window: list[Reading] = field(default_factory=list)
    true_cause: str | None = None


@dataclass
class Evidence:
    """One piece of investigation output, attributed to the tool that found it.

    `citations` are opaque handles (doc ids, prior-incident ids) that the final
    report renders as references, so every claim traces back to a source.
    """

    source: str  # tool name that produced this
    summary: str
    citations: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


@dataclass
class Hypothesis:
    root_cause: str
    confidence: float
    reasoning: str


@dataclass
class IncidentReport:
    asset: str
    ts: datetime
    trigger_signal: str
    score: float
    hypothesis: Hypothesis
    evidence: list[Evidence]
    verdict: str  # critic's call: "accept" or "revise"
    narrative: str

    def summary_line(self) -> str:
        """One-liner used as the memory key/value when persisting the incident."""
        return (
            f"{self.asset}: {self.hypothesis.root_cause} "
            f"(trigger={self.trigger_signal}, score={self.score:.1f})"
        )

    def to_markdown(self) -> str:
        cites = sorted({c for e in self.evidence for c in e.citations})
        lines = [
            f"# Incident report: {self.asset}",
            "",
            f"- **Time:** {self.ts.isoformat()}",
            f"- **Trigger signal:** `{self.trigger_signal}` (deviation score {self.score:.1f})",
            f"- **Root cause (hypothesis):** {self.hypothesis.root_cause}",
            f"- **Confidence:** {self.hypothesis.confidence:.0%}",
            f"- **Critic verdict:** {self.verdict}",
            "",
            "## Reasoning",
            self.hypothesis.reasoning,
            "",
            "## Evidence",
        ]
        for e in self.evidence:
            cite = f" {' '.join('[' + c + ']' for c in e.citations)}" if e.citations else ""
            lines.append(f"- **{e.source}:** {e.summary}{cite}")
        if cites:
            lines += ["", "## References", *[f"- [{c}]" for c in cites]]
        lines += ["", "## Narrative", self.narrative]
        return "\n".join(lines)
