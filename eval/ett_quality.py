"""Frozen stretches in the public ETT transformer dataset.

ETT (github.com/zhouhaoyi/ETDataset) is a widely used forecasting benchmark:
two real transformers, hourly load and oil temperature over two years. This
script looks for runs of identical consecutive values, the signature of a stuck
transducer or a stale value held by a gateway, and reports the longest run per
column and how many hours sit in runs of 6 h or more.

It reads the raw CSVs only (download them into data/ett/ first, see README).

    python -m eval.ett_quality
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path("data/ett")
COLUMNS = ("HUFL", "HULL", "MUFL", "MULL", "LUFL", "LULL", "OT")


def frozen_runs(values: list[str], min_run: int = 6) -> tuple[int, str, int]:
    """(longest run, value it froze at, hours inside runs of at least `min_run`)."""
    best, best_v, run, in_runs = 1, values[0], 1, 0
    for a, b in zip(values, values[1:]):
        if a == b:
            run += 1
            if run > best:
                best, best_v = run, b
        else:
            if run >= min_run:
                in_runs += run
            run = 1
    if run >= min_run:
        in_runs += run
    return best, best_v, in_runs


def main() -> None:
    for name in ("ETTh1", "ETTh2"):
        path = ROOT / f"{name}.csv"
        if not path.exists():
            raise SystemExit(f"{path} missing; download ETT into data/ett/ first")
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        print(f"{name}: {len(rows)} hourly rows")
        for col in COLUMNS:
            longest, value, hours = frozen_runs([r[col] for r in rows])
            share = hours / len(rows)
            print(f"  {col:5s} longest identical run {longest:5d} h (at {float(value):.3f}); "
                  f"{hours:5d} h ({share:.1%}) in runs of 6 h or more")


if __name__ == "__main__":
    main()
