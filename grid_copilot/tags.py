"""Decode industrial tag names into plain descriptions.

On live runs the agent kept misreading HAI tags, calling a pressure-control-valve
position (``P1_PCV01Z``) a "pressure signal", because a raw tag carries no meaning
to the model. Retrieval of a documentation note was not enough; the model ignored
an optional doc. Putting the decoded meaning directly into the primary telemetry
evidence removes the ambiguity at the source.

Decoding is best-effort and uses standard ISA-style instrument letters plus the
HAI process prefixes. Tags that do not match (e.g. the synthetic generator's
``bearing_temp_c``) decode to an empty string, so annotation is simply skipped
and nothing downstream changes for non-HAI data.
"""

from __future__ import annotations

import re

_PROCESS = {
    "P1": "boiler",
    "P2": "turbine",
    "P3": "water-treatment",
    "P4": "HIL",
}

# Instrument code -> description. Longer codes are checked first.
_INSTRUMENT = [
    ("PIT", "pressure transmitter"),
    ("LIT", "level transmitter"),
    ("TIT", "temperature transmitter"),
    ("SIT", "shaft/speed transmitter"),
    ("FCV", "flow-control-valve"),
    ("PCV", "pressure-control-valve"),
    ("LCV", "level-control-valve"),
    ("VXT", "vibration sensor"),
    ("VYT", "vibration sensor"),
    ("VTR", "vibration sensor"),
    ("FT", "flow transmitter"),
    ("PT", "pressure transmitter"),
    ("LT", "level transmitter"),
    ("TT", "temperature transmitter"),
    ("VT", "vibration sensor"),
    ("PP", "pump"),
]

_TAG = re.compile(r"^(P[1-4])_([A-Z]+)")


def process_name(asset: str) -> str:
    """Human name for a HAI process id ('P1' -> 'boiler'); unchanged if unknown."""
    return _PROCESS.get(asset[:2], asset)


_MEASUREMENT = ("PIT", "LIT", "TIT", "SIT", "FT", "PT", "LT", "TT", "VXT", "VYT", "VTR", "VT")


def tag_role(tag: str) -> str:
    """Classify a tag's causal role: 'command' (controller output to an actuator),
    'position' (actuator feedback), 'measurement' (a process variable), or 'other'.

    The distinction is what lets an investigation reason about causal direction: a
    command that moves before the measurements is a driver (a setpoint change or a
    spoofed command); a measurement that moves first is a disturbance the controls
    then respond to.
    """
    m = _TAG.match(tag)
    if not m:
        return "other"
    letters = m.group(2)
    if tag.endswith("D"):
        return "command"
    if "CV" in letters and tag.endswith("Z"):
        return "position"
    if letters.startswith(_MEASUREMENT):
        return "measurement"
    return "other"


def decode_tag(tag: str) -> str:
    """Return a short plain-language description of a tag, or "" if unrecognized."""
    m = _TAG.match(tag)
    if not m:
        return ""
    process = _PROCESS.get(m.group(1), "")
    letters = m.group(2)
    kind = next((desc for code, desc in _INSTRUMENT if letters.startswith(code)), "")
    if not kind:
        return process or ""
    # Trailing D = controller demand/command, Z = measured position/feedback.
    suffix = ""
    if tag.endswith("D"):
        suffix = " command"
    elif tag.endswith("Z"):
        suffix = " position" if "control-valve" in kind else " feedback"
    parts = [p for p in (process, kind + suffix) if p]
    return " ".join(parts)
