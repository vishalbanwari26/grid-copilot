"""The transformer investigation profile: persona, conclusion format, validator.

A conclusion here is structured, because an asset manager acts on it: a fault
class from a fixed list, the subsystem, an urgency, a recommended action, and the
tools whose evidence it rests on. The validator checks it before anyone sees it:

- the fields are present and the fault class and urgency are from the lists;
- the fault class belongs to the subsystem the conclusion names (an "arcing"
  placed in the tap changer is a contradiction the model has to resolve);
- the cited evidence is from tools that actually ran, and includes the tool
  that class needs (no cooling failure without the thermal check);
- it does not rest on data that failed the data-quality checks, or on online gas
  readings the laboratory contradicts;
- it states what observation would prove it wrong, so a reviewer knows what to
  check;
- it does not contradict the deterministic tools without saying why. Naming a
  gas-producing fault when the DGA increment is too small to classify, calling a
  phase-locked PD pattern interference, calling it a cooling failure when the
  measured top oil matches the healthy model, or closing an acetylene alarm as
  "no fault" are all sent back with the reason.

A conclusion that fails goes back to the model (generate, validate, retry); one
that still fails is kept and marked, never passed off as valid.
"""

from __future__ import annotations

import re

from grid_copilot.agent.investigator import DomainProfile
from grid_copilot.agent.tools import Investigation
from grid_copilot.transformer.sim import FAULT_CLASSES, FAULTS
from grid_copilot.transformer.tools import TransformerContext
from grid_copilot.types import Anomaly

URGENCIES = ("immediate", "weeks", "next_outage", "routine")
SUBSYSTEMS = ("active_part", "oltc", "cooling", "monitoring", "none")
GAS_FAULTS = {"thermal_fault_high", "thermal_fault_paper", "arcing", "oltc_compartment_leak"}
# The tool whose evidence a fault class cannot be claimed without.
REQUIRED_TOOL = {
    "thermal_fault_high": "dga_diagnose", "thermal_fault_paper": "dga_diagnose", "arcing": "dga_diagnose",
    "oltc_compartment_leak": "dga_diagnose", "partial_discharge": "pd_analyze",
    "external_interference": "pd_analyze", "cooling_failure": "thermal_check", "overload": "thermal_check",
    "oltc_contact_overheating": "oltc_check", "oltc_drive_mechanism": "oltc_check", "sensor_fault": "data_quality",
}
# Which subsystem each fault class lives in (decoys and no_fault: "none").
CLASS_SUBSYSTEM = {f.fault_class: f.subsystem for f in FAULTS.values()} | {"no_fault": "none"}

SYSTEM = (
    "ROLE: INVESTIGATOR. DOMAIN: TRANSFORMER. You are a transformer condition-monitoring "
    "engineer investigating an alarm on an oil-immersed power transformer with an on-load tap "
    "changer. Work from the tool evidence, not assumption, and weigh the tools against each "
    "other: the same gas pattern can have different sources, alarms are often false, and the data "
    "itself can be wrong, so check the data-quality grades before trusting a reading. Your "
    "conclusion is a proposal that an engineer will accept or override, so make it checkable. Do "
    "not call a tool you have already used. Respond with ONE JSON object and nothing else."
)

CONCLUDE_FORMAT = (
    '{"action":"conclude","fault_class":"<one of: ' + ", ".join(FAULT_CLASSES) + '>",'
    '"subsystem":"<one of: ' + ", ".join(SUBSYSTEMS) + '>",'
    '"root_cause":"<one sentence>","confidence":<0..1>,'
    '"urgency":"<one of: ' + ", ".join(URGENCIES) + '>",'
    '"recommended_action":"<what the asset manager should do next>",'
    '"evidence_used":["<tool names whose evidence the conclusion rests on>"],'
    '"would_change_conclusion":"<the observation that would prove this conclusion wrong>",'
    '"reasoning":"<why, citing the numbers from the evidence>"}'
)

DETAIL_KEYS = ("fault_class", "subsystem", "urgency", "recommended_action", "evidence_used",
               "would_change_conclusion")


def describe(anomaly: Anomaly, inv: Investigation | None) -> str:
    ctx = inv.context if inv is not None else None
    if not isinstance(ctx, TransformerContext):
        return f"Alarm {anomaly.detector} on {anomaly.asset} at {anomaly.ts.isoformat()}."
    p = ctx.asset.plate
    a = ctx.alarm
    return (
        f"Alarm {a.code} on transformer {p.asset} ({p.mva:g} MVA, {p.kv}, built {p.year}, substation "
        f"{p.substation}, {p.customers} customers downstream) at {a.ts:%Y-%m-%d %H:%M}: {a.text} "
        f"({a.signal} = {a.value}, limit {a.threshold}). You are investigating at "
        f"{ctx.asset.ts[ctx.as_of]:%Y-%m-%d %H:%M} with the data up to then."
    )


def _finding(inv: Investigation, source: str, key: str) -> str | None:
    for e in inv.evidence:
        if e.source == source:
            m = re.search(rf"\b{re.escape(key)}=([^;\s]+)", e.summary)
            if m:
                return m.group(1)
    return None


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def validate(decision: dict, inv: Investigation) -> str | None:
    missing = [k for k in ("fault_class", "subsystem", "urgency", "recommended_action", "root_cause",
                           "would_change_conclusion") if not decision.get(k)]
    if missing:
        return f"missing fields: {', '.join(missing)}"
    fc = decision["fault_class"]
    if fc not in FAULT_CLASSES:
        return f"fault_class '{fc}' is not one of: {', '.join(FAULT_CLASSES)}"
    if decision["urgency"] not in URGENCIES:
        return f"urgency '{decision['urgency']}' is not one of: {', '.join(URGENCIES)}"
    if decision["subsystem"] not in SUBSYSTEMS:
        return f"subsystem '{decision['subsystem']}' is not one of: {', '.join(SUBSYSTEMS)}"
    expected_sub = CLASS_SUBSYSTEM[fc]
    if decision["subsystem"] != expected_sub:
        return (f"fault_class '{fc}' is a fault of subsystem '{expected_sub}', but you gave subsystem "
                f"'{decision['subsystem']}'. Pick the fault class that matches where you place the source "
                f"(classes by subsystem: " + "; ".join(
                    f"{sub}: {', '.join(sorted(c for c, s in CLASS_SUBSYSTEM.items() if s == sub))}"
                    for sub in SUBSYSTEMS) + ")")
    used = {e.source for e in inv.evidence}
    cited = decision.get("evidence_used") or []
    if not isinstance(cited, list) or not cited:
        return "evidence_used must list the tools whose evidence the conclusion rests on"
    unknown = [c for c in cited if c not in used]
    if unknown:
        return f"evidence_used cites tools that did not run: {', '.join(map(str, unknown))} (ran: {', '.join(sorted(used))})"
    need = REQUIRED_TOOL.get(fc)
    if need and need not in cited:
        return (f"'{fc}' cannot be concluded without evidence from {need}"
                + ("" if need in used else f", which has not run (you may not call tools now, so choose a class "
                   f"the evidence you have supports)") )

    trigger_grade = _finding(inv, "data_quality", "trigger_grade")
    if trigger_grade == "untrusted" and fc != "sensor_fault":
        trig = _finding(inv, "data_quality", "trigger_channel")
        return (f"the alarm's own signal ({trig}) failed the data-quality checks (untrusted), so it cannot "
                f"support '{fc}'; conclude sensor_fault or base the conclusion on trusted channels only")
    lab_dev = _num(_finding(inv, "lab_sample", "online_vs_lab"))
    if lab_dev is not None and abs(lab_dev) > 0.5 and fc in GAS_FAULTS | {"partial_discharge"}:
        gas = _finding(inv, "lab_sample", "lab_worst_gas")
        return (f"the laboratory does not confirm the online {gas} reading (online vs lab {lab_dev:+.0%}); a "
                f"gas-based fault class needs gas the laboratory can see")
    zone = _finding(inv, "dga_diagnose", "duval_zone")
    if fc in GAS_FAULTS and zone == "none":
        return (f"'{fc}' produces gas, but dga_diagnose found the gas increase too small to classify "
                "(duval_zone=none)")
    lock = _num(_finding(inv, "pd_analyze", "phase_locking"))
    if fc == "external_interference" and lock is not None and lock >= 0.05:
        return (f"pd_analyze found the PD phase-locked (phase_locking={lock}), which is typical of an "
                "internal source, not interference; explain or change the class")
    peak = _num(_finding(inv, "thermal_check", "residual_peak_6h_k"))
    if fc == "cooling_failure" and peak is not None and peak < 3.0:
        return (f"thermal_check shows measured top oil within {peak} K of the healthy-cooling model even in "
                "its worst 6-hour stretch, which does not support lost cooling")
    alarm = inv.context.alarm.code if isinstance(inv.context, TransformerContext) else ""
    if fc == "no_fault" and alarm == "DGA_C2H2":
        return "an acetylene alarm cannot be closed as no_fault without naming the source of the acetylene"
    return None


TRANSFORMER_PROFILE = DomainProfile(
    system=SYSTEM,
    conclude_format=CONCLUDE_FORMAT,
    conclude_hint="(even a low-confidence one)",
    revise_hint="Check whether another tool's evidence explains the pattern better.",
    describe=describe,
    detail_keys=DETAIL_KEYS,
    validator=validate,
)
