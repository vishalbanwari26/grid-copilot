"""LLM-as-judge tests on the deterministic mock.

The mock judge grades by recall of the true cause's key terms in the stated
cause, so it is deterministic and needs no key. These lock the behavior that
matters: a cause matching the mechanism scores high, one that only names the
affected signal scores low, and an unrelated cause fails.
"""

from __future__ import annotations

from eval.judge import LLMJudge
from grid_copilot.agent.mock_llm import GridMockClient


def _judge() -> LLMJudge:
    return LLMJudge(GridMockClient())


def test_matching_cause_scores_high():
    v = _judge().score(
        context="bearing_temp_c on turbine_1",
        hypothesis="bearing overheating causing an incipient bearing fault on turbine_1",
        ground_truth="bearing overheating / incipient bearing fault on turbine_1",
    )
    assert v.verdict == "correct"
    assert v.score >= 0.5


def test_signal_only_cause_is_not_full_credit():
    # Names the affected signal (frequency) but misses the mechanism.
    v = _judge().score(
        context="frequency_hz on grid_bus_3",
        hypothesis="sustained deviation in frequency",
        ground_truth="grid frequency excursion on grid_bus_3 from load-generation imbalance",
    )
    assert v.verdict != "correct"
    assert v.score < 0.5


def test_unrelated_cause_fails():
    v = _judge().score(
        context="bearing_temp_c on turbine_1",
        hypothesis="pump cavitation from loss of inlet pressure",
        ground_truth="bearing overheating / incipient bearing fault on turbine_1",
    )
    assert v.verdict == "incorrect"
