"""Deterministic offline brain, so the full loop runs with no API key.

Same idea as Cortex's `MockClient`: recognize each agent by the ``ROLE`` tag in
its system prompt and return scripted JSON. But this mock stays *grounded in the
evidence it is shown* rather than hard-coding an answer: it reads the telemetry
summary already in the prompt, forms its documentation query from the signals
that actually moved, and names a root cause consistent with them. That means it
walks a sensible investigation (gather telemetry, check docs, check memory,
conclude) and cannot assert a conclusion the evidence contradicts, for any of the
built-in fault types. Point a real `LLMClient` at the same prompts for genuine
reasoning instead of this deterministic walk.
"""

from __future__ import annotations

import json
import re

from cortex.llm import ImageInput, LLMClient, LLMResponse

# Signal-pattern -> (root cause, human phrase). Checked in order.
_FAULT_RULES: list[tuple[set[str], set[str], str]] = [
    # rising signals, falling signals, root cause
    ({"bearing_temp_c", "vibration_mm_s"}, set(),
     "bearing overheating / incipient bearing fault"),
    ({"motor_current_a"}, {"inlet_pressure_bar"},
     "pump cavitation from loss of inlet pressure"),
]


class GridMockClient(LLMClient):
    def complete(
        self,
        system: str,
        prompt: str,
        image: ImageInput | None = None,
    ) -> LLMResponse:
        if "ROLE: INVESTIGATOR" in system:
            return LLMResponse(text=self._investigate(prompt))
        if "ROLE: CRITIC" in system:
            return LLMResponse(
                text=json.dumps(
                    {"verdict": "accept", "reason": "Evidence supports the stated root cause."}
                )
            )
        if "ROLE: JUDGE" in system:
            return LLMResponse(text=self._judge(prompt))
        return LLMResponse(text="{}")

    @staticmethod
    def _judge(prompt: str) -> str:
        """Deterministic stand-in for the LLM judge: recall of the true cause's
        key terms in the stated cause."""
        truth = _field(prompt, "True root cause:")
        stated = _field(prompt, "Agent's stated root cause:")
        true_terms = _terms(truth)
        if not true_terms:
            return json.dumps({"score": 0.0, "verdict": "incorrect", "justification": "no truth"})
        overlap = true_terms & _terms(stated)
        score = round(len(overlap) / len(true_terms), 2)
        verdict = "correct" if score >= 0.5 else "partial" if score >= 0.25 else "incorrect"
        missed = ", ".join(sorted(true_terms - overlap)) or "nothing"
        return json.dumps(
            {"score": score, "verdict": verdict,
             "justification": f"captured {sorted(overlap)}; missed {missed}"}
        )

    def _investigate(self, prompt: str) -> str:
        used = _used_tools(prompt)
        rose, fell = _moved_signals(prompt)

        if "query_telemetry" not in used:
            return json.dumps(
                {"action": "call_tool", "tool": "query_telemetry", "arg": "",
                 "why": "See which signals moved and whether any co-moved."}
            )
        if "retrieve_docs" not in used:
            query = " ".join(sorted(rose | fell)).replace("_", " ") or "fault signature"
            return json.dumps(
                {"action": "call_tool", "tool": "retrieve_docs", "arg": query,
                 "why": "Match the signals that moved to a known fault signature."}
            )
        if "recall_incident" not in used:
            return json.dumps(
                {"action": "call_tool", "tool": "recall_incident",
                 "arg": " ".join(sorted(rose | fell)).replace("_", " "),
                 "why": "Check whether this asset has failed this way before."}
            )

        cause = _classify(rose, fell)
        moved = ", ".join(sorted(rose | fell)).replace("_", " ") or "the tripped signal"
        doc = _doc_title(prompt)
        reasoning = (
            f"The signals that moved together over the window ({moved}) match the "
            f"documented signature"
            + (f" in '{doc}'" if doc else "")
            + f", pointing to {cause} rather than a sensor or load artifact."
        )
        return json.dumps(
            {"action": "conclude", "root_cause": cause, "confidence": 0.8, "reasoning": reasoning}
        )


def _classify(rose: set[str], fell: set[str]) -> str:
    for want_rise, want_fall, cause in _FAULT_RULES:
        if want_rise <= rose and want_fall <= fell:
            return cause
    moved = ", ".join(sorted(rose | fell)).replace("_", " ")
    return f"sustained deviation in {moved}" if moved else "an undetermined process deviation"


def _used_tools(prompt: str) -> set[str]:
    for line in prompt.splitlines():
        if "Tools already used:" in line:
            tail = line.split("Tools already used:", 1)[1]
            return {t.strip() for t in tail.split(",") if t.strip() and t.strip() != "none"}
    return set()


def _moved_signals(prompt: str) -> tuple[set[str], set[str]]:
    """Parse the query_telemetry evidence line for signals that rose / fell."""
    rose: set[str] = set()
    fell: set[str] = set()
    for line in prompt.splitlines():
        if "query_telemetry" not in line:
            continue
        for sig, direction in re.findall(r"([a-z_]+) (rose|fell)", line):
            (rose if direction == "rose" else fell).add(sig)
    return rose, fell


def _doc_title(prompt: str) -> str:
    for line in prompt.splitlines():
        if "retrieve_docs" in line:
            m = re.search(r"'([^']+)'", line)
            if m:
                return m.group(1)
    return ""


_STOP = {"the", "and", "from", "for", "with", "due", "that", "this", "sustained",
         "deviation", "fault", "incipient"}


def _field(prompt: str, marker: str) -> str:
    for line in prompt.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return ""


def _terms(text: str) -> set[str]:
    """Content words of a cause phrase, for overlap scoring."""
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3 and t not in _STOP}
