"""Evaluate detection on the real HAI dataset, comparing detectors.

Ground truth is HAI's own per-process attack labels. Each detector is streamed
over the real telemetry and scored against those labels:

- **interval recall**: of the labeled attack intervals, how many were flagged at
  least once (within a tolerance for detection lag)?
- **precision**: of all anomalies fired, how many fell inside a labeled attack?
- **latency**: for caught intervals, mean samples after onset to the first flag.

The default runs both the fixed-baseline z-score and the small autoencoder, so
the precision improvement (or trade-off) is visible directly. With `--provider`,
it also runs the full investigation loop on the first in-attack anomaly, so the
agentic report is grounded in real telemetry. Measuring this honestly, including
where a detector's precision runs out, is the point of having an eval.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from grid_copilot.agent.investigator import Investigator
from grid_copilot.agent.mock_llm import GridMockClient
from grid_copilot.ingest.hai import HaiData, attack_intervals, load_hai
from grid_copilot.ingest.replay import replay
from grid_copilot.types import Anomaly


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


@dataclass
class Metrics:
    detector: str
    tp: int  # point-adjusted
    fp: int
    fn: int
    raw_tp: int  # un-adjusted point-wise (stricter, no segment crediting)
    raw_fp: int
    raw_fn: int
    caught: int
    intervals: int
    latency: float | None
    first_in_attack: tuple[int, Anomaly] | None
    seconds: float

    @property
    def precision(self) -> float:
        return _prf(self.tp, self.fp, self.fn)[0]

    @property
    def recall(self) -> float:
        return _prf(self.tp, self.fp, self.fn)[1]

    @property
    def f1(self) -> float:
        return _prf(self.tp, self.fp, self.fn)[2]

    @property
    def raw_f1(self) -> float:
        return _prf(self.raw_tp, self.raw_fp, self.raw_fn)[2]


def _predict(
    data: HaiData, detector, fit: HaiData | None = None
) -> tuple[dict[str, set[int]], list[tuple[int, Anomaly]]]:
    """Stream a (cooldown-free) detector and collect per-timestep predictions.

    If `fit` is given (an attack-free train set), the detector is first fitted on
    it and frozen, then scored on `data`. This is the correct protocol: learn
    normal on dedicated training data, detect on unseen test data.
    """
    if fit is not None:
        for reading in replay(fit.readings):
            detector.update(reading)  # trains once its baseline (= fit length) is reached
    preds: dict[str, set[int]] = {}
    fired: list[tuple[int, Anomaly]] = []
    last_ts, step = None, -1
    for reading in replay(data.readings):
        if reading.ts != last_ts:
            step, last_ts = step + 1, reading.ts
        for anomaly in detector.update(reading):
            preds.setdefault(anomaly.asset, set()).add(step)
            fired.append((step, anomaly))
    return preds, fired


def _point_adjust(preds: dict[str, set[int]], data: HaiData, tol: int) -> Metrics:
    """Point-adjusted precision/recall/F1 (the standard for SWaT/WADI/HAI).

    If any timestep inside a labeled attack segment is flagged, the whole segment
    counts as detected; precision is then scored per timestep. Also reports how
    many of the discrete attack intervals were caught, and detection latency.
    """
    tp = fp = fn = raw_tp = raw_fp = raw_fn = caught = intervals = 0
    latencies: list[int] = []
    for asset, flags in data.attack_by_asset.items():
        ivs = attack_intervals(flags)
        intervals += len(ivs)
        pred = preds.get(asset, set())
        gt = {i for i, f in enumerate(flags) if f}
        # Un-adjusted point-wise counts (stricter: no segment crediting).
        raw_tp += len(pred & gt)
        raw_fp += len(pred - gt)
        raw_fn += len(gt - pred)
        # Point-adjusted: a segment with any detected point counts as fully found.
        adjusted = set(pred)
        for s0, s1 in ivs:
            hit = [p for p in pred if s0 <= p <= s1 + tol]
            if hit:
                caught += 1
                latencies.append(max(0, min(hit) - s0))
                adjusted |= set(range(s0, s1 + 1))
        tp += len(adjusted & gt)
        fp += len(adjusted - gt)
        fn += len(gt - adjusted)
    return Metrics(
        detector="", tp=tp, fp=fp, fn=fn, raw_tp=raw_tp, raw_fp=raw_fp, raw_fn=raw_fn,
        caught=caught, intervals=intervals,
        latency=(sum(latencies) / len(latencies)) if latencies else None,
        first_in_attack=None, seconds=0.0,
    )


def score_detector(data: HaiData, detector, tol: int) -> Metrics:
    t0 = time.time()
    preds, fired = _predict(data, detector)
    m = _point_adjust(preds, data, tol)
    m.detector = detector.name
    m.seconds = time.time() - t0
    m.first_in_attack = next(
        (
            (s, a)
            for s, a in fired
            if any(s0 <= s <= s1 + tol
                   for s0, s1 in attack_intervals(data.attack_by_asset.get(a.asset, [])))
        ),
        None,
    )
    return m


def build_detector(name: str, baseline: int):
    # cooldown=0 for evaluation: we want a per-timestep prediction, not debounced
    # events. The agent-facing pipeline keeps the default cooldown for triggering.
    if name == "zscore":
        from grid_copilot.detect.statistical import ZScoreDetector

        return ZScoreDetector(baseline=baseline, detect_flatline=False, cooldown=0)
    if name == "autoencoder":
        from grid_copilot.detect.autoencoder import AutoencoderDetector

        return AutoencoderDetector(baseline=baseline, cooldown=0)
    raise SystemExit(f"unknown detector: {name}")


def _ensemble(anchor: dict[str, set[int]], support: dict[str, set[int]], w: int) -> dict[str, set[int]]:
    """Agreement ensemble: keep an `anchor` prediction only if the `support`
    detector also fired within w samples. Anchor on the higher-precision detector
    and require corroboration, trading a little recall for precision."""
    out: dict[str, set[int]] = {}
    for asset, sa in anchor.items():
        ss = support.get(asset, set())
        if not ss:
            continue
        cover: set[int] = set()
        for x in ss:  # expand support steps by +/- w once (fast), then intersect
            cover.update(range(x - w, x + w + 1))
        out[asset] = sa & cover
    return out


def _build_retriever(retriever: str, docs_path: str | None = None):
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


def _hai_reference(data: HaiData, asset: str, interval: tuple[int, int]) -> str:
    """Build a ground-truth reference for the judge from the labels + telemetry.

    HAI does not ship a per-interval textual cause, so we derive one from what the
    labels do tell us: the affected process, and the signals that measurably
    deviated (interval mean vs the 200 samples of normal before it, in baseline
    stds). The judge then scores whether the agent identified the right equipment
    and a telemetry-consistent mechanism, which is grounding correctness rather
    than a match to the attacker's exact intent.
    """
    import statistics

    from grid_copilot.tags import decode_tag, process_name

    s0, s1 = interval
    reads = [r for r in data.readings if r.asset == asset]  # step-ordered for the asset
    s1 = min(s1, len(reads) - 1)
    b0 = max(0, s0 - 200)
    movers: list[tuple[float, str, float]] = []
    for sig in data.signals_by_asset.get(asset, []):
        base = [reads[i].values[sig] for i in range(b0, s0) if sig in reads[i].values]
        atk = [reads[i].values[sig] for i in range(s0, s1 + 1) if sig in reads[i].values]
        if len(base) < 5 or not atk:
            continue
        bstd = statistics.pstdev(base) or 1e-9
        delta = statistics.fmean(atk) - statistics.fmean(base)
        movers.append((abs(delta) / bstd, sig, delta))
    movers.sort(reverse=True)
    desc = "; ".join(
        f"{sig} ({decode_tag(sig) or 'signal'}) {'rose' if d > 0 else 'fell'}"
        for _, sig, d in movers[:3]
    )
    proc = process_name(asset)
    # Framed as a physical anomaly, not an "attack": the agent does physical RCA
    # from telemetry and cannot infer malicious intent, so the judge should score
    # whether it identified the right equipment and a telemetry-consistent
    # mechanism, not whether it used security language.
    return (
        f"An anomaly on the {proc} process ({asset}). The signals that deviated from "
        f"normal during this period were: {desc or 'unclear'}. A correct root cause "
        f"identifies the {proc} process and gives a mechanism consistent with these "
        f"signals (for example a valve, control-loop, or sensor issue on that process)."
    )


def _interval_of(data: HaiData, asset: str, step: int, tol: int) -> tuple[int, int] | None:
    for s0, s1 in attack_intervals(data.attack_by_asset.get(asset, [])):
        if s0 <= step <= s1 + tol:
            return (s0, s1)
    return None


def run(path: str, baseline: int, limit: int | None, tol: int, provider: str,
        train: str | None = None, train_limit: int = 25000, retriever: str = "keyword",
        judge_provider: str = "none", docs_path: str | None = None) -> None:
    t0 = time.time()
    fit: HaiData | None = None
    if train:
        fit = load_hai(train, limit=train_limit)
        # Force test to use the exact signal set learned on train.
        data = load_hai(path, limit=limit, signals=fit.signals_by_asset)
        baseline = fit.n_steps  # detector freezes at the end of the train stream
    else:
        data = load_hai(path, baseline=baseline, limit=limit)

    n_signals = sum(len(s) for s in data.signals_by_asset.values())
    total_attacks = sum(len(attack_intervals(f)) for f in data.attack_by_asset.values())
    fit_note = f"fit on {fit.n_steps} train steps  |  " if fit else f"fit on first {baseline} steps  |  "
    print(
        f"\nHAI eval (point-adjusted)  |  {fit_note}{data.n_steps} test steps, {n_signals} "
        f"continuous signals across {len(data.signals_by_asset)} assets  |  {total_attacks} "
        f"labeled attack intervals  |  loaded in {time.time()-t0:.1f}s\n"
    )

    # Run the two base detectors once; derive the agreement ensemble from them.
    base: dict[str, tuple[dict[str, set[int]], list]] = {}
    names: dict[str, str] = {}
    for key in ("zscore", "autoencoder"):
        det = build_detector(key, baseline)
        base[key] = _predict(data, det, fit=fit)
        names[key] = det.name

    # Anchor the ensemble on the autoencoder (higher precision), require z-score support.
    ens_preds = _ensemble(base["autoencoder"][0], base["zscore"][0], w=tol)
    scored: list[Metrics] = []
    for label, preds, fired in [
        (names["zscore"], *base["zscore"]),
        (names["autoencoder"], *base["autoencoder"]),
        ("ensemble(z&ae)", ens_preds, base["autoencoder"][1]),
    ]:
        m = _point_adjust(preds, data, tol)
        m.detector = label
        m.first_in_attack = next(
            (
                (s, a)
                for s, a in fired
                if any(s0 <= s <= s1 + tol
                       for s0, s1 in attack_intervals(data.attack_by_asset.get(a.asset, [])))
            ),
            None,
        )
        scored.append(m)

    print(f"{'detector':<16}{'precision':<12}{'recall':<10}{'F1(adj)':<9}"
          f"{'F1(raw)':<9}{'intervals':<11}{'latency'}")
    print("-" * 76)
    for m in scored:
        lat = "-" if m.latency is None else f"+{m.latency:.0f}"
        print(f"{m.detector:<16}{m.precision:<12.0%}{m.recall:<10.0%}{m.f1:<9.2f}"
              f"{m.raw_f1:<9.2f}{m.caught}/{m.intervals:<9}{lat}")
    print("-" * 76)
    print("F1(adj): point-adjusted (a detected attack segment credited in full, the "
          "SWaT/WADI/HAI standard).\nF1(raw): un-adjusted point-wise (stricter; every "
          "attack timestep must be flagged).\n")

    if provider == "none":
        return
    target = next((m for m in scored if m.first_in_attack), None)
    if target is None:
        print("No in-attack anomaly to investigate.")
        return
    step, anomaly = target.first_in_attack
    print(f"Investigating first in-attack anomaly: {anomaly.signal} on {anomaly.asset} "
          f"(step {step}, score {anomaly.score})\n")
    from grid_copilot.telemetry import TelemetryLog

    report = Investigator(
        _build_llm(provider), retriever=_build_retriever(retriever, docs_path),
        telemetry=TelemetryLog.from_readings(data.readings),
    ).investigate(anomaly)
    print("\n" + "=" * 70)
    print(report.to_markdown())

    # Score the stated cause against a labels-derived reference.
    if judge_provider != "none":
        from eval.judge import LLMJudge

        interval = _interval_of(data, anomaly.asset, step, tol)
        if interval is not None:
            reference = _hai_reference(data, anomaly.asset, interval)
            verdict = LLMJudge(_build_llm(judge_provider)).score(
                context=f"{anomaly.signal} on {anomaly.asset}",
                hypothesis=f"{report.hypothesis.root_cause}. {report.hypothesis.reasoning}",
                ground_truth=reference,
            )
            print("\n" + "-" * 70)
            print(f"Judge ({judge_provider}) vs labels-derived reference: "
                  f"{verdict.score:.2f} {verdict.verdict}")
            print(f"  reference: {reference}")
            print(f"  justification: {verdict.justification}")


def _build_llm(provider: str):
    if provider == "mock":
        return GridMockClient()
    from grid_copilot.config import load_env

    load_env()
    if provider == "anthropic":
        from cortex.llm.anthropic_client import AnthropicClient

        return AnthropicClient()
    if provider == "groq":
        from cortex.llm.groq_client import GroqClient

        return GroqClient()
    raise SystemExit(f"unknown provider: {provider}")


def main() -> None:
    p = argparse.ArgumentParser(description="Evaluate + compare detectors on real HAI data.")
    p.add_argument("--path", default="data/test1.csv")
    p.add_argument("--baseline", type=int, default=1500)
    p.add_argument("--limit", type=int, default=22000, help="max timesteps (0 = all)")
    p.add_argument("--tol", type=int, default=30, help="detection-lag tolerance in samples")
    p.add_argument("--provider", default="none", choices=["none", "mock", "anthropic", "groq"])
    p.add_argument("--train", default=None,
                   help="attack-free train CSV to fit on (e.g. data/train1.csv.gz)")
    p.add_argument("--train-limit", type=int, default=25000, help="max train timesteps")
    p.add_argument("--retriever", default="keyword", choices=["keyword", "vector"])
    p.add_argument("--judge", default="none", choices=["none", "mock", "anthropic", "groq"],
                   help="grade the stated cause against a labels-derived reference")
    p.add_argument("--docs", default=None,
                   help="path to extra spec/manual text files to retrieve over")
    args = p.parse_args()
    run(args.path, args.baseline, args.limit or None, args.tol, args.provider,
        train=args.train, train_limit=args.train_limit, retriever=args.retriever,
        judge_provider=args.judge, docs_path=args.docs)


if __name__ == "__main__":
    main()
