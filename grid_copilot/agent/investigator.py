"""The investigation loop: the domain equivalent of Cortex's orchestrator.

Cortex sequences perceive -> plan -> execute -> critique for a robot. Here the
loop is retargeted to root-cause analysis:

    anomaly -> investigate (agent picks a tool) -> gather evidence
            -> ... (bounded rounds) ... -> conclude -> critique -> report
                          ^                                   |
                          |__________ revise (replan) ________|

The intelligence lives in two thin agents built on Cortex's `Agent` base (a JSON
prompt/parse contract): an `InvestigatorAgent` that decides the next tool call or
concludes, and a `CriticAgent` that accepts or asks for a revision. The loop is
bounded by `max_rounds` so a stubborn anomaly cannot spin forever, and every step
emits an event so the reasoning is observable in real time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cortex.agents.base import Agent

from grid_copilot.agent.tools import Investigation, ToolRegistry, default_registry
from grid_copilot.events import Event, EventBus, RCAEvent
from grid_copilot.memory.store import IncidentStore, LocalIncidentStore
from grid_copilot.rag.retriever import KeywordRetriever, Retriever
from grid_copilot.telemetry import TelemetryLog
from grid_copilot.types import Anomaly, Evidence, Hypothesis, IncidentReport


_GENERIC_CONCLUDE = (
    '{"action":"conclude","root_cause":"<text>","confidence":<0..1>,'
    '"reasoning":"<text grounded in the evidence>"}'
)


def _generic_describe(anomaly: Anomaly, inv: Investigation | None) -> str:
    return (
        f"Anomaly: signal `{anomaly.signal}` on asset `{anomaly.asset}` deviated "
        f"(score {anomaly.score}) at {anomaly.ts.isoformat()}."
    )


@dataclass
class DomainProfile:
    """What changes between domains: the persona, the shape of a conclusion, and
    the checks a conclusion must pass. The loop itself does not change.

    `validator` returns None for a valid conclusion or a reason it is not; an
    invalid conclusion goes back to the investigator with that reason (generate,
    validate, retry). `detail_keys` are the structured fields of a conclusion kept
    on the hypothesis beyond root cause, confidence and reasoning.
    """

    system: str
    conclude_format: str = _GENERIC_CONCLUDE
    conclude_hint: str = "(even a partial or low-confidence one; name the signals that moved)"
    revise_hint: str = ""
    describe: Callable[[Anomaly, Investigation | None], str] = _generic_describe
    detail_keys: tuple[str, ...] = ()
    validator: Callable[[dict, Investigation], str | None] | None = None


GENERIC_PROFILE = DomainProfile(
    system=(
        "ROLE: INVESTIGATOR. You are an industrial reliability engineer doing "
        "root-cause analysis on grid/plant telemetry. Work from evidence, not "
        "assumption. Do not call a tool you have already used. Respond with ONE "
        "JSON object and nothing else."
    ),
    revise_hint=(
        "Pay attention to what each tag is: a control-valve tag (…CV…) is an "
        "actuator position, not a pressure or flow measurement."
    ),
)


class InvestigatorAgent(Agent):
    role = "INVESTIGATOR"

    def decide(
        self,
        anomaly: Anomaly,
        evidence: list[Evidence],
        catalog: str,
        must_conclude: bool = False,
        feedback: str = "",
        profile: DomainProfile = GENERIC_PROFILE,
        inv: Investigation | None = None,
        feedback_kind: str = "reviewer",
    ) -> dict:
        used = ", ".join(e.source for e in evidence) or "none"
        collected = "\n".join(f"- {e.source}: {e.summary}" for e in evidence) or "(none yet)"
        if not feedback:
            revise = ""
        elif feedback_kind == "validation" and must_conclude:
            revise = (
                f"\n\nYour previous conclusion FAILED A CHECK: {feedback} Return a corrected "
                "conclusion in the same format that passes it, grounded in the same evidence."
            )
        elif feedback_kind == "validation":
            revise = (
                f"\n\nYour previous conclusion FAILED A CHECK: {feedback} Either call a tool you have not "
                "used to get the evidence you are missing, or return a corrected conclusion that passes it."
            )
        else:
            revise = (
                f"\n\nA reviewer REJECTED your previous conclusion: {feedback} Produce a "
                "revised root cause that addresses this, grounded in the same evidence."
                + (f" {profile.revise_hint}" if profile.revise_hint else "")
            )
        fmt = profile.conclude_format
        close = (
            "You have gathered enough evidence. You MUST now conclude with the best "
            f"root cause the evidence supports {profile.conclude_hint}. Return {fmt}."
            if must_conclude
            else (
                "Decide the next action. To gather more evidence with a tool you have "
                'not used, return {"action":"call_tool","tool":"<name>","arg":"<string>",'
                f'"why":"<short>"}}. When the evidence is sufficient, return {fmt}.'
            )
        )
        prompt = (
            f"{profile.describe(anomaly, inv)}\n\n"
            f"Tools available:\n{catalog}\n\n"
            f"Tools already used: {used}\n\n"
            f"Evidence collected so far:\n{collected}\n\n"
            f"{close}{revise}"
        )
        return self._parse_json(self.llm.complete(profile.system, prompt).text)


class CriticAgent(Agent):
    role = "CRITIC"

    def review(self, hypothesis: Hypothesis, evidence: list[Evidence]) -> dict:
        system = (
            "ROLE: CRITIC. You audit a root-cause hypothesis against the evidence. "
            "Accept only if the evidence actually supports it. Respond with ONE JSON "
            "object and nothing else."
        )
        collected = "\n".join(f"- {e.source}: {e.summary}" for e in evidence)
        prompt = (
            f"Hypothesized root cause: {hypothesis.root_cause}\n"
            f"Stated reasoning: {hypothesis.reasoning}\n\n"
            f"Evidence:\n{collected}\n\n"
            'Return {"verdict":"accept"} if the evidence supports it, or '
            '{"verdict":"revise","reason":"<what is missing or inconsistent>"} otherwise.'
        )
        return self._parse_json(self.llm.complete(system, prompt).text)


class Investigator:
    """Runs one anomaly to a cited incident report."""

    def __init__(
        self,
        llm,
        registry: ToolRegistry | None = None,
        retriever: Retriever | None = None,
        store: IncidentStore | None = None,
        bus: EventBus | None = None,
        max_rounds: int = 5,
        max_revisions: int = 1,
        telemetry: TelemetryLog | None = None,
        profile: DomainProfile | None = None,
        max_validation_retries: int = 2,
        preflight: tuple[str, ...] = (),
    ) -> None:
        self.registry = registry or default_registry()
        self.retriever = retriever or KeywordRetriever()
        self.store = store or LocalIncidentStore()
        self.bus = bus or EventBus()
        self.max_rounds = max_rounds
        self.max_revisions = max_revisions
        self.telemetry = telemetry
        self.profile = profile or GENERIC_PROFILE
        self.max_validation_retries = max_validation_retries
        # Tools run before the agent gets a turn, so their evidence is always
        # there (e.g. data-quality checks: never optional, never skipped).
        self.preflight = preflight
        self.investigator = InvestigatorAgent(llm)
        self.critic = CriticAgent(llm)

    def _emit(self, type_: RCAEvent, message: str, **payload) -> None:
        self.bus.emit(Event(type=type_, message=message, payload=payload))  # type: ignore[arg-type]

    def _hypothesis(self, decision: dict, fallback: Hypothesis | None = None) -> Hypothesis:
        fb = fallback or Hypothesis("undetermined", 0.0, "")
        try:
            confidence = float(decision.get("confidence", fb.confidence))
        except (TypeError, ValueError):
            confidence = fb.confidence
        details = {k: decision[k] for k in self.profile.detail_keys if k in decision}
        return Hypothesis(
            root_cause=str(decision.get("root_cause", fb.root_cause)),
            confidence=confidence,
            reasoning=str(decision.get("reasoning", fb.reasoning)),
            details=details or dict(fb.details),
        )

    def _validated(self, decision: dict, anomaly: Anomaly, inv: Investigation,
                   fallback: Hypothesis | None = None) -> Hypothesis:
        """Generate, validate, retry: a conclusion that fails the domain checks goes
        back to the investigator with the reason, up to `max_validation_retries`
        times. If it still fails, it is kept and marked, never silently accepted."""
        validator = self.profile.validator
        hypothesis = self._hypothesis(decision, fallback)
        if validator is None:
            return hypothesis
        for attempt in range(self.max_validation_retries + 1):
            error = validator(decision, inv)
            if error is None:
                return hypothesis
            self._emit(RCAEvent.VALIDATION, error, attempt=attempt + 1)
            if attempt == self.max_validation_retries:
                break
            decision = self.investigator.decide(
                anomaly, inv.evidence, self.registry.catalog(), must_conclude=True,
                feedback=error, profile=self.profile, inv=inv, feedback_kind="validation",
            )
            hypothesis = self._hypothesis(decision, hypothesis)
            self._emit(RCAEvent.HYPOTHESIS, hypothesis.root_cause, confidence=hypothesis.confidence,
                       **hypothesis.details)
        hypothesis.details = {**hypothesis.details, "validation": f"failed: {error}"}
        return hypothesis

    def investigate(self, anomaly: Anomaly, context: object | None = None) -> IncidentReport:
        self._emit(
            RCAEvent.ANOMALY_DETECTED,
            f"{anomaly.signal} on {anomaly.asset} (score {anomaly.score})",
            asset=anomaly.asset,
            signal=anomaly.signal,
        )
        inv = Investigation(
            anomaly=anomaly, retriever=self.retriever, store=self.store,
            telemetry=self.telemetry, context=context,
        )

        for name in self.preflight:
            tool = self.registry.get(name)
            if tool is None:
                continue
            self._emit(RCAEvent.TOOL_CALLED, f"{name}('') — always run first", tool=name)
            evidence = tool.run("", inv)
            inv.evidence.append(evidence)
            self._emit(RCAEvent.TOOL_RESULT, evidence.summary, tool=name, citations=evidence.citations)

        n_tools = len(self.registry.names())
        hypothesis: Hypothesis | None = None
        feedback = ""  # a validation failure sent back while the agent can still gather evidence
        early_retries = 0
        for round_i in range(self.max_rounds):
            # Force a decision when every tool has run or the budget is nearly
            # spent, so the loop cannot burn rounds re-gathering the same evidence.
            must_conclude = (
                len({e.source for e in inv.evidence}) >= n_tools
                or round_i >= self.max_rounds - 1
            )
            decision = self.investigator.decide(
                anomaly, inv.evidence, self.registry.catalog(), must_conclude=must_conclude,
                profile=self.profile, inv=inv, feedback=feedback,
                feedback_kind="validation" if feedback else "reviewer",
            )
            feedback = ""
            action = decision.get("action")

            if action == "call_tool" and not must_conclude:
                name = decision.get("tool", "")
                arg = str(decision.get("arg", ""))
                tool = self.registry.get(name)
                if name in {e.source for e in inv.evidence}:
                    continue  # ignore a repeat call; next round will force a conclusion
                self._emit(RCAEvent.TOOL_CALLED, f"{name}({arg!r}) — {decision.get('why','')}", tool=name)
                if tool is None:
                    inv.evidence.append(Evidence(source=name, summary=f"Unknown tool '{name}'."))
                    continue
                evidence = tool.run(arg, inv)
                inv.evidence.append(evidence)
                self._emit(RCAEvent.TOOL_RESULT, evidence.summary, tool=name, citations=evidence.citations)
                continue

            if action == "conclude":
                hypothesis = self._hypothesis(decision)
                self._emit(RCAEvent.HYPOTHESIS, hypothesis.root_cause, confidence=hypothesis.confidence,
                           **hypothesis.details)
                validator = self.profile.validator
                error = validator(decision, inv) if validator else None
                # With rounds and retries left, a failed check sends the agent back to
                # the tools, so it can fetch the evidence it lacked instead of only
                # rewording its answer.
                if error and not must_conclude and early_retries < self.max_validation_retries:
                    early_retries += 1
                    self._emit(RCAEvent.VALIDATION, error, attempt=early_retries)
                    feedback = error
                    hypothesis = None
                    continue
                hypothesis = self._validated(decision, anomaly, inv, hypothesis)
                break

        if hypothesis is None:
            # Ran out of rounds without concluding: emit an honest abort report.
            hypothesis = Hypothesis(
                root_cause="undetermined (investigation did not converge)",
                confidence=0.0,
                reasoning="The agent did not reach a conclusion within the round budget.",
            )
            self._emit(RCAEvent.ABORTED, hypothesis.root_cause)

        # Critic loop: if the reviewer rejects the hypothesis, feed the reason back
        # and let the investigator revise, bounded by `max_revisions`. This is the
        # domain analogue of Cortex's replan-on-critique; without it the critic has
        # no teeth. The last verdict stands.
        verdict = "accept"
        for _ in range(self.max_revisions + 1):
            critique = self.critic.review(hypothesis, inv.evidence)
            verdict = critique.get("verdict", "accept")
            reason = critique.get("reason", "")
            self._emit(RCAEvent.CRITIQUE, verdict, reason=reason)
            if verdict != "revise":
                break
            revised = self.investigator.decide(
                anomaly, inv.evidence, self.registry.catalog(),
                must_conclude=True, feedback=reason, profile=self.profile, inv=inv,
            )
            hypothesis = self._hypothesis(revised, hypothesis)
            self._emit(RCAEvent.HYPOTHESIS, hypothesis.root_cause, confidence=hypothesis.confidence,
                       **hypothesis.details)
            hypothesis = self._validated(revised, anomaly, inv, hypothesis)

        report = IncidentReport(
            asset=anomaly.asset,
            ts=anomaly.ts,
            trigger_signal=anomaly.signal,
            score=anomaly.score,
            hypothesis=hypothesis,
            evidence=inv.evidence,
            verdict=verdict,
            narrative=self._narrate(anomaly, hypothesis, inv.evidence),
        )
        incident_id = self.store.remember(report)
        self._emit(RCAEvent.REPORT_READY, f"{incident_id}: {report.summary_line()}", incident_id=incident_id)
        return report

    @staticmethod
    def _narrate(anomaly: Anomaly, hypothesis: Hypothesis, evidence: list[Evidence]) -> str:
        steps = " ".join(f"Then {e.source} found: {e.summary}" for e in evidence)
        return (
            f"An anomaly on {anomaly.signal} ({anomaly.asset}) opened the investigation. "
            f"{steps} On that basis the likely root cause is {hypothesis.root_cause}."
        )
