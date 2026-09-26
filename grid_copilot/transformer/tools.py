"""Investigation tools for the transformer domain.

Each tool reads the monitoring data of one asset up to the moment of the
investigation (never after it) and returns `Evidence`: a short plain-language
summary for the agent, and a `Findings:` line of key=value pairs so the numbers
the conclusion depends on are stated once, exactly, and can be checked.

The tools do the arithmetic. The agent decides which ones to call, weighs what
they say against each other, and explains the result.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import timedelta

from grid_copilot.agent.tools import Investigation, Tool, ToolRegistry, recall_incident
from grid_copilot.transformer.diagnostics import (
    classify_prpd,
    co2_co_ratio,
    duval_triangle_1,
    event_correlation,
    gas_increment,
    iec_ratios,
    prpd_phase_locking,
    prpd_symmetry,
    rate_per_day,
)
from grid_copilot.transformer import quality as dq
from grid_copilot.transformer.monitor import WARMUP_H, Alarm, expected_top_oil_for
from grid_copilot.transformer.sim import GASES, AssetData
from grid_copilot.types import Evidence

MIN_INCREMENT_PPM = 8.0  # below this much new hydrocarbon gas, do not classify...
MIN_C2H2_PPM = 1.0  # ...unless acetylene rose this much, which is significant on its own


@dataclass
class TransformerContext:
    asset: AssetData
    alarm: Alarm
    as_of: int  # index of the last sample the investigation may see
    expected_top_oil: list[float] = field(default_factory=list)
    _quality: dict | None = None

    @property
    def quality(self) -> dict[str, dq.ChannelQuality]:
        if self._quality is None:
            self._quality = dq.assess(self.asset, self.as_of)
        return self._quality

    @classmethod
    def at_alarm(cls, asset: AssetData, alarm: Alarm, hours_after: int = 72) -> "TransformerContext":
        """Investigate a few days after the alarm, the way an engineer would pick it up."""
        as_of = min(len(asset.ts) - 1, alarm.h + hours_after)
        return cls(asset=asset, alarm=alarm, as_of=as_of, expected_top_oil=expected_top_oil_for(asset))


def _ctx(inv: Investigation) -> TransformerContext:
    ctx = inv.context
    if not isinstance(ctx, TransformerContext):
        raise RuntimeError("transformer tools need a TransformerContext on the investigation")
    return ctx


def _series(ctx: TransformerContext, name: str, days: float) -> list[float]:
    end = ctx.as_of + 1
    return ctx.asset.channels[name][max(0, end - int(days * 24)):end]


def _findings(**kv) -> str:
    return "Findings: " + "; ".join(f"{k}={v}" for k, v in kv.items())


def _caveat(ctx: TransformerContext, channels: list[str]) -> str:
    """A data-quality warning for the channels a tool just used, if any failed."""
    bad = [ctx.quality[c] for c in channels if c in ctx.quality and ctx.quality[c].grade != "trusted"]
    if not bad:
        return ""
    return " DATA QUALITY: " + "; ".join(f"{q.channel} is {q.grade} ({', '.join(q.reasons)})" for q in bad) + "."


# --- data quality -----------------------------------------------------------------


def data_quality(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    q = ctx.quality
    trigger = ctx.alarm.signal
    trig = q.get(trigger)
    grade = trig.grade if trig else "not_checked"
    bad = sorted(c for c, v in q.items() if v.grade != "trusted")
    summary = (f"Data-quality checks on {len(q)} channels (measured, and calculated ones graded by their inputs) "
               f"over the last 7 days: {dq.summary(q)} "
               f"The alarm's own signal, {trigger}, is {grade}.")
    kv = {"trigger_channel": trigger, "trigger_grade": grade, "flagged": ",".join(bad) or "none"}
    return Evidence("data_quality", summary + " " + _findings(**kv), payload=kv)


def lab_sample(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    ch = ctx.asset.channels
    lab = dq.lab_sample(ctx.asset, ctx.as_of)
    online = {g: ch[g.lower() + "_ppm"][ctx.as_of] for g in GASES}
    parts, worst_gas, worst = [], "", 0.0
    for g in GASES:
        ref = max(lab[g], 1.0)
        dev = (online[g] - lab[g]) / ref
        parts.append(f"{g} lab {lab[g]:.0f} / online {online[g]:.0f}")
        if lab[g] + online[g] >= 20 and abs(dev) > abs(worst):
            worst, worst_gas = dev, g
    summary = ("Oil sample sent to the laboratory (reference method; costs a site visit, result in about two "
               "days): " + ", ".join(parts) + ".")
    if worst_gas and abs(worst) > 0.3:
        summary += f" The online monitor reads {worst_gas} {worst:+.0%} against the laboratory."
    else:
        summary += " The online monitor agrees with the laboratory within 30% on every gas above 20 ppm."
    kv = {"lab_worst_gas": worst_gas or "none", "online_vs_lab": f"{worst:+.2f}",
          "lab_h2_ppm": lab["H2"], "lab_c2h2_ppm": lab["C2H2"]}
    return Evidence("lab_sample", summary + " " + _findings(**kv), payload={"lab": lab, "online": online, **kv})


# --- events -------------------------------------------------------------------


def query_events(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    days = 14
    for tok in arg.replace(",", " ").split():
        if tok.rstrip("d").isdigit():
            days = max(1, min(60, int(tok.rstrip("d"))))
    end_ts = ctx.asset.ts[ctx.as_of]
    start_ts = end_ts - timedelta(days=days)
    window = [e for e in ctx.asset.events if start_ts <= e.ts <= end_ts]
    taps = [e for e in window if e.code == "TAP_CHANGE"]
    fans = [e for e in window if e.code == "FANS_ON"]
    alarms = [e for e in window if e.severity == "alarm"]
    other = [e for e in window if e.code not in ("TAP_CHANGE", "FANS_ON", "FANS_OFF") and e.severity != "alarm"]
    parts = [f"{len(window)} events on {ctx.asset.plate.asset} in the {days} days to {end_ts:%Y-%m-%d %H:%M}"]
    parts.append(f"{len(taps)} tap changes ({len(taps) / days:.1f} per day), {len(fans)} cooling-fan starts")
    if alarms:
        parts.append("alarms: " + "; ".join(f"{e.ts:%m-%d %H:%M} {e.code} ({e.text})" for e in alarms[:8]))
    else:
        parts.append("no alarms")
    if other:
        parts.append("other: " + "; ".join(f"{e.ts:%m-%d %H:%M} {e.code}" for e in other[:5]))
    return Evidence("query_events", ". ".join(parts) + ".",
                    payload={"tap_changes": len(taps), "alarms": [e.code for e in alarms]})


# --- DGA ------------------------------------------------------------------------


def dga_diagnose(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    ch = ctx.asset.channels
    base = (0, 24 * 10)  # the first ten days of the record, before any alarm
    recent = (ctx.as_of - 24, ctx.as_of + 1)
    inc = {g: gas_increment(ch[g.lower() + "_ppm"], base, recent) for g in GASES}
    latest = {g: ch[g.lower() + "_ppm"][ctx.as_of] for g in GASES}
    hydrocarbons = sum(inc[g] for g in ("H2", "CH4", "C2H6", "C2H4", "C2H2"))
    rates = {g: rate_per_day(_series(ctx, g.lower() + "_ppm", 14)) for g in ("H2", "C2H4", "C2H2", "CO")}
    # Correlate over the period the gas has actually been rising: from the first
    # detectable acetylene in the last 30 days (at most 14 days, at least 5), so
    # quiet weeks before the rise do not dilute the comparison.
    c2h2_30 = _series(ctx, "c2h2_ppm", 30)
    first = next((i for i, v in enumerate(c2h2_30) if v >= 0.5), len(c2h2_30))
    days = max(5.0, min(14.0, (len(c2h2_30) - first) / 24))
    tap_counts = _series(ctx, "tap_ops", days)
    c2h2_corr = event_correlation(_series(ctx, "c2h2_ppm", days), tap_counts)
    h2_corr = event_correlation(_series(ctx, "h2_ppm", days), tap_counts)

    latest_txt = ", ".join(f"{g} {latest[g]:.0f}" for g in GASES)
    inc_txt = ", ".join(f"{g} +{inc[g]:.1f}" for g in GASES)
    summary = f"Latest ppm: {latest_txt}. Increase since the baseline: {inc_txt}."
    kv: dict[str, object] = {"increment_hydrocarbons_ppm": round(hydrocarbons, 1)}
    if hydrocarbons < MIN_INCREMENT_PPM and inc["C2H2"] < MIN_C2H2_PPM:
        summary += (f" The increase is below {MIN_INCREMENT_PPM:.0f} ppm with no acetylene rise, too small "
                    "to classify.")
        kv["duval_zone"] = "none"
    else:
        duval = duval_triangle_1(inc["CH4"], inc["C2H4"], inc["C2H2"])
        ratios = iec_ratios(inc["H2"], inc["CH4"], inc["C2H6"], inc["C2H4"], inc["C2H2"])
        if duval:
            summary += (f" Duval triangle 1 on the increment: zone {duval.zone} ({duval.meaning}; "
                        f"CH4 {duval.pct_ch4}%, C2H4 {duval.pct_c2h4}%, C2H2 {duval.pct_c2h2}%).")
            kv["duval_zone"] = duval.zone
        summary += (f" IEC ratio method: {ratios.code} (C2H2/C2H4 {ratios.c2h2_c2h4}, CH4/H2 {ratios.ch4_h2}, "
                    f"C2H4/C2H6 {ratios.c2h4_c2h6}).")
        kv["iec_code"] = ratios.code
    if inc["H2"] + inc["C2H2"] >= 2:
        acet_ratio = inc["C2H2"] / max(inc["H2"], 0.5)
        summary += f" C2H2/H2 in the increment: {acet_ratio:.2f}"
        summary += (" (above about 2 is the usual indicator of oil from an OLTC diverter compartment reaching "
                    "the main tank)." if acet_ratio > 2 else ".")
        kv["c2h2_h2_ratio"] = round(acet_ratio, 2)
    cr = co2_co_ratio(inc["CO2"], inc["CO"])
    if cr is not None:
        summary += f" CO2/CO in the increment: {cr:.1f} (CO +{inc['CO']:.0f} ppm)."
        kv["co2_co_ratio"] = round(cr, 1)
    else:
        kv["co2_co_ratio"] = "n/a"
    summary += (" 14-day trend ppm/day: " + ", ".join(f"{g} {r:+.2f}" for g, r in rates.items()) + ".")
    c = c2h2_corr
    summary += (f" Gas rise against tap changes over the last {days:.0f} days in 8-hour windows: C2H2 rose {c['busy']:+.2f} ppm "
                f"per window when the tap changer was busier than usual and {c['quiet']:+.2f} ppm when quieter "
                f"(detrended r={c['r']:+.2f}, {c['per_event']:+.3f} ppm per operation); H2 r={h2_corr['r']:+.2f}.")
    kv["c2h2_vs_tap_ops_r"] = c["r"]
    kv["h2_vs_tap_ops_r"] = h2_corr["r"]
    summary += _caveat(ctx, ["h2_ppm", "ch4_ppm", "c2h4_ppm", "co_ppm"])
    return Evidence("dga_diagnose", summary + " " + _findings(**kv),
                    payload={"increment": inc, "latest": latest, **kv})


# --- thermal --------------------------------------------------------------------


def thermal_check(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    ch = ctx.asset.channels
    end = ctx.as_of + 1
    lo = max(0, end - 24 * 7)
    # Forced-air capacity only matters while the fans run, so the residual
    # (measured minus healthy-model top oil) is split by fan state, and the worst
    # 6-hour stretch of the week is reported too: lost cooling shows as a large
    # residual on busy, hot afternoons and nothing at night.
    resid = [ch["top_oil_c"][i] - ctx.expected_top_oil[i] for i in range(lo, end)]
    fans = [ch["fans_on"][i] for i in range(lo, end)]
    on = [r for r, f in zip(resid, fans) if f]
    off = [r for r, f in zip(resid, fans) if not f]
    med_on = statistics.median(on) if on else 0.0
    med_off = statistics.median(off) if off else 0.0
    peak = max((statistics.fmean(resid[i:i + 6]) for i in range(0, max(1, len(resid) - 5))), default=0.0)
    load = sorted(_series(ctx, "load_pu", 30))
    hs = sorted(_series(ctx, "hotspot_c", 30))
    amb = _series(ctx, "ambient_c", 30)
    v = statistics.fmean(_series(ctx, "ageing_rate", 30))
    life_h = sum(_series(ctx, "ageing_rate", 30))
    top = _series(ctx, "top_oil_c", 30)
    p99 = lambda xs: xs[int(0.99 * (len(xs) - 1))]  # noqa: E731
    summary = (
        f"Last 30 days: load p99 {p99(load):.2f} pu (max {load[-1]:.2f}), ambient max {max(amb):.0f} C, "
        f"top oil max {max(top):.0f} C, calculated hot spot p99 {p99(hs):.0f} C (max {hs[-1]:.0f} C). "
        f"Measured minus modelled top oil (the model assumes healthy cooling), last 7 days: {med_on:+.1f} K "
        f"median while the fans ran ({len(on)} h), {med_off:+.1f} K with the fans off ({len(off)} h), worst "
        f"6-hour stretch {peak:+.1f} K. Mean ageing rate {v:.2f}x rated, {life_h:.0f} h of rated life used in "
        f"30 days (720 h at rated)."
    )
    kv = {"residual_fans_on_k": round(med_on, 1), "residual_fans_off_k": round(med_off, 1),
          "residual_peak_6h_k": round(peak, 1), "hotspot_p99_c": round(p99(hs), 1),
          "load_p99_pu": round(p99(load), 2), "ageing_rate_mean": round(v, 2)}
    summary += _caveat(ctx, ["top_oil_c", "ambient_c", "load_pu"])
    return Evidence("thermal_check", summary + " " + _findings(**kv), payload=kv)


# --- OLTC -----------------------------------------------------------------------


def oltc_check(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    a = ctx.asset
    ch = a.channels
    base_ops = [op for op in a.tap_ops if op.h < WARMUP_H]
    t0 = a.ts[ctx.as_of] - timedelta(days=7)
    recent_ops = [op for op in a.tap_ops if t0 <= op.ts <= a.ts[ctx.as_of]]
    med = lambda xs: statistics.median(xs) if xs else 0.0  # noqa: E731
    b_time, r_time = med([o.duration_s for o in base_ops]), med([o.duration_s for o in recent_ops])
    b_mot, r_mot = med([o.motor_peak_a for o in base_ops]), med([o.motor_peak_a for o in recent_ops])
    r_spread = statistics.pstdev([o.duration_s for o in recent_ops]) if len(recent_ops) > 1 else 0.0
    incomplete = [o for o in a.tap_ops if not o.completed and o.h <= ctx.as_of]
    diff_base = sorted(ch["oltc_temp_diff_c"][:WARMUP_H])
    diff_recent = sorted(_series(ctx, "oltc_temp_diff_c", 7))
    p90 = lambda xs: xs[int(0.9 * (len(xs) - 1))]  # noqa: E731
    # Does the compartment excess track the load squared (resistive heating)?
    d = _series(ctx, "oltc_temp_diff_c", 7)
    k2 = [k * k for k in _series(ctx, "load_pu", 7)]
    r_load = 0.0
    if statistics.pstdev(d) > 0 and statistics.pstdev(k2) > 0:
        md, mk = statistics.fmean(d), statistics.fmean(k2)
        r_load = sum((x - md) * (y - mk) for x, y in zip(d, k2)) / len(d) / (statistics.pstdev(d) * statistics.pstdev(k2))
    total_ops = sum(1 for o in a.tap_ops if o.h <= ctx.as_of)
    summary = (
        f"{total_ops} operations in the record, {len(recent_ops)} in the last 7 days. Operating time "
        f"median {r_time:.2f} s (baseline {b_time:.2f} s, spread {r_spread:.2f} s); motor peak current "
        f"{r_mot:.2f} A (baseline {b_mot:.2f} A). Incomplete operations: {len(incomplete)}. "
        f"Compartment minus main-tank temperature, 90th percentile: {p90(diff_recent):+.1f} K in the last "
        f"7 days vs {p90(diff_base):+.1f} K at baseline; correlation with load squared r={r_load:+.2f}."
    )
    kv = {"op_time_delta_s": round(r_time - b_time, 2), "motor_current_delta_a": round(r_mot - b_mot, 2),
          "incomplete_ops": len(incomplete), "temp_diff_p90_delta_k": round(p90(diff_recent) - p90(diff_base), 1),
          "temp_diff_vs_load2_r": round(r_load, 2)}
    summary += _caveat(ctx, ["oltc_temp_c", "top_oil_c"])
    return Evidence("oltc_check", summary + " " + _findings(**kv), payload=kv)


# --- PD -------------------------------------------------------------------------


def pd_analyze(arg: str, inv: Investigation) -> Evidence:
    ctx = _ctx(inv)
    a = ctx.asset
    end = ctx.as_of + 1
    lo = max(0, end - 24 * 7)
    pcs = a.channels["pd_pc"][lo:end]
    active = [i for i in range(lo, end) if a.channels["pd_pc"][i] >= 100]
    ordered = sorted(pcs)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    h2_rate = rate_per_day(_series(ctx, "h2_ppm", 14))
    if not active:
        summary = (f"PD p95 {p95:.0f} pC over 7 days, no hours above 100 pC. Hydrogen trend {h2_rate:+.2f} ppm/day.")
        kv = {"pd_p95_pc": round(p95), "pd_max_pc": round(max(pcs)), "active_share": 0.0, "phase_locking": 0.0,
              "h2_rate_ppm_day": round(h2_rate, 2)}
        return Evidence("pd_analyze", summary + " " + _findings(**kv), payload=kv)
    hist = [sum(a.prpd[i][b] for i in active) for b in range(len(a.prpd[0]))]
    lock = prpd_phase_locking(hist)
    sym = prpd_symmetry(hist)
    kind = classify_prpd(hist)
    hours = [a.ts[i].hour for i in active]
    day_share = sum(1 for h in hours if 7 <= h <= 18) / len(hours)
    share = len(active) / (end - lo)
    summary = (
        f"PD p95 {p95:.0f} pC (max {max(pcs):.0f} pC) over 7 days; {share:.0%} of hours above 100 pC, {day_share:.0%} of those in "
        f"working hours (07-18). Phase-resolved pattern over the active hours: {kind} "
        f"(phase locking {lock:.2f}, half-cycle symmetry {sym:.2f}). Hydrogen trend {h2_rate:+.2f} ppm/day."
    )
    kv = {"pd_p95_pc": round(p95), "pd_max_pc": round(max(pcs)), "active_share": round(share, 2), "phase_locking": lock,
          "daytime_share": round(day_share, 2), "h2_rate_ppm_day": round(h2_rate, 2)}
    return Evidence("pd_analyze", summary + " " + _findings(**kv), payload={**kv, "prpd": hist})


def retrieve_notes(arg: str, inv: Investigation) -> Evidence:
    """Like the generic retrieve_docs, but returns the notes' text, not just a title:
    the transformer notes are short and the useful part is rarely the first sentence."""
    query = arg.strip() or inv.anomaly.signal.replace("_", " ")
    hits = inv.retriever.search(query, k=2)
    if not hits:
        return Evidence("retrieve_docs", f"No notes matched '{query}'.")
    text = " ".join(f"[{d.id}] {d.title}: {d.text}" for d, _ in hits)
    return Evidence("retrieve_docs", text, citations=[d.id for d, _ in hits])


def transformer_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool("data_quality",
                      "Data-quality grades (trusted, suspect, untrusted) for the monitoring channels: frozen "
                      "values, impossible physics, sudden steps, communication gaps. Runs first automatically.",
                      data_quality))
    reg.register(Tool("query_events",
                      "List alarms, tap changes, fan starts and other events on this transformer before the "
                      "investigation. Argument: number of days to look back (default 14).", query_events))
    reg.register(Tool("dga_diagnose",
                      "Dissolved gas analysis: latest gases, the increase since baseline, Duval triangle 1 and "
                      "IEC ratios on that increase, CO2/CO, 14-day trends, and whether gas rises with tap "
                      "changes. No argument.", dga_diagnose))
    reg.register(Tool("thermal_check",
                      "Thermal model check: load, hot spot, ageing, and measured top oil against a healthy-"
                      "cooling model with the fans on and off, and the worst 6-hour stretch. No argument.", thermal_check))
    reg.register(Tool("oltc_check",
                      "Tap-changer check: operating time, motor current, incomplete operations, and the "
                      "compartment-to-main-tank temperature difference. No argument.", oltc_check))
    reg.register(Tool("pd_analyze",
                      "Partial discharge: level, how often it is active, time of day, the phase-resolved "
                      "pattern (phase-locked or not) and the hydrogen trend. No argument.", pd_analyze))
    reg.register(Tool("lab_sample",
                      "Send an oil sample to the laboratory (the reference for the online DGA monitor) and "
                      "compare it with the online readings. Costs a site visit; use it when the online gas "
                      "data is in doubt or the decision is expensive. No argument.", lab_sample))
    reg.register(Tool("retrieve_docs",
                      "Search condition-monitoring notes. Argument: a short query (e.g. 'acetylene tap "
                      "changer leak').", retrieve_notes))
    reg.register(Tool("recall_incident",
                      "Recall earlier incidents and maintenance on this transformer. Argument: a short query.",
                      recall_incident))
    return reg
