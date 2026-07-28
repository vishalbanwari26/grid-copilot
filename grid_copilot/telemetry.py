"""A queryable telemetry history for the investigation.

The detector attaches only the readings it buffered *before* it fired, so an
anomaly's window is an early snapshot of a developing incident. On real HAI data
that made the agent name the signal that moved first (a pressure-control valve)
and miss the one that dominated the full attack (a level-control valve). This log
lets the investigation ask for a window centered on the event that reaches both
back before onset (for a clean baseline) and forward past detection (to the
developed incident), rather than reasoning over the snapshot alone.

Reaching forward means waiting for post-detection samples, which is realistic: an
operator lets an incident develop for a moment before diagnosing it. In replay
those samples are already in hand; a live deployment would buffer briefly.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from grid_copilot.types import Reading


class TelemetryLog:
    def __init__(self) -> None:
        self._by_asset: dict[str, list[Reading]] = defaultdict(list)
        self._ts: dict[str, list[datetime]] = {}

    @classmethod
    def from_readings(cls, readings: Iterable[Reading]) -> "TelemetryLog":
        log = cls()
        for r in readings:
            log._by_asset[r.asset].append(r)
        for asset, reads in log._by_asset.items():
            reads.sort(key=lambda r: r.ts)
            log._ts[asset] = [r.ts for r in reads]
        return log

    def window(self, asset: str, center: datetime, before: int, after: int) -> list[Reading]:
        """Readings from `before` samples ahead of `center` to `after` samples past
        it (clipped to what exists), so the analysis spans the whole incident."""
        reads = self._by_asset.get(asset, [])
        if not reads:
            return []
        i = bisect_left(self._ts[asset], center)
        return reads[max(0, i - before) : min(len(reads), i + after + 1)]
