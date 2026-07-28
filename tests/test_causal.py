"""Causal-ordering tests: query_telemetry should report which signal moved first
and, when a command precedes a measurement, that the command drove the event.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from grid_copilot.agent.tools import Investigation, _onset, query_telemetry
from grid_copilot.memory.store import LocalIncidentStore
from grid_copilot.rag.retriever import KeywordRetriever
from grid_copilot.tags import tag_role
from grid_copilot.types import Anomaly, Reading

_T0 = datetime(2026, 1, 1)


def test_tag_role_classifies_command_measurement_position():
    assert tag_role("P1_LCV01D") == "command"
    assert tag_role("P1_LCV01Z") == "position"
    assert tag_role("P1_PIT01") == "measurement"
    assert tag_role("P1_FT01Z") == "measurement"  # transmitter Z channel, not a valve
    assert tag_role("bearing_temp_c") == "other"


def _window(cmd_onset: int, meas_onset: int, n: int = 120) -> list[Reading]:
    reads = []
    for i in range(n):
        cmd = 10.0 + (10.0 if i >= cmd_onset else 0.0)
        meas = 50.0 + (-10.0 if i >= meas_onset else 0.0)
        reads.append(Reading(ts=_T0 + timedelta(seconds=i), asset="P1",
                             values={"P1_PCV01D": cmd, "P1_PIT01": meas}))
    return reads


def _investigate(window: list[Reading]) -> str:
    anomaly = Anomaly(asset="P1", ts=window[-1].ts, signal="P1_PIT01", score=5.0,
                      detector="t", window=window)
    inv = Investigation(anomaly=anomaly, retriever=KeywordRetriever(), store=LocalIncidentStore())
    return query_telemetry("", inv).summary


def test_command_before_measurement_reads_as_driver():
    summary = _investigate(_window(cmd_onset=45, meas_onset=70))
    assert "onset order" in summary.lower()
    assert "command deviated before" in summary.lower()


def test_measurement_before_command_reads_as_response():
    summary = _investigate(_window(cmd_onset=70, meas_onset=45))
    assert "responding to an upstream disturbance" in summary.lower()


def test_onset_ignores_early_wobble_below_magnitude_gate():
    # An early small bump (to 1) must not count as onset when the signal's real
    # move is large (to 100); the magnitude gate marks onset at the real move.
    series = [0.0] * 20 + [1.0] * 10 + [0.0] * 5 + [100.0] * 20
    onset = _onset(series, base_mean=0.0, base_std=0.0, total_delta=100.0)
    assert onset is not None and onset >= 30  # the +100 move, not the early +1 bump
