"""Investigation tools and their registry.

This mirrors Cortex's skill-registry pattern (a named, self-describing
capability the agent discovers rather than hard-codes) but retargets it from
robot actions to evidence-gathering. The agent is handed the `catalog()` text
and chooses which tool to call next; the orchestrator runs it and appends the
resulting `Evidence`. Adding a new investigation capability (say, a tool that
correlates against a maintenance log) means registering one more `Tool`, with no
change to the agent loop.

Each tool returns `Evidence` attributed to its own name, and the two
knowledge-backed tools attach citations, so the final report can trace every
claim to telemetry, a document, or a past incident.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field

from grid_copilot.memory.store import IncidentStore
from grid_copilot.rag.retriever import Retriever
from grid_copilot.tags import decode_tag, tag_role
from grid_copilot.telemetry import TelemetryLog
from grid_copilot.types import Anomaly, Evidence, Reading


@dataclass
class Investigation:
    """Mutable context threaded through a single investigation."""

    anomaly: Anomaly
    retriever: Retriever
    store: IncidentStore
    telemetry: TelemetryLog | None = None  # if set, query_telemetry uses it
    window_before: int = 180  # samples before detection (for a pre-onset baseline)
    window_after: int = 180  # samples after detection (to see the developed incident)
    evidence: list[Evidence] = field(default_factory=list)

    def used_tools(self) -> list[str]:
        return [e.source for e in self.evidence]

    def analysis_window(self) -> list[Reading]:
        """The readings query_telemetry analyzes: a wide window around the event
        from the log when available, else the detector's pre-detection snapshot."""
        if self.telemetry is not None:
            window = self.telemetry.window(
                self.anomaly.asset, self.anomaly.ts, self.window_before, self.window_after
            )
            if window:
                return window
        return self.anomaly.window


ToolFn = Callable[[str, Investigation], Evidence]


@dataclass
class Tool:
    name: str
    description: str
    run: ToolFn


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def catalog(self) -> str:
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())


# --- tool implementations ---------------------------------------------------


def _window_stats(window: list[Reading]) -> dict[str, dict[str, float]]:
    """Compare the first vs last third of each signal in the analysis window.

    `base_std` is the signal's variability over the first (pre-anomaly) third, so
    a shift can be measured as an effect size (deviations of baseline noise)
    rather than a percentage of magnitude. The latter is degenerate for signals
    centered near zero, such as a phase angle. With a window that spans the whole
    incident, the last third is the developed event, so signals that ramp late are
    captured, not just the one that moved first.
    """
    stats: dict[str, dict[str, float]] = {}
    if len(window) < 6:
        return stats
    third = max(1, len(window) // 3)
    signals = window[-1].values.keys()
    for sig in signals:
        series = [r.values[sig] for r in window if sig in r.values]
        if len(series) < 6:
            continue
        head = series[:third]
        start = statistics.fmean(head)
        end = statistics.fmean(series[-third:])
        base_std = statistics.pstdev(head) if len(head) > 1 else 0.0
        stats[sig] = {"start": start, "end": end, "delta": end - start, "base_std": base_std}
    return stats


def _onset(series: list[float], base_mean: float, base_std: float, total_delta: float,
           k: float = 4.0, frac: float = 0.5, sustain: int = 3) -> int | None:
    """Index at which a signal reaches a real fraction of its eventual deviation.

    The threshold is the larger of a noise gate (`k` baseline stds) and a magnitude
    gate (`frac` of the signal's total shift). The magnitude gate is what makes the
    onset robust for causal ordering: a tightly controlled signal that ultimately
    moves only a fraction of a unit cannot appear to "lead" on an early noise
    wobble, because its onset is only marked once it has actually moved a
    meaningful share of the way to its final value. A step-change command reaches
    that fraction almost at once, a signal that merely responds reaches it later,
    which is exactly the ordering causal direction turns on.
    """
    thresh = max(k * (base_std + 1e-9), frac * abs(total_delta))
    run = 0
    for i, v in enumerate(series):
        if abs(v - base_mean) >= thresh:
            run += 1
            if run >= sustain:
                return i - sustain + 1
        else:
            run = 0
    return None


def _causal_order(window: list[Reading], movers: list[tuple[str, dict]]) -> str:
    """Order the movers by when they began deviating and, if a command and a
    measurement both moved, state which led. Command-before-measurement means the
    command drove the event (a setpoint change or spoofed command); the reverse
    means the controls were responding to an upstream disturbance. Putting this in
    the evidence gives the agent the causal direction it otherwise has to guess."""
    third = max(1, len(window) // 3)
    onsets: list[tuple[int, str, str]] = []
    for sig, s in movers:
        series = [r.values[sig] for r in window if sig in r.values]
        head = series[:third]
        bmean = statistics.fmean(head)
        bstd = statistics.pstdev(head) if len(head) > 1 else 0.0
        o = _onset(series, bmean, bstd, s["delta"])
        if o is not None:
            onsets.append((o, sig, tag_role(sig)))
    if len(onsets) < 2:
        return ""
    onsets.sort()
    order_str = ", ".join(f"{sig} ({role}) at +{o}" for o, sig, role in onsets)
    cmd = next((o for o, _, role in onsets if role == "command"), None)
    meas = next((o for o, _, role in onsets if role == "measurement"), None)
    verdict = ""
    if cmd is not None and meas is not None:
        if cmd < meas:
            verdict = (
                " The command deviated before the measured process variable(s), consistent "
                "with a command or setpoint change driving the event rather than the actuator "
                "responding to an upstream disturbance."
            )
        elif meas < cmd:
            verdict = (
                " A measured process variable deviated before any command, consistent with "
                "the controls responding to an upstream disturbance."
            )
    return f" Deviation onset order: {order_str}.{verdict}"


def query_telemetry(arg: str, inv: Investigation) -> Evidence:
    """Summarize how each signal moved across the incident window, and in what order."""
    window = inv.analysis_window()
    stats = _window_stats(window)
    if not stats:
        return Evidence(source="query_telemetry", summary="Insufficient window to analyze.")

    def effect(s: dict[str, float]) -> float:
        # Shift measured in baseline standard deviations (a scale-free effect
        # size). Falls back to a small absolute scale when baseline noise is ~0.
        return abs(s["delta"]) / (s["base_std"] + 1e-6)

    # A signal is a "mover" only if its shift clears ~1 baseline std, so sensor
    # noise is not mistaken for a fault. Movers and stable signals are disjoint.
    floor = 1.0
    movers = [
        (sig, s)
        for sig, s in sorted(stats.items(), key=lambda kv: effect(kv[1]), reverse=True)
        if effect(s) >= floor
    ][:3]
    # Always include the signal that triggered the investigation, even if others
    # moved more, so the agent never loses sight of what the detector flagged.
    trigger = inv.anomaly.signal
    if trigger in stats and trigger not in {sig for sig, _ in movers}:
        movers.append((trigger, stats[trigger]))
    reported = {sig for sig, _ in movers}
    stable = [sig for sig, s in stats.items() if effect(s) < floor and sig not in reported]

    if not movers:
        summary = f"On {inv.anomaly.asset}, no signal moved significantly over the window."
        return Evidence(source="query_telemetry", summary=summary, payload=stats)

    parts = []
    for sig, s in movers:
        decoded = decode_tag(sig)
        label = f"{sig} ({decoded})" if decoded else sig
        parts.append(
            f"{label} {'rose' if s['delta'] > 0 else 'fell'} {s['delta']:+.2f} "
            f"({s['start']:.1f}->{s['end']:.1f})"
        )
    summary = f"On {inv.anomaly.asset}, over the incident window: " + "; ".join(parts) + "."
    if stable:
        summary += f" Stable: {', '.join(stable)}."
    summary += _causal_order(window, movers)
    return Evidence(source="query_telemetry", summary=summary, payload=stats)


def retrieve_docs(arg: str, inv: Investigation) -> Evidence:
    """Retrieve domain notes relevant to the anomaly, with citations."""
    query = arg.strip() or _auto_query(inv)
    hits = inv.retriever.search(query, k=2)
    if not hits:
        return Evidence(source="retrieve_docs", summary=f"No documents matched '{query}'.")
    top = hits[0][0]
    citations = [doc.id for doc, _ in hits]
    summary = f"Closest reference '{top.title}': {top.text.split('.')[0].strip()}."
    return Evidence(source="retrieve_docs", summary=summary, citations=citations)


def recall_incident(arg: str, inv: Investigation) -> Evidence:
    """Recall prior incidents on the same asset from memory."""
    query = arg.strip() or _auto_query(inv)
    priors = inv.store.recall(inv.anomaly.asset, query, k=3)
    if not priors:
        return Evidence(
            source="recall_incident",
            summary=f"No prior incidents recorded for {inv.anomaly.asset}.",
        )
    citations = [p.incident_id for p in priors]
    listed = "; ".join(f"{p.incident_id}: {p.summary}" for p in priors)
    return Evidence(
        source="recall_incident",
        summary=f"{len(priors)} prior incident(s) on {inv.anomaly.asset}: {listed}",
        citations=citations,
    )


def _auto_query(inv: Investigation) -> str:
    """Fallback query built from the tripped signal and the asset."""
    return f"{inv.anomaly.signal} {inv.anomaly.asset}".replace("_", " ")


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            "query_telemetry",
            "Analyze how each signal moved across the anomaly window (start vs end, "
            "which signals co-moved, which stayed stable). Takes no argument.",
            query_telemetry,
        )
    )
    reg.register(
        Tool(
            "retrieve_docs",
            "Search equipment/protocol documentation for a fault signature. Argument: "
            "a short search query (e.g. 'bearing temperature vibration').",
            retrieve_docs,
        )
    )
    reg.register(
        Tool(
            "recall_incident",
            "Recall prior incidents on this same asset from memory. Argument: a short "
            "query describing the symptom.",
            recall_incident,
        )
    )
    return reg
