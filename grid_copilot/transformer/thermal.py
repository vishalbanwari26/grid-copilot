"""Thermal model of an oil-immersed transformer: top oil, hot spot, ageing.

This is the difference-equation form of the thermal model in the IEC loading
guide (IEC 60076-7), written out from the published structure: top-oil rise
follows the load with a long oil time constant, and the hot-spot gradient over
top oil is the difference of two first-order terms, which reproduces the
overshoot a winding shows after a load step. Parameter values are typical for a
mid-size ONAF unit, not any specific transformer.

Cooling has two stages. With fans off the unit behaves as ONAN, whose rating is a
fraction of the ONAF rating, so the same load is a larger per-unit load. Fans
switch on and off on top-oil temperature with hysteresis, the way a cooling
controller does, and a fan-group failure is modelled as a loss of part of the
forced-air rating.

Ageing uses the relative ageing rate for non-thermally-upgraded paper, which
doubles for every 6 K of hot spot above 98 C.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThermalParams:
    rated_top_oil_rise: float = 45.0  # K, at rated ONAF load
    hotspot_gradient: float = 26.0  # K, hot spot over top oil at rated load (H * g_r)
    loss_ratio: float = 8.0  # R: load losses / no-load losses at rated load
    oil_exponent: float = 0.8  # x
    winding_exponent: float = 1.3  # y
    k11: float = 0.5
    k21: float = 2.0
    k22: float = 2.0
    tau_oil_min: float = 150.0
    tau_winding_min: float = 7.0
    onan_fraction: float = 0.6  # ONAN rating as a fraction of ONAF
    fans_on_c: float = 50.0  # top oil at which the fans start
    fans_off_c: float = 42.0  # and stop


@dataclass
class ThermalState:
    top_oil: float
    dh1: float = 0.0
    dh2: float = 0.0
    fans_on: bool = False

    @property
    def hotspot_rise(self) -> float:
        return self.dh1 - self.dh2


def ageing_rate(hotspot_c: float) -> float:
    """Relative ageing rate V for non-upgraded paper (1.0 at a 98 C hot spot)."""
    return 2.0 ** ((hotspot_c - 98.0) / 6.0)


class ThermalModel:
    """Steps top oil and hot spot forward in time for one transformer.

    `forced_air_available` is the fraction of the ONAF forced-air capacity that
    works (1.0 healthy, lower after a fan-group failure). The model used by the
    monitoring layer to compute an *expected* top oil always assumes 1.0, which is
    exactly what makes a cooling failure visible as a residual.
    """

    def __init__(self, params: ThermalParams | None = None, ambient0: float = 15.0) -> None:
        self.p = params or ThermalParams()
        self.state = ThermalState(top_oil=ambient0 + 5.0)

    def _effective_k(self, k: float, forced_air_available: float) -> float:
        p = self.p
        if not self.state.fans_on:
            return k / p.onan_fraction
        # With fans on, capacity sits between ONAN and full ONAF in proportion to
        # the working share of the forced air.
        capacity = p.onan_fraction + (1.0 - p.onan_fraction) * forced_air_available
        return k / capacity

    def step(self, k: float, ambient: float, minutes: float = 60.0,
             forced_air_available: float = 1.0, substep_min: float = 2.0) -> ThermalState:
        """Advance by `minutes` at per-unit load `k` (relative to the ONAF rating)."""
        p = self.p
        s = self.state
        n = max(1, int(round(minutes / substep_min)))
        dt = minutes / n
        for _ in range(n):
            if s.top_oil >= p.fans_on_c:
                s.fans_on = True
            elif s.top_oil <= p.fans_off_c:
                s.fans_on = False
            ke = self._effective_k(k, forced_air_available)
            ultimate_rise = p.rated_top_oil_rise * ((1 + ke * ke * p.loss_ratio) / (1 + p.loss_ratio)) ** p.oil_exponent
            s.top_oil += dt / (p.k11 * p.tau_oil_min) * (ultimate_rise - (s.top_oil - ambient))
            grad = p.hotspot_gradient * ke ** p.winding_exponent
            s.dh1 += dt / (p.k22 * p.tau_winding_min) * (p.k21 * grad - s.dh1)
            s.dh2 += dt / ((1.0 / p.k22) * p.tau_oil_min) * ((p.k21 - 1.0) * grad - s.dh2)
        return s

    @property
    def hotspot(self) -> float:
        return self.state.top_oil + self.state.hotspot_rise


def expected_top_oil(loads: list[float], ambients: list[float], params: ThermalParams | None = None,
                     top_oil0: float | None = None) -> list[float]:
    """Top oil a healthy unit would show for this load and ambient history.

    The monitoring layer compares this with the measured top oil; a sustained
    positive residual at high load is the signature of lost cooling capacity.
    """
    model = ThermalModel(params, ambient0=ambients[0] if ambients else 15.0)
    if top_oil0 is not None:
        model.state.top_oil = top_oil0
    out = []
    for k, amb in zip(loads, ambients):
        model.step(k, amb)
        out.append(model.state.top_oil)
    return out
