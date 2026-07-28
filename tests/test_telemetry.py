"""TelemetryLog + widened investigation window tests.

The window the agent analyzes must span before onset (for a baseline) and past
detection (to see the developed incident), not just the detector's pre-detection
snapshot.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from grid_copilot.agent.tools import Investigation
from grid_copilot.rag.retriever import KeywordRetriever
from grid_copilot.memory.store import LocalIncidentStore
from grid_copilot.telemetry import TelemetryLog
from grid_copilot.types import Anomaly, Reading

_T0 = datetime(2026, 1, 1)


def _reads(n: int, asset: str = "a") -> list[Reading]:
    return [Reading(ts=_T0 + timedelta(seconds=i), asset=asset, values={"x": float(i)}) for i in range(n)]


def test_window_spans_before_and_after_center():
    log = TelemetryLog.from_readings(_reads(100))
    w = log.window("a", _T0 + timedelta(seconds=50), before=10, after=20)
    xs = [r.values["x"] for r in w]
    assert xs[0] == 40.0 and xs[-1] == 70.0  # reaches back 10 and forward 20
    assert len(w) == 31


def test_analysis_window_prefers_log_over_snapshot():
    # The anomaly's own window is a tiny early snapshot; the log has the full run.
    anomaly = Anomaly(
        asset="a", ts=_T0 + timedelta(seconds=50), signal="x", score=5.0,
        detector="t", window=_reads(5),  # snapshot: only 5 readings
    )
    log = TelemetryLog.from_readings(_reads(100))
    inv = Investigation(
        anomaly=anomaly, retriever=KeywordRetriever(), store=LocalIncidentStore(),
        telemetry=log, window_before=30, window_after=30,
    )
    assert len(inv.analysis_window()) > 5  # uses the wide log window, not the snapshot

    # With no log, it falls back to the detector's snapshot.
    inv_no_log = Investigation(anomaly=anomaly, retriever=KeywordRetriever(), store=LocalIncidentStore())
    assert len(inv_no_log.analysis_window()) == 5
