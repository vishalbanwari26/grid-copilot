"""Real load profiles from the public ETT (Electricity Transformer Temperature) dataset.

ETT (github.com/zhouhaoyi/ETDataset, CC BY-ND 4.0) records two real transformers
hourly for two years: the load on the high, middle and low voltage sides as
"useful" and "useless" load (read here as active and reactive), plus oil
temperature. The files are downloaded by the user, never committed, and never
modified; this module only reads them.

Each voltage side of each station becomes one load profile: the apparent load
sqrt(useful^2 + useless^2), scaled so that its 99th percentile sits at a chosen
per-unit loading. That gives the simulated fleet six load shapes with real daily,
weekly and seasonal structure, including real outages and ramps.

When the files are absent (tests, the keyless offline demo), `synthetic_profile`
produces a plausible daily and weekly shape instead, and callers are told which
source they got.
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

ETT_DIR = Path("data/ett")
_SIDES = (("HUFL", "HULL"), ("MUFL", "MULL"), ("LUFL", "LULL"))
ETT_URL = "https://raw.githubusercontent.com/zhouhaoyi/ETDataset/main/ETT-small/{name}.csv"


def ett_available(root: Path = ETT_DIR) -> bool:
    return (root / "ETTh1.csv").exists() and (root / "ETTh2.csv").exists()


def load_ett_profiles(root: Path = ETT_DIR) -> dict[str, list[float]]:
    """Apparent-load series (raw units) keyed like 'ETTh1:HV'."""
    profiles: dict[str, list[float]] = {}
    for name in ("ETTh1", "ETTh2"):
        with open(root / f"{name}.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        for (p, q), side in zip(_SIDES, ("HV", "MV", "LV")):
            profiles[f"{name}:{side}"] = [math.hypot(float(r[p]), float(r[q])) for r in rows]
    return profiles


def scale_to_pu(series: list[float], p99_pu: float) -> list[float]:
    """Scale a raw load series so its 99th percentile equals `p99_pu`."""
    ordered = sorted(series)
    p99 = ordered[int(0.99 * (len(ordered) - 1))] or 1.0
    return [v / p99 * p99_pu for v in series]


def synthetic_profile(hours: int, seed: int, p99_pu: float) -> list[float]:
    """Daily and weekly load shape with noise, for when ETT is not downloaded."""
    rng = random.Random(seed)
    out = []
    for h in range(hours):
        hod, dow = h % 24, (h // 24) % 7
        daily = 0.55 + 0.25 * math.sin(math.pi * (hod - 6) / 12) ** 2 * (1 if 6 <= hod <= 22 else 0.3)
        weekly = 0.85 if dow >= 5 else 1.0
        seasonal = 1.0 + 0.1 * math.cos(2 * math.pi * h / (24 * 365))
        out.append(max(0.05, daily * weekly * seasonal + rng.gauss(0, 0.03)))
    return scale_to_pu(out, p99_pu)


def fleet_profiles(n: int, hours: int, p99s: list[float], seed: int = 0,
                   root: Path = ETT_DIR, prefer_real: bool = True) -> tuple[list[list[float]], str]:
    """`n` per-unit load profiles of length `hours`, and the source used.

    Real profiles are cut from ETT at different offsets so that two assets
    sharing a voltage side still see different weeks.
    """
    if prefer_real and ett_available(root):
        raw = list(load_ett_profiles(root).values())
        out = []
        for i in range(n):
            series = raw[i % len(raw)]
            offset = (i // len(raw)) * 24 * 97 + (i * 24 * 13) % (24 * 60)
            cut = series[offset:offset + hours]
            if len(cut) < hours:
                cut = (cut * (hours // max(1, len(cut)) + 1))[:hours]
            out.append(scale_to_pu(cut, p99s[i]))
        return out, "ETT (real load, zhouhaoyi/ETDataset)"
    return [synthetic_profile(hours, seed * 100 + i, p99s[i]) for i in range(n)], "synthetic load"
