"""A tiny domain knowledge base for retrieval-grounded root-cause analysis.

Every note below is written from scratch in plain language: general engineering
knowledge about fault signatures and a lay summary of what each industrial
protocol is for. Nothing here is copied from a standard or a vendor manual. In
the real system this corpus is replaced by retrieval over public protocol specs
(IEC 61850, Modbus, DNP3) and equipment documentation; the interface is the same,
so swapping the source does not change the retriever or the agent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Doc:
    id: str  # short citation handle, e.g. "KB-BEARING-THERMAL"
    title: str
    text: str


CORPUS: list[Doc] = [
    Doc(
        id="KB-BEARING-THERMAL",
        title="Bearing thermal-fault signature",
        text=(
            "A degrading rolling-element bearing usually shows a slow, sustained rise "
            "in bearing temperature that tracks together with rising vibration "
            "amplitude. Because friction both heats the bearing and excites "
            "vibration, seeing temperature and vibration climb together on the same "
            "shaft is a stronger indicator of an incipient bearing fault than either "
            "signal alone. Rotor speed and output power typically stay near nominal "
            "in the early stage."
        ),
    ),
    Doc(
        id="KB-PUMP-CAVITATION",
        title="Pump cavitation signature",
        text=(
            "Cavitation occurs when inlet pressure falls below the vapor pressure of "
            "the fluid and vapor bubbles form and collapse. The telltale pattern is a "
            "drop in inlet or suction pressure accompanied by a spike and increased "
            "noise in motor current, often with erratic flow. Sustained cavitation "
            "erodes the impeller, so it is treated as an actionable fault rather than "
            "a transient."
        ),
    ),
    Doc(
        id="KB-GRID-FREQUENCY",
        title="Grid frequency regulation",
        text=(
            "Interconnected AC grids hold frequency close to a nominal value (50 Hz in "
            "much of the world, 60 Hz in North America). Frequency reflects the "
            "instantaneous balance between generation and load: a shortfall of "
            "generation pulls frequency down, a surplus pushes it up. A sustained "
            "excursion of more than a few tens of millihertz signals a real imbalance "
            "rather than measurement noise and triggers regulation reserves."
        ),
    ),
    Doc(
        id="KB-SENSOR-FLATLINE",
        title="Stuck / flatlined sensor",
        text=(
            "A sensor whose reported value stops changing while related signals keep "
            "moving is likely stuck: a frozen transmitter, a failed ADC channel, or a "
            "cached value on the bus. This is a data-quality fault, not a process "
            "fault, and is distinguished from a genuine steady state by checking that "
            "correlated signals on the same asset still show normal variance."
        ),
    ),
    Doc(
        id="KB-MODBUS",
        title="Modbus in plain terms",
        text=(
            "Modbus is a simple request/response protocol common in industrial gear. A "
            "client polls a server for numeric registers (holding and input registers) "
            "and single-bit coils. It carries no timestamps, quality flags, or "
            "built-in security, so a stale or zeroed register often surfaces as a flat "
            "or implausible telemetry value downstream rather than as an explicit error."
        ),
    ),
    Doc(
        id="KB-DNP3",
        title="DNP3 in plain terms",
        text=(
            "DNP3 is widely used in electric and water utilities. Unlike simple polling "
            "protocols it supports event-based reporting and unsolicited responses, so "
            "a device can push a change-of-state or an out-of-range event with its own "
            "timestamp and quality flag. That makes DNP3 event data useful for lining "
            "up when an anomaly actually began at the device versus when it was polled."
        ),
    ),
    Doc(
        id="KB-IEC61850",
        title="IEC 61850 in plain terms",
        text=(
            "IEC 61850 is a substation-automation standard. It models equipment as "
            "logical nodes with standardized data objects, and uses fast GOOSE messages "
            "on the station LAN to publish protection and status events between "
            "devices. Its self-describing model means an anomaly on a bay can often be "
            "mapped to a specific logical node and function rather than a raw tag name."
        ),
    ),
    Doc(
        id="KB-HAI-PROCESSES",
        title="HAI testbed processes (P1 to P4)",
        text=(
            "The HAI testbed couples four processes through a hardware-in-the-loop "
            "simulator. P1 is a boiler process that moves heat between water loops at "
            "low pressure and moderate temperature. P2 is a turbine process, a rotating "
            "machine that stands in for a steam turbine and reports speed, vibration and "
            "temperature. P3 is a water-treatment process that pumps water to an upper "
            "reservoir and releases it back, standing in for pumped-storage hydropower. "
            "P4 is the HIL layer that ties the physical rigs to virtual steam-turbine "
            "and pumped-storage generation models. A tag's leading P1/P2/P3/P4 says "
            "which of these an anomaly belongs to."
        ),
    ),
    Doc(
        id="KB-ISA-TAGS",
        title="Reading instrument tag names (ISA-style)",
        text=(
            "Industrial tag names encode the instrument type. The measurement letters "
            "are FT for a flow transmitter, PT or PIT for a pressure transmitter, LT or "
            "LIT for a level transmitter, TT or TIT for a temperature transmitter, and "
            "SIT or VT for speed and vibration on rotating machines. Actuators use CV "
            "for control valve: FCV is a flow control valve, PCV a pressure control "
            "valve, LCV a level control valve, and PP marks a pump. A trailing D usually "
            "denotes the controller's demand or command output to the actuator, while a "
            "trailing Z denotes the measured position or feedback. So a sharp change in "
            "a PCV demand tag is a change in the commanded valve position, which should "
            "track its Z feedback and move the associated pressure transmitter."
        ),
    ),
    Doc(
        id="KB-CONTROL-VALVE",
        title="Control-valve command vs process response",
        text=(
            "Under normal closed-loop control, a valve's commanded position and its "
            "measured position move together, and the controlled variable (pressure, "
            "flow or level) responds accordingly. A command that jumps while the "
            "feedback or the process variable does not follow, or a controlled variable "
            "that departs from setpoint without a matching command, points to a control "
            "or sensor fault rather than a normal operator action. In an attack context "
            "this pattern can indicate a manipulated command or a spoofed measurement."
        ),
    ),
    Doc(
        id="KB-CAUSAL-ORDER",
        title="Telling cause from effect by onset order",
        text=(
            "When several signals deviate together, the order in which they start "
            "moving tells cause from effect. If a controller command (a setpoint or a "
            "valve command) deviates before the measured process variables, the command "
            "is the driver: a setpoint change, an operator action, or a spoofed command. "
            "If a measured process variable deviates first and the command only moves "
            "afterward, the controls are responding to an upstream disturbance, and the "
            "root cause lies with whatever perturbed the measurement, not with the valve. "
            "Command and position moving together is ordinary actuation; what marks a "
            "fault is whether that motion led the process response or followed it."
        ),
    ),
]
