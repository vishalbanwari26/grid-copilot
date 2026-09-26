"""Score the transformer investigation against injected ground truth.

Every fault in the catalogue (and a healthy unit) is simulated on several load
profiles. Where monitoring raises an alarm, three readers classify it from the
same tool evidence:

- `textbook`: one method per alarm, no cross-checks (see transformer/rules.py);
- `expert`:   hand-written cross-checks, written knowing the catalogue (an upper
  bound for rules, not a fair opponent);
- `agent`:    the investigator with the chosen provider, which gets the tools and
  the notes but none of those rules.

Reported per reader: accuracy of the fault class, critical misses (a fault that
could end in a failure, called a decoy or no fault), false escalations (a decoy
called a real fault), and for the agent the validation retries, tools used and
time. Scenarios where monitoring raised no alarm are counted as monitoring misses:
no investigation ever starts for them, whatever the reader.

    python -m eval.transformer_eval --provider mock
    python -m eval.transformer_eval --provider groq --seeds 2
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from grid_copilot.agent.tools import Investigation
from grid_copilot.events import EventBus
from grid_copilot.memory.store import LocalIncidentStore
from grid_copilot.rag.retriever import KeywordRetriever
from grid_copilot.transformer import fleet as F
from grid_copilot.transformer.corpus import TRANSFORMER_CORPUS
from grid_copilot.transformer.ett import ett_available
from grid_copilot.transformer.rules import TransformerMockClient, expert, parse_findings, textbook
from grid_copilot.transformer.run import anomaly_from, investigate_alarm
from grid_copilot.transformer.sim import FAULTS
from grid_copilot.transformer.tools import TransformerContext, transformer_registry

DECOYS = {"external_interference", "overload", "no_fault", "sensor_fault"}


def build_llm(provider: str):
    if provider == "mock":
        return TransformerMockClient()
    from grid_copilot.config import load_env
    from grid_copilot.llm_retry import RetryingClient

    load_env()
    if provider == "groq":
        from cortex.llm.groq_client import GroqClient

        return RetryingClient(GroqClient())
    from cortex.llm.anthropic_client import AnthropicClient

    return RetryingClient(AnthropicClient())


def tool_findings(sc: F.Scenario, asset: str) -> dict[str, str]:
    """Run every data tool once, deterministically, for the rule baselines."""
    a = sc.fleet.assets[asset]
    alarm = sc.first_alarm(asset)
    inv = Investigation(anomaly_from(alarm), KeywordRetriever(TRANSFORMER_CORPUS), LocalIncidentStore(),
                        context=TransformerContext.at_alarm(a, alarm))
    reg = transformer_registry()
    tools = ["data_quality", "dga_diagnose", "thermal_check", "oltc_check", "pd_analyze"]
    if alarm.code.startswith("DGA_"):
        tools.append("lab_sample")  # the same site visit the rules would order for a gas alarm
    text = "\n".join(reg.get(n).run("", inv).summary for n in tools)
    return parse_findings(text)


def run(provider: str, seeds: int, kinds: list[str], only: set[tuple[str, int]] | None = None) -> dict:
    llm = build_llm(provider)
    real = ett_available()
    rows = []
    for kind in kinds:
        for seed in range(seeds):
            if only is not None and (kind, seed) not in only:
                continue
            sc = F.single(kind, seed=seed, plate_index=seed, prefer_real=real)
            asset = next(iter(sc.fleet.assets))
            truth = FAULTS[kind].fault_class if kind != "none" else "no_fault"
            critical = FAULTS[kind].critical if kind != "none" else False
            alarm = sc.first_alarm(asset)
            row = {"kind": kind, "seed": seed, "truth": truth, "critical": critical,
                   "alarm": alarm.code if alarm else None}
            if alarm is None:
                rows.append(row)
                continue
            fs = tool_findings(sc, asset)
            row["textbook"] = textbook(alarm.code, fs)
            row["expert"] = expert(alarm.code, fs)
            events: list[tuple[str, str]] = []
            bus = EventBus()
            bus.subscribe(lambda e: events.append((e.type.value, e.message)))
            t0 = time.time()
            try:
                report = investigate_alarm(sc, asset, llm, bus=bus)
                d = report.hypothesis.details
                row.update(agent=d.get("fault_class", "none"), urgency=d.get("urgency"),
                           validation_failed=bool(d.get("validation")),
                           root_cause=report.hypothesis.root_cause, reasoning=report.hypothesis.reasoning,
                           action=d.get("recommended_action"), verdict=report.verdict)
            except Exception as exc:  # a run that errors is scored as a miss, and reported
                row.update(agent="error", error=str(exc)[:300])
            row["seconds"] = round(time.time() - t0, 1)
            row["tools"] = [m.split("(")[0] for k, m in events if k == "tool_called"]
            row["validation_retries"] = sum(1 for k, _ in events if k == "validation")
            rows.append(row)
            print(f"{kind:18s} seed {seed}: alarm {row['alarm']}, truth {truth}, textbook {row['textbook']}, "
                  f"expert {row['expert']}, agent {row.get('agent')} ({row['seconds']}s, "
                  f"{row['validation_retries']} validation retries)", flush=True)
    return {"provider": provider, "load": "ETT" if real else "synthetic", "rows": rows,
            "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    alarmed = [r for r in rows if r.get("alarm")]
    out: dict = {"scenarios": len(rows), "alarmed": len(alarmed),
                 "monitoring_misses": [f"{r['kind']}#{r['seed']}" for r in rows if not r.get("alarm") and r["truth"] != "no_fault"],
                 "false_alarms_on_healthy": sum(1 for r in rows if r["truth"] == "no_fault" and r.get("alarm"))}
    for reader in ("textbook", "expert", "agent"):
        got = [r for r in alarmed if reader in r]
        correct = sum(r[reader] == r["truth"] for r in got)
        crit = [r for r in got if r["critical"] and r[reader] in DECOYS]
        false_esc = [r for r in got if r["truth"] in DECOYS and r[reader] not in DECOYS]
        wrong = Counter(f"{r['truth']} -> {r[reader]}" for r in got if r[reader] != r["truth"])
        out[reader] = {"accuracy": round(correct / len(got), 3) if got else None, "n": len(got),
                       "critical_misses": len(crit), "false_escalations": len(false_esc),
                       "confusions": dict(wrong.most_common())}
    agent = [r for r in alarmed if "agent" in r]
    if agent:
        out["agent"]["validation_retries"] = sum(r["validation_retries"] for r in agent)
        out["agent"]["validation_failed"] = sum(1 for r in agent if r.get("validation_failed"))
        out["agent"]["mean_seconds"] = round(sum(r["seconds"] for r in agent) / len(agent), 1)
        out["agent"]["mean_tools"] = round(sum(len(r["tools"]) for r in agent) / len(agent), 1)
        per_tool = Counter(t for r in agent for t in r["tools"])
        out["agent"]["tool_use"] = {t: round(c / len(agent), 2) for t, c in per_tool.most_common()}
    per_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in alarmed:
        for reader in ("textbook", "expert", "agent"):
            if reader in r:
                per_kind[r["kind"]][reader] += int(r[reader] == r["truth"])
        per_kind[r["kind"]]["n"] += 1
    out["per_kind"] = {k: dict(v) for k, v in per_kind.items()}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock", choices=["mock", "groq", "anthropic"])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--first-seed", type=int, default=0,
                    help="skip seeds below this, e.g. to split a live run over two days")
    ap.add_argument("--kinds", nargs="*", default=list(FAULTS) + ["none"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--retry-errors", default=None,
                    help="a previous results file: rerun only its errored agent runs and merge")
    args = ap.parse_args()
    if args.retry_errors:
        prev = json.loads(Path(args.retry_errors).read_text())
        todo = {(r["kind"], r["seed"]) for r in prev["rows"] if r.get("agent") == "error"}
        fresh = run(prev["provider"], 1 + max(s for _, s in todo), sorted({k for k, _ in todo}), only=todo) if todo else {"rows": []}
        redone = {(r["kind"], r["seed"]): r for r in fresh["rows"]}
        rows = [redone.get((r["kind"], r["seed"]), r) for r in prev["rows"]]
        res = {**prev, "rows": rows, "summary": summarize(rows), "retried": sorted(f"{k}#{s}" for k, s in todo)}
        args.provider = prev["provider"]
        args.out = args.out or args.retry_errors
    else:
        only = {(k, s) for k in args.kinds for s in range(args.first_seed, args.seeds)} if args.first_seed else None
        res = run(args.provider, args.seeds, args.kinds, only=only)
    s = res["summary"]
    print(f"\n{s['alarmed']} of {s['scenarios']} scenarios raised an alarm ({res['load']} load). "
          f"Monitoring misses: {', '.join(s['monitoring_misses']) or 'none'}.")
    for reader in ("textbook", "expert", "agent"):
        v = s[reader]
        print(f"{reader:9s} accuracy {v['accuracy']:.0%}  critical misses {v['critical_misses']}  "
              f"false escalations {v['false_escalations']}  wrong: {v['confusions']}")
    if "validation_retries" in s["agent"]:
        a = s["agent"]
        print(f"agent: {a['validation_retries']} validation retries, {a['validation_failed']} still invalid, "
              f"{a['mean_tools']} tools and {a['mean_seconds']} s per investigation")
    out = Path(args.out or f"eval/results/transformer_{args.provider}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=str))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
