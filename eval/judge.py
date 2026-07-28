"""An LLM-as-judge scorer for root-cause quality.

Keyword matching is a poor grader: it calls "sustained deviation in frequency" a
correct diagnosis of a load-generation imbalance just because the word
"frequency" appears, and it cannot tell "named the affected signal" from
"identified the fault mechanism". This judge reads the agent's stated cause and
the ground-truth cause and grades how well the former captures the latter, on the
same provider-agnostic `LLMClient` the agents use.

Like every other model call here it runs on the deterministic mock offline (so
the eval and tests are reproducible without a key) and on a real provider when
one is selected. Best practice is to judge with a different, strong model than
the one under test; the harness lets you pick the judge provider separately.
"""

from __future__ import annotations

from dataclasses import dataclass

from cortex.agents.base import Agent


@dataclass
class JudgeVerdict:
    score: float  # 0..1, how well the stated cause captures the true cause
    verdict: str  # "correct" | "partial" | "incorrect"
    justification: str


class LLMJudge(Agent):
    role = "JUDGE"

    def score(self, context: str, hypothesis: str, ground_truth: str) -> JudgeVerdict:
        system = (
            "ROLE: JUDGE. You grade an industrial root-cause analysis. Compare the "
            "agent's stated root cause to the true cause and rate how well it captures "
            "the actual fault mechanism, not merely the affected signal. Naming the "
            "signal that moved is partial credit at best. Respond with ONE JSON object "
            "and nothing else."
        )
        prompt = (
            f"Anomaly context: {context}\n\n"
            f"True root cause: {ground_truth}\n\n"
            f"Agent's stated root cause: {hypothesis}\n\n"
            'Return {"score":<0..1>,"verdict":"correct|partial|incorrect",'
            '"justification":"<one sentence>"}.'
        )
        d = self._parse_json(self.llm.complete(system, prompt).text)
        score = max(0.0, min(1.0, float(d.get("score", 0.0))))
        return JudgeVerdict(
            score=score,
            verdict=str(d.get("verdict", "incorrect")),
            justification=str(d.get("justification", "")),
        )
