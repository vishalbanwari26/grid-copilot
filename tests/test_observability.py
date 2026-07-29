"""Offline tests for the Langfuse observability wrapper.

Nothing here imports langfuse. build_tracer() falls back to NullTracer when
Langfuse keys are absent from the environment, and the client decorator plus
event listener are exercised against a small fake tracer defined in this file,
so the whole suite stays exactly as offline as the rest of tests/.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from grid_copilot.agent.investigator import Investigator
from grid_copilot.agent.mock_llm import GridMockClient
from grid_copilot.detect.statistical import ZScoreDetector
from grid_copilot.events import EventBus
from grid_copilot.ingest.replay import replay
from grid_copilot.ingest.synthetic import demo_scenario
from grid_copilot.observability import (
    LangfuseEventListener,
    LangfuseObservedClient,
    NullTracer,
    build_tracer,
)


def test_build_tracer_is_null_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    # No .env in the test working directory, and no langfuse import should
    # happen: if it did, and the package were not installed, this would raise.
    tracer = build_tracer()
    assert isinstance(tracer, NullTracer)


def test_null_tracer_handle_is_a_safe_no_op():
    handle = NullTracer().trace(name="x")
    # Every call is chainable and returns None/self; nothing raises.
    handle.span(name="y").generation(name="z", model="m", input="i", output="o")
    handle.end()


@dataclass
class _FakeSpan:
    name: str
    generations: list[dict] = field(default_factory=list)
    children: list["_FakeSpan"] = field(default_factory=list)
    ended: bool = False
    end_output: Any = None
    updated: bool = False
    update_output: Any = None

    def span(self, name: str, **kwargs: Any) -> "_FakeSpan":
        child = _FakeSpan(name=name)
        self.children.append(child)
        return child

    def generation(self, name: str, model: str, input: Any, output: Any, **kwargs: Any) -> None:
        self.generations.append({"name": name, "model": model, "input": input, "output": output})

    def end(self, **kwargs: Any) -> None:
        # Real Langfuse spans/generations have .end(); a top-level trace does
        # not, so LangfuseEventListener never calls this on a trace object.
        self.ended = True
        self.end_output = kwargs.get("output")

    def update(self, **kwargs: Any) -> None:
        # What LangfuseEventListener actually calls on the top-level trace,
        # since traces close implicitly rather than via .end().
        self.updated = True
        self.update_output = kwargs.get("output")


@dataclass
class _FakeTracer:
    """Records every trace() call; the shape LangfuseEventListener expects."""

    traces: list[_FakeSpan] = field(default_factory=list)

    def trace(self, name: str, session_id=None, tags=None, **kwargs: Any) -> _FakeSpan:
        t = _FakeSpan(name=name)
        self.traces.append(t)
        return t


def test_observed_client_records_one_generation_per_role():
    tracer = _FakeTracer()
    bus = EventBus()
    bus.subscribe(LangfuseEventListener(tracer))

    llm = LangfuseObservedClient(GridMockClient())
    scenario = demo_scenario()
    detector = ZScoreDetector()
    anomaly = next(a for r in replay(scenario.readings) for a in detector.update(r))

    Investigator(llm, bus=bus).investigate(anomaly)

    assert len(tracer.traces) == 1, "one investigate() call should open exactly one trace"
    trace = tracer.traces[0]
    assert trace.updated, "the trace should be updated with its final output by REPORT_READY or ABORTED"
    assert not trace.ended, "a trace has no .end() in the real Langfuse SDK; only spans/generations do"

    roles = {g["name"] for g in trace.generations}
    assert "INVESTIGATOR" in roles
    assert "CRITIC" in roles
    assert all(g["model"] == "GridMockClient" for g in trace.generations)


def test_event_listener_opens_a_span_per_tool_call():
    tracer = _FakeTracer()
    bus = EventBus()
    bus.subscribe(LangfuseEventListener(tracer))

    llm = LangfuseObservedClient(GridMockClient())
    scenario = demo_scenario()
    detector = ZScoreDetector()
    anomaly = next(a for r in replay(scenario.readings) for a in detector.update(r))

    Investigator(llm, bus=bus).investigate(anomaly)

    trace = tracer.traces[0]
    tool_spans = [c for c in trace.children if c.name.startswith("tool:")]
    assert tool_spans, "at least one tool call should open a span"
    assert all(s.ended for s in tool_spans), "every opened tool span should be closed"


def test_listener_without_a_started_trace_is_a_safe_no_op():
    """If ANOMALY_DETECTED never fires (or the listener is used standalone), later
    events must not raise even though no trace is open."""
    from grid_copilot.events import Event, RCAEvent

    listener = LangfuseEventListener(_FakeTracer())
    listener(Event(type=RCAEvent.TOOL_CALLED, message="x", payload={"tool": "query_telemetry"}))
    listener(Event(type=RCAEvent.REPORT_READY, message="done", payload={}))
