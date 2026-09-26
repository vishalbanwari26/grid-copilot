"""HTTP endpoints for the transformer fleet view.

- GET  /api/fleet                  ranked fleet: health, risk, first alarm, data trust, latest decision
- GET  /api/fleet/asset/{asset}    one transformer: trends, alarms, events, Duval point, PRPD, data quality
- GET  /api/fleet/investigate      stream an investigation (SSE); ends with a proposal
- POST /api/fleet/decision         a named reviewer accepts, overrides or defers a proposal
- GET  /api/fleet/decisions        the decision log and whether its hash chain is intact
- GET  /api/fleet/classes          fault classes, for the override form

The fleet is simulated once per process. Proposals live in memory until someone
decides on them; decisions are appended to data/decisions.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import threading
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from grid_copilot.events import Event, EventBus
from grid_copilot.transformer import fleet as F
from grid_copilot.transformer import quality as dq
from grid_copilot.transformer.decisions import DecisionLog, Proposal
from grid_copilot.transformer.diagnostics import duval_triangle_1, gas_increment
from grid_copilot.transformer.health import rank
from grid_copilot.transformer.monitor import expected_top_oil_for
from grid_copilot.transformer.rules import TransformerMockClient
from grid_copilot.transformer.run import anomaly_from, build_investigator, default_history
from grid_copilot.transformer.semantics import SIGNALS, decision_submodel, ref
from grid_copilot.transformer.sim import FAULT_CLASSES, GASES
from grid_copilot.transformer.tools import TransformerContext

router = APIRouter(prefix="/api/fleet")
DECISIONS = DecisionLog(Path("data/decisions.jsonl"))
_PROPOSALS: dict[str, Proposal] = {}


@lru_cache(maxsize=1)
def scenario() -> F.Scenario:
    return F.build()


def _latest_decision(asset: str) -> dict | None:
    recs = [r for r in DECISIONS.records() if r.proposal["asset"] == asset]
    if not recs:
        return None
    r = recs[-1]
    return {"verdict": r.verdict, "reviewer": r.reviewer, "fault_class": r.final_fault_class,
            "reason": r.reason, "decided_at": r.decided_at}


@router.get("")
def fleet() -> dict:
    sc = scenario()
    out = []
    for h in rank(sc.fleet):
        a = sc.fleet.assets[h.asset]
        alarm = sc.first_alarm(h.asset)
        q = dq.assess(a, len(a.ts) - 1)
        flagged = sorted(c for c, v in q.items() if v.grade != "trusted")
        trust = "untrusted" if any(q[c].grade == "untrusted" for c in flagged) else "suspect" if flagged else "trusted"
        out.append({
            **h.to_dict(), "mva": a.plate.mva, "kv": a.plate.kv, "year": a.plate.year, "customers": a.plate.customers,
            "alarm": {"code": alarm.code, "ts": alarm.ts.isoformat(), "text": alarm.text} if alarm else None,
            "data_trust": trust, "flagged_channels": flagged, "decision": _latest_decision(h.asset),
        })
    end = sc.fleet.start.isoformat()
    return {"load_source": sc.fleet.load_source, "start": end, "hours": sc.fleet.hours, "assets": out}


def _every(series: list[float], step: int) -> list[float]:
    """Mean over each block of `step` samples, for readable trend lines."""
    return [round(sum(series[i:i + step]) / len(series[i:i + step]), 2) for i in range(0, len(series), step)]


@router.get("/asset/{asset}")
def asset_detail(asset: str) -> dict:
    sc = scenario()
    if asset not in sc.fleet.assets:
        raise HTTPException(404, f"unknown asset {asset}")
    a = sc.fleet.assets[asset]
    ch = a.channels
    step = 12
    end = len(a.ts) - 1
    alarm = sc.first_alarm(asset)
    expected = expected_top_oil_for(a)
    inc = {g: gas_increment(ch[g.lower() + "_ppm"], (0, 240), (end - 24, end + 1)) for g in GASES}
    duval = duval_triangle_1(inc["CH4"], inc["C2H4"], inc["C2H2"])
    active = [i for i in range(end - 24 * 7, end + 1) if ch["pd_pc"][i] >= 100]
    prpd = [round(sum(a.prpd[i][b] for i in active)) for b in range(12)] if active else [0] * 12
    q = dq.assess(a, end)
    channels = ["load_pu", "top_oil_c", "hotspot_c", "h2_ppm", "c2h2_ppm", "c2h4_ppm", "ch4_ppm", "co_ppm",
                "pd_pc", "oltc_temp_diff_c", "oltc_op_time_s"]
    events = [e for e in a.events if e.code not in ("TAP_CHANGE", "FANS_ON", "FANS_OFF")]
    return {
        "asset": asset, "plate": a.plate.__dict__, "ts": [t.isoformat() for t in a.ts[::step]],
        "series": {c: {"values": _every(ch[c], step), "label": SIGNALS[c].label, "unit": SIGNALS[c].unit,
                       "ref": ref(c), "grade": q[c].grade if c in q else "not_checked",
                       "reasons": q[c].reasons if c in q else []} for c in channels},
        "expected_top_oil": _every(expected, step),
        "alarms": [{"code": x.code, "ts": x.ts.isoformat(), "text": x.text, "value": x.value, "limit": x.threshold,
                    "index": x.h // step} for x in sc.alarms.get(asset, [])],
        "events": [{"ts": e.ts.isoformat(), "code": e.code, "text": e.text, "severity": e.severity} for e in events][-40:],
        "duval": duval.__dict__ | {"meaning": duval.meaning} if duval and sum(inc[g] for g in ("CH4", "C2H4", "C2H2")) >= 2 else None,
        "prpd": prpd, "first_alarm": alarm.code if alarm else None,
        "tap_ops_total": len(a.tap_ops),
    }


@router.get("/classes")
def classes() -> list[str]:
    return FAULT_CLASSES


def _llm(provider: str):
    if provider == "mock":
        return TransformerMockClient(), "mock (rule-based)"
    from grid_copilot.config import load_env
    from grid_copilot.llm_retry import RetryingClient

    load_env()
    from cortex.llm.groq_client import GroqClient

    client = GroqClient()
    return RetryingClient(client), f"groq:{client.model}"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _investigate(asset: str, provider: str, pace: float):
    sc = scenario()
    alarm = sc.first_alarm(asset)
    if alarm is None:
        yield _sse("error", {"message": f"{asset} has no alarm to investigate."})
        return
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    bus = EventBus()
    bus.subscribe(lambda e: loop.call_soon_threadsafe(queue.put_nowait, ("event", e)))

    def run() -> None:
        try:
            llm, model = _llm(provider)
            ctx = TransformerContext.at_alarm(sc.fleet.assets[asset], alarm)
            inv = build_investigator(llm, store=default_history(DECISIONS), bus=bus)
            report = inv.investigate(anomaly_from(alarm), context=ctx)
            proposal = Proposal.from_report(report, alarm.code, model)
            _PROPOSALS[proposal.proposal_id] = proposal
            loop.call_soon_threadsafe(queue.put_nowait, ("proposal", proposal))
        except Exception as exc:  # surface to the UI
            loop.call_soon_threadsafe(queue.put_nowait, ("error", {"message": str(exc)[:400]}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    threading.Thread(target=run, daemon=True).start()
    while True:
        kind, obj = await queue.get()
        if kind == "done":
            break
        if kind == "event":
            ev: Event = obj
            yield _sse("event", {"type": ev.type.value, "message": ev.message, "payload": ev.payload})
            if provider == "mock" and pace > 0:
                await asyncio.sleep(pace)
        elif kind == "proposal":
            yield _sse("proposal", obj.__dict__)
        else:
            yield _sse("error", obj)


@router.get("/investigate")
async def investigate(asset: str, provider: str = "groq", pace: float = 0.35) -> StreamingResponse:
    return StreamingResponse(_investigate(asset, provider, pace), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class DecisionIn(BaseModel):
    proposal_id: str
    reviewer: str
    verdict: str
    reason: str = ""
    final_fault_class: str | None = None
    final_action: str | None = None


@router.post("/decision")
def decide(body: DecisionIn) -> dict:
    proposal = _PROPOSALS.get(body.proposal_id)
    if proposal is None:
        raise HTTPException(404, "unknown or expired proposal; run the investigation again")
    try:
        rec = DECISIONS.record(proposal, body.reviewer, body.verdict, body.reason,
                               body.final_fault_class, body.final_action)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    record = rec.__dict__
    return {"record": record, "submodel": decision_submodel(record)}


@router.get("/decisions")
def decisions() -> dict:
    ok, why = DECISIONS.verify_chain()
    recs = DECISIONS.records()
    return {"chain_ok": ok, "chain": why, "records": [
        {"decision_id": r.decision_id, "asset": r.proposal["asset"], "alarm": r.proposal["alarm"],
         "proposed": r.proposal["fault_class"], "final": r.final_fault_class, "verdict": r.verdict,
         "reviewer": r.reviewer, "reason": r.reason, "decided_at": r.decided_at, "model": r.proposal["model"],
         "hash": r.hash[:12]} for r in reversed(recs)]}


@router.get("/decision/{decision_id}/submodel")
def submodel(decision_id: str) -> dict:
    for r in DECISIONS.records():
        if r.decision_id == decision_id:
            return decision_submodel(r.__dict__)
    raise HTTPException(404, "unknown decision")


# --- digital twin ---------------------------------------------------------------

twin_router = APIRouter(prefix="/api/twin")
_HEALTH_CACHE: dict = {}


def _twin():
    from grid_copilot.transformer.twin import Twin

    # Health only depends on the (fixed) simulated data, so it is shared across
    # requests; decisions are read fresh every time.
    return Twin(scenario(), DECISIONS, _HEALTH_CACHE)


@twin_router.get("/grid")
def twin_grid() -> dict:
    return _twin().grid()


@twin_router.get("/substation/{sid}")
def twin_substation(sid: str) -> dict:
    try:
        return _twin().substation(sid)
    except KeyError as exc:
        raise HTTPException(404, f"unknown substation {sid}") from exc


@twin_router.get("/transformer/{asset}")
def twin_transformer(asset: str) -> dict:
    if asset not in scenario().fleet.assets:
        raise HTTPException(404, f"unknown transformer {asset}")
    return _twin().transformer(asset)


@twin_router.get("/component/{asset}/{cid}")
def twin_component(asset: str, cid: str) -> dict:
    if asset not in scenario().fleet.assets:
        raise HTTPException(404, f"unknown transformer {asset}")
    try:
        return _twin().component(asset, cid)
    except KeyError as exc:
        raise HTTPException(404, f"unknown component {cid}") from exc
