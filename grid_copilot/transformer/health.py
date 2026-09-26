"""Health index per subsystem and the fleet risk ranking.

A deliberately transparent scoring: each subsystem gets a 0-100 score from a few
banded measurements (100 is healthy), the overall index leans on the worst
subsystem, and risk multiplies how unhealthy a unit is by how much depends on it.
The bands are illustrative, not taken from a standard, and every score carries
the reasons that produced it so the ranking can be argued with.

The index is computed from monitoring data only. It does not know the ground
truth, and it is naive on purpose about one thing: a loud PD sensor lowers the PD
score whether or not the activity is phase-locked. Working out that it is
interference is the investigation's job, and the fleet view shows both.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime

from grid_copilot.transformer.diagnostics import rate_per_day
from grid_copilot.transformer.monitor import WARMUP_H, expected_top_oil_for
from grid_copilot.transformer.sim import AssetData, Fleet

# (normal, caution) levels per gas, ppm. Above caution scores lowest.
GAS_LEVELS = {
    "h2_ppm": (100, 200), "ch4_ppm": (75, 150), "c2h6_ppm": (65, 130),
    "c2h4_ppm": (50, 100), "c2h2_ppm": (1, 5), "co_ppm": (400, 600),
}


@dataclass
class SubScore:
    name: str
    score: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class AssetHealth:
    asset: str
    substation: str
    health_index: int
    risk: int
    subscores: dict[str, SubScore]
    worst: str
    action: str

    def to_dict(self) -> dict:
        return {
            "asset": self.asset, "substation": self.substation, "health_index": self.health_index,
            "risk": self.risk, "worst": self.worst, "action": self.action,
            "subscores": {k: {"score": v.score, "reasons": v.reasons} for k, v in self.subscores.items()},
        }


def _band(value: float, bands: list[tuple[float, int]], last: int) -> int:
    for limit, score in bands:
        if value < limit:
            return score
    return last


def dga_score(a: AssetData, end: int) -> SubScore:
    ch = a.channels
    score, reasons = 100, []
    for sig, (normal, caution) in GAS_LEVELS.items():
        v = ch[sig][end]
        s = _band(v, [(normal, 100), (caution, 60)], 25)
        if s < 100:
            reasons.append(f"{sig.removesuffix('_ppm').upper()} {v:.0f} ppm (normal < {normal}, caution < {caution})")
        score = min(score, s)
    window = 24 * 30
    tdcg = [sum(ch[g][i] for g in ("h2_ppm", "ch4_ppm", "c2h6_ppm", "c2h4_ppm", "c2h2_ppm"))
            for i in range(max(0, end - window), end + 1)]
    rate = rate_per_day(tdcg)
    if rate > 3:
        score = min(score, 40 if rate > 8 else 60)
        reasons.append(f"combustible gases rising {rate:.1f} ppm/day over 30 days")
    return SubScore("dga", score, reasons)


def thermal_score(a: AssetData, end: int, expected: list[float]) -> SubScore:
    ch = a.channels
    lo = max(0, end - 24 * 30)
    hs = sorted(ch["hotspot_c"][lo:end + 1])
    p99 = hs[int(0.99 * (len(hs) - 1))]
    score = _band(p99, [(98, 100), (110, 75), (120, 50)], 25)
    reasons = [f"hot spot p99 {p99:.0f} C over 30 days"] if score < 100 else []
    resid = [m - e for m, e, k in zip(ch["top_oil_c"][end - 24 * 7:end + 1], expected[end - 24 * 7:end + 1],
                                      ch["load_pu"][end - 24 * 7:end + 1]) if k > 0.4]
    if resid:
        r = statistics.median(resid)
        if r > 4:
            score = min(score, 35)
            reasons.append(f"top oil {r:.1f} K above the thermal model (7-day median)")
    v = statistics.fmean(ch["ageing_rate"][lo:end + 1])
    if v > 1:
        score = min(score, 50)
        reasons.append(f"insulation ageing at {v:.1f}x the rated rate")
    return SubScore("thermal", score, reasons)


def oltc_score(a: AssetData, end: int) -> SubScore:
    ch = a.channels
    # Contact heating scales with current squared, so judge the difference at the
    # busy hours (90th percentile of the week), not the quiet nights.
    week = sorted(ch["oltc_temp_diff_c"][end - 24 * 7:end + 1])
    diff = week[int(0.9 * (len(week) - 1))]
    s1 = _band(diff, [(-0.5, 100), (1.5, 70), (4, 45)], 20)
    base = statistics.median(ch["oltc_op_time_s"][:WARMUP_H])
    delta = statistics.median(ch["oltc_op_time_s"][end - 24 * 7:end + 1]) - base
    s2 = _band(delta, [(0.2, 100), (0.6, 70), (1.2, 45)], 25)
    reasons = []
    if s1 < 100:
        reasons.append(f"OLTC compartment {diff:+.1f} K relative to main tank (7-day p90)")
    if s2 < 100:
        reasons.append(f"tap-change time {delta:+.2f} s over its baseline")
    score = min(s1, s2)
    if any(e.code == "TAP_CHANGE_INCOMPLETE" and a.index_at(e.ts) <= end for e in a.events):
        score = min(score, 20)
        reasons.append("incomplete tap change recorded")
    return SubScore("oltc", score, reasons)


def pd_score(a: AssetData, end: int) -> SubScore:
    pcs = sorted(a.channels["pd_pc"][end - 24 * 7:end + 1])
    p95 = pcs[int(0.95 * (len(pcs) - 1))]
    score = _band(p95, [(100, 100), (300, 70), (1000, 45)], 25)
    return SubScore("pd", score, [f"PD p95 {p95:.0f} pC over 7 days"] if score < 100 else [])


_ACTIONS = {
    "dga": "Take a lab oil sample to confirm, raise the DGA sampling rate, and investigate the fault type",
    "thermal": "Check cooling and loading; limit load until the thermal cause is understood",
    "oltc": "Inspect the tap changer (drive, contacts, compartment) at the next opportunity",
    "pd": "Confirm the PD source (internal or external) before planning an outage",
}


def assess(a: AssetData, end: int | None = None) -> AssetHealth:
    end = len(a.ts) - 1 if end is None else end
    expected = expected_top_oil_for(a)
    subs = {s.name: s for s in (dga_score(a, end), thermal_score(a, end, expected), oltc_score(a, end), pd_score(a, end))}
    scores = [s.score for s in subs.values()]
    hi = round(0.6 * min(scores) + 0.4 * statistics.fmean(scores))
    worst = min(subs.values(), key=lambda s: s.score)
    # Criticality 0.5-1.0 from the number of customers downstream.
    crit = 0.5 + 0.5 * min(1.0, math.log10(max(10, a.plate.customers)) / math.log10(150000))
    risk = round((100 - hi) * crit)
    action = _ACTIONS[worst.name] if worst.score < 100 else "No action beyond routine monitoring"
    return AssetHealth(a.plate.asset, a.plate.substation, hi, risk, subs, worst.name if worst.score < 100 else "", action)


def rank(fleet: Fleet, at: datetime | None = None) -> list[AssetHealth]:
    out = []
    for a in fleet.assets.values():
        end = a.index_at(at) if at else None
        out.append(assess(a, end))
    return sorted(out, key=lambda h: (-h.risk, h.health_index))
