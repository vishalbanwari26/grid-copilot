"""What each signal means, in terms other systems understand.

Internally the simulator and tools use short channel names (`h2_ppm`,
`oltc_temp_diff_c`). That is fine inside one program and useless across system
boundaries, where a value is only meaningful if the other side knows which
equipment it belongs to, what it measures, in which unit, and whether it was
measured or calculated. This module is that shared vocabulary:

- the equipment it belongs to (main tank, tap changer, cooling, PD sensing);
- the IEC 61850-7-4 logical-node class a substation device would publish it
  under (SIML for insulating-liquid supervision including gas-in-oil, YLTC for
  the tap changer, SPDC for partial-discharge supervision, STMP for temperature
  supervision, CCGR for the cooling group, MMXU for electrical measurements,
  MMET for meteorological data, YPTR for the power transformer itself);
- the CIM class of the equipment (PowerTransformer, TapChanger) and of the value
  (Analog for measured quantities, Discrete for states and counts);
- measured or calculated, and for calculated values the inputs, which is what
  lets data-quality grades flow from inputs to results.

The mapping is at the level of logical-node and CIM classes. Data-object names
inside a logical node vary by edition and vendor profile and are not claimed
here; a real integration would pin them against the edition in use.

`decision_submodel` renders a reviewed decision as a JSON document shaped like an
Asset Administration Shell submodel (idShort, semanticId, submodelElements), so it
could travel with the asset's digital twin. It is not validated against the AAS
metamodel.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

_NS = uuid.UUID("6f1c2b1e-9a3d-4c55-8f1e-2d7a0c9b4e11")  # fixed namespace for stable ids


@dataclass(frozen=True)
class Signal:
    name: str
    label: str
    unit: str
    equipment: str  # main_tank | tap_changer | cooling | pd_sensing | environment | grid
    iec61850_ln: str
    cim_class: str  # of the value
    kind: str = "measured"  # measured | calculated
    inputs: tuple[str, ...] = ()


def _gas(name: str, label: str) -> Signal:
    return Signal(f"{name.lower()}_ppm", f"{label} ({name}) dissolved in oil", "ppm", "main_tank", "SIML", "Analog")


SIGNALS: dict[str, Signal] = {s.name: s for s in [
    Signal("load_pu", "Load relative to the ONAF rating", "pu", "grid", "MMXU", "Analog"),
    Signal("ambient_c", "Ambient air temperature", "degC", "environment", "MMET", "Analog"),
    Signal("top_oil_c", "Top-oil temperature", "degC", "main_tank", "STMP", "Analog"),
    Signal("hotspot_c", "Winding hot-spot temperature (thermal model)", "degC", "main_tank", "STMP", "Analog",
           "calculated", ("top_oil_c", "load_pu")),
    Signal("ageing_rate", "Relative insulation ageing rate", "1", "main_tank", "YPTR", "Analog",
           "calculated", ("top_oil_c", "load_pu")),
    Signal("fans_on", "Cooling fans running", "bool", "cooling", "CCGR", "Discrete"),
    Signal("oltc_temp_c", "Tap-changer compartment oil temperature", "degC", "tap_changer", "STMP", "Analog"),
    Signal("oltc_temp_diff_c", "Tap-changer compartment minus main-tank temperature", "K", "tap_changer", "STMP",
           "Analog", "calculated", ("oltc_temp_c", "top_oil_c")),
    Signal("tap_position", "Tap position", "step", "tap_changer", "YLTC", "Discrete"),
    Signal("tap_ops", "Tap operations in the hour", "count", "tap_changer", "YLTC", "Discrete"),
    Signal("oltc_op_time_s", "Tap-change operating time", "s", "tap_changer", "YLTC", "Analog"),
    Signal("oltc_motor_peak_a", "Motor-drive peak current", "A", "tap_changer", "YLTC", "Analog"),
    Signal("pd_pc", "Partial discharge apparent charge", "pC", "pd_sensing", "SPDC", "Analog"),
    Signal("pd_rate", "Partial discharge pulse rate", "1/s", "pd_sensing", "SPDC", "Analog"),
    _gas("H2", "Hydrogen"), _gas("CH4", "Methane"), _gas("C2H6", "Ethane"), _gas("C2H4", "Ethylene"),
    _gas("C2H2", "Acetylene"), _gas("CO", "Carbon monoxide"), _gas("CO2", "Carbon dioxide"),
]}

EQUIPMENT_CIM = {
    "main_tank": "PowerTransformer", "tap_changer": "TapChanger", "cooling": "PowerTransformer",
    "pd_sensing": "PowerTransformer", "environment": "Substation", "grid": "PowerTransformer",
}


def ref(channel: str) -> str:
    """Short standard reference for a channel, e.g. 'SIML (Hydrogen (H2) dissolved in oil, ppm)'."""
    s = SIGNALS.get(channel)
    if s is None:
        return channel
    return f"{s.iec61850_ln} ({s.label}, {s.unit}{', calculated' if s.kind == 'calculated' else ''})"


def mrid(asset: str, equipment: str = "PowerTransformer") -> str:
    """A stable identifier for a piece of equipment, in the spirit of a CIM mRID."""
    return str(uuid.uuid5(_NS, f"{asset}/{equipment}"))


def decision_submodel(record: dict) -> dict:
    """A reviewed decision as an AAS-shaped submodel (see module docstring)."""
    p = record["proposal"]
    asset = p["asset"]
    return {
        "idShort": "ConditionDecision",
        "id": f"urn:gridcopilot:decision:{record['decision_id']}",
        "semanticId": "urn:gridcopilot:semantics:ConditionDecision:1",
        "asset": {"name": asset, "cimClass": "PowerTransformer", "mRID": mrid(asset)},
        "submodelElements": [
            {"idShort": "Trigger", "value": p["alarm"], "timestamp": p["alarm_ts"]},
            {"idShort": "ProposedFaultClass", "value": p["fault_class"], "proposedBy": p["model"],
             "promptVersion": p["prompt_version"], "confidence": p["confidence"]},
            {"idShort": "DecidedFaultClass", "value": record["final_fault_class"]},
            {"idShort": "Verdict", "value": record["verdict"], "reviewer": record["reviewer"],
             "reason": record["reason"], "decidedAt": record["decided_at"]},
            {"idShort": "Action", "value": record["final_action"], "urgency": p["urgency"]},
            {"idShort": "WouldChangeConclusion", "value": p["would_change_conclusion"]},
            {"idShort": "DataQuality", "value": p["data_quality"]},
            {"idShort": "Evidence", "value": [e["tool"] for e in p["evidence"]]},
            {"idShort": "Signals", "value": [
                {"name": s.name, "iec61850LN": s.iec61850_ln, "cimClass": s.cim_class, "unit": s.unit,
                 "kind": s.kind, "equipment": EQUIPMENT_CIM[s.equipment],
                 "equipmentMRID": mrid(asset, EQUIPMENT_CIM[s.equipment])}
                for s in SIGNALS.values()]},
            {"idShort": "Integrity", "hash": record["hash"], "previousHash": record["prev_hash"]},
        ],
    }
