"""The digital twin: an asset model with lifecycle state, over the simulated fleet.

The twin is the structure the rest of the system hangs off:

    grid (one fictional regional network)
      substation (schematic position, voltage levels, lines to neighbours)
        transformer (nameplate, lifecycle, health, alarms, decisions)
          component (active part, tap changer, cooling, bushings)
          sensor (online DGA, top-oil PT100, OLTC temperature, PD sensor)

Every node has a stable id (derived like a CIM mRID) and, where it applies, the
IEC 61850 logical-node class it would publish under, so the same object can be
referred to across systems. Condition flows up the tree: sensors carry their
data-quality grade, components their condition from the subsystem scores, a
transformer its health index, a substation and the grid their worst and average.

Lifecycle is what makes it more than a snapshot: age against design life,
insulation life consumed (an assumed history before the simulated window, plus
the ageing the thermal model computes inside it), tap-changer operations since
the last service against the service interval, sensor calibration due dates,
and the maintenance and reviewed-decision history.

Everything here is fictional: the network, the sites, their positions and the
histories. The positions are schematic, not geographic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from grid_copilot.transformer import quality as dq
from grid_copilot.transformer.decisions import DecisionLog
from grid_copilot.transformer.fleet import Scenario
from grid_copilot.transformer.health import AssetHealth, assess
from grid_copilot.transformer.monitor import RULES
from grid_copilot.transformer.run import HISTORY
from grid_copilot.transformer.semantics import SIGNALS, mrid
from grid_copilot.transformer.sim import AssetData

GRID = {"id": "mittelland", "name": "Mittelland regional network (fictional)",
        "operator": "Netz Mittelland (fictional DSO)"}

# Schematic layout (0-100 on both axes) and voltage levels. Invented.
SUBSTATIONS = {
    "Kreuzweg": {"x": 50, "y": 18, "kv": "220/110 kV", "role": "Grid supply point from the transmission network"},
    "Nordhafen": {"x": 20, "y": 45, "kv": "110/20 kV", "role": "Port and industrial area"},
    "Altmühl": {"x": 78, "y": 45, "kv": "110/20 kV", "role": "Town centre and hospital feeders"},
    "Sandberg": {"x": 30, "y": 80, "kv": "110/10 kV", "role": "Residential and a solar park"},
    "Lindenau": {"x": 72, "y": 82, "kv": "110/20 kV", "role": "Rural feeders and wind connection"},
}
LINES = [("Kreuzweg", "Nordhafen"), ("Kreuzweg", "Altmühl"), ("Nordhafen", "Sandberg"),
         ("Altmühl", "Lindenau"), ("Sandberg", "Lindenau")]

DESIGN_LIFE_Y = 40
OLTC_SERVICE_OPS = 150_000
OLTC_SERVICE_YEARS = 7
# Assumed average ageing before the simulated window, in multiples of the rated
# rate (distribution transformers usually run well below rated on average).
HISTORIC_AGEING = 0.35

# Invented date of the last tap-changer service per unit. Operations since then
# are estimated from the operating rate seen in the simulated window. TR-02 was
# overhauled recently (see its maintenance history).
OLTC_LAST_SERVICE = {
    "TR-01": "2020-05-14", "TR-02": "2024-10-08", "TR-03": "2016-09-02", "TR-04": "2021-04-20",
    "TR-05": "2020-11-11", "TR-06": "2018-06-30", "TR-07": "2022-03-30", "TR-08": "2019-10-01",
    "TR-09": "2021-07-12",
}

COMPONENTS = {
    "active_part": {"name": "Active part (core and windings)", "subsystems": ("dga", "pd"),
                    "ln": "YPTR", "cim": "PowerTransformer",
                    "signals": ("h2_ppm", "c2h2_ppm", "c2h4_ppm", "co_ppm", "hotspot_c", "pd_pc")},
    "oltc": {"name": "On-load tap changer", "subsystems": ("oltc",), "ln": "YLTC", "cim": "TapChanger",
             "signals": ("tap_position", "oltc_op_time_s", "oltc_motor_peak_a", "oltc_temp_diff_c")},
    "cooling": {"name": "Cooling (ONAN/ONAF, two fan groups)", "subsystems": ("thermal",), "ln": "CCGR",
                "cim": "PowerTransformer", "signals": ("top_oil_c", "fans_on", "load_pu", "ambient_c")},
}

SENSORS = {
    "dga_monitor": {"name": "Online DGA monitor (7 gases)", "ln": "SIML",
                    "channels": ("h2_ppm", "ch4_ppm", "c2h6_ppm", "c2h4_ppm", "c2h2_ppm", "co_ppm", "co2_ppm"),
                    "calibration_months": 12},
    "top_oil_pt100": {"name": "Top-oil temperature (PT100)", "ln": "STMP", "channels": ("top_oil_c",),
                      "calibration_months": 36},
    "oltc_temp": {"name": "Tap-changer compartment temperature", "ln": "STMP", "channels": ("oltc_temp_c",),
                  "calibration_months": 36},
    "pd_uhf": {"name": "Partial discharge sensor (UHF)", "ln": "SPDC", "channels": ("pd_pc", "pd_rate"),
               "calibration_months": 24},
    "load_measurement": {"name": "Load measurement (SCADA, CT/VT)", "ln": "MMXU", "channels": ("load_pu",),
                         "calibration_months": 48},
}


def _months_ago(today: date, months: int) -> date:
    y, m = divmod(today.month - 1 - months, 12)
    return date(today.year + y, m + 1, min(today.day, 28))


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def _grade_worst(grades: list[str]) -> str:
    order = {"trusted": 0, "suspect": 1, "untrusted": 2}
    return max(grades, key=lambda g: order.get(g, 0)) if grades else "trusted"


@dataclass
class Twin:
    scenario: Scenario
    decisions: DecisionLog | None = None
    _health: dict[str, AssetHealth] = field(default_factory=dict)

    # --- shared helpers ------------------------------------------------------

    @property
    def now(self) -> datetime:
        a = next(iter(self.scenario.fleet.assets.values()))
        return a.ts[-1]

    def health(self, asset: str) -> AssetHealth:
        if asset not in self._health:
            self._health[asset] = assess(self.scenario.fleet.assets[asset])
        return self._health[asset]

    def _asset(self, asset: str) -> AssetData:
        return self.scenario.fleet.assets[asset]

    def _quality(self, asset: str) -> dict[str, dq.ChannelQuality]:
        a = self._asset(asset)
        return dq.assess(a, len(a.ts) - 1)

    def _decisions(self, asset: str) -> list[dict]:
        if self.decisions is None:
            return []
        return [{"decided_at": r.decided_at, "verdict": r.verdict, "reviewer": r.reviewer,
                 "fault_class": r.final_fault_class, "reason": r.reason, "alarm": r.proposal["alarm"]}
                for r in self.decisions.records() if r.proposal["asset"] == asset]

    def assets_in(self, substation: str) -> list[str]:
        return [n for n, a in self.scenario.fleet.assets.items() if a.plate.substation == substation]

    # --- lifecycle ------------------------------------------------------------

    def lifecycle(self, asset: str) -> dict:
        a = self._asset(asset)
        now = self.now
        age = now.year - a.plate.year + (now.timetuple().tm_yday / 365)
        window_h = len(a.ts)
        historic_h = max(0.0, (age - window_h / 8760) * 8760 * HISTORIC_AGEING)
        window_life_h = sum(a.channels["ageing_rate"])
        used_h = historic_h + window_life_h
        design_h = DESIGN_LIFE_Y * 8760
        recent_rate = sum(a.channels["ageing_rate"][-24 * 30:]) / (24 * 30)
        remaining_h = max(0.0, design_h - used_h)
        years_left = remaining_h / 8760 / max(recent_rate, HISTORIC_AGEING)
        service = OLTC_LAST_SERVICE.get(asset, f"{max(a.plate.year, 2015)}-01-01")
        service_date = date.fromisoformat(service)
        ops_per_day = len(a.tap_ops) / (window_h / 24)
        ops_now = int((now.date() - service_date).days * ops_per_day)
        ops_left = OLTC_SERVICE_OPS - ops_now
        due_by_ops = now.date().toordinal() + int(ops_left / max(ops_per_day, 1e-6)) if ops_left > 0 else now.date().toordinal()
        due_by_time = _add_months(service_date, OLTC_SERVICE_YEARS * 12)
        oltc_due = min(date.fromordinal(due_by_ops), due_by_time)
        history = [{"date": d, "text": t, "kind": "maintenance"} for d, t in HISTORY.get(asset, [])]
        history += [{"date": r["decided_at"][:10], "text": f"{r['verdict']} {r['fault_class']} ({r['reviewer']})"
                     + (f": {r['reason']}" if r["reason"] else ""), "kind": "decision"} for r in self._decisions(asset)]
        return {
            "age_years": round(age, 1), "design_life_years": DESIGN_LIFE_Y,
            "insulation_life_used_pct": round(100 * used_h / design_h, 1),
            "insulation_life_used_in_window_h": round(window_life_h),
            "ageing_rate_30d": round(recent_rate, 3),
            "remaining_life_years_at_current_rate": round(min(years_left, 50), 1),
            "remaining_life_capped": years_left >= 50,
            "oltc_ops_since_service": ops_now,
            "oltc_service_interval_ops": OLTC_SERVICE_OPS, "oltc_last_service": service,
            "oltc_ops_per_day": round(ops_per_day, 1), "oltc_service_due": oltc_due.isoformat(),
            "oltc_service_overdue": oltc_due <= now.date(),
            "history": sorted(history, key=lambda h: h["date"], reverse=True),
            "assumptions": (f"Insulation life before the simulated window assumes an average ageing rate of "
                            f"{HISTORIC_AGEING}x rated; design life {DESIGN_LIFE_Y} years; tap-changer service every "
                            f"{OLTC_SERVICE_OPS:,} operations or {OLTC_SERVICE_YEARS} years, whichever comes first; "
                            f"operations since the last service are estimated from the rate in the simulated window; "
                            f"remaining insulation life is shown up to 50 years."),
        }

    # --- components and sensors -------------------------------------------------

    def components(self, asset: str) -> list[dict]:
        h = self.health(asset)
        out = []
        for cid, c in COMPONENTS.items():
            subs = [h.subscores[s] for s in c["subsystems"]]
            score = min(s.score for s in subs)
            out.append({"id": cid, "name": c["name"], "condition": score,
                        "reasons": [r for s in subs for r in s.reasons], "iec61850_ln": c["ln"], "cim": c["cim"],
                        "mrid": mrid(asset, cid), "signals": list(c["signals"])})
        return out

    def sensors(self, asset: str) -> list[dict]:
        a = self._asset(asset)
        q = self._quality(asset)
        today = self.now.date()
        # A reviewed "sensor fault" decision marks the sensor behind that alarm as
        # untrusted: the twin keeps what a person established, not only what the
        # automatic checks can see (a smooth drift passes them all).
        rule_signal = {r.code: r.signal for r in RULES}
        condemned = {}
        if self.decisions is not None:
            for r in self.decisions.records():
                if r.proposal["asset"] == asset and r.final_fault_class == "sensor_fault" and r.verdict != "defer":
                    sig = rule_signal.get(r.proposal["alarm"])
                    if sig:
                        condemned[sig] = f"sensor fault confirmed by {r.reviewer} on {r.decided_at[:10]}"
        out = []
        for i, (sid, s) in enumerate(SENSORS.items()):
            grades = [q[c].grade for c in s["channels"] if c in q]
            reasons = [f"{c}: {r}" for c in s["channels"] if c in q for r in q[c].reasons]
            for c in s["channels"]:
                if c in condemned:
                    grades.append("untrusted")
                    reasons.append(f"{c}: {condemned[c]}")
            # Invented calibration history, staggered per asset and sensor.
            offset = (sum(map(ord, asset)) + 5 * i) % s["calibration_months"]
            last_cal = _months_ago(today, offset + 1)
            due = _add_months(last_cal, s["calibration_months"])
            out.append({"id": sid, "name": s["name"], "iec61850_ln": s["ln"], "channels": list(s["channels"]),
                        "grade": _grade_worst(grades), "reasons": reasons, "last_calibration": last_cal.isoformat(),
                        "calibration_due": due.isoformat(), "calibration_overdue": due <= today,
                        "installed": str(max(a.plate.year, 2012)), "mrid": mrid(asset, sid)})
        return out

    # --- views -------------------------------------------------------------------

    def transformer_summary(self, asset: str) -> dict:
        a = self._asset(asset)
        h = self.health(asset)
        alarm = self.scenario.first_alarm(asset)
        sensors = self.sensors(asset)
        decisions = self._decisions(asset)
        load = a.channels["load_pu"][-24 * 7:]
        return {
            "asset": asset, "substation": a.plate.substation, "mva": a.plate.mva, "kv": a.plate.kv,
            "year": a.plate.year, "customers": a.plate.customers, "health_index": h.health_index, "risk": h.risk,
            "worst": h.worst, "action": h.action,
            "alarm": {"code": alarm.code, "ts": alarm.ts.isoformat(), "text": alarm.text} if alarm else None,
            "data_trust": _grade_worst([s["grade"] for s in sensors]),
            "decision": decisions[-1] if decisions else None,
            "load_now_pu": round(load[-1], 2), "load_peak_7d_pu": round(max(load), 2),
            "top_oil_now_c": round(a.channels["top_oil_c"][-1], 1), "mrid": mrid(asset),
        }

    def grid(self) -> dict:
        units = {n: self.transformer_summary(n) for n in self.scenario.fleet.assets}
        subs = []
        for sid, s in SUBSTATIONS.items():
            members = [units[n] for n in self.assets_in(sid)]
            subs.append({"id": sid, **s, "transformers": [m["asset"] for m in members],
                         "worst_hi": min((m["health_index"] for m in members), default=100),
                         "alarms": sum(1 for m in members if m["alarm"]),
                         "awaiting_review": sum(1 for m in members if m["alarm"] and not m["decision"]),
                         "customers": max((m["customers"] for m in members), default=0),
                         "data_trust": _grade_worst([m["data_trust"] for m in members])})
        his = [u["health_index"] for u in units.values()]
        return {
            **GRID, "as_of": self.now.isoformat(), "load_source": self.scenario.fleet.load_source,
            "substations": subs, "lines": [{"a": a, "b": b} for a, b in LINES],
            "kpis": {
                "transformers": len(units), "substations": len(subs),
                "mean_health_index": round(sum(his) / len(his)),
                "alarms": sum(1 for u in units.values() if u["alarm"]),
                "awaiting_review": sum(1 for u in units.values() if u["alarm"] and not u["decision"]),
                "customers_behind_poor_units": sum(s["customers"] for s in subs if s["worst_hi"] < 50),
                "data_not_trusted": sum(1 for u in units.values() if u["data_trust"] != "trusted"),
            },
            "ranking": sorted(units.values(), key=lambda u: (-u["risk"], u["health_index"])),
        }

    def substation(self, sid: str) -> dict:
        if sid not in SUBSTATIONS:
            raise KeyError(sid)
        units = [self.transformer_summary(n) for n in self.assets_in(sid)]
        neighbours = sorted({b if a == sid else a for a, b in LINES if sid in (a, b)})
        return {"id": sid, **SUBSTATIONS[sid], "grid": GRID["name"], "neighbours": neighbours,
                "transformers": units,
                "combined_mva": sum(u["mva"] for u in units), "mrid": mrid(sid, "Substation")}

    def transformer(self, asset: str) -> dict:
        return {**self.transformer_summary(asset), "lifecycle": self.lifecycle(asset),
                "components": self.components(asset), "sensors": self.sensors(asset),
                "subscores": {k: {"score": v.score, "reasons": v.reasons}
                              for k, v in self.health(asset).subscores.items()}}

    def component(self, asset: str, cid: str) -> dict:
        a = self._asset(asset)
        q = self._quality(asset)
        if cid in COMPONENTS:
            node = next(c for c in self.components(asset) if c["id"] == cid)
            kind = "component"
        elif cid in SENSORS:
            node = next(s for s in self.sensors(asset) if s["id"] == cid)
            node["signals"] = node["channels"]
            kind = "sensor"
        else:
            raise KeyError(cid)
        step = 12
        series = {}
        for c in node["signals"]:
            vals = a.channels[c]
            series[c] = {"values": [round(sum(vals[i:i + step]) / len(vals[i:i + step]), 3)
                                    for i in range(0, len(vals), step)],
                         "label": SIGNALS[c].label, "unit": SIGNALS[c].unit, "ln": SIGNALS[c].iec61850_ln,
                         "kind": SIGNALS[c].kind, "grade": q[c].grade if c in q else "not_checked",
                         "reasons": q[c].reasons if c in q else []}
        extra = {}
        if cid == "oltc":
            lc = self.lifecycle(asset)
            extra = {k: lc[k] for k in ("oltc_ops_since_service", "oltc_service_interval_ops", "oltc_last_service",
                                        "oltc_ops_per_day", "oltc_service_due", "oltc_service_overdue")}
            recent = a.tap_ops[-10:]
            extra["recent_operations"] = [{"ts": o.ts.isoformat(), "from": o.from_tap, "to": o.to_tap,
                                           "duration_s": o.duration_s, "motor_a": o.motor_peak_a,
                                           "completed": o.completed} for o in reversed(recent)]
        alarms = [{"code": x.code, "ts": x.ts.isoformat(), "text": x.text}
                  for x in self.scenario.alarms.get(asset, []) if x.signal in node["signals"]]
        return {"asset": asset, "substation": a.plate.substation, "kind": kind, **node, "series": series,
                "ts": [t.isoformat() for t in a.ts[::step]], "alarms": alarms, **extra}
