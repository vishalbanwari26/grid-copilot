"""End-to-end offline test: synthetic fault -> detection -> cited report.

Runs entirely on the deterministic mock brain and the local incident store, so
it needs no API key and no network, and asserts the two properties that matter:
the detector catches the injected fault on the right asset, and the agent's
report is grounded (its conclusion matches the injected ground truth and every
knowledge-backed claim carries a citation).
"""

from __future__ import annotations

from grid_copilot.agent.investigator import Investigator
from grid_copilot.agent.mock_llm import GridMockClient
from grid_copilot.detect.statistical import ZScoreDetector
from grid_copilot.ingest.replay import replay
from grid_copilot.ingest.synthetic import SyntheticGrid, demo_scenario
from grid_copilot.memory.store import LocalIncidentStore


def _detect_first(scenario):
    detector = ZScoreDetector()
    for reading in replay(scenario.readings):
        found = detector.update(reading)
        if found:
            return found[0]
    return None


def test_bearing_fault_detected_and_explained():
    scenario = demo_scenario()
    anomaly = _detect_first(scenario)

    assert anomaly is not None, "detector missed the injected fault"
    assert anomaly.asset == "turbine_1"
    assert anomaly.signal in {"bearing_temp_c", "vibration_mm_s"}

    report = Investigator(GridMockClient()).investigate(anomaly)

    assert "bearing" in report.hypothesis.root_cause.lower()
    assert report.verdict == "accept"
    # Every investigation tool ran, and at least one claim is cited.
    sources = {e.source for e in report.evidence}
    assert {"query_telemetry", "retrieve_docs", "recall_incident"} <= sources
    assert any(e.citations for e in report.evidence)


def test_memory_recalls_prior_incident_on_same_asset():
    """A second occurrence on the same asset should surface the first from memory."""
    store = LocalIncidentStore()
    llm = GridMockClient()

    first = Investigator(llm, store=store).investigate(_detect_first(demo_scenario()))
    assert store.recall("turbine_1", "bearing overheating"), "incident was not persisted"

    # Recall directly returns the prior incident keyed by asset.
    priors = store.recall("turbine_1", "bearing overheating vibration")
    assert priors and priors[0].incident_id == "INC-0001"
    assert first.asset == "turbine_1"


def test_no_anomaly_on_clean_data():
    """A scenario with no injected fault should not trip the detector."""
    clean = SyntheticGrid(seed=3).generate(samples=400, inject=[])
    assert _detect_first(clean) is None
