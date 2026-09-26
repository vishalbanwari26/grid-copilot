"""Rule-based baselines, and the deterministic offline brain built on them.

Two baselines the agent is scored against:

- `textbook`: one method per alarm, no cross-checks. A DGA alarm is classified
  by its Duval zone, a PD alarm is partial discharge, a temperature alarm is
  overload. This is what a rulebook without context does.
- `expert`: the same tool findings with cross-checks written in: gas that rises
  with tap changes is an OLTC leak, PD that is not phase-locked and brings no
  hydrogen is interference, a top oil that matches the healthy model is overload.
  It was written knowing the fault catalogue, so it is an upper bound for rules,
  not a fair opponent. The interesting question is how close an agent gets to it
  without anyone writing those rules, and where the textbook rules fail.

`TransformerMockClient` walks every tool, then concludes with the expert rules,
so the offline demo and the tests run the full loop with no API key.
"""

from __future__ import annotations

import json
import re

from cortex.llm import ImageInput, LLMResponse

from grid_copilot.agent.mock_llm import GridMockClient

TOOL_ORDER = ["query_events", "dga_diagnose", "lab_sample", "thermal_check", "oltc_check", "pd_analyze",
              "retrieve_docs", "recall_incident"]

_URGENCY = {
    "arcing": "immediate", "thermal_fault_high": "weeks", "thermal_fault_paper": "weeks",
    "partial_discharge": "weeks", "oltc_contact_overheating": "weeks", "oltc_drive_mechanism": "next_outage",
    "oltc_compartment_leak": "next_outage", "cooling_failure": "weeks", "overload": "routine",
    "external_interference": "routine", "no_fault": "routine", "sensor_fault": "weeks",
}
_SUBSYSTEM = {
    "arcing": "active_part", "thermal_fault_high": "active_part", "thermal_fault_paper": "active_part",
    "partial_discharge": "active_part", "oltc_contact_overheating": "oltc", "oltc_drive_mechanism": "oltc",
    "oltc_compartment_leak": "oltc", "cooling_failure": "cooling", "overload": "none",
    "external_interference": "none", "no_fault": "none", "sensor_fault": "monitoring",
}
_ACTION = {
    "arcing": "Plan to take the unit out of service; confirm with a lab oil sample and electrical tests",
    "thermal_fault_high": "Confirm with a lab sample, check core ground and joints, increase DGA sampling",
    "thermal_fault_paper": "Confirm with a lab sample incl. furans, reduce load, plan an internal inspection",
    "partial_discharge": "Locate the PD source (acoustic or UHF), reduce stress where possible, plan an outage",
    "oltc_contact_overheating": "Inspect the diverter contacts and measure contact resistance soon",
    "oltc_drive_mechanism": "Service the motor drive (gears, brake, lubrication) at the next opportunity",
    "oltc_compartment_leak": "Compare conservator oil levels, sample the OLTC compartment, reseal at the next outage",
    "cooling_failure": "Inspect fans and their supply, restore cooling, limit load until repaired",
    "overload": "Manage the load (transfer, restrict peaks); no internal inspection needed",
    "external_interference": "Close the PD alarm as interference; tune the sensor gating",
    "no_fault": "No action beyond routine monitoring",
    "sensor_fault": "Treat the affected readings as invalid, repair or recalibrate the sensor, confirm the asset "
                    "state by an independent measurement",
}
_FALSIFIER = {
    "arcing": "a lab sample without acetylene, or acetylene that rises only with tap changes",
    "thermal_fault_high": "a lab sample without the ethylene rise, or gas that stops when load is reduced",
    "thermal_fault_paper": "no CO/CO2 rise in a lab sample, or normal furan content",
    "partial_discharge": "PD that is not phase-locked, or no hydrogen rise in a lab sample",
    "oltc_contact_overheating": "a compartment temperature difference that does not follow load",
    "oltc_drive_mechanism": "normal operating times after a drive inspection",
    "oltc_compartment_leak": "equal conservator levels and compartment oil with no acetylene",
    "cooling_failure": "all fans confirmed running with normal airflow",
    "overload": "top oil above the healthy model at the same load",
    "external_interference": "phase-locked PD or a hydrogen rise",
    "no_fault": "any gas, PD or temperature trend at the next check",
    "sensor_fault": "a lab sample or reference sensor that confirms the online reading",
}


def parse_findings(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "Findings:" in line:
            for k, v in re.findall(r"(\w+)=([^;\s]+)", line.split("Findings:", 1)[1]):
                out[k] = v
    return out


def _f(fs: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(fs.get(key, default))
    except ValueError:
        return default


def _dga_class(fs: dict) -> str:
    zone = fs.get("duval_zone", "none")
    ratio = fs.get("co2_co_ratio", "n/a")
    paper = ratio != "n/a" and _f(fs, "co2_co_ratio", 99) < 3
    return {
        "PD": "partial_discharge", "T1": "thermal_fault_paper" if paper else "thermal_fault_high",
        "T2": "thermal_fault_paper" if paper else "thermal_fault_high", "T3": "thermal_fault_high",
        "D1": "arcing", "D2": "arcing", "DT": "arcing",
    }.get(zone, "no_fault")


def textbook(alarm: str, fs: dict) -> str:
    if alarm.startswith("DGA_"):
        return _dga_class(fs)
    if alarm == "PD_HIGH":
        return "partial_discharge"
    if alarm in ("HOTSPOT_HIGH", "TOP_OIL_HIGH"):
        return "overload"
    if alarm == "TOP_OIL_ABOVE_MODEL":
        return "cooling_failure"
    if alarm == "OLTC_TEMP_DIFF":
        return "oltc_contact_overheating"
    if alarm in ("OLTC_OP_TIME", "TAP_CHANGE_INCOMPLETE"):
        return "oltc_drive_mechanism"
    return "no_fault"


def expert(alarm: str, fs: dict) -> str:
    if fs.get("trigger_grade") == "untrusted" or abs(_f(fs, "online_vs_lab")) > 0.5:
        return "sensor_fault"
    if _f(fs, "incomplete_ops") > 0 or _f(fs, "op_time_delta_s") > 0.5:
        return "oltc_drive_mechanism"
    if _f(fs, "temp_diff_p90_delta_k") > 2.5:
        return "oltc_contact_overheating"
    zone = fs.get("duval_zone", "none")
    if zone in ("D1", "D2", "DT") and (_f(fs, "c2h2_h2_ratio") > 2 or _f(fs, "c2h2_vs_tap_ops_r") > 0.5):
        return "oltc_compartment_leak"
    pd_active = _f(fs, "active_share") > 0.02 or _f(fs, "pd_p95_pc") >= 300
    # PD is checked before the triangle: triangle 1 ignores hydrogen, the main gas
    # of PD, so a PD source often lands in a thermal zone.
    if pd_active and _f(fs, "phase_locking") >= 0.05 and zone not in ("D1", "D2", "DT", "T3"):
        return "partial_discharge"
    if zone != "none":
        return _dga_class(fs)
    if pd_active:
        return "external_interference"
    if _f(fs, "residual_peak_6h_k") > 5 and _f(fs, "residual_fans_on_k") > 1.5:
        return "cooling_failure"
    if _f(fs, "hotspot_p99_c") >= 105 or alarm in ("HOTSPOT_HIGH", "TOP_OIL_HIGH"):
        return "overload"
    return "no_fault"


def conclusion(fault_class: str, fs: dict, evidence_used: list[str], why: str) -> dict:
    return {
        "action": "conclude", "fault_class": fault_class, "subsystem": _SUBSYSTEM[fault_class],
        "root_cause": fault_class.replace("_", " "), "confidence": 0.8,
        "urgency": _URGENCY[fault_class], "recommended_action": _ACTION[fault_class],
        "evidence_used": evidence_used, "would_change_conclusion": _FALSIFIER[fault_class], "reasoning": why,
    }


class TransformerMockClient(GridMockClient):
    """Offline brain: every tool in a fixed order, then the expert rules."""

    def complete(self, system: str, prompt: str, image: ImageInput | None = None) -> LLMResponse:
        if "ROLE: INVESTIGATOR" in system and "DOMAIN: TRANSFORMER" in system:
            return LLMResponse(text=self._transformer(prompt))
        return super().complete(system, prompt, image)

    @staticmethod
    def _transformer(prompt: str) -> str:
        used = set()
        for line in prompt.splitlines():
            if line.startswith("Tools already used:"):
                used = {t.strip() for t in line.split(":", 1)[1].split(",") if t.strip() != "none"}
        must = "You MUST now conclude" in prompt
        alarm = (re.search(r"^Alarm (\w+)", prompt, re.M) or [None, ""])[1]
        fs = parse_findings(prompt)
        if not must:
            for tool in TOOL_ORDER:
                if tool == "lab_sample" and not alarm.startswith("DGA_"):
                    continue  # a site visit is only worth it when the online gas data decides the case
                if tool not in used:
                    arg = ""
                    if tool == "retrieve_docs":
                        arg = {
                            "D1": "acetylene tap changer leak", "D2": "acetylene arcing",
                            "T3": "thermal fault ethylene", "PD": "partial discharge hydrogen",
                        }.get(fs.get("duval_zone", ""), alarm.replace("_", " ").lower())
                    elif tool == "recall_incident":
                        arg = alarm.replace("_", " ").lower()
                    return json.dumps({"action": "call_tool", "tool": tool, "arg": arg,
                                       "why": "Gather this evidence before concluding."})
        fc = expert(alarm, fs)
        shown = ", ".join(f"{k}={v}" for k, v in fs.items() if k in (
            "duval_zone", "iec_code", "c2h2_vs_tap_ops_r", "phase_locking", "pd_p95_pc",
            "residual_peak_6h_k", "temp_diff_p90_delta_k", "op_time_delta_s", "c2h2_h2_ratio",
            "trigger_grade", "online_vs_lab"))
        return json.dumps(conclusion(fc, fs, sorted(used) or ["query_events"],
                                     f"Rule-based reading of the tool findings ({shown})."))
