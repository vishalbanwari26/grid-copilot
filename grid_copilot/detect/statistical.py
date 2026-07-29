"""A fixed-baseline z-score detector.

Deliberately unglamorous, but with one design choice that matters for *developing*
faults. A naive rolling z-score recomputes its mean over a sliding window, so a
slow ramp (a bearing heating up over minutes) is quietly absorbed into the moving
average and never trips, while random noise spikes do. This detector instead
learns each signal's normal operating range over an initial ``baseline`` period
(assumed nominal) and then *freezes* it, scoring every later sample against that
fixed reference. Sustained drifts accumulate score instead of being adapted away.

Two more guards keep the signal-to-noise high:

- **persistence**: a signal must stay out of band for ``persistence`` consecutive
  samples before it is flagged, so a single 4-sigma noise blip is ignored.
- **flatline**: a signal whose variance collapses while its siblings keep moving
  is flagged as a stuck sensor, which a pure magnitude test would miss.

This is the "well-understood, easy-to-evaluate" half of detection; a small
autoencoder can be added behind the same `Detector` interface for correlated,
multivariate faults. The eval harness is what tells you where this baseline's
recall runs out and the autoencoder starts earning its keep.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque

from grid_copilot.types import Anomaly, Reading


class ZScoreDetector:
    name = "zscore_baseline"

    def __init__(
        self,
        baseline: int = 150,
        z_threshold: float = 4.0,
        persistence: int = 5,
        cooldown: int = 150,
        window: int = 120,
        recent: int = 30,
        detect_flatline: bool = True,
        min_base_std: float = 0.0,
    ) -> None:
        self.baseline = baseline
        self.z_threshold = z_threshold
        self.persistence = persistence
        self.cooldown = cooldown
        # A signal whose baseline noise is below `min_base_std` is treated as
        # unscoreable (a near-constant control tag): a z-score against ~0
        # variance would explode on any legitimate change. `detect_flatline`
        # gates stuck-sensor detection, which is noise on tags that are normally
        # constant, so it is disabled for such datasets.
        self.detect_flatline = detect_flatline
        self.min_base_std = min_base_std
        # Frozen baseline stats per (asset, signal), set once `baseline` samples seen.
        self._baseline_buf: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._stats: dict[tuple[str, str], tuple[float, float]] = {}
        # Recent values (for flatline test) and recent readings (for the window).
        self._recent_vals: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=recent)
        )
        self._recent_reads: dict[str, deque[Reading]] = defaultdict(lambda: deque(maxlen=window))
        self._streak: dict[tuple[str, str], int] = defaultdict(int)
        self._cooldown_left: dict[tuple[str, str], int] = defaultdict(int)

    def update(self, reading: Reading) -> list[Anomaly]:
        self._recent_reads[reading.asset].append(reading)
        anomalies: list[Anomaly] = []

        for signal, value in reading.values.items():
            key = (reading.asset, signal)
            self._recent_vals[key].append(value)

            # Still learning the baseline: buffer, freeze when full, score nothing.
            if key not in self._stats:
                buf = self._baseline_buf[key]
                buf.append(value)
                if len(buf) >= self.baseline:
                    mean = statistics.fmean(buf)
                    std = statistics.pstdev(buf)
                    # Near-constant signal: record a sentinel so it is skipped,
                    # rather than z-scored against ~0 variance.
                    if std <= self.min_base_std:
                        self._stats[key] = (mean, 0.0)
                    else:
                        self._stats[key] = (mean, std)
                continue

            if self._cooldown_left[key] > 0:
                self._cooldown_left[key] -= 1

            mean, std = self._stats[key]
            if std == 0.0:  # unscoreable near-constant signal
                continue
            z = abs(value - mean) / std
            flatlined = self.detect_flatline and self._flatlined(reading.asset, signal)

            if z >= self.z_threshold or flatlined:
                self._streak[key] += 1
            else:
                self._streak[key] = 0

            if self._streak[key] >= self.persistence and self._cooldown_left[key] == 0:
                anomalies.append(
                    Anomaly(
                        asset=reading.asset,
                        ts=reading.ts,
                        signal=signal,
                        score=round(z, 2),
                        detector=self.name,
                        window=list(self._recent_reads[reading.asset]),
                    )
                )
                self._cooldown_left[key] = self.cooldown
                self._streak[key] = 0

        return anomalies

    def reset_runtime(self) -> None:
        """Clear persistence/cooldown/recent-value state, keep the frozen baseline.

        For evaluating multiple independent HAI test files against one already-
        fitted baseline: without this, a streak or cooldown left over from the
        tail of one file would bleed into the start of the next unrelated file.
        """
        self._streak.clear()
        self._cooldown_left.clear()
        self._recent_vals.clear()
        self._recent_reads.clear()

    def _flatlined(self, asset: str, signal: str) -> bool:
        """True if this signal's recent variance collapsed while a sibling moves."""
        key = (asset, signal)
        vals = self._recent_vals[key]
        if len(vals) < vals.maxlen or statistics.pstdev(vals) >= 1e-6:
            return False
        for (a, s), other in self._recent_vals.items():
            if a == asset and s != signal and len(other) >= 5 and statistics.pstdev(other) > 1e-3:
                return True
        return False
