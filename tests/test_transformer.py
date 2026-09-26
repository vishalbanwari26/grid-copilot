"""Transformer domain: diagnostics, simulator, validator, and the offline loop.

All offline and deterministic: synthetic load (no ETT download), the mock brain.
"""

from __future__ import annotations

import json

import pytest

from grid_copilot.agent.tools import Investigation
from grid_copilot.events import EventBus
from grid_copilot.llm_retry import RetryingClient
from grid_copilot.memory.store import LocalIncidentStore
from grid_copilot.rag.retriever import KeywordRetriever
from grid_copilot.transformer import fleet as F
from grid_copilot.transformer.diagnostics import (
    classify_prpd,
    duval_triangle_1,
    event_correlation,
    iec_ratios,
    prpd_phase_locking,
)
from grid_copilot.transformer.domain import validate
from grid_copilot.transformer.health import rank
from grid_copilot.transformer.rules import TransformerMockClient, expert, textbook
from grid_copilot.transformer.run import anomaly_from, investigate_alarm
from grid_copilot.transformer.sim import _SIGNATURES, GASES
from grid_copilot.transformer.tools import TransformerContext, dga_diagnose, pd_analyze, thermal_check
from grid_copilot.types import Evidence


def _sig(kind: str) -> dict[str, float]:
    return dict(zip(GASES, _SIGNATURES[kind]))


@pytest.mark.parametrize("kind,zone,code", [
    ("thermal_t3", "T3", "T3"),
    ("paper_overheating", "T2", "T2"),
    ("internal_pd", "PD", "PD"),
    ("arcing_d2", "D2", "D2"),
])
def test_fault_signatures_land_in_their_zone(kind, zone, code):
    g = _sig(kind)
    assert duval_triangle_1(g["CH4"], g["C2H4"], g["C2H2"]).zone == zone
    assert iec_ratios(g["H2"], g["CH4"], g["C2H6"], g["C2H4"], g["C2H2"]).code == code


def test_duval_edges():
    assert duval_triangle_1(0, 0, 0) is None
    assert duval_triangle_1(99, 1, 0).zone == "PD"
    assert duval_triangle_1(10, 5, 85).zone == "D1"
    assert duval_triangle_1(20, 40, 40).zone == "D2"


def test_prpd_phase_locking_separates_internal_from_noise():
    internal = [3, 5, 4, 1, 0.3, 0.2, 3, 5, 4, 1, 0.3, 0.2]
    noise = [1.0] * 12
    assert prpd_phase_locking(noise) == 0.0
    assert prpd_phase_locking(internal) > 0.1
    assert "internal" in classify_prpd(internal)
    assert "not phase-locked" in classify_prpd(noise)


def test_event_correlation_tells_step_gas_from_steady_gas():
    import random

    rng = random.Random(0)
    ops = [rng.choice([0, 0, 1, 2, 4]) for _ in range(240)]
    stepped, steady, s1, s2 = [], [], 0.0, 0.0
    for n in ops:
        s1 += 0.1 * n
        s2 += 0.2
        stepped.append(s1)
        steady.append(s2)
    assert event_correlation(stepped, ops)["r"] > 0.8
    assert abs(event_correlation(steady, ops)["r"]) < 0.1


@pytest.fixture(scope="module")
def scenarios():
    kinds = ["oltc_leak", "thermal_t3", "internal_pd", "external_pd_noise", "cooling_failure", "none"]
    return {k: F.single(k, seed=1, prefer_real=False) for k in kinds}


def _only(sc):
    return next(iter(sc.fleet.assets.values()))


def test_simulator_is_deterministic():
    a = F.single("thermal_t3", seed=3, prefer_real=False)
    b = F.single("thermal_t3", seed=3, prefer_real=False)
    assert _only(a).channels["c2h4_ppm"] == _only(b).channels["c2h4_ppm"]


def test_alarms_fire_for_faults_and_not_for_a_healthy_unit(scenarios):
    first = {k: (sc.first_alarm(_only(sc).plate.asset) or None) for k, sc in scenarios.items()}
    assert first["none"] is None
    assert first["oltc_leak"].code == "DGA_C2H2"
    assert first["thermal_t3"].code == "DGA_C2H4_HIGH"
    assert first["internal_pd"].code == "PD_HIGH"
    assert first["external_pd_noise"].code == "PD_HIGH"


def _inv(sc):
    asset = _only(sc)
    alarm = sc.first_alarm(asset.plate.asset)
    ctx = TransformerContext.at_alarm(asset, alarm)
    return Investigation(anomaly_from(alarm), KeywordRetriever(), LocalIncidentStore(), context=ctx)


def test_leak_looks_like_a_discharge_but_carries_acetylene_rich_gas(scenarios):
    leak = dga_diagnose("", _inv(scenarios["oltc_leak"])).payload
    arcing = dga_diagnose("", _inv(F.single("arcing_d2", seed=1, prefer_real=False))).payload
    assert leak["duval_zone"] in ("D1", "D2", "DT")  # the triangle alone says discharge
    assert arcing["duval_zone"] == "D2"
    assert leak["c2h2_h2_ratio"] > 2 > 1 > arcing["c2h2_h2_ratio"]


def test_pd_tool_separates_internal_pd_from_interference(scenarios):
    internal = pd_analyze("", _inv(scenarios["internal_pd"])).payload
    noise = pd_analyze("", _inv(scenarios["external_pd_noise"])).payload
    assert internal["phase_locking"] >= 0.05
    assert noise["phase_locking"] < 0.05


def test_thermal_residual_shows_lost_cooling(scenarios):
    assert thermal_check("", _inv(scenarios["cooling_failure"])).payload["residual_peak_6h_k"] > 5


@pytest.mark.parametrize("kind,expected", [
    ("oltc_leak", "oltc_compartment_leak"),
    ("thermal_t3", "thermal_fault_high"),
    ("internal_pd", "partial_discharge"),
    ("external_pd_noise", "external_interference"),
    ("cooling_failure", "cooling_failure"),
])
def test_offline_loop_reaches_the_right_class(scenarios, kind, expected):
    sc = scenarios[kind]
    events = []
    bus = EventBus()
    bus.subscribe(lambda e: events.append(e.type.value))
    report = investigate_alarm(sc, _only(sc).plate.asset, TransformerMockClient(), bus=bus)
    assert report.hypothesis.details["fault_class"] == expected
    assert "validation" not in report.hypothesis.details
    assert events[0] == "anomaly_detected" and events[-1] == "report_ready"


def test_textbook_rules_fall_for_the_leak():
    fs = {"duval_zone": "D1", "c2h2_vs_tap_ops_r": "0.7"}
    assert textbook("DGA_C2H2", fs) == "arcing"
    assert expert("DGA_C2H2", fs) == "oltc_compartment_leak"


def _inv_with(sc, *evidence: Evidence) -> Investigation:
    inv = _inv(sc)
    inv.evidence.extend(evidence)
    return inv


def _decision(**kw):
    base = {"fault_class": "arcing", "subsystem": "active_part", "urgency": "immediate",
            "recommended_action": "x", "root_cause": "x", "evidence_used": ["dga_diagnose"],
            "would_change_conclusion": "x"}
    return base | kw


def test_validator_rejects_contradictions(scenarios):
    sc = scenarios["oltc_leak"]
    dga = Evidence("dga_diagnose", "... Findings: duval_zone=D1; c2h2_vs_tap_ops_r=0.7")
    inv = _inv_with(sc, dga)
    assert validate(_decision(), inv) is None
    assert "not one of" in validate(_decision(fault_class="lightning"), inv)
    assert "subsystem" in validate(_decision(subsystem="oltc"), inv)
    assert "did not run" in validate(_decision(evidence_used=["pd_analyze"]), inv)
    assert "acetylene" in validate(_decision(fault_class="no_fault", subsystem="none", urgency="routine"), inv)
    quiet = _inv_with(sc, Evidence("dga_diagnose", "Findings: duval_zone=none"),
                      Evidence("thermal_check", "Findings: residual_peak_6h_k=0.4"))
    assert "too small" in validate(_decision(evidence_used=["dga_diagnose"]), quiet)
    assert "healthy-cooling" in validate(_decision(fault_class="cooling_failure", subsystem="cooling",
                                                   evidence_used=["thermal_check"]), quiet)


def test_fleet_ranking_puts_healthy_units_last():
    sc = F.build(prefer_real=False)
    ranked = rank(sc.fleet)
    healthy = {"TR-01", "TR-08"}
    assert {h.asset for h in ranked[-2:]} == healthy
    assert all(h.health_index == 100 for h in ranked if h.asset in healthy)


class _Flaky:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def complete(self, system, prompt, image=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        from cortex.llm import LLMResponse
        return LLMResponse(text="{}")


def test_retrying_client_retries_rate_limits_and_bad_json():
    inner = _Flaky([RuntimeError("Error code: 429 rate_limit try again in 1ms"),
                    RuntimeError("json_validate_failed")])
    client = RetryingClient(inner, base_wait=0.0)
    assert client.complete("s", "p").text == "{}"
    assert inner.calls == 3
    with pytest.raises(ValueError):
        RetryingClient(_Flaky([ValueError("boom")]), base_wait=0.0).complete("s", "p")


# --- data trust, semantics, accountability ---------------------------------------

from grid_copilot.transformer.decisions import DecisionLog, Proposal, ReviewedHistory  # noqa: E402
from grid_copilot.transformer.semantics import SIGNALS, decision_submodel  # noqa: E402
from grid_copilot.transformer import quality as dq  # noqa: E402


def test_every_channel_has_semantics_and_derived_inputs_exist():
    sc = F.single("none", seed=0, prefer_real=False)
    assert set(_only(sc).channels) == set(SIGNALS)
    for s in SIGNALS.values():
        assert all(i in SIGNALS for i in s.inputs)
    assert set(dq.DERIVED) <= {s.name for s in SIGNALS.values() if s.kind == "calculated"}


def test_frozen_sensor_is_untrusted_and_its_calculations_inherit_that():
    sc = F.single("top_oil_sensor_stuck", seed=1, prefer_real=False)
    a = _only(sc)
    q = dq.assess(a, len(a.ts) - 1)
    assert q["top_oil_c"].grade == "untrusted"
    assert q["hotspot_c"].grade == "untrusted"
    assert q["oltc_temp_diff_c"].grade == "untrusted"


def test_frozen_sensor_and_drifting_gas_cell_are_called_sensor_faults():
    for kind in ("top_oil_sensor_stuck", "dga_sensor_drift"):
        sc = F.single(kind, seed=1, prefer_real=False)
        report = investigate_alarm(sc, _only(sc).plate.asset, TransformerMockClient())
        assert report is not None, kind
        assert report.hypothesis.details["fault_class"] == "sensor_fault", kind


def _proposal() -> Proposal:
    sc = F.single("thermal_t3", seed=1, prefer_real=False)
    report = investigate_alarm(sc, _only(sc).plate.asset, TransformerMockClient())
    return Proposal.from_report(report, "DGA_C2H4_HIGH", "mock")


def test_decisions_need_a_reviewer_and_overrides_need_a_reason(tmp_path):
    log = DecisionLog(tmp_path / "d.jsonl")
    p = _proposal()
    with pytest.raises(ValueError):
        log.record(p, "", "accept")
    with pytest.raises(ValueError):
        log.record(p, "A. Engineer", "override", final_fault_class="no_fault")
    rec = log.record(p, "A. Engineer", "override", reason="lab sample normal", final_fault_class="sensor_fault")
    assert rec.final_fault_class == "sensor_fault"
    sub = decision_submodel(json.loads(log.path.read_text().splitlines()[0]))
    assert sub["idShort"] == "ConditionDecision" and sub["asset"]["cimClass"] == "PowerTransformer"


def test_decision_log_is_tamper_evident(tmp_path):
    log = DecisionLog(tmp_path / "d.jsonl")
    p = _proposal()
    log.record(p, "A. Engineer", "accept")
    log.record(p, "B. Engineer", "defer", reason="waiting for the lab")
    assert log.verify_chain() == (True, "chain intact")
    lines = log.path.read_text().splitlines()
    lines[0] = lines[0].replace("A. Engineer", "Someone Else")
    log.path.write_text("\n".join(lines) + "\n")
    ok, why = log.verify_chain()
    assert not ok and "changed" in why


def test_history_holds_reviewed_decisions_not_unreviewed_proposals(tmp_path):
    log = DecisionLog(tmp_path / "d.jsonl")
    history = ReviewedHistory(log=log)
    p = _proposal()
    history.remember(None)  # what the investigator calls at the end: stores nothing
    assert history.recall(p.asset, "x") == []
    log.record(p, "A. Engineer", "accept")
    recalled = history.recall(p.asset, "x")
    assert len(recalled) == 1 and "A. Engineer" in recalled[0].summary


def test_a_conclusion_without_its_supporting_tool_goes_back_to_the_tools():
    """A model that claims a cooling failure before running the thermal check is
    sent back, runs the check, and only then concludes."""
    from cortex.llm import LLMResponse

    sc = F.single("cooling_failure", seed=1, prefer_real=False)
    script = [
        {"action": "conclude", "fault_class": "cooling_failure", "subsystem": "cooling", "urgency": "weeks",
         "root_cause": "fans", "recommended_action": "check fans", "evidence_used": ["data_quality"],
         "would_change_conclusion": "fans fine", "confidence": 0.9, "reasoning": "hot"},
        {"action": "call_tool", "tool": "thermal_check", "arg": "", "why": "need the model residual"},
        {"action": "conclude", "fault_class": "cooling_failure", "subsystem": "cooling", "urgency": "weeks",
         "root_cause": "fans", "recommended_action": "check fans", "evidence_used": ["thermal_check"],
         "would_change_conclusion": "fans fine", "confidence": 0.8, "reasoning": "residual"},
    ]
    prompts = []

    class Scripted(TransformerMockClient):
        def complete(self, system, prompt, image=None):
            if "ROLE: INVESTIGATOR" in system:
                prompts.append(prompt)
                return LLMResponse(text=json.dumps(script.pop(0)))
            return super().complete(system, prompt, image)

    events = []
    bus = EventBus()
    bus.subscribe(lambda e: events.append(e.type.value))
    report = investigate_alarm(sc, _only(sc).plate.asset, Scripted(), bus=bus)
    assert "validation" in events
    assert "cannot be concluded without evidence from thermal_check" in prompts[1]
    assert "Either call a tool" in prompts[1]
    assert report.hypothesis.details["evidence_used"] == ["thermal_check"]
    assert "validation" not in report.hypothesis.details
