"""Decision records: the agent proposes, a named person decides.

An investigation ends in a *proposal*. It becomes a decision only when an
engineer accepts it, overrides it (with a reason, which is required), or defers
it. Every record carries what the decision was based on: the alarm, the evidence
summaries, the data-quality grades at the time, the model and prompt versions,
and the tool list, so a decision can be explained and audited later.

Records are appended to a JSONL log where each record includes the hash of the
previous one. Editing or deleting an old record breaks the chain, and
`verify_chain` says where. This is tamper-evidence, not access control.

Only reviewed decisions become asset history. `ReviewedHistory` is the
`IncidentStore` the investigator recalls from: it returns decisions a person
signed off (and seeded maintenance records), never the agent's own unreviewed
proposals, so an agent cannot learn from its own unchecked guesses.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from grid_copilot.memory.store import PriorIncident
from grid_copilot.types import IncidentReport

PROMPT_VERSION = "transformer-investigator-v1"
VERDICTS = ("accept", "override", "defer")
GENESIS = "0" * 64


@dataclass
class Proposal:
    proposal_id: str
    asset: str
    alarm: str
    alarm_ts: str
    fault_class: str
    subsystem: str
    urgency: str
    root_cause: str
    recommended_action: str
    would_change_conclusion: str
    confidence: float
    reasoning: str
    evidence: list[dict]
    data_quality: str
    model: str
    prompt_version: str = PROMPT_VERSION
    validation: str = "passed"
    critic: str = "accept"

    @classmethod
    def from_report(cls, report: IncidentReport, alarm_code: str, model: str) -> "Proposal":
        d = report.hypothesis.details
        dq = next((e.summary for e in report.evidence if e.source == "data_quality"), "not run")
        return cls(
            proposal_id=str(uuid.uuid4()), asset=report.asset, alarm=alarm_code, alarm_ts=report.ts.isoformat(),
            fault_class=str(d.get("fault_class", "")), subsystem=str(d.get("subsystem", "")),
            urgency=str(d.get("urgency", "")), root_cause=report.hypothesis.root_cause,
            recommended_action=str(d.get("recommended_action", "")),
            would_change_conclusion=str(d.get("would_change_conclusion", "")),
            confidence=report.hypothesis.confidence, reasoning=report.hypothesis.reasoning,
            evidence=[{"tool": e.source, "summary": e.summary, "citations": e.citations} for e in report.evidence],
            data_quality=dq, model=model, validation=str(d.get("validation", "passed")), critic=report.verdict,
        )


@dataclass
class DecisionRecord:
    decision_id: str
    proposal: dict
    reviewer: str
    verdict: str  # accept | override | defer
    final_fault_class: str
    final_action: str
    reason: str
    decided_at: str
    prev_hash: str = GENESIS
    hash: str = ""

    def body(self) -> dict:
        d = asdict(self)
        d.pop("hash")
        return d

    def compute_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True).encode()).hexdigest()


class DecisionLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[DecisionRecord]:
        if not self.path.exists():
            return []
        return [DecisionRecord(**json.loads(line)) for line in self.path.read_text().splitlines() if line.strip()]

    def record(self, proposal: Proposal, reviewer: str, verdict: str, reason: str = "",
               final_fault_class: str | None = None, final_action: str | None = None) -> DecisionRecord:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("a decision needs a named reviewer")
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}")
        if verdict in ("override", "defer") and not reason.strip():
            raise ValueError(f"an {verdict} needs a reason")
        if verdict == "override" and not final_fault_class:
            raise ValueError("an override needs the fault class the reviewer decided on")
        prior = self.records()
        rec = DecisionRecord(
            decision_id=str(uuid.uuid4()), proposal=asdict(proposal), reviewer=reviewer, verdict=verdict,
            final_fault_class=final_fault_class if verdict == "override" else proposal.fault_class,
            final_action=final_action or proposal.recommended_action, reason=reason.strip(),
            decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            prev_hash=prior[-1].hash if prior else GENESIS,
        )
        rec.hash = rec.compute_hash()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(rec), sort_keys=True) + "\n")
        return rec

    def verify_chain(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, rec in enumerate(self.records()):
            if rec.prev_hash != prev:
                return False, f"record {i} ({rec.decision_id}) does not follow the record before it"
            if rec.compute_hash() != rec.hash:
                return False, f"record {i} ({rec.decision_id}) was changed after it was written"
            prev = rec.hash
        return True, "chain intact"


@dataclass
class ReviewedHistory:
    """Asset history for the recall tool: maintenance records plus decisions a
    person reviewed. The agent's own proposals are not stored here."""

    log: DecisionLog | None = None
    maintenance: dict[str, list[PriorIncident]] = field(default_factory=dict)

    def add_maintenance(self, asset: str, day: str, text: str) -> None:
        items = self.maintenance.setdefault(asset, [])
        items.append(PriorIncident(f"MNT-{asset}-{len(items) + 1}", asset, text, datetime.fromisoformat(day)))

    def remember(self, report: IncidentReport) -> str:
        # Deliberately a no-op: a proposal is not history until someone reviews it.
        return "unreviewed"

    def recall(self, asset: str, query: str, k: int = 3) -> list[PriorIncident]:
        out = list(self.maintenance.get(asset, []))
        if self.log is not None:
            for rec in self.log.records():
                if rec.proposal["asset"] != asset or rec.verdict == "defer":
                    continue
                what = (f"{rec.final_fault_class} ({rec.verdict} by {rec.reviewer}"
                        + (f": {rec.reason}" if rec.reason else "") + f"); action: {rec.final_action}")
                out.append(PriorIncident(f"DEC-{rec.decision_id[:8]}", asset, what,
                                         datetime.fromisoformat(rec.proposal["alarm_ts"])))
        return sorted(out, key=lambda p: p.occurred_at, reverse=True)[:k]
