"""Fleet simulator and fault catalogue.

Every transformer is stepped hour by hour: the thermal model on a real (or
synthetic) load profile, dissolved gases from normal ageing plus any injected
fault, an online DGA monitor that samples every four hours with noise, the OLTC
regulating voltage (each operation with a duration and a motor current), and a PD
sensor that reports apparent charge, pulse rate and a phase-resolved histogram.

The fault catalogue is the ground truth. Each fault has a canonical
`fault_class` the investigator must name and a `subsystem`. Two of the entries
are decoys that raise alarms without an internal fault (external PD
interference, and plain overload), because telling those apart from real faults
is most of the job in condition monitoring. Two more are data faults: the
transformer is fine and a sensor is not, and the right answer is to distrust the
data rather than diagnose the asset.

The gas signatures are chosen so that the gas *increments* a fault produces fall
in the Duval triangle zone and IEC ratio code that fault type is known for. They
are illustrative generation rates, not measurements.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from grid_copilot.transformer.thermal import ThermalModel, ThermalParams, ageing_rate

GASES = ("H2", "CH4", "C2H6", "C2H4", "C2H2", "CO", "CO2")
PRPD_BINS = 12  # 30 degree phase bins over one power-frequency cycle


@dataclass(frozen=True)
class FaultSpec:
    name: str
    label: str
    subsystem: str  # active_part | oltc | cooling | monitoring | none
    fault_class: str  # the canonical answer the investigator must give
    true_cause: str  # plain-language ground truth, for the judge and the UI
    critical: bool  # missing it could end in a failure


FAULTS: dict[str, FaultSpec] = {f.name: f for f in [
    FaultSpec("thermal_t3", "Core / connection hot spot", "active_part", "thermal_fault_high",
              "high-temperature thermal fault (>700 C) in the active part, such as a bad joint or circulating current",
              True),
    FaultSpec("paper_overheating", "Winding overheating with paper", "active_part", "thermal_fault_paper",
              "medium-temperature winding hot spot that is degrading the cellulose (paper) insulation", True),
    FaultSpec("internal_pd", "Internal partial discharge", "active_part", "partial_discharge",
              "partial discharge in the insulation inside the tank (voids or gas bubbles)", True),
    FaultSpec("arcing_d2", "Internal arcing", "active_part", "arcing",
              "high-energy arcing inside the main tank", True),
    FaultSpec("oltc_leak", "OLTC compartment leak", "oltc", "oltc_compartment_leak",
              "oil from the OLTC diverter compartment leaking into the main tank, carrying switching-arc gases",
              False),
    FaultSpec("oltc_coking", "OLTC contact coking", "oltc", "oltc_contact_overheating",
              "overheating OLTC contacts (rising contact resistance and coking) in the diverter compartment",
              True),
    FaultSpec("oltc_drive", "OLTC drive mechanism", "oltc", "oltc_drive_mechanism",
              "degrading OLTC motor-drive mechanism: slow, laboured tap changes", False),
    FaultSpec("cooling_failure", "Cooling fan failure", "cooling", "cooling_failure",
              "loss of forced-air cooling capacity (a failed fan group)", False),
    FaultSpec("external_pd_noise", "External interference (decoy)", "none", "external_interference",
              "no internal fault: the PD sensor picks up external interference", False),
    FaultSpec("overload", "Overload, healthy unit (decoy)", "none", "overload",
              "no internal fault: the unit is healthy but loaded above its rating in hot weather", False),
    FaultSpec("dga_sensor_drift", "DGA sensor drift (data fault)", "monitoring", "sensor_fault",
              "no internal fault: the online DGA monitor's hydrogen reading is drifting upward", False),
    FaultSpec("top_oil_sensor_stuck", "Frozen top-oil sensor (data fault)", "monitoring", "sensor_fault",
              "no internal fault: the top-oil temperature sensor is frozen at one value", False),
]}
FAULT_CLASSES = sorted({f.fault_class for f in FAULTS.values()} | {"no_fault"})

# Gas generation at full severity, ppm per day, in GASES order.
_SIGNATURES: dict[str, tuple[float, ...]] = {
    "thermal_t3": (1.5, 3.0, 0.6, 6.0, 0.12, 0.5, 4.0),
    "paper_overheating": (0.8, 3.0, 1.0, 2.0, 0.01, 6.0, 15.0),
    "internal_pd": (12.0, 1.0, 0.12, 0.012, 0.0, 0.3, 1.0),
    "arcing_d2": (4.0, 1.0, 0.15, 2.0, 1.4, 0.5, 2.0),
}
# Gases carried per tap operation once the OLTC barrier leaks (diverter switching
# oil). Acetylene-rich relative to hydrogen, because hydrogen escapes from the
# compartment oil far faster than acetylene does.
_LEAK_PER_OP = (0.03, 0.02, 0.004, 0.025, 0.09, 0.02, 0.1)
_BACKGROUND = (0.05, 0.03, 0.02, 0.01, 0.0, 0.8, 6.0)  # ppm/day at a 98 C hot spot
_DGA_FLOOR = (2.0, 1.0, 1.0, 0.5, 0.2, 5.0, 20.0)  # measurement noise floor
_INITIAL = (12.0, 8.0, 5.0, 3.0, 0.0, 250.0, 2500.0)


@dataclass
class InjectedFault:
    kind: str
    onset_h: int
    ramp_h: int = 24 * 14
    severity: float = 1.0

    def level(self, h: int) -> float:
        """0 before onset, ramping linearly to `severity`."""
        if h < self.onset_h:
            return 0.0
        return self.severity * min(1.0, (h - self.onset_h) / max(1, self.ramp_h))


@dataclass(frozen=True)
class Nameplate:
    asset: str
    substation: str
    mva: float
    kv: str
    year: int
    customers: int  # downstream customers, drives criticality
    p99_load_pu: float  # how hard this unit is loaded


@dataclass
class TapOp:
    h: int
    ts: datetime
    from_tap: int
    to_tap: int
    duration_s: float
    motor_peak_a: float
    completed: bool = True


@dataclass
class FleetEvent:
    ts: datetime
    asset: str
    code: str
    text: str
    severity: str = "info"  # info | warning | alarm


@dataclass
class AssetData:
    plate: Nameplate
    ts: list[datetime]
    channels: dict[str, list[float]]
    prpd: list[list[float]]  # per hour, PRPD_BINS pulse counts
    tap_ops: list[TapOp]
    events: list[FleetEvent]
    faults: list[InjectedFault] = field(default_factory=list)
    # What is really in the oil, hour by hour. Never shown to monitoring or the
    # agent directly; only a lab sample (with its own error) reads from it.
    true_gas: list[tuple[float, ...]] = field(default_factory=list)

    def index_at(self, ts: datetime) -> int:
        """Index of the last sample at or before `ts`."""
        step = (self.ts[1] - self.ts[0]) if len(self.ts) > 1 else timedelta(hours=1)
        i = int((ts - self.ts[0]) / step)
        return max(0, min(len(self.ts) - 1, i))

    @property
    def truth(self) -> FaultSpec | None:
        return FAULTS[self.faults[0].kind] if self.faults else None


@dataclass
class Fleet:
    assets: dict[str, AssetData]
    start: datetime
    hours: int
    load_source: str

    def events(self, asset: str | None = None) -> list[FleetEvent]:
        items = [e for a in self.assets.values() if asset in (None, a.plate.asset) for e in a.events]
        return sorted(items, key=lambda e: e.ts)


def ambient_c(ts: datetime, rng: random.Random) -> float:
    """Central-European ambient: seasonal plus diurnal swing, with weather noise."""
    doy = ts.timetuple().tm_yday
    seasonal = 10.0 + 9.0 * math.sin(2 * math.pi * (doy - 105) / 365)
    diurnal = 5.0 * math.sin(2 * math.pi * (ts.hour - 9) / 24)
    return seasonal + diurnal + rng.gauss(0, 1.0)


def _prpd(kind: str, rate: float, rng: random.Random) -> list[float]:
    """Pulse counts per phase bin for one hour of PD activity of a given kind."""
    if rate <= 0:
        return [0.0] * PRPD_BINS
    if kind == "internal":
        # Void discharges cluster on the rising slope of each half cycle, symmetric.
        weights = [3, 5, 4, 1, 0.3, 0.2, 3, 5, 4, 1, 0.3, 0.2]
    else:
        weights = [1.0] * PRPD_BINS  # interference is not phase-locked
    total = sum(weights)
    return [max(0.0, rate * 60 * w / total * (1 + rng.gauss(0, 0.15))) for w in weights]


class FleetSimulator:
    def __init__(self, seed: int = 7, start: datetime = datetime(2026, 5, 1), hours: int = 24 * 120,
                 params: ThermalParams | None = None) -> None:
        self.seed = seed
        self.start = start
        self.hours = hours
        self.params = params or ThermalParams()

    def run(self, plates: list[Nameplate], loads: list[list[float]],
            faults: dict[str, list[InjectedFault]], load_source: str = "") -> Fleet:
        assets = {}
        for i, plate in enumerate(plates):
            rng = random.Random(f"{self.seed}:{plate.asset}")
            assets[plate.asset] = self._simulate(plate, loads[i], faults.get(plate.asset, []), rng)
        return Fleet(assets=assets, start=self.start, hours=self.hours, load_source=load_source)

    def _simulate(self, plate: Nameplate, load: list[float], faults: list[InjectedFault],
                  rng: random.Random) -> AssetData:
        p = self.params
        kinds = {f.kind: f for f in faults}
        ts_list = [self.start + timedelta(hours=h) for h in range(self.hours)]
        amb0 = ambient_c(ts_list[0], rng)
        model = ThermalModel(p, ambient0=amb0)
        # Spin the thermal state up on the first day so the series starts settled.
        for _ in range(48):
            model.step(load[0], amb0)

        ch: dict[str, list[float]] = {k: [] for k in (
            "load_pu", "ambient_c", "top_oil_c", "hotspot_c", "fans_on", "ageing_rate",
            "oltc_temp_c", "oltc_temp_diff_c", "tap_position", "tap_ops", "oltc_op_time_s",
            "oltc_motor_peak_a", "pd_pc", "pd_rate", *[g.lower() + "_ppm" for g in GASES],
        )}
        prpd: list[list[float]] = []
        tap_ops: list[TapOp] = []
        events: list[FleetEvent] = []
        gas_true = list(_INITIAL)
        gas_meas = list(_INITIAL)
        tap = 0
        last_op_time, last_motor = 5.1, 3.8
        fans_prev = model.state.fans_on
        noise_burst = 0
        true_gas: list[tuple[float, ...]] = []
        stuck_at: float | None = None

        for h, ts in enumerate(ts_list):
            k = max(0.0, load[h] if h < len(load) else load[-1])
            if "overload" in kinds and h >= kinds["overload"].onset_h:
                k *= 1.0 + 0.5 * kinds["overload"].level(h)
            amb = ambient_c(ts, rng)
            if "overload" in kinds:
                amb += 8.0 * kinds["overload"].level(h)  # the heat wave that comes with it
            forced = 1.0
            if "cooling_failure" in kinds:
                forced = 1.0 - 0.6 * kinds["cooling_failure"].level(h)
            st = model.step(k, amb, forced_air_available=forced)
            true_top_oil = st.top_oil + rng.gauss(0, 0.3)
            top_oil = true_top_oil
            stuck = kinds.get("top_oil_sensor_stuck")
            if stuck and h >= stuck.onset_h:
                # The transducer freezes on a hot afternoon and keeps reporting that value.
                if stuck_at is None and ts.hour == 15:
                    stuck_at = round(true_top_oil, 2)
                if stuck_at is not None:
                    top_oil = stuck_at
            # A monitor computes hot spot from measured top oil plus the modelled gradient.
            hotspot = top_oil + st.hotspot_rise
            if st.fans_on != fans_prev:
                events.append(FleetEvent(ts, plate.asset, "FANS_ON" if st.fans_on else "FANS_OFF",
                                         f"Cooling fans {'started' if st.fans_on else 'stopped'} at top oil {top_oil:.1f} C"))
                fans_prev = st.fans_on

            # --- OLTC: regulate voltage with the load, one step per operation.
            target = int(round((k - 0.6) * 10 + rng.gauss(0, 0.6)))
            target = max(-8, min(8, target))
            ops_this_hour = 0
            drive = kinds.get("oltc_drive")
            dlev = drive.level(h) if drive else 0.0
            durations, motors = [], []
            while target != tap and ops_this_hour < 4:
                step = 1 if target > tap else -1
                duration = 5.1 + rng.gauss(0, 0.05) + 1.8 * dlev + abs(rng.gauss(0, 0.35 * dlev))
                motor = 3.8 + rng.gauss(0, 0.08) + 1.3 * dlev + abs(rng.gauss(0, 0.2 * dlev))
                completed = not (dlev > 0.75 and rng.random() < 0.04)
                op = TapOp(h, ts + timedelta(minutes=rng.randint(0, 59)), tap,
                           tap + step if completed else tap, round(duration, 2), round(motor, 2), completed)
                tap_ops.append(op)
                durations.append(duration)
                motors.append(motor)
                ops_this_hour += 1
                events.append(FleetEvent(op.ts, plate.asset, "TAP_CHANGE",
                                         f"Tap {op.from_tap:+d} -> {op.to_tap:+d}, {op.duration_s:.2f} s, motor {op.motor_peak_a:.2f} A"))
                if not completed:
                    events.append(FleetEvent(op.ts, plate.asset, "TAP_CHANGE_INCOMPLETE",
                                             f"Tap change {op.from_tap:+d} -> {op.from_tap + step:+d} did not complete", "alarm"))
                    break
                tap += step
                if "oltc_leak" in kinds and kinds["oltc_leak"].level(h) > 0:
                    lev = kinds["oltc_leak"].level(h)
                    for g in range(len(GASES)):
                        gas_true[g] += _LEAK_PER_OP[g] * lev
            if durations:
                last_op_time = sum(durations) / len(durations)
                last_motor = sum(motors) / len(motors)

            coke = kinds.get("oltc_coking")
            oltc_temp = true_top_oil - 2.5 + rng.gauss(0, 0.3)
            if coke:
                oltc_temp += 14.0 * coke.level(h) * max(0.2, k) ** 2

            # --- Gases: background ageing plus fault generation, per hour.
            v = ageing_rate(true_top_oil + st.hotspot_rise)
            for g in range(len(GASES)):
                gas_true[g] += _BACKGROUND[g] * min(v, 8.0) / 24.0
            for kind, sig in _SIGNATURES.items():
                if kind in kinds:
                    lev = kinds[kind].level(h)
                    for g in range(len(GASES)):
                        gas_true[g] += sig[g] * lev / 24.0
            if h % 4 == 0:  # online DGA samples every four hours
                for g in range(len(GASES)):
                    val = gas_true[g] * (1 + rng.gauss(0, 0.03)) + rng.gauss(0, _DGA_FLOOR[g] * 0.3)
                    if GASES[g] == "C2H2" and val < 0.5:
                        val = 0.0
                    gas_meas[g] = round(max(0.0, val), 1)
                drift = kinds.get("dga_sensor_drift")
                if drift and h >= drift.onset_h:
                    # A smooth upward drift of the hydrogen cell, about 4 ppm a day.
                    gas_meas[0] = round(gas_meas[0] + 4.0 * drift.severity * (h - drift.onset_h) / 24, 1)
            true_gas.append(tuple(round(x, 2) for x in gas_true))

            # --- Partial discharge.
            pd_pc = math.exp(rng.gauss(math.log(8.0), 0.3))
            pd_rate = max(0.0, rng.gauss(2.0, 0.5))
            pattern = _prpd("noise", pd_rate, rng)
            if "internal_pd" in kinds and kinds["internal_pd"].level(h) > 0:
                lev = kinds["internal_pd"].level(h)
                pd_pc = max(pd_pc, 40 + 750 * lev * math.exp(rng.gauss(0, 0.2)))
                pd_rate = 5 + 60 * lev * (1 + rng.gauss(0, 0.1))
                pattern = [a + b for a, b in zip(_prpd("internal", pd_rate, rng), pattern)]
            if "arcing_d2" in kinds and kinds["arcing_d2"].level(h) > 0.3 and rng.random() < 0.3:
                pd_pc = max(pd_pc, 150 + 150 * rng.random())
                pd_rate = max(pd_rate, 15.0)
                pattern = [a + b for a, b in zip(_prpd("internal", 15.0, rng), pattern)]
            if "external_pd_noise" in kinds and kinds["external_pd_noise"].level(h) > 0:
                # Interference comes in bursts, mostly in working hours.
                if noise_burst == 0 and 7 <= ts.hour <= 18 and rng.random() < 0.08:
                    noise_burst = rng.randint(3, 8)
                if noise_burst > 0:
                    noise_burst -= 1
                    pd_pc = max(pd_pc, 400 + 1100 * rng.random())
                    pd_rate = 150 + 100 * rng.random()
                    pattern = _prpd("noise", pd_rate, rng)

            ch["load_pu"].append(round(k, 3))
            ch["ambient_c"].append(round(amb, 2))
            ch["top_oil_c"].append(round(top_oil, 2))
            ch["hotspot_c"].append(round(hotspot, 2))
            ch["fans_on"].append(1.0 if st.fans_on else 0.0)
            ch["ageing_rate"].append(round(v, 4))
            ch["oltc_temp_c"].append(round(oltc_temp, 2))
            ch["oltc_temp_diff_c"].append(round(oltc_temp - top_oil, 2))
            ch["tap_position"].append(float(tap))
            ch["tap_ops"].append(float(ops_this_hour))
            ch["oltc_op_time_s"].append(round(last_op_time, 3))
            ch["oltc_motor_peak_a"].append(round(last_motor, 3))
            ch["pd_pc"].append(round(pd_pc, 1))
            ch["pd_rate"].append(round(pd_rate, 2))
            for g, name in enumerate(GASES):
                ch[name.lower() + "_ppm"].append(gas_meas[g])
            prpd.append([round(x, 1) for x in pattern])

            if rng.random() < 0.0015:
                events.append(FleetEvent(ts, plate.asset, "COMMS_LOSS", "Monitoring gateway lost contact for 1 h"))

        return AssetData(plate=plate, ts=ts_list, channels=ch, prpd=prpd, tap_ops=tap_ops,
                         events=sorted(events, key=lambda e: e.ts), faults=faults, true_gas=true_gas)
