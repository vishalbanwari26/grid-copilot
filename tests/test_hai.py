"""HAI dataset integration test.

Skips automatically when the dataset is not present, so a fresh checkout (which
does not ship the ~6 MB file) still passes. Download it with:

    curl -L -o data/test1.csv.gz \\
      https://raw.githubusercontent.com/icsdataset/hai/master/hai-21.03/test1.csv.gz
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grid_copilot.detect.statistical import ZScoreDetector
from grid_copilot.ingest.hai import attack_intervals, load_hai
from grid_copilot.ingest.replay import replay

_PATHS = [Path("data/test1.csv"), Path("data/test1.csv.gz")]
_HAI = next((p for p in _PATHS if p.exists()), None)

pytestmark = pytest.mark.skipif(_HAI is None, reason="HAI dataset not downloaded")


def test_hai_loads_and_labels_align():
    data = load_hai(_HAI, baseline=1500, limit=6000)
    assert data.n_steps == 6000
    # Continuous signals selected for each process, binaries dropped.
    assert set(data.signals_by_asset) <= {"P1", "P2", "P3", "P4"}
    assert all(len(sigs) > 0 for sigs in data.signals_by_asset.values())
    # This slice contains exactly one labeled attack interval, on P1.
    assert attack_intervals(data.attack) == [(2111, 2302)]


def test_hai_reference_names_the_affected_process():
    from eval.hai_eval import _hai_reference

    data = load_hai(_HAI, baseline=1500, limit=6000)
    interval = attack_intervals(data.attack_by_asset["P1"])[0]
    ref = _hai_reference(data, "P1", interval)
    # The judge reference identifies the boiler process and decodes the moved tags.
    assert "boiler" in ref
    assert "P1" in ref
    assert "valve" in ref or "transmitter" in ref


def test_hai_detector_catches_the_attack():
    data = load_hai(_HAI, baseline=1500, limit=6000)
    detector = ZScoreDetector(baseline=1500, detect_flatline=False)

    last_ts, step = None, -1
    caught = False
    (start, end) = attack_intervals(data.attack_by_asset["P1"])[0]
    for reading in replay(data.readings):
        if reading.ts != last_ts:
            step, last_ts = step + 1, reading.ts
        for anomaly in detector.update(reading):
            if anomaly.asset == "P1" and start <= step <= end + 30:
                caught = True
    assert caught, "detector missed the labeled P1 attack"
