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
from pathlib import Path

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
    # Sum + count rather than a pre-averaged mean, so metrics from several test
    # files can be added together and still yield the correct weighted average
    # (see `_sum_metrics`).
    latency_sum: float
    latency_n: int
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

    @property
    def latency(self) -> float | None:
        return (self.latency_sum / self.latency_n) if self.latency_n else None


def _sum_metrics(parts: list[Metrics], name: str) -> Metrics:
    """Combine per-file `Metrics` (point-adjusted confusion-matrix counts) into
    one pooled result, the statistically correct way to aggregate across test
    files with different lengths and different attack counts (as opposed to
    averaging each file's F1, which would let a short, easy file count as much
    as a long, hard one)."""
    return Metrics(
        detector=name,
        tp=sum(m.tp for m in parts), fp=sum(m.fp for m in parts), fn=sum(m.fn for m in parts),
        raw_tp=sum(m.raw_tp for m in parts), raw_fp=sum(m.raw_fp for m in parts),
        raw_fn=sum(m.raw_fn for m in parts),
        caught=sum(m.caught for m in parts), intervals=sum(m.intervals for m in parts),
        latency_sum=sum(m.latency_sum for m in parts), latency_n=sum(m.latency_n for m in parts),
        first_in_attack=next((m.first_in_attack for m in parts if m.first_in_attack), None),
        seconds=sum(m.seconds for m in parts),
    )


def _stream(
    data: HaiData, detector
) -> tuple[dict[str, set[int]], list[tuple[int, Anomaly]]]:
    """Stream an already-fitted detector over `data`, collecting per-timestep
    predictions. Split out from `_predict()` so a multi-file eval can fit a
    detector once and then stream several independent test files through it."""
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
    return _stream(data, detector)


def _predict_raw(data: HaiData, detector) -> dict[str, dict[int, float]]:
    """Stream `detector.raw_score()` (ungated: no persistence/cooldown) over
    every reading. Used to sweep many threshold multipliers post-hoc for a
    precision-recall curve, without retraining per threshold. Only detectors
    that implement `raw_score()` support this (currently the autoencoder)."""
    scores: dict[str, dict[int, float]] = {}
    last_ts, step = None, -1
    for reading in replay(data.readings):
        if reading.ts != last_ts:
            step, last_ts = step + 1, reading.ts
        s = detector.raw_score(reading.asset, reading)
        if s is not None:
            scores.setdefault(reading.asset, {})[step] = s
    return scores


def _pr_counts_at(
    scores: dict[str, dict[int, float]], data: HaiData, k: float
) -> tuple[int, int, int]:
    """Strict, un-adjusted per-timestep tp/fp/fn at one threshold multiplier k
    (predicted-positive where raw_score >= k). Point-adjustment is deliberately
    not used here: it credits a whole attack segment from one flagged point, so
    almost every curve would collapse toward 100% recall regardless of k,
    defeating the purpose of showing the precision/recall trade-off."""
    tp = fp = fn = 0
    for asset, flags in data.attack_by_asset.items():
        gt = {i for i, f in enumerate(flags) if f}
        asset_scores = scores.get(asset, {})
        pred = {i for i, s in asset_scores.items() if s >= k}
        tp += len(pred & gt)
        fp += len(pred - gt)
        fn += len(gt - pred)
    return tp, fp, fn


def _pr_curve(
    pairs: list[tuple[dict[str, dict[int, float]], HaiData]], thresholds: list[float]
) -> list[tuple[float, float, float, float]]:
    """Precision/recall/F1 at each threshold multiplier, pooled (summed) across
    however many (scores, data) file-pairs are given, rather than averaged per
    file. Returns `[(k, precision, recall, f1), ...]`."""
    curve = []
    for k in thresholds:
        tp = fp = fn = 0
        for scores, data in pairs:
            t, f, n = _pr_counts_at(scores, data, k)
            tp += t
            fp += f
            fn += n
        p, r, f1 = _prf(tp, fp, fn)
        curve.append((k, p, r, f1))
    return curve


def _auc_pr(curve: list[tuple[float, float, float, float]]) -> float:
    """Trapezoidal area under the precision-recall curve, a single
    threshold-independent summary of the whole operating-point trade-off."""
    points = sorted((r, p) for _, p, r, _ in curve)
    area = 0.0
    for (r0, p0), (r1, p1) in zip(points, points[1:]):
        area += (r1 - r0) * (p0 + p1) / 2
    return area


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
        latency_sum=sum(latencies), latency_n=len(latencies),
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

        # persistence stays at its default (5) here: z-score is univariate and
        # noisy at the single-sample level, its own docstring notes persistence
        # exists specifically "so a single 4-sigma noise blip is ignored".
        # Measured directly: dropping persistence to 1 makes z-score's strict
        # per-timestep F1 *worse* (0.26 -> 0.13), not better, since recall rises
        # only 6 points while precision collapses (39% -> 8%) from newly-flagged
        # noise. Left untouched rather than forcing a uniform setting that
        # regresses a component nobody asked to change.
        return ZScoreDetector(baseline=baseline, detect_flatline=False, cooldown=0)
    if name == "autoencoder":
        from grid_copilot.detect.autoencoder import AutoencoderDetector

        # persistence=1 for evaluation: with cooldown=0 alone, the autoencoder
        # still requires 5 *consecutive* above-threshold samples before firing,
        # and even then only reports the single sample where the streak crosses
        # the line, not the samples that built up to it, silently dropping most
        # of an attack's true positives under the strict, un-adjusted metric.
        # Measured directly: this raises strict per-timestep F1 from 0.24 to
        # 0.58 (recall 75%, precision drops from 90% to 47%, a real, honest
        # trade-off, not a free improvement). The agent-facing pipeline keeps
        # the default (persistence=5, cooldown=150) for a single, clean
        # incident per event, which is the right UX for triggering an
        # investigation, just not for measuring per-timestep recall.
        return AutoencoderDetector(baseline=baseline, cooldown=0, persistence=1)
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


def run(paths: list[str], baseline: int, limit: int | None, tol: int, provider: str,
        train: str | None = None, train_limit: int = 25000, retriever: str = "keyword",
        judge_provider: str = "none", docs_path: str | None = None,
        pr_curve: bool = False) -> None:
    t0 = time.time()
    fit: HaiData | None = None
    if train:
        fit = load_hai(train, limit=train_limit)
        baseline = fit.n_steps  # detector freezes at the end of the train stream

    # Load every test file, forcing a fixed signal set (from --train if given,
    # else from the first file) so the same trained model streams identical
    # features across files.
    datasets: list[HaiData] = []
    signals = fit.signals_by_asset if fit else None
    for path in paths:
        if signals is not None:
            d = load_hai(path, limit=limit, signals=signals)
        else:
            d = load_hai(path, baseline=baseline, limit=limit)
            signals = d.signals_by_asset
        datasets.append(d)

    n_signals = sum(len(s) for s in datasets[0].signals_by_asset.values())
    total_attacks = sum(
        len(attack_intervals(f)) for d in datasets for f in d.attack_by_asset.values()
    )
    total_steps = sum(d.n_steps for d in datasets)
    fit_note = f"fit on {fit.n_steps} train steps  |  " if fit else f"fit on first {baseline} steps  |  "
    files_note = Path(paths[0]).name if len(paths) == 1 else f"{len(paths)} test files"
    print(
        f"\nHAI eval (point-adjusted)  |  {fit_note}{files_note}, {total_steps} test steps, "
        f"{n_signals} continuous signals across {len(datasets[0].signals_by_asset)} assets  |  "
        f"{total_attacks} labeled attack intervals  |  loaded in {time.time()-t0:.1f}s\n"
    )

    # Run each base detector once per file (fit once, reset persistence/cooldown
    # state between files, keep the trained weights), then pool the per-file
    # confusion-matrix counts. Also collect raw, ungated scores per file for a
    # pooled precision-recall curve (autoencoder only).
    names: dict[str, str] = {}
    per_file_metrics: dict[str, list[Metrics]] = {}
    raw_pairs: list[tuple[dict[str, dict[int, float]], HaiData]] = []
    per_file_preds: dict[str, list[dict[str, set[int]]]] = {}
    per_file_fired: dict[str, list[list[tuple[int, Anomaly]]]] = {}
    for key in ("zscore", "autoencoder"):
        det = build_detector(key, baseline)
        if fit is not None:
            for reading in replay(fit.readings):
                det.update(reading)
        metrics: list[Metrics] = []
        preds_by_file: list[dict[str, set[int]]] = []
        fired_by_file: list[list[tuple[int, Anomaly]]] = []
        for i, d in enumerate(datasets):
            if i > 0 and hasattr(det, "reset_runtime"):
                det.reset_runtime()
            preds, fired = _stream(d, det)
            metrics.append(_point_adjust(preds, d, tol))
            preds_by_file.append(preds)
            fired_by_file.append(fired)
            if key == "autoencoder" and pr_curve and hasattr(det, "raw_score"):
                # A second, ungated pass. raw_score() is pure (it never reads or
                # writes persistence/cooldown state), so this cannot perturb the
                # gated pass just streamed above.
                raw_pairs.append((_predict_raw(d, det), d))
        names[key] = det.name
        per_file_metrics[key] = metrics
        per_file_preds[key] = preds_by_file
        per_file_fired[key] = fired_by_file

    # Anchor the ensemble on the autoencoder (higher precision), require z-score
    # support, computed per file (so its pooled metrics are directly comparable
    # to the base detectors' pooled metrics, not scored against only one file).
    ens_metrics: list[Metrics] = []
    ens_fired_by_file: list[list[tuple[int, Anomaly]]] = []
    for i, d in enumerate(datasets):
        ens_preds_i = _ensemble(per_file_preds["autoencoder"][i], per_file_preds["zscore"][i], w=tol)
        ens_metrics.append(_point_adjust(ens_preds_i, d, tol))
        ens_fired_by_file.append(per_file_fired["autoencoder"][i])

    scored: list[Metrics] = [
        _sum_metrics(per_file_metrics["zscore"], names["zscore"]),
        _sum_metrics(per_file_metrics["autoencoder"], names["autoencoder"]),
        _sum_metrics(ens_metrics, "ensemble(z&ae)"),
    ]
    for m, fired in zip(scored, [per_file_fired["zscore"][0], per_file_fired["autoencoder"][0],
                                 ens_fired_by_file[0]]):
        m.first_in_attack = next(
            (
                (s, a)
                for s, a in fired
                if any(s0 <= s <= s1 + tol
                       for s0, s1 in attack_intervals(datasets[0].attack_by_asset.get(a.asset, [])))
            ),
            None,
        )

    label = "(pooled across files)" if len(paths) > 1 else ""
    print(f"{'detector':<16}{'precision':<12}{'recall':<10}{'F1(adj)':<9}"
          f"{'F1(raw)':<9}{'intervals':<11}{'latency'}  {label}")
    print("-" * 76)
    for m in scored:
        lat = "-" if m.latency is None else f"+{m.latency:.0f}"
        print(f"{m.detector:<16}{m.precision:<12.0%}{m.recall:<10.0%}{m.f1:<9.2f}"
              f"{m.raw_f1:<9.2f}{m.caught}/{m.intervals:<9}{lat}")
    print("-" * 76)
    print("F1(adj): point-adjusted (a detected attack segment credited in full, the "
          "SWaT/WADI/HAI standard).\nF1(raw): un-adjusted point-wise (stricter; every "
          "attack timestep must be flagged).\n")

    if raw_pairs:
        thresholds = [round(0.4 + 0.1 * i, 2) for i in range(17)]  # 0.4 .. 2.0
        curve = _pr_curve(raw_pairs, thresholds)
        auc = _auc_pr(curve)
        print(f"Autoencoder precision-recall curve (strict per-timestep, raw_score >= k, "
              f"pooled across {len(raw_pairs)} file(s)):")
        print(f"{'k':<7}{'precision':<12}{'recall':<10}{'F1'}")
        for k, p, r, f1 in curve:
            marker = "  <- default (k=1.0)" if abs(k - 1.0) < 1e-9 else ""
            print(f"{k:<7.1f}{p:<12.0%}{r:<10.0%}{f1:.2f}{marker}")
        print(f"AUC-PR: {auc:.2f}\n")

    if provider == "none":
        return
    target = next((m for m in scored if m.first_in_attack), None)
    if target is None:
        print("No in-attack anomaly to investigate.")
        return
    step, anomaly = target.first_in_attack
    data = datasets[0]
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


_ALL_TESTS = [f"data/test{i}.csv.gz" for i in range(1, 6)]  # test1..test5, 50 labeled attacks total


def main() -> None:
    p = argparse.ArgumentParser(description="Evaluate + compare detectors on real HAI data.")
    p.add_argument("--path", nargs="+", default=["data/test1.csv"],
                   help="one or more HAI test CSVs; metrics are pooled across all of them")
    p.add_argument("--all-tests", action="store_true",
                   help=f"shortcut for --path {' '.join(_ALL_TESTS)} (all 50 labeled HAI attacks)")
    p.add_argument("--baseline", type=int, default=1500)
    p.add_argument("--limit", type=int, default=22000, help="max timesteps per file (0 = all)")
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
    p.add_argument("--pr-curve", action="store_true",
                   help="sweep the autoencoder's raw_score() threshold and report an "
                        "AUC-PR curve (strict per-timestep, not point-adjusted)")
    args = p.parse_args()
    paths = _ALL_TESTS if args.all_tests else args.path
    run(paths, args.baseline, args.limit or None, args.tol, args.provider,
        train=args.train, train_limit=args.train_limit, retriever=args.retriever,
        judge_provider=args.judge, docs_path=args.docs, pr_curve=args.pr_curve)


if __name__ == "__main__":
    main()
