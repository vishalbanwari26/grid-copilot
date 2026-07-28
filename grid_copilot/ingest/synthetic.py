"""A labeled synthetic grid, so the whole pipeline runs with no download.

The real system replays a public dataset (HAI: a steam-turbine + pumped-storage
testbed with labeled attacks). This module stands in for that during the offline
demo and, more importantly, is the fault *injector* the eval harness needs:
every fault it injects carries a ground-truth `FaultLabel`, so we can later score
whether the agent's stated root cause matches what actually happened.

Assets and signals are modeled on that same power domain:

- ``turbine_1``: rotor speed, bearing temperature, vibration, output power
- ``pump_2``:    flow, motor current, inlet pressure
- ``grid_bus_3``: voltage, frequency, phase angle

Generation is deterministic given a seed, so demos and tests are reproducible.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from grid_copilot.types import Reading


def _stable_phase(name: str) -> int:
    """A per-signal phase that is identical across processes.

    `hash()` is salted per process (PYTHONHASHSEED), which would make the
    generator non-reproducible run to run; a content hash is stable.
    """
    return int(hashlib.sha1(name.encode()).hexdigest(), 16) % 7

# Nominal operating point for each signal: (mean, noise_std).
_ASSETS: dict[str, dict[str, tuple[float, float]]] = {
    "turbine_1": {
        "rotor_speed_rpm": (3600.0, 4.0),
        "bearing_temp_c": (65.0, 0.6),
        "vibration_mm_s": (2.5, 0.15),
        "output_mw": (120.0, 1.5),
    },
    "pump_2": {
        "flow_m3h": (800.0, 6.0),
        "motor_current_a": (140.0, 1.2),
        "inlet_pressure_bar": (4.0, 0.05),
    },
    "grid_bus_3": {
        "voltage_kv": (22.0, 0.05),
        "frequency_hz": (50.0, 0.01),
        "phase_angle_deg": (0.0, 0.4),
    },
}


@dataclass
class FaultLabel:
    """Ground truth for one injected fault. Never shown to the agent."""

    asset: str
    signals: list[str]
    start: int  # sample index (inclusive)
    end: int  # sample index (inclusive)
    cause: str  # human-readable root cause, the eval target


@dataclass
class Scenario:
    readings: list[Reading]
    faults: list[FaultLabel] = field(default_factory=list)


# A fault is a function of (progress in [0,1] through the fault) -> per-signal
# additive/override deltas. Each entry documents the physical signature the
# detector should catch and the agent should explain.
def _bearing_overheat(p: float) -> dict[str, float]:
    # Incipient bearing fault: temperature ramps and vibration climbs with it.
    return {"bearing_temp_c": 18.0 * p, "vibration_mm_s": 3.5 * p}


def _cavitation(p: float) -> dict[str, float]:
    # Pump cavitation: inlet pressure collapses, motor current spikes, flow noisy.
    return {
        "inlet_pressure_bar": -1.6 * p,
        "motor_current_a": 22.0 * p,
        "flow_m3h": -40.0 * p,
    }


def _freq_excursion(p: float) -> dict[str, float]:
    # Load-generation imbalance: frequency drifts off nominal.
    return {"frequency_hz": -0.35 * p}


FAULTS = {
    "bearing_overheat": ("turbine_1", ["bearing_temp_c", "vibration_mm_s"], _bearing_overheat),
    "cavitation": ("pump_2", ["inlet_pressure_bar", "motor_current_a", "flow_m3h"], _cavitation),
    "freq_excursion": ("grid_bus_3", ["frequency_hz"], _freq_excursion),
}

# Readable cause strings, kept out of the agent's view (eval ground truth only).
_CAUSES = {
    "bearing_overheat": "bearing overheating / incipient bearing fault on turbine_1",
    "cavitation": "pump cavitation on pump_2 from loss of inlet pressure",
    "freq_excursion": "grid frequency excursion on grid_bus_3 from load-generation imbalance",
}


class SyntheticGrid:
    """Deterministic generator of labeled multivariate telemetry."""

    def __init__(self, seed: int = 7, start: datetime | None = None, step_s: int = 1) -> None:
        self._rng = random.Random(seed)
        self._start = start or datetime(2026, 1, 1, 0, 0, 0)
        self._step = timedelta(seconds=step_s)

    def generate(self, samples: int = 600, inject: list[tuple[str, int, int]] | None = None) -> Scenario:
        """Produce `samples` timesteps for every asset.

        `inject` is a list of ``(fault_name, start_idx, end_idx)``. Overlapping
        faults on different assets are fine; the labels record each one.
        """
        inject = inject or []
        # Resolve each injection into (asset, signals, fn, start, end, cause).
        active: list[tuple[str, list[str], object, int, int, str]] = []
        labels: list[FaultLabel] = []
        for name, s0, s1 in inject:
            asset, signals, fn = FAULTS[name]
            active.append((asset, signals, fn, s0, s1, _CAUSES[name]))
            labels.append(FaultLabel(asset, signals, s0, s1, _CAUSES[name]))

        readings: list[Reading] = []
        for i in range(samples):
            ts = self._start + i * self._step
            for asset, signals in _ASSETS.items():
                values: dict[str, float] = {}
                for sig, (mean, noise) in signals.items():
                    # Baseline: mean + slow sinusoidal drift + gaussian noise.
                    drift = 0.4 * noise * math.sin(i / 90.0 + _stable_phase(sig))
                    val = mean + drift + self._rng.gauss(0.0, noise)
                    values[sig] = val
                # Apply any active fault touching this asset.
                for f_asset, _f_sig, fn, s0, s1, _cause in active:
                    if f_asset == asset and s0 <= i <= s1:
                        p = (i - s0) / max(1, (s1 - s0))
                        for k, delta in fn(p).items():  # type: ignore[operator]
                            values[k] = values.get(k, 0.0) + delta
                readings.append(Reading(ts=ts, asset=asset, values=values))
        return Scenario(readings=readings, faults=labels)


def demo_scenario() -> Scenario:
    """The scripted scenario used by the offline demo: a bearing overheating on
    turbine_1 that builds over the back half of the window."""
    return SyntheticGrid(seed=7).generate(samples=400, inject=[("bearing_overheat", 220, 380)])
