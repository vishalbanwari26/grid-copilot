"""Command-line entry point: run one anomaly end to end.

    python -m grid_copilot.cli            # offline, deterministic (no API key)
    python -m grid_copilot.cli --provider anthropic   # real reasoning (needs key)

It generates the labeled demo scenario, streams it through the detector, takes
the first anomaly, and runs the investigation loop, printing every event as it
happens and the final cited report. The last line confirms the agent's
conclusion against the injected ground truth, since the demo data is labeled.
"""

from __future__ import annotations

import argparse

from grid_copilot.agent.investigator import Investigator
from grid_copilot.agent.mock_llm import GridMockClient
from grid_copilot.detect.statistical import ZScoreDetector
from grid_copilot.events import Event, EventBus, RCAEvent
from grid_copilot.ingest.replay import replay
from grid_copilot.ingest.synthetic import demo_scenario

_ICON = {
    RCAEvent.ANOMALY_DETECTED: "[!]",
    RCAEvent.INVESTIGATING: "[..]",
    RCAEvent.TOOL_CALLED: "[>>]",
    RCAEvent.TOOL_RESULT: "  ->",
    RCAEvent.HYPOTHESIS: "[=]",
    RCAEvent.CRITIQUE: "[?]",
    RCAEvent.REPORT_READY: "[done]",
    RCAEvent.ABORTED: "[x]",
}


def _print_event(event: Event) -> None:
    icon = _ICON.get(event.type, "   ")  # type: ignore[arg-type]
    print(f" {icon} {event.message}")


def build_llm(provider: str):
    if provider == "mock":
        return GridMockClient()
    from grid_copilot.config import load_env

    load_env()  # pick up ANTHROPIC_API_KEY / GROQ_API_KEY from a local .env
    if provider == "anthropic":
        from cortex.llm.anthropic_client import AnthropicClient

        return AnthropicClient()
    if provider == "groq":
        from cortex.llm.groq_client import GroqClient

        return GroqClient()
    raise SystemExit(f"unknown provider: {provider}")


def build_store(memory: str):
    if memory == "mnemos":
        from grid_copilot.memory.store import MnemosIncidentStore

        return MnemosIncidentStore()
    from grid_copilot.memory.store import LocalIncidentStore

    return LocalIncidentStore()


def build_retriever(retriever: str, docs_path: str | None = None):
    from grid_copilot.rag.corpus import CORPUS

    corpus = list(CORPUS)
    if docs_path:
        from grid_copilot.rag.loader import load_documents

        corpus += load_documents(docs_path)
    if retriever == "vector":
        from grid_copilot.rag.vector import VectorRetriever

        return VectorRetriever(docs=corpus)
    from grid_copilot.rag.retriever import KeywordRetriever

    return KeywordRetriever(docs=corpus)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid Copilot: agentic RCA on telemetry.")
    parser.add_argument("--provider", default="groq", choices=["mock", "anthropic", "groq"],
                        help="LLM provider; default is live groq (needs GROQ_API_KEY). "
                             "Use --provider mock to run offline with no key.")
    parser.add_argument("--memory", default="local", choices=["local", "mnemos"],
                        help="incident memory backend (mnemos persists across runs)")
    parser.add_argument("--retriever", default="keyword", choices=["keyword", "vector"],
                        help="documentation retriever (vector uses embeddings)")
    parser.add_argument("--docs", default=None,
                        help="path to extra spec/manual text files to retrieve over")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--brief", action="store_true",
                        help="print only the investigation stream, not the full report")
    args = parser.parse_args()

    scenario = demo_scenario()
    detector = ZScoreDetector()
    anomalies = [a for r in replay(scenario.readings) for a in detector.update(r)]

    if not anomalies:
        print("No anomalies detected in the demo scenario.")
        return

    anomaly = anomalies[0]
    print(f"Detected {len(anomalies)} anomaly signal(s); investigating the first.\n")

    from grid_copilot.telemetry import TelemetryLog

    bus = EventBus()
    bus.subscribe(_print_event)
    investigator = Investigator(
        build_llm(args.provider), retriever=build_retriever(args.retriever, args.docs),
        store=build_store(args.memory), bus=bus, max_rounds=args.max_rounds,
        telemetry=TelemetryLog.from_readings(scenario.readings),
    )
    report = investigator.investigate(anomaly)

    if not args.brief:
        print("\n" + "=" * 70)
        print(report.to_markdown())

    # The demo data is labeled, so we can state whether the agent got it right.
    truth = next(
        (f.cause for f in scenario.faults if f.asset == anomaly.asset),
        None,
    )
    if truth:
        print("\n" + "-" * 70)
        print(f"Ground truth (injected): {truth}")


if __name__ == "__main__":
    main()
