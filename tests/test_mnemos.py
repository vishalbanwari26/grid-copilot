"""Mnemos-backed incident memory integration test.

Skips automatically unless Mnemos is installed (it pulls a heavy embedding
stack). Proves the real path end to end on the zero-server backend (embedded
qdrant + local embeddings): two incidents persisted under the same asset, and a
later recall on that asset surfaces the prior one. This is the same
`IncidentStore` interface the local store implements, so the agent's recall tool
is unchanged.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("mnemos", reason="Mnemos not installed")
pytest.importorskip("sentence_transformers", reason="embedding stack not installed")

from grid_copilot.memory.store import MnemosIncidentStore  # noqa: E402
from grid_copilot.types import Hypothesis, IncidentReport  # noqa: E402


def _report(asset: str, cause: str, signal: str) -> IncidentReport:
    return IncidentReport(
        asset=asset,
        ts=datetime(2026, 1, 1, 12, 0, 0),
        trigger_signal=signal,
        score=5.0,
        hypothesis=Hypothesis(root_cause=cause, confidence=0.8, reasoning="test"),
        evidence=[],
        verdict="accept",
        narrative="test",
    )


def test_mnemos_recalls_prior_incident_on_same_asset(tmp_path):
    store = MnemosIncidentStore(qdrant_path=str(tmp_path / "qdrant"))
    try:
        store.remember(_report("turbine_1", "bearing overheating on turbine_1", "bearing_temp_c"))
        store.remember(_report("pump_2", "cavitation on pump_2", "inlet_pressure_bar"))

        priors = store.recall("turbine_1", "bearing temperature rising overheating", k=3)
        assert priors, "expected a prior incident on turbine_1"
        assert any("bearing" in p.summary.lower() for p in priors)
        # Recall is asset-scoped: the pump incident must not surface under turbine_1.
        assert all(p.asset == "turbine_1" for p in priors)
    finally:
        store.close()
