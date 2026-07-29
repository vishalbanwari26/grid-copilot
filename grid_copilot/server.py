"""FastAPI backend for the Grid Copilot dashboard.

Thin layer over the existing pipeline: it generates a labeled scenario, runs the
real detector and the real `Investigator`, and streams the actual `EventBus`
events to the browser over Server-Sent Events. The investigation runs on a worker
thread and pushes each event onto an asyncio queue as it fires, so the UI shows
the agent's reasoning unfold live (a small pace is added for the instant offline
mock so it is readable; a live provider streams at its own speed).

Run it with:  uvicorn grid_copilot.server:app --reload
"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from grid_copilot.agent.investigator import Investigator
from grid_copilot.agent.mock_llm import GridMockClient
from grid_copilot.detect.statistical import ZScoreDetector
from grid_copilot.events import Event, EventBus
from grid_copilot.ingest.replay import replay
from grid_copilot.ingest.synthetic import FAULTS, SyntheticGrid
from grid_copilot.observability import LangfuseObservedClient, LangfuseEventListener, build_tracer
from grid_copilot.telemetry import TelemetryLog
from grid_copilot.types import Anomaly, IncidentReport

app = FastAPI(title="Grid Copilot")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Human-facing metadata for each injectable fault (the demo scenarios).
FAULT_META = {
    "bearing_overheat": {
        "asset": "turbine_1",
        "label": "Bearing overheat",
        "blurb": "Bearing temperature and vibration climb together on the turbine.",
    },
    "cavitation": {
        "asset": "pump_2",
        "label": "Pump cavitation",
        "blurb": "Inlet pressure collapses while motor current spikes on the pump.",
    },
    "freq_excursion": {
        "asset": "grid_bus_3",
        "label": "Frequency excursion",
        "blurb": "Grid frequency drifts off nominal from a load-generation imbalance.",
    },
}

_WINDOW = (200, 380)  # fault injection window used for every scenario


def _scenario(fault: str):
    grid = SyntheticGrid(seed=7)
    return grid.generate(samples=420, inject=[(fault, *_WINDOW)])


def _first_anomaly(scenario, asset: str) -> tuple[Anomaly | None, int]:
    detector = ZScoreDetector()
    t0 = scenario.readings[0].ts
    for reading in replay(scenario.readings):
        for anomaly in detector.update(reading):
            if anomaly.asset == asset:
                return anomaly, int((anomaly.ts - t0).total_seconds())
    return None, -1


def _report_json(report: IncidentReport, truth: str | None) -> dict:
    cited = sorted({c for e in report.evidence for c in e.citations})
    root = report.hypothesis.root_cause
    return {
        "asset": report.asset,
        "trigger_signal": report.trigger_signal,
        "score": report.score,
        "root_cause": root,
        "confidence": report.hypothesis.confidence,
        "reasoning": report.hypothesis.reasoning,
        "verdict": report.verdict,
        "evidence": [
            {"source": e.source, "summary": e.summary, "citations": e.citations}
            for e in report.evidence
        ],
        "references": cited,
        "narrative": report.narrative,
        "ground_truth": truth,
        "correct": bool(truth and any(w in root.lower() for w in ("bearing", "cavitation", "frequency"))
                        and truth.split()[0].lower() in root.lower()),
    }


@app.get("/health")
def health() -> dict:
    """Liveness/readiness probe target. Unconditional: no live-provider or
    network check inside it, so a probe cannot flap on an unrelated outage."""
    return {"status": "ok"}


@app.get("/api/faults")
def faults() -> list[dict]:
    return [{"name": k, **v} for k, v in FAULT_META.items()]


@app.get("/api/telemetry")
def telemetry(fault: str = "bearing_overheat") -> dict:
    meta = FAULT_META[fault]
    asset = meta["asset"]
    scenario = _scenario(fault)
    _anomaly, idx = _first_anomaly(scenario, asset)
    fault_signals = set(FAULTS[fault][1])
    rows = [r for r in scenario.readings if r.asset == asset]
    signal_names = list(rows[0].values.keys())
    signals = [
        {
            "name": name,
            "faulted": name in fault_signals,
            "values": [round(r.values[name], 3) for r in rows],
        }
        for name in signal_names
    ]
    return {
        "asset": asset,
        "fault_window": list(_WINDOW),
        "anomaly_index": idx,
        "sample_count": len(rows),
        "signals": signals,
    }


def _build_llm(provider: str):
    if provider == "mock":
        llm = GridMockClient()
    else:
        from grid_copilot.config import load_env

        load_env()
        if provider == "groq":
            from cortex.llm.groq_client import GroqClient

            llm = GroqClient()
        else:
            from cortex.llm.anthropic_client import AnthropicClient

            llm = AnthropicClient()
    return LangfuseObservedClient(llm)


async def _investigate_events(fault: str, provider: str, pace: float):
    meta = FAULT_META[fault]
    asset = meta["asset"]
    scenario = _scenario(fault)
    anomaly, _idx = _first_anomaly(scenario, asset)
    truth = next((f.cause for f in scenario.faults if f.asset == asset), None)

    if anomaly is None:
        yield _sse("error", {"message": "No anomaly detected."})
        return

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def listener(event: Event) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("event", event))

    bus = EventBus()
    bus.subscribe(listener)
    bus.subscribe(LangfuseEventListener(build_tracer()))
    investigator = Investigator(
        _build_llm(provider), bus=bus,
        telemetry=TelemetryLog.from_readings(scenario.readings),
    )

    def run() -> None:
        try:
            report = investigator.investigate(anomaly)
            loop.call_soon_threadsafe(queue.put_nowait, ("report", (report, truth)))
        except Exception as exc:  # surface backend errors to the UI
            loop.call_soon_threadsafe(queue.put_nowait, ("error", {"message": str(exc)}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    threading.Thread(target=run, daemon=True).start()

    while True:
        kind, obj = await queue.get()
        if kind == "done":
            break
        if kind == "event":
            yield _sse("event", {"type": obj.type.value, "message": obj.message, "payload": obj.payload})
            if provider == "mock" and pace > 0:
                await asyncio.sleep(pace)  # pace the instant mock so it is readable
        elif kind == "report":
            report, truth = obj
            yield _sse("report", _report_json(report, truth))
        elif kind == "error":
            yield _sse("error", obj)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/investigate")
async def investigate(
    fault: str = "bearing_overheat", provider: str = "mock", pace: float = 0.45
) -> StreamingResponse:
    return StreamingResponse(
        _investigate_events(fault, provider, pace),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/eval")
def eval_results() -> dict:
    """Precomputed HAI test1 detector comparison (from eval/hai_eval.py, point-
    adjusted, fit on the attack-free train file). Static so the dashboard loads
    instantly; reproduce with the eval harness."""
    return {
        "dataset": "HAI test1 (5 labeled attacks, 44 signals, 4 processes)",
        "detectors": [
            {"name": "z-score baseline", "precision": 0.39, "recall": 1.0, "f1_adj": 0.57, "f1_raw": 0.26},
            {"name": "autoencoder", "precision": 0.90, "recall": 1.0, "f1_adj": 0.95, "f1_raw": 0.24},
            {"name": "ensemble (z & ae)", "precision": 0.90, "recall": 1.0, "f1_adj": 0.95, "f1_raw": 0.24},
        ],
        "note": "F1(adj) is point-adjusted (SWaT/WADI/HAI standard); F1(raw) is the stricter per-timestep score.",
    }
