"""Load the real HAI dataset into the same `Reading` stream the pipeline expects.

HAI is one ICS testbed whose ~80 tags are named by process: ``P1_*`` (boiler),
``P2_*`` (turbine), ``P3_*`` (water treatment), ``P4_*`` (the HIL simulator). We
treat each process as an *asset*, so its tags become that asset's signals and the
existing per-asset detector and per-asset memory work unchanged. This is the seam
`replay.py` described: the synthetic generator and this loader both produce
``list[Reading]``, and nothing downstream knows which one it got.

Two dataset realities are handled here:

- Many tags are binary control flags (OnOff, AutoGO) that a z-score cannot model.
  We keep only *continuous* signals, detected by their distinct-value count over
  the attack-free baseline slice.
- HAI ships ground-truth labels (``attack`` and per-process ``attack_P1..P3``),
  which we carry as a per-timestep flag so the eval can score detection against
  real attack intervals instead of injected ones.

Download (about 6 MB, official repo):
    curl -L -o data/test1.csv.gz \\
      https://raw.githubusercontent.com/icsdataset/hai/master/hai-21.03/test1.csv.gz
"""

from __future__ import annotations

import csv
import gzip
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from grid_copilot.types import Reading

PROCESSES = ("P1", "P2", "P3", "P4")
_ATTACK_COL = {"P1": "attack_P1", "P2": "attack_P2", "P3": "attack_P3"}


@dataclass
class HaiData:
    readings: list[Reading]  # per timestep, per asset (one Reading each)
    attack: list[int]  # global attack flag, one per timestep
    attack_by_asset: dict[str, list[int]] = field(default_factory=dict)
    signals_by_asset: dict[str, list[str]] = field(default_factory=dict)
    n_steps: int = 0


def _open(path: Path) -> io.TextIOWrapper:
    raw = gzip.open(path, "rt") if path.suffix == ".gz" else open(path)
    return raw  # type: ignore[return-value]


def load_hai(
    path: str | Path,
    baseline: int = 1500,
    min_distinct: int = 20,
    limit: int | None = None,
    signals: dict[str, list[str]] | None = None,
) -> HaiData:
    """Read a HAI CSV (optionally gzipped) into a `HaiData`.

    `baseline` is the number of leading (attack-free) rows used to pick continuous
    signals. `limit` caps the number of timesteps read. Pass `signals` (an
    ``asset -> [columns]`` map) to force a fixed signal set instead of selecting
    one, so a train and a test file can be loaded with identical features.
    """
    path = Path(path)
    with _open(path) as fh:
        reader = csv.DictReader(fh, skipinitialspace=True)
        header = reader.fieldnames or []
        # Candidate continuous columns per process, from the header only.
        cols_by_asset: dict[str, list[str]] = {p: [] for p in PROCESSES}
        for col in header:
            for p in PROCESSES:
                if col.startswith(p + "_"):
                    cols_by_asset[p].append(col)

        rows: list[dict[str, str]] = []
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            rows.append(row)

    # Select continuous signals by distinct-value count over the baseline slice,
    # unless a fixed signal set was supplied (train/test alignment).
    if signals is not None:
        signals_by_asset = {a: list(s) for a, s in signals.items()}
    else:
        signals_by_asset = {}
        for p, cols in cols_by_asset.items():
            keep = []
            for col in cols:
                seen = {rows[i].get(col, "") for i in range(min(baseline, len(rows)))}
                if len(seen) >= min_distinct:
                    keep.append(col)
            if keep:
                signals_by_asset[p] = keep

    readings: list[Reading] = []
    attack: list[int] = []
    attack_by_asset: dict[str, list[int]] = {p: [] for p in signals_by_asset}
    for row in rows:
        ts = datetime.strptime(row["time"].strip(), "%Y-%m-%d %H:%M:%S")
        attack.append(_flag(row.get("attack")))
        for asset, sigs in signals_by_asset.items():
            values = {s: float(row[s]) for s in sigs if row.get(s) not in (None, "")}
            readings.append(Reading(ts=ts, asset=asset, values=values))
            col = _ATTACK_COL.get(asset)
            attack_by_asset[asset].append(_flag(row.get(col)) if col else 0)

    return HaiData(
        readings=readings,
        attack=attack,
        attack_by_asset=attack_by_asset,
        signals_by_asset=signals_by_asset,
        n_steps=len(rows),
    )


def _flag(v: str | None) -> int:
    try:
        return 1 if v is not None and int(float(v)) == 1 else 0
    except ValueError:
        return 0


def attack_intervals(flags: list[int]) -> list[tuple[int, int]]:
    """Compress a 0/1 flag list into inclusive (start, end) index intervals."""
    intervals: list[tuple[int, int]] = []
    start: int | None = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            intervals.append((start, i - 1))
            start = None
    if start is not None:
        intervals.append((start, len(flags) - 1))
    return intervals
