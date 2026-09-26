"""The demo fleet and scenario builders.

Nine fictional transformers across five fictional substations. Names, sites and
customer counts are invented. Six carry a fault from the catalogue, one has a
drifting gas sensor (the data is wrong, the transformer is fine), two are
healthy, so the ranking has something to separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grid_copilot.transformer.ett import ETT_DIR, fleet_profiles
from grid_copilot.transformer.monitor import Alarm, annotate
from grid_copilot.transformer.sim import Fleet, FleetSimulator, InjectedFault, Nameplate

DAY = 24

PLATES = [
    Nameplate("TR-01", "Nordhafen", 40, "110/20 kV", 1998, 22000, 0.85),
    Nameplate("TR-02", "Nordhafen", 40, "110/20 kV", 2004, 22000, 0.80),
    Nameplate("TR-03", "Altmühl", 63, "110/20 kV", 1987, 41000, 0.90),
    Nameplate("TR-04", "Altmühl", 63, "110/20 kV", 2011, 41000, 0.95),
    Nameplate("TR-05", "Sandberg", 31.5, "110/10 kV", 2015, 9000, 0.75),
    Nameplate("TR-06", "Sandberg", 31.5, "110/10 kV", 1992, 9000, 0.80),
    Nameplate("TR-07", "Kreuzweg", 80, "220/110 kV", 2001, 120000, 0.85),
    Nameplate("TR-08", "Kreuzweg", 80, "220/110 kV", 2019, 120000, 0.80),
    Nameplate("TR-09", "Lindenau", 40, "110/20 kV", 2009, 18000, 0.80),
]

# One fault per affected asset; onset and ramp in hours from the start of the run.
DEMO_FAULTS: dict[str, list[InjectedFault]] = {
    "TR-02": [InjectedFault("oltc_leak", onset_h=55 * DAY, ramp_h=10 * DAY)],
    "TR-03": [InjectedFault("thermal_t3", onset_h=60 * DAY, ramp_h=25 * DAY)],
    "TR-04": [InjectedFault("cooling_failure", onset_h=70 * DAY, ramp_h=2 * DAY)],
    "TR-05": [InjectedFault("external_pd_noise", onset_h=65 * DAY, ramp_h=1)],
    "TR-06": [InjectedFault("internal_pd", onset_h=50 * DAY, ramp_h=40 * DAY)],
    "TR-07": [InjectedFault("oltc_coking", onset_h=62 * DAY, ramp_h=30 * DAY)],
    "TR-09": [InjectedFault("dga_sensor_drift", onset_h=75 * DAY, ramp_h=1)],
}


@dataclass
class Scenario:
    fleet: Fleet
    alarms: dict[str, list[Alarm]]

    def first_alarm(self, asset: str) -> Alarm | None:
        alarms = self.alarms.get(asset) or []
        return alarms[0] if alarms else None


def build(faults: dict[str, list[InjectedFault]] | None = None, plates: list[Nameplate] | None = None,
          seed: int = 7, hours: int = 120 * DAY, prefer_real: bool = True,
          ett_root: Path = ETT_DIR) -> Scenario:
    plates = plates or PLATES
    loads, source = fleet_profiles(len(plates), hours, [p.p99_load_pu for p in plates], seed=seed,
                                   root=ett_root, prefer_real=prefer_real)
    sim = FleetSimulator(seed=seed, hours=hours)
    fleet = sim.run(plates, loads, DEMO_FAULTS if faults is None else faults, load_source=source)
    return Scenario(fleet=fleet, alarms=annotate(fleet))


def single(kind: str, seed: int = 0, severity: float = 1.0, onset_day: int = 60, ramp_days: int = 20,
           hours: int = 120 * DAY, prefer_real: bool = True, plate_index: int = 0) -> Scenario:
    """One transformer with one fault (or none, with kind='none'), for the eval."""
    base = PLATES[plate_index % len(PLATES)]
    plate = Nameplate(f"EV-{kind}-{seed}", base.substation, base.mva, base.kv, base.year, base.customers,
                      1.0 if kind == "overload" else base.p99_load_pu)
    faults = {} if kind == "none" else {
        plate.asset: [InjectedFault(kind, onset_h=onset_day * DAY, ramp_h=max(1, ramp_days * DAY), severity=severity)]
    }
    loads, source = fleet_profiles(1 + plate_index, hours, [plate.p99_load_pu] * (1 + plate_index), seed=seed,
                                   prefer_real=prefer_real)
    sim = FleetSimulator(seed=seed, hours=hours)
    fleet = sim.run([plate], [loads[plate_index]], faults, load_source=source)
    return Scenario(fleet=fleet, alarms=annotate(fleet))
