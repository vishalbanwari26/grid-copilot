"""Real-time event stream for the investigation loop.

This deliberately reuses Cortex's `EventBus`/`Event` transport rather than
reinventing pub/sub: the whole point of that layer is that a run's reasoning is
observable as it happens, and the same property is what makes an RCA agent
trustworthy. We only supply a domain-specific event vocabulary here.

`Event.type` is a str-enum on the Cortex side and is not runtime-validated, so
passing our own `RCAEvent` values through the same bus works without any change
to Cortex.
"""

from __future__ import annotations

from enum import Enum

from cortex.events import Event, EventBus, Listener

__all__ = ["RCAEvent", "Event", "EventBus", "Listener"]


class RCAEvent(str, Enum):
    ANOMALY_DETECTED = "anomaly_detected"
    INVESTIGATING = "investigating"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    HYPOTHESIS = "hypothesis"
    CRITIQUE = "critique"
    REPORT_READY = "report_ready"
    ABORTED = "aborted"
