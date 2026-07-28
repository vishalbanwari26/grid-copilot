"""Grid Copilot: anomaly detection + agentic root-cause analysis on grid/OT telemetry.

Detect an anomaly in industrial telemetry, then let an agent investigate it,
gathering evidence from the telemetry window, equipment/protocol documentation,
and memory of prior incidents on the same asset, and produce a cited root-cause
report. The orchestration primitives (provider-agnostic LLM layer, agent base,
event bus) are reused from Cortex; the per-asset incident memory is Mnemos.
"""

from grid_copilot.types import Anomaly, Evidence, Hypothesis, IncidentReport, Reading

__all__ = ["Reading", "Anomaly", "Evidence", "Hypothesis", "IncidentReport"]
__version__ = "0.1.0"
