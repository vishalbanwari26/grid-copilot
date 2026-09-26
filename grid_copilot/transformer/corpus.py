"""Knowledge notes for transformer condition monitoring.

Written from scratch in plain language: general engineering practice, not text
from any standard, guide or vendor manual. Where a note names a method (Duval
triangle, the IEC ratio method, the loading-guide thermal model) it describes how
the method is used, not its published wording.
"""

from __future__ import annotations

from grid_copilot.rag.corpus import Doc

TRANSFORMER_CORPUS: list[Doc] = [
    Doc("KB-DGA-INCREMENT", "Diagnose the gas increment, not the total",
        "Dissolved gas totals include years of normal ageing, so fault identification should use the "
        "gas a fault has added: the difference between recent values and a baseline taken before the "
        "rise began. Ratios and triangle methods applied to totals in an old unit can point at a fault "
        "type that is really the background. Small increments are dominated by measurement noise, so an "
        "increment of a few ppm should not be classified at all."),
    Doc("KB-DUVAL-T1", "Duval triangle 1 in practice",
        "The triangle uses the relative shares of methane, ethylene and acetylene. Methane-dominated "
        "gas points at partial discharge, ethylene-dominated gas at hot thermal faults, and a large "
        "acetylene share at electrical discharges, low energy (D1) or arcing (D2). It always returns a "
        "zone, even for gas that did not come from the active part, which is why the source of the gas "
        "has to be checked before the zone is trusted."),
    Doc("KB-IEC-RATIOS", "Three-ratio method",
        "The ratios acetylene/ethylene, methane/hydrogen and ethylene/ethane are compared with coded "
        "ranges for partial discharge, discharges of low and high energy, and thermal faults of rising "
        "temperature. A result of 'unclassified' is common and means the gas does not fit a single "
        "fault type; agreement between the ratio method and the triangle raises confidence."),
    Doc("KB-ACETYLENE", "Acetylene in the main tank",
        "Acetylene forms only at very high temperatures, in arcs or in faults above roughly 700 C. Any "
        "sustained acetylene in main-tank oil is treated as significant and is not closed without "
        "finding its source. A steady rise with time suggests an active internal discharge; step rises "
        "that follow tap changes suggest the gas comes from the tap changer instead."),
    Doc("KB-PAPER-CO", "Paper involvement: carbon oxides",
        "Cellulose insulation produces carbon monoxide and carbon dioxide when it overheats. A thermal "
        "fault that involves paper shows carbon oxides rising together with the hydrocarbons, with a low "
        "CO2/CO ratio in the increment (below about 3). Paper damage is not reversible, so a thermal "
        "fault with paper involvement is more urgent than one in oil or steel."),
    Doc("KB-OLTC-LEAK", "OLTC compartment leak into the main tank",
        "A diverter switch breaks load current in its own oil compartment, and that oil is full of "
        "acetylene and hydrogen from normal switching. If the barrier or a seal between the compartment "
        "and the main tank leaks, those gases appear in the main tank and the main-tank DGA looks like "
        "an internal discharge. The giveaway is that the gas rises in steps with tap operations rather "
        "than steadily with time, that the added gas is unusually rich in acetylene compared with hydrogen "
        "(hydrogen escapes from the compartment oil much faster, so a C2H2/H2 ratio above about 2 in the "
        "main-tank increase is a warning sign), and that other signs of an internal discharge (PD, heating) "
        "are absent. "
        "Confirm by comparing the oil levels of the two conservators and by sampling the compartment oil. "
        "Mistaking a leak for internal arcing can take a healthy unit out of service."),
    Doc("KB-OLTC-CONTACTS", "OLTC contact overheating and coking",
        "Worn or coked contacts in a tap changer have a rising contact resistance and heat with the "
        "square of the current. Monitoring compares the tap-changer compartment temperature with the "
        "main-tank top oil: normally the compartment runs slightly cooler, and a difference that grows "
        "and tracks the load is the signature of contact heating. Left alone it can end in a contact "
        "failure, so it calls for an inspection soon."),
    Doc("KB-OLTC-DRIVE", "Tap-changer motor drive health",
        "Each tap change has a characteristic operating time and motor current. A drive that takes "
        "longer and draws more current per operation points at mechanical trouble: worn gears, a "
        "sticking brake, lubrication or spring problems. An operation that does not complete is an "
        "alarm in itself. Drive problems do not produce gas in the main tank."),
    Doc("KB-THERMAL-MODEL", "Thermal model residual and cooling",
        "The loading-guide thermal model predicts top-oil and hot-spot temperature from load and "
        "ambient. When the measured top oil sits well above the prediction for a healthy unit, "
        "especially at high load with the fans running, the unit has lost cooling capacity: failed fans "
        "or pumps, blocked radiators, or closed valves. If measured and predicted agree, a high "
        "temperature is simply the load and the weather, not a fault."),
    Doc("KB-OVERLOAD-AGEING", "Overload and insulation ageing",
        "Paper ages faster as the hot spot rises: roughly doubling for every 6 K above the reference "
        "temperature. Short overloads in cool weather are acceptable, sustained overloads in hot weather "
        "consume insulation life quickly. The response to a pure overload is load management (transfer "
        "load, restrict, schedule), not an internal inspection."),
    Doc("KB-PD-SOURCE", "Internal PD or external interference",
        "Partial discharge inside the insulation is locked to the phase of the applied voltage, and for "
        "voids it clusters on the rising part of both half cycles, roughly symmetrically. External "
        "interference (switching, radio, corona on nearby lines) is often not phase-locked, arrives in "
        "bursts, and follows the time of day or the weather. Internal PD in oil also produces hydrogen, "
        "so a PD alarm with flat hydrogen and no phase locking is most likely external. PD alarms are "
        "common false alarms, and confirming the source comes before planning an outage."),
    Doc("KB-PD-INTERNAL", "Internal partial discharge",
        "Sustained, phase-locked PD that grows over weeks, together with rising hydrogen in the oil, "
        "indicates discharge in voids or on surfaces inside the tank. It erodes insulation and can "
        "develop into a breakdown. The usual response is to confirm with additional sensors or an "
        "acoustic location, reduce stress if possible, and plan an outage."),
    Doc("KB-SENSOR-FAULTS", "When the data is wrong, not the transformer",
        "Online monitors fail too. A transducer can freeze and keep reporting one value, a gas cell can "
        "drift, a gateway can drop data. Signs: a value that does not change for hours when it normally "
        "moves with load and weather, a reading that breaks physics, a single gas rising smoothly while "
        "every related measurement stays flat. A frozen top-oil reading distorts everything computed from "
        "it, including the hot spot and the tap-changer temperature difference. Before acting on an alarm, "
        "check the grade of the data behind it; the right conclusion for bad data is a sensor fault and a "
        "repair, not a diagnosis of the transformer."),
    Doc("KB-LAB-REFERENCE", "The laboratory as reference",
        "Online DGA monitors are calibrated against laboratory analysis, which remains the reference. "
        "When an online reading drives an expensive decision, or when it looks inconsistent with the rest "
        "of the evidence, an oil sample for the laboratory settles whether the gas is really in the oil. "
        "A large disagreement between online and laboratory values points at the monitor."),
    Doc("KB-URGENCY", "Choosing an urgency",
        "Immediate: arcing or a fast-developing fault in the active part, where waiting risks a failure. "
        "Weeks: a confirmed fault that is developing slowly, or a tap-changer defect that could fail in "
        "service. Next outage: a defect that is stable or contained, such as a compartment leak or a "
        "drive that still completes its operations. Routine: no fault, including overload that only "
        "needs load management and external interference. A sensor fault is weeks: the asset is probably "
        "fine, but it is being watched with a broken instrument until the sensor is repaired."),
]
