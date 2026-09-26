"""Alarm rules: what the online monitoring raises before anyone investigates.

The thresholds are illustrative settings of the kind a utility configures on its
monitoring system, not values from a standard. Each rule needs its condition to
hold for a few consecutive samples before it latches, and it latches once, so a
fault produces one alarm per rule rather than a flood.

The cooling rule compares measured top oil with what a healthy unit would show
for the same load and ambient (the thermal model with full cooling). That
residual is the only way to see a failed fan group from outside the tank.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from grid_copilot.transformer.sim import AssetData, Fleet, FleetEvent
from grid_copilot.transformer.thermal import expected_top_oil

WARMUP_H = 24 * 14  # rules only arm after two weeks, once baselines exist


@dataclass(frozen=True)
class Rule:
    code: str
    signal: str
    text: str
    threshold: float
    persist_h: int
    mode: str = "above"  # above | residual_above | baseline_delta_above


RULES = [
    Rule("DGA_C2H2", "c2h2_ppm", "Acetylene detected in oil", 2.0, 8),
    Rule("DGA_H2_HIGH", "h2_ppm", "Hydrogen above alarm level", 150.0, 8),
    Rule("DGA_C2H4_HIGH", "c2h4_ppm", "Ethylene above alarm level", 60.0, 8),
    Rule("DGA_CO_HIGH", "co_ppm", "Carbon monoxide rising above alarm level", 450.0, 8),
    Rule("TOP_OIL_HIGH", "top_oil_c", "Top-oil temperature high", 85.0, 2),
    Rule("HOTSPOT_HIGH", "hotspot_c", "Calculated hot-spot temperature high", 110.0, 2),
    Rule("TOP_OIL_ABOVE_MODEL", "top_oil_c", "Top oil above the thermal model for this load", 6.0, 6,
         "residual_above"),
    Rule("PD_HIGH", "pd_pc", "Partial discharge level high", 300.0, 3),
    Rule("OLTC_TEMP_DIFF", "oltc_temp_diff_c", "OLTC compartment warmer than the main tank", 1.5, 6),
    Rule("OLTC_OP_TIME", "oltc_op_time_s", "Tap-change operating time above normal", 0.6, 6,
         "baseline_delta_above"),
]


@dataclass
class Alarm:
    asset: str
    ts: datetime
    h: int
    code: str
    signal: str
    value: float
    threshold: float
    text: str


def expected_top_oil_for(asset: AssetData) -> list[float]:
    ch = asset.channels
    return expected_top_oil(ch["load_pu"], ch["ambient_c"], top_oil0=ch["top_oil_c"][0])


def evaluate(asset: AssetData) -> list[Alarm]:
    ch = asset.channels
    n = len(asset.ts)
    expected = expected_top_oil_for(asset)
    alarms: list[Alarm] = []
    for rule in RULES:
        series = ch[rule.signal]
        if rule.mode == "residual_above":
            values = [m - e if ch["load_pu"][i] > 0.4 else 0.0 for i, (m, e) in enumerate(zip(series, expected))]
        elif rule.mode == "baseline_delta_above":
            base = sorted(series[:WARMUP_H])[len(series[:WARMUP_H]) // 2]
            values = [v - base for v in series]
        else:
            values = series
        run = 0
        for h in range(WARMUP_H, n):
            run = run + 1 if values[h] >= rule.threshold else 0
            if run >= rule.persist_h:
                alarms.append(Alarm(asset.plate.asset, asset.ts[h], h, rule.code, rule.signal,
                                    round(values[h], 2), rule.threshold, rule.text))
                break
    for e in asset.events:
        if e.code == "TAP_CHANGE_INCOMPLETE":
            h = asset.index_at(e.ts)
            alarms.append(Alarm(asset.plate.asset, e.ts, h, e.code, "tap_position", 0.0, 0.0, e.text))
            break
    return sorted(alarms, key=lambda a: a.ts)


def annotate(fleet: Fleet) -> dict[str, list[Alarm]]:
    """Run the rules on every asset and add the alarms to its event log."""
    out = {}
    for name, asset in fleet.assets.items():
        alarms = evaluate(asset)
        out[name] = alarms
        for a in alarms:
            asset.events.append(FleetEvent(a.ts, name, a.code,
                                           f"{a.text}: {a.signal} {a.value} (limit {a.threshold})", "alarm"))
        asset.events.sort(key=lambda e: e.ts)
    return out
