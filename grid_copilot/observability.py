"""Optional LLM observability via Langfuse.

The investigation loop already emits an event for every step
(`grid_copilot/events.py`), and every agent call goes through one interface,
`LLMClient.complete()` (`cortex/llm/base.py`). This module adds tracing on top
of both seams without touching either: a client decorator that records one
Langfuse generation per LLM call, and an `EventBus` listener that turns the
`RCAEvent` lifecycle into a nested trace.

Activation is env-var gated (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`),
loaded the same way `GROQ_API_KEY` and `ANTHROPIC_API_KEY` already are, via
`grid_copilot.config.load_env()`. Absent those keys, `build_tracer()` returns a
`NullTracer` and the `langfuse` package is never imported, so the offline path
stays exactly as import-light as it was.
"""

from __future__ import annotations

import contextvars
import os
import time
from contextlib import contextmanager
from typing import Any, Protocol

from cortex.llm.base import ImageInput, LLMClient, LLMResponse

from grid_copilot.events import Event, RCAEvent


class TraceHandle(Protocol):
    def span(self, name: str, **kwargs: Any) -> "TraceHandle": ...
    def generation(self, name: str, model: str, input: Any, output: Any, **kwargs: Any) -> None: ...
    def end(self, **kwargs: Any) -> None: ...
    def update(self, **kwargs: Any) -> None: ...


class Tracer(Protocol):
    def trace(
        self,
        name: str,
        session_id: str | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> TraceHandle: ...


class _NullHandle:
    """No-op handle. Every call returns itself or nothing, at zero cost."""

    def span(self, name: str, **kwargs: Any) -> "_NullHandle":
        return self

    def generation(self, name: str, model: str, input: Any, output: Any, **kwargs: Any) -> None:
        return None

    def end(self, **kwargs: Any) -> None:
        return None

    def update(self, **kwargs: Any) -> None:
        return None


class NullTracer:
    """Zero-dependency default. Used whenever Langfuse keys are not configured."""

    def trace(
        self,
        name: str,
        session_id: str | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> _NullHandle:
        return _NullHandle()


class _LangfuseTracer:
    """Thin adapter from the real Langfuse SDK onto the Tracer protocol above."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def trace(
        self,
        name: str,
        session_id: str | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        return self._client.trace(name=name, session_id=session_id, tags=tags or [], **kwargs)


def build_tracer() -> Tracer:
    """Return a real Langfuse-backed tracer if keys are configured, else a no-op.

    This is the only place in the codebase that imports the langfuse package.
    Everything else here (and the rest of grid-copilot) talks to the
    Tracer/TraceHandle protocol only, so the project never depends on langfuse
    unless a user opts in by setting LANGFUSE_PUBLIC_KEY and
    LANGFUSE_SECRET_KEY, following the exact same env-var-presence convention
    GROQ_API_KEY and ANTHROPIC_API_KEY already use for the live LLM providers.
    """
    from grid_copilot.config import load_env

    load_env()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return NullTracer()

    from langfuse import Langfuse  # local import: the one opt-in dependency

    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    return _LangfuseTracer(client)


# ---------------------------------------------------------------------------
# Client decorator: one Langfuse generation per LLMClient.complete() call.
# ---------------------------------------------------------------------------

# Holds whatever trace/span is currently open, so the client wrapper (which
# knows nothing about RCAEvent) and the event listener (which never touches an
# LLMClient) can cooperate without calling each other directly. A ContextVar,
# not an instance attribute, because server.py runs each investigation on its
# own worker thread.
_current_trace: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_current_trace", default=None
)


def _agent_role(system: str) -> str:
    """Recover the calling agent's role from its system prompt.

    Every Agent subclass (InvestigatorAgent, CriticAgent, LLMJudge) starts its
    system prompt with "ROLE: <role>." (see cortex/agents/base.py and
    grid_copilot/agent/investigator.py). Reading it back out of the prompt
    string means this wrapper needs no change to Agent, Investigator, or any
    concrete agent to get per-agent labeling in Langfuse.
    """
    prefix = "ROLE: "
    if system.startswith(prefix):
        return system[len(prefix):].split(".", 1)[0].strip()
    return "UNKNOWN"


class LangfuseObservedClient(LLMClient):
    """Decorates any concrete LLMClient with Langfuse generation tracing.

    Construct the real client first, then wrap it:

        llm = LangfuseObservedClient(GroqClient())

    Every .complete() call is timed and recorded as one generation against
    whatever trace is current (opened by LangfuseEventListener below). With no
    active trace, generation() still gets called but lands on a no-op handle
    and costs nothing.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner

    def complete(self, system: str, prompt: str, image: ImageInput | None = None) -> LLMResponse:
        started = time.monotonic()
        response = self._inner.complete(system, prompt, image=image)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        handle = _current_trace.get() or _NullHandle()
        handle.generation(
            name=_agent_role(system),
            model=type(self._inner).__name__,
            input={"system": system, "prompt": prompt},
            output=response.text,
            metadata={"latency_ms": elapsed_ms},
        )
        return response


# ---------------------------------------------------------------------------
# EventBus listener: turns the RCAEvent lifecycle into a nested trace.
# ---------------------------------------------------------------------------

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_session_id", default=None)
_session_tags: contextvars.ContextVar[list[str]] = contextvars.ContextVar("_session_tags", default=[])


@contextmanager
def observed_session(session_id: str, tags: list[str] | None = None):
    """Group one or more investigate() calls under one Langfuse session.

    Used by eval/harness.py to group a batch of scenarios (one investigate()
    call per injected fault) so they show up together in the Langfuse UI,
    tagged by fault name. This is Langfuse's session-plus-tag grouping, not its
    separate Datasets/Experiments API, which needs pre-uploaded dataset items
    this project does not have.
    """
    session_token = _session_id.set(session_id)
    tags_token = _session_tags.set(tags or [])
    try:
        yield
    finally:
        _session_id.reset(session_token)
        _session_tags.reset(tags_token)


class LangfuseEventListener:
    """Subscribe to grid_copilot's EventBus; emit a nested Langfuse trace.

    One investigate() call becomes one trace: ANOMALY_DETECTED opens it,
    each TOOL_CALLED/TOOL_RESULT pair opens and closes a span, HYPOTHESIS and
    CRITIQUE are recorded as trace-level spans, and REPORT_READY or ABORTED
    records the final output on the trace (a trace has no .end() in Langfuse,
    only spans and generations do; it closes implicitly). Built on contextvars
    (see _current_trace above) so this stays correct when server.py runs an
    investigation on a worker thread.
    """

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer
        self._open_tool: TraceHandle | None = None

    def __call__(self, event: Event) -> None:
        if event.type == RCAEvent.ANOMALY_DETECTED:
            trace = self._tracer.trace(
                name="investigate",
                session_id=_session_id.get(),
                tags=_session_tags.get(),
                metadata=dict(event.payload),
            )
            _current_trace.set(trace)
            return

        trace = _current_trace.get()
        if trace is None:
            return  # tracer never started a trace (e.g. events arrived out of order)

        if event.type == RCAEvent.TOOL_CALLED:
            self._open_tool = trace.span(name=f"tool:{event.payload.get('tool', '?')}")
        elif event.type == RCAEvent.TOOL_RESULT:
            if self._open_tool is not None:
                self._open_tool.end(output=event.message)
                self._open_tool = None
        elif event.type in (RCAEvent.HYPOTHESIS, RCAEvent.CRITIQUE):
            trace.span(name=event.type.value, metadata=dict(event.payload)).end(output=event.message)
        elif event.type in (RCAEvent.REPORT_READY, RCAEvent.ABORTED):
            # A trace itself has no .end() in the real Langfuse SDK (only spans
            # and generations do); a trace closes implicitly once its
            # observations stop. .update() records the final output/metadata.
            trace.update(output=event.message, metadata=dict(event.payload))
            _current_trace.set(None)
