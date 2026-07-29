"""Evaluation harness: inject known faults, score the agent's root cause.

This is the piece most portfolio projects skip. It injects each built-in fault
into otherwise-nominal telemetry, runs the full detect -> investigate pipeline,
and scores three things against ground truth:

- **detection**: did the detector flag the right asset at all, and how many
  samples after fault onset (latency)?
- **root-cause quality**: two graders, shown side by side. A cheap keyword match
  (does the stated cause contain the fault's key term?) and an LLM-as-judge that
  reads the stated cause against the ground-truth cause and scores how well it
  captures the actual mechanism, not just the affected signal. The contrast is
  the point: keyword matching over-credits "named the signal" as a correct
  diagnosis, which the judge catches.
- **cost**: how many investigation rounds (a proxy for tokens/latency the live
  providers will bill) did it take?

It is provider-agnostic: run it on the mock brain to prove the plumbing, then on
``--provider groq`` for real reasoning. The judge runs on its own provider
(``--judge``), defaulting to the deterministic mock so the numbers stay
reproducible; point it at a strong model to grade a weaker one under test.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from eval.judge import LLMJudge
from grid_copilot.agent.investigator import Investigator
from grid_copilot.agent.mock_llm import GridMockClient
from grid_copilot.detect.statistical import ZScoreDetector
from grid_copilot.events import EventBus
from grid_copilot.ingest.replay import replay
from grid_copilot.ingest.synthetic import SyntheticGrid
from grid_copilot.observability import LangfuseEventListener, LangfuseObservedClient, build_tracer, observed_session
from grid_copilot.telemetry import TelemetryLog
from grid_copilot.types import Anomaly

# (fault name, injection start, injection end, expected key term in the cause).
SCENARIOS: list[tuple[str, int, int, str]] = [
    ("bearing_overheat", 220, 380, "bearing"),
    ("cavitation", 220, 380, "cavitation"),
    ("freq_excursion", 220, 380, "frequency"),
]


@dataclass
class Result:
    fault: str
    detected: bool
    asset_ok: bool
    latency: int | None  # samples from fault onset to detection
    rounds: int
    keyword_hit: bool  # cheap grader: key term present in the stated cause
    judge_score: float  # LLM-as-judge, 0..1
    judge_verdict: str
    hypothesis: str


def _first_anomaly(scenario) -> Anomaly | None:
    detector = ZScoreDetector()
    for reading in replay(scenario.readings):
        found = detector.update(reading)
        if found:
            return found[0]
    return None


def run(provider: str = "mock", judge_provider: str = "mock") -> list[Result]:
    llm = _build_llm(provider)
    judge = LLMJudge(_build_llm(judge_provider))
    tracer = build_tracer()
    session_id = f"eval-{provider}-vs-{judge_provider}"
    results: list[Result] = []
    for fault, s0, s1, key in SCENARIOS:
        scenario = SyntheticGrid(seed=11).generate(samples=420, inject=[(fault, s0, s1)])
        expected_asset = scenario.faults[0].asset
        ground_truth = scenario.faults[0].cause
        anomaly = _first_anomaly(scenario)

        if anomaly is None:
            results.append(Result(fault, False, False, None, 0, False, 0.0, "incorrect", "(not detected)"))
            continue

        # Timestep index recovered from the timestamp (1 sample/second from t0).
        t0 = scenario.readings[0].ts
        at_idx = int((anomaly.ts - t0).total_seconds())

        bus = EventBus()
        bus.subscribe(LangfuseEventListener(tracer))
        with observed_session(session_id, tags=["eval", fault]):
            report = Investigator(
                llm, bus=bus, telemetry=TelemetryLog.from_readings(scenario.readings)
            ).investigate(anomaly)
        cause = report.hypothesis.root_cause
        verdict = judge.score(
            context=f"{anomaly.signal} on {anomaly.asset}",
            hypothesis=f"{cause}. {report.hypothesis.reasoning}",
            ground_truth=ground_truth,
        )
        results.append(
            Result(
                fault=fault,
                detected=True,
                asset_ok=anomaly.asset == expected_asset,
                latency=max(0, at_idx - s0),
                rounds=len(report.evidence),
                keyword_hit=key.lower() in cause.lower(),
                judge_score=verdict.score,
                judge_verdict=verdict.verdict,
                hypothesis=cause,
            )
        )
    return results


def _build_llm(provider: str):
    if provider == "mock":
        llm = GridMockClient()
    else:
        from grid_copilot.config import load_env

        load_env()
        if provider == "anthropic":
            from cortex.llm.anthropic_client import AnthropicClient

            llm = AnthropicClient()
        elif provider == "groq":
            from cortex.llm.groq_client import GroqClient

            llm = GroqClient()
        else:
            raise SystemExit(f"unknown provider: {provider}")
    return LangfuseObservedClient(llm)


def _report(results: list[Result], provider: str, judge_provider: str, elapsed: float) -> None:
    detected = sum(r.detected for r in results)
    kw = sum(r.keyword_hit for r in results)
    judged = [r for r in results if r.detected]
    mean_judge = sum(r.judge_score for r in judged) / len(judged) if judged else 0.0
    n = len(results)
    print(f"\nGrid Copilot eval  |  agent={provider}  judge={judge_provider}  |  "
          f"{n} scenarios  |  {elapsed:.1f}s\n")
    print(f"{'fault':<18}{'detected':<10}{'latency':<9}{'rounds':<8}{'keyword':<9}"
          f"{'judge':<15}hypothesis")
    print("-" * 100)
    for r in results:
        lat = "-" if r.latency is None else f"+{r.latency}"
        judge = f"{r.judge_score:.2f} {r.judge_verdict}"
        print(
            f"{r.fault:<18}{'yes' if r.detected else 'NO':<10}{lat:<9}{r.rounds:<8}"
            f"{'hit' if r.keyword_hit else 'MISS':<9}{judge:<15}{r.hypothesis[:34]}"
        )
    print("-" * 100)
    print(
        f"detection recall: {detected}/{n}   keyword accuracy: {kw}/{n}   "
        f"judge mean score: {mean_judge:.2f}   (latency = samples after fault onset)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid Copilot evaluation harness.")
    parser.add_argument("--provider", default="mock", choices=["mock", "anthropic", "groq"])
    parser.add_argument("--judge", default="mock", choices=["mock", "anthropic", "groq"],
                        help="provider for the LLM-as-judge grader")
    args = parser.parse_args()
    start = time.time()
    results = run(args.provider, args.judge)
    _report(results, args.provider, args.judge, time.time() - start)


if __name__ == "__main__":
    main()
