"""Deterministic transformer diagnostics.

These are the checks a condition-monitoring engineer runs by hand, written as pure
functions so the agent can call them and cite the result instead of doing the
arithmetic in its head. The model reasons over their output; it never replaces
them.

- `duval_triangle_1`: fault zone from the relative shares of CH4, C2H4 and C2H2.
- `iec_ratios`: the three-ratio method (C2H2/C2H4, CH4/H2, C2H4/C2H6).
- `gas_increment`: what a fault added, as the difference between recent and
  baseline averages. Diagnosing on the increment, not the total, keeps years of
  normal ageing gas out of the answer.
- `co2_co_ratio`: a low CO2/CO ratio in the increment points at paper involvement.
- `prpd_phase_locking`: how strongly PD pulses are tied to the phase of the
  voltage. Internal PD is phase-locked; external interference usually is not.
- `event_correlation`: what share of gas increases follow an event of a given
  kind (tap changes, for an OLTC compartment leak).

Zone boundaries and ratio limits follow the published Duval triangle 1 and IEC
60599 tables; the values are facts about the method, the wording here is ours.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

ZONE_NAMES = {
    "PD": "partial discharge",
    "T1": "thermal fault below 300 C",
    "T2": "thermal fault 300-700 C",
    "T3": "thermal fault above 700 C",
    "D1": "low-energy discharge",
    "D2": "high-energy discharge (arcing)",
    "DT": "mix of thermal and electrical faults",
}


@dataclass
class DuvalResult:
    zone: str
    pct_ch4: float
    pct_c2h4: float
    pct_c2h2: float

    @property
    def meaning(self) -> str:
        return ZONE_NAMES.get(self.zone, self.zone)


def duval_triangle_1(ch4: float, c2h4: float, c2h2: float) -> DuvalResult | None:
    """Duval triangle 1 zone for the three gases (ppm, or ppm increments)."""
    total = ch4 + c2h4 + c2h2
    if total <= 0:
        return None
    m, e, a = 100 * ch4 / total, 100 * c2h4 / total, 100 * c2h2 / total
    if m >= 98:
        zone = "PD"
    elif e < 23 and a > 13:
        zone = "D1"
    elif (e >= 23 and a > 29) or (23 <= e < 40 and 13 < a <= 29):
        zone = "D2"
    elif e < 20 and a < 4:
        zone = "T1"
    elif 20 <= e < 50 and a < 4:
        zone = "T2"
    elif e >= 50 and a < 15:
        zone = "T3"
    else:
        zone = "DT"
    return DuvalResult(zone, round(m, 1), round(e, 1), round(a, 1))


@dataclass
class RatioResult:
    code: str  # PD, D1, D2, T1, T2, T3, or "unclassified"
    c2h2_c2h4: float
    ch4_h2: float
    c2h4_c2h6: float


def iec_ratios(h2: float, ch4: float, c2h6: float, c2h4: float, c2h2: float) -> RatioResult:
    """IEC 60599 three-ratio interpretation. Returns 'unclassified' when no row fits,
    which happens often in practice and is itself useful information."""
    eps = 1e-9
    r1 = c2h2 / (c2h4 + eps)
    r2 = ch4 / (h2 + eps)
    r3 = c2h4 / (c2h6 + eps)
    code = "unclassified"
    if r2 < 0.1 and r3 < 0.2:
        code = "PD"
    elif r1 > 1 and 0.1 <= r2 <= 0.5 and r3 > 1:
        code = "D1"
    elif 0.6 <= r1 <= 2.5 and 0.1 <= r2 <= 1 and r3 > 2:
        code = "D2"
    elif r1 < 0.2 and r2 > 1 and r3 > 4:
        code = "T3"
    elif r1 < 0.1 and r2 > 1 and 1 <= r3 <= 4:
        code = "T2"
    elif r2 > 1 and r3 < 1:
        code = "T1"
    return RatioResult(code, round(r1, 3), round(r2, 3), round(r3, 3))


def gas_increment(series: list[float], base: tuple[int, int], recent: tuple[int, int]) -> float:
    """Mean over `recent` minus mean over `base` (index ranges), floored at zero."""
    b = series[base[0]:base[1]] or series[:1]
    r = series[recent[0]:recent[1]] or series[-1:]
    return max(0.0, statistics.fmean(r) - statistics.fmean(b))


def rate_per_day(series: list[float], hours_per_sample: float = 1.0) -> float:
    """Least-squares slope in units per day."""
    n = len(series)
    if n < 3:
        return 0.0
    xs = range(n)
    mx, my = (n - 1) / 2, statistics.fmean(series)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, series))
    return sxy / sxx * 24.0 / hours_per_sample


def co2_co_ratio(d_co2: float, d_co: float) -> float | None:
    if d_co < 5:  # too little CO produced to say anything
        return None
    return d_co2 / d_co


def prpd_phase_locking(hist: list[float]) -> float:
    """0 for pulses spread evenly over the cycle, towards 1 when they are
    concentrated in a few phase bins. Computed as 1 - normalised entropy."""
    import math

    total = sum(hist)
    if total <= 0:
        return 0.0
    ps = [h / total for h in hist if h > 0]
    entropy = -sum(p * math.log(p) for p in ps)
    return round(1.0 - entropy / math.log(len(hist)), 3)


def prpd_symmetry(hist: list[float]) -> float:
    """1.0 when the positive and negative half cycles carry equal activity."""
    half = len(hist) // 2
    a, b = sum(hist[:half]), sum(hist[half:])
    if a + b <= 0:
        return 0.0
    return round(1.0 - abs(a - b) / (a + b), 3)


def classify_prpd(hist: list[float]) -> str:
    """Rough source class from a phase-resolved histogram."""
    lock = prpd_phase_locking(hist)
    if sum(hist) <= 0:
        return "no activity"
    if lock < 0.05:
        return "not phase-locked (typical of external interference or noise)"
    rising = sum(hist[0:3]) + sum(hist[6:9])
    if rising / sum(hist) > 0.7 and prpd_symmetry(hist) > 0.7:
        return "phase-locked on the rising slopes of both half cycles (typical of internal voids)"
    return "phase-locked, asymmetric (surface or corona discharge)"


def _detrend(values: list[float]) -> list[float]:
    """Residuals of a straight-line fit against the index."""
    n = len(values)
    mx, my = (n - 1) / 2, statistics.fmean(values)
    sxx = sum((i - mx) ** 2 for i in range(n)) or 1.0
    slope = sum((i - mx) * (v - my) for i, v in enumerate(values)) / sxx
    return [v - (my + slope * (i - mx)) for i, v in enumerate(values)]


def event_correlation(gas_series: list[float], event_counts: list[float],
                      sample_every: int = 8) -> dict[str, float]:
    """Does a gas rise in step with the number of events, or with time?

    The series are cut into windows of `sample_every` hours. For each window we
    take the gas increase and the number of events (tap changes) in it. Both are
    detrended first, so a fault that is simply getting worse over the period does
    not look like a link to the events. Returned:

    - `r`: correlation between the detrended increase and the detrended count;
    - `per_event`: gas increase per event from the same fit;
    - `busy`, `quiet`: mean raw gas increase per window in windows with more, and
      with fewer, events than the median;
    - `windows`: how many windows went in.

    A fault inside the tank makes gas at a rate set by time and load, so busy and
    quiet windows rise alike. Gas carried in by each tap operation rises with the
    operation count, so busy windows rise faster.
    """
    xs, ys = [], []
    for start in range(0, len(gas_series) - sample_every, sample_every):
        end = start + sample_every
        xs.append(sum(event_counts[start:end]))
        ys.append(gas_series[end] - gas_series[start])
    out = {"r": 0.0, "per_event": 0.0, "busy": 0.0, "quiet": 0.0, "windows": float(len(xs))}
    if len(xs) < 4:
        return out
    med = statistics.median(xs)
    busy = [y for x, y in zip(xs, ys) if x > med]
    quiet = [y for x, y in zip(xs, ys) if x <= med]
    out.update(busy=round(statistics.fmean(busy), 3) if busy else 0.0,
               quiet=round(statistics.fmean(quiet), 3) if quiet else 0.0)
    dx, dy = _detrend(xs), _detrend(ys)
    sx, sy = statistics.pstdev(dx), statistics.pstdev(dy)
    if sx == 0 or sy == 0:
        return out
    cov = sum(a * b for a, b in zip(dx, dy)) / len(dx)
    out.update(r=round(cov / (sx * sy), 3), per_event=round(cov / (sx * sx), 4))
    return out
