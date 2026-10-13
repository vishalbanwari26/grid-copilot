"""Fleet-level signals from repeated overrides, without letting one override
rewrite the fleet.

An override stays with its asset. `ReviewedHistory` only recalls an asset's own
reviewed decisions, so an engineer correcting the agent on one transformer
changes how that transformer is judged next time and nothing else. Assets have
local context (history, sensor drift, loading) that makes a blanket change
risky.

When the same correction repeats across several transformers (the agent
proposed X, reviewers decided Y, on N different assets), that is a signal the
shared logic may be wrong: a deterministic tool, a rule, or the investigator
prompt. `find_patterns` surfaces it as an `OverridePattern` with a change
request that names the tools and prompt versions involved.

A pattern changes nothing by itself. A named reviewer approves it, rejects it,
or keeps watching, and must give a reason. Approval means an engineer changes
the tool or prompt as a new version, which is then evaluated like any other
change. Reviews go in a hash-chained JSONL log, the same tamper-evidence as the
decision log.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from grid_copilot.transformer.decisions import GENESIS, DecisionRecord

MIN_ASSETS = 3
REVIEW_VERDICTS = ("approve_change", "reject", "keep_watching")


@dataclass
class OverridePattern:
    proposed: str
    final: str
    assets: list[str]
    decision_ids: list[str]
    reasons: list[str]
    tools_cited: list[str]
    prompt_versions: list[str]
    first_at: str
    last_at: str

    @property
    def pattern_id(self) -> str:
        return f"{self.proposed}->{self.final}"

    @property
    def n_assets(self) -> int:
        return len(self.assets)

    def change_request(self) -> str:
        tools = ", ".join(self.tools_cited) or "none recorded"
        prompts = ", ".join(self.prompt_versions) or "unknown"
        return (
            f"On {self.n_assets} transformers ({', '.join(self.assets)}) the agent proposed {self.proposed} "
            f"and reviewers decided {self.final}. Review why the shared logic reaches {self.proposed} here: "
            f"the tools it relied on ({tools}) and the investigator prompt ({prompts}). Read each overridden "
            f"case before changing anything; a fix ships as a new tool or prompt version and is re-evaluated."
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "pattern_id": self.pattern_id, "n_assets": self.n_assets,
                "change_request": self.change_request()}


def find_patterns(records: list[DecisionRecord], min_assets: int = MIN_ASSETS) -> list[OverridePattern]:
    """Group overrides by (proposed, decided) class and keep the ones seen on at
    least `min_assets` different transformers. Accepts and defers are ignored;
    the same asset overridden twice still counts once."""
    groups: dict[tuple[str, str], list[DecisionRecord]] = {}
    for rec in records:
        if rec.verdict != "override":
            continue
        proposed = rec.proposal.get("fault_class", "")
        if proposed == rec.final_fault_class:
            continue
        groups.setdefault((proposed, rec.final_fault_class), []).append(rec)

    out = []
    for (proposed, final), recs in groups.items():
        assets = sorted({r.proposal["asset"] for r in recs})
        if len(assets) < min_assets:
            continue
        tools = sorted({e["tool"] for r in recs for e in r.proposal.get("evidence", []) if e.get("tool")})
        prompts = sorted({r.proposal.get("prompt_version", "") for r in recs} - {""})
        times = sorted(r.decided_at for r in recs)
        out.append(OverridePattern(
            proposed=proposed, final=final, assets=assets, decision_ids=[r.decision_id for r in recs],
            reasons=[r.reason for r in recs if r.reason], tools_cited=tools, prompt_versions=prompts,
            first_at=times[0], last_at=times[-1],
        ))
    return sorted(out, key=lambda p: (-p.n_assets, p.pattern_id))


@dataclass
class PatternReview:
    review_id: str
    pattern_id: str
    pattern: dict
    reviewer: str
    verdict: str  # approve_change | reject | keep_watching
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


class PatternReviewLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[PatternReview]:
        if not self.path.exists():
            return []
        return [PatternReview(**json.loads(line)) for line in self.path.read_text().splitlines() if line.strip()]

    def record(self, pattern: OverridePattern, reviewer: str, verdict: str, reason: str) -> PatternReview:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("a fleet-level review needs a named reviewer")
        if verdict not in REVIEW_VERDICTS:
            raise ValueError(f"verdict must be one of {REVIEW_VERDICTS}")
        if not reason.strip():
            raise ValueError("a fleet-level review needs a reason")
        prior = self.records()
        rec = PatternReview(
            review_id=str(uuid.uuid4()), pattern_id=pattern.pattern_id, pattern=pattern.to_dict(),
            reviewer=reviewer, verdict=verdict, reason=reason.strip(),
            decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            prev_hash=prior[-1].hash if prior else GENESIS,
        )
        rec.hash = rec.compute_hash()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(rec), sort_keys=True) + "\n")
        return rec

    def latest(self, pattern_id: str) -> PatternReview | None:
        recs = [r for r in self.records() if r.pattern_id == pattern_id]
        return recs[-1] if recs else None

    def status(self, pattern: OverridePattern) -> dict:
        """Where a pattern stands: open until someone reviews it, and flagged if
        more assets showed the same override after the last review."""
        last = self.latest(pattern.pattern_id)
        if last is None:
            return {"status": "open", "reviewer": None, "reason": None, "decided_at": None,
                    "assets_at_review": None, "new_since_review": pattern.n_assets}
        seen = set(last.pattern.get("assets", []))
        return {"status": last.verdict, "reviewer": last.reviewer, "reason": last.reason,
                "decided_at": last.decided_at, "assets_at_review": len(seen),
                "new_since_review": len(set(pattern.assets) - seen)}

    def verify_chain(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, rec in enumerate(self.records()):
            if rec.prev_hash != prev:
                return False, f"review {i} ({rec.review_id}) does not follow the review before it"
            if rec.compute_hash() != rec.hash:
                return False, f"review {i} ({rec.review_id}) was changed after it was written"
            prev = rec.hash
        return True, "chain intact"
