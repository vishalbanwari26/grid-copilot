"""Autoencoder detector test. Skips if torch is not installed.

Confirms the multivariate detector learns normal correlations on a clean warmup
and then flags an injected fault, using the same synthetic generator as the
statistical detector's tests.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch", reason="torch not installed")

from grid_copilot.detect.autoencoder import AutoencoderDetector  # noqa: E402
from grid_copilot.ingest.replay import replay  # noqa: E402
from grid_copilot.ingest.synthetic import SyntheticGrid  # noqa: E402


def test_autoencoder_flags_injected_fault():
    scenario = SyntheticGrid(seed=7).generate(samples=420, inject=[("bearing_overheat", 200, 400)])
    det = AutoencoderDetector(baseline=150, epochs=40, cooldown=60)
    anomalies = [a for r in replay(scenario.readings) for a in det.update(r)]
    turbine = [a for a in anomalies if a.asset == "turbine_1"]
    assert turbine, "autoencoder missed the injected turbine fault"
    # The reported trigger should be one of the signals the fault actually moved.
    assert any(a.signal in {"bearing_temp_c", "vibration_mm_s"} for a in turbine)


def test_autoencoder_quiet_on_clean_data():
    clean = SyntheticGrid(seed=3).generate(samples=420, inject=[])
    det = AutoencoderDetector(baseline=150, epochs=40)
    anomalies = [a for r in replay(clean.readings) for a in det.update(r)]
    assert not anomalies, "autoencoder fired on clean data"
