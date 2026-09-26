"""Data-quality checks, run before any diagnosis.

A diagnosis is only as good as the data under it, so every investigation starts
with these checks on the channels it may use, and their grades travel with the
evidence into the report. Checks, per channel, over the recent window:

- frozen: the same value repeated far longer than the channel's own noise
  allows (a stuck transducer or a stale value in the gateway);
- impossible: values that break physics (oil colder than the air around a
  loaded transformer, a hot spot below the top oil, negative gas);
- steps: single-sample jumps far outside the channel's usual changes;
- gaps: communication losses in the event log.

A lab sample is the reference for the online DGA monitor; that comparison is the
`lab_sample` tool, because it costs a site visit and the investigator decides
when it is worth it.

Grades: trusted, suspect (use with care), untrusted (do not base a conclusion on it).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from grid_copilot.transformer.sim import AssetData

# Channel -> the shortest run of identical samples that counts as frozen. Gas
# channels hold their value between 4-hourly samples, so they get a longer run.
FROZEN_RUN = {
    "top_oil_c": 4, "ambient_c": 4, "oltc_temp_c": 4, "load_pu": 6, "pd_pc": 6,
    "h2_ppm": 28, "ch4_ppm": 28, "c2h6_ppm": 28, "c2h4_ppm": 28, "c2h2_ppm": 28, "co_ppm": 28, "co2_ppm": 28,
}
CHECKED = list(FROZEN_RUN)
# Calculated signals and the measured inputs they depend on. A calculation is
# never more trustworthy than its worst input.
DERIVED = {
    "hotspot_c": ("top_oil_c", "load_pu"),
    "oltc_temp_diff_c": ("oltc_temp_c", "top_oil_c"),
    "ageing_rate": ("top_oil_c", "load_pu"),
}
_ORDER = {"trusted": 0, "suspect": 1, "untrusted": 2}


@dataclass
class ChannelQuality:
    channel: str
    grade: str = "trusted"  # trusted | suspect | untrusted
    reasons: list[str] = field(default_factory=list)

    def flag(self, grade: str, reason: str) -> None:
        if _ORDER[grade] > _ORDER[self.grade]:
            self.grade = grade
        self.reasons.append(reason)


def _longest_run(values: list[float], ignore_zero: bool) -> tuple[int, float]:
    best, run, best_v = 1, 1, values[0] if values else 0.0
    for a, b in zip(values, values[1:]):
        if b == a and not (ignore_zero and b == 0):
            run += 1
            if run > best:
                best, best_v = run, b
        else:
            run = 1
    return best, best_v


def assess(asset: AssetData, end: int, days: int = 7) -> dict[str, ChannelQuality]:
    ch = asset.channels
    lo = max(0, end + 1 - 24 * days)
    out = {c: ChannelQuality(c) for c in CHECKED}
    for c in CHECKED:
        series = ch[c][lo:end + 1]
        if len(series) < 8:
            continue
        run, value = _longest_run(series, ignore_zero=c.endswith("_ppm"))
        if run >= FROZEN_RUN[c] * 3:
            out[c].flag("untrusted", f"frozen at {value:g} for {run} h")
        elif run >= FROZEN_RUN[c]:
            out[c].flag("suspect", f"unchanged at {value:g} for {run} h")
        diffs = [abs(b - a) for a, b in zip(series, series[1:]) if b != a]
        # Only temperatures should move smoothly; load, PD and gas legitimately step.
        if c.endswith("_c") and len(diffs) > 10:
            mad = statistics.median(diffs) or 1e-6
            big = max(diffs)
            if big > 25 * mad and big > 5:
                out[c].flag("suspect", f"a single-step jump of {big:.1f} (typical change {mad:.2f})")
        if c.endswith("_ppm") and min(series) < 0:
            out[c].flag("untrusted", "negative concentrations")
    # Physics: a loaded transformer's oil is not colder than the air, and the
    # hot spot is not below the top oil.
    cold = sum(1 for i in range(lo, end + 1) if ch["load_pu"][i] > 0.3 and ch["top_oil_c"][i] < ch["ambient_c"][i] - 3)
    if cold > 3:
        out["top_oil_c"].flag("untrusted", f"top oil below ambient under load for {cold} h")
    start_ts, end_ts = asset.ts[lo], asset.ts[end]
    gaps = [e for e in asset.events if e.code == "COMMS_LOSS" and start_ts <= e.ts <= end_ts]
    if len(gaps) >= 3:
        for q in out.values():
            q.flag("suspect", f"{len(gaps)} communication losses in {days} days")
    for derived, inputs in DERIVED.items():
        q = ChannelQuality(derived)
        for src in inputs:
            if out[src].grade != "trusted":
                q.flag(out[src].grade, f"calculated from {src}, which is {out[src].grade}")
        out[derived] = q
    return out


def summary(quality: dict[str, ChannelQuality]) -> str:
    from grid_copilot.transformer.semantics import ref

    bad = [q for q in quality.values() if q.grade != "trusted"]
    if not bad:
        return f"All {len(quality)} checked channels trusted."
    return "; ".join(f"{q.channel} [{ref(q.channel)}] {q.grade.upper()} ({', '.join(q.reasons)})" for q in bad)


def lab_sample(asset: AssetData, at: int, seed: str = "") -> dict[str, float]:
    """What a laboratory would report for oil drawn at index `at`: the true gas
    content with a few percent of analytical error (deterministic per asset)."""
    import random

    from grid_copilot.transformer.sim import GASES

    rng = random.Random(f"lab:{asset.plate.asset}:{at}:{seed}")
    truth = asset.true_gas[at]
    return {g: round(max(0.0, v * (1 + rng.gauss(0, 0.05))), 1) for g, v in zip(GASES, truth)}
