"""Replay a batch of readings as a stream.

Right now this just yields readings in timestamp order, which is all the offline
demo needs. It is the seam where a real feed slots in: replaying a HAI CSV, or
pulling from a historian/OPC-UA bridge, becomes another function returning the
same `Iterator[Reading]`, and nothing downstream changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from grid_copilot.types import Reading


def replay(readings: Iterable[Reading]) -> Iterator[Reading]:
    """Yield readings sorted by (timestamp, asset), the order a live feed sees."""
    yield from sorted(readings, key=lambda r: (r.ts, r.asset))
