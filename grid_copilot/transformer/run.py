"""Wire the transformer domain into the investigation loop.

`investigate_alarm` turns a monitoring alarm into an `Anomaly`, builds the
investigation context (the asset's record up to a few days after the alarm), and
runs the standard `Investigator` with the transformer tools, notes and profile.
`default_history` gives each demo asset a short, invented maintenance history,
and, when a decision log is passed, the decisions engineers reviewed. The agent's
own unreviewed proposals are never part of it.
"""

from __future__ import annotations

from grid_copilot.agent.investigator import Investigator
from grid_copilot.events import EventBus
from grid_copilot.memory.store import IncidentStore
from grid_copilot.rag.retriever import KeywordRetriever
from grid_copilot.transformer.corpus import TRANSFORMER_CORPUS
from grid_copilot.transformer.decisions import DecisionLog, ReviewedHistory
from grid_copilot.transformer.domain import TRANSFORMER_PROFILE
from grid_copilot.transformer.fleet import Scenario
from grid_copilot.transformer.monitor import Alarm
from grid_copilot.transformer.tools import TransformerContext, transformer_registry
from grid_copilot.types import Anomaly, IncidentReport

# Invented history for the demo fleet. Nothing here is from a real utility.
HISTORY: dict[str, list[tuple[str, str]]] = {
    "TR-02": [("2024-10-08", "OLTC diverter overhaul after 180,000 operations; compartment gaskets replaced")],
    "TR-03": [("2019-04-12", "slightly elevated ethylene after a through-fault; core ground tested OK, gas stabilised")],
    "TR-05": [("2025-09-17", "PD alarm during a week of switching on the adjacent 110 kV bay; no internal source found")],
    "TR-06": [("2021-06-02", "bushing replaced on the HV side after a routine power factor test")],
    "TR-07": [("2022-03-30", "OLTC contacts inspected, within wear limits")],
}


def default_history(log: DecisionLog | None = None) -> ReviewedHistory:
    history = ReviewedHistory(log=log)
    for asset, items in HISTORY.items():
        for day, text in items:
            history.add_maintenance(asset, day, text)
    return history


def anomaly_from(alarm: Alarm) -> Anomaly:
    return Anomaly(asset=alarm.asset, ts=alarm.ts, signal=alarm.signal, score=alarm.value,
                   detector=f"monitor:{alarm.code}")


def build_investigator(llm, store: IncidentStore | None = None, bus: EventBus | None = None) -> Investigator:
    return Investigator(
        llm, registry=transformer_registry(), retriever=KeywordRetriever(TRANSFORMER_CORPUS),
        store=store if store is not None else default_history(), bus=bus, max_rounds=10, profile=TRANSFORMER_PROFILE,
        preflight=("data_quality",),
    )


def investigate_alarm(scenario: Scenario, asset: str, llm, store: IncidentStore | None = None,
                      bus: EventBus | None = None, alarm: Alarm | None = None,
                      hours_after: int = 72) -> IncidentReport | None:
    alarm = alarm or scenario.first_alarm(asset)
    if alarm is None:
        return None
    ctx = TransformerContext.at_alarm(scenario.fleet.assets[asset], alarm, hours_after=hours_after)
    return build_investigator(llm, store, bus).investigate(anomaly_from(alarm), context=ctx)
