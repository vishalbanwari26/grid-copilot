"""The detector contract.

A detector consumes readings one at a time and emits `Anomaly`s. Keeping it to a
single streaming method means the statistical baseline here and a future
autoencoder are interchangeable behind the same interface: the pipeline calls
`update` and does not care which one is wired in.
"""

from __future__ import annotations

from typing import Protocol

from grid_copilot.types import Anomaly, Reading


class Detector(Protocol):
    name: str

    def update(self, reading: Reading) -> list[Anomaly]:
        """Ingest one reading; return any anomalies it triggers (often none)."""
        ...
