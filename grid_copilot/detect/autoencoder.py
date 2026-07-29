"""A small per-asset autoencoder detector for correlated multivariate faults.

The fixed-baseline z-score is univariate: it flags any single signal outside its
own learned range, so legitimate multi-modal operation (setpoint changes, mode
transitions) trips it, which is why its precision on real ICS data is poor. An
autoencoder learns the *joint* structure of an asset's signals from normal data.
A coherent change that preserves the usual correlations reconstructs well (low
error); an attack that pushes one signal out of step with the others breaks those
correlations and reconstructs badly (high error). That is the precision lever.

It is trained per asset (one small network over that process's continuous
signals), which fits the streaming `Detector` interface without needing every
asset's reading at once, and still captures within-process correlations. Torch is
imported lazily, so the offline z-score demo never needs it.

Design mirrors the z-score detector: learn on an attack-free baseline, freeze,
then score, with a persistence guard and a per-asset cooldown so one event yields
one anomaly. The trigger signal reported is the one contributing most to the
reconstruction error, so the anomaly still points at a specific tag.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque

from grid_copilot.types import Anomaly, Reading


class AutoencoderDetector:
    name = "autoencoder"

    def __init__(
        self,
        baseline: int = 1500,
        epochs: int = 60,
        lr: float = 1e-2,
        latent_frac: float = 0.34,
        threshold_pct: float = 99.9,
        margin: float = 1.5,
        persistence: int = 5,
        cooldown: int = 150,
        window: int = 120,
        min_base_std: float = 1e-6,
        seed: int = 0,
    ) -> None:
        self.baseline = baseline
        self.epochs = epochs
        self.lr = lr
        self.latent_frac = latent_frac
        self.threshold_pct = threshold_pct
        self.margin = margin
        self.persistence = persistence
        self.cooldown = cooldown
        self.min_base_std = min_base_std
        self.seed = seed

        self._buf: dict[str, list[dict[str, float]]] = defaultdict(list)
        self._model: dict[str, dict] = {}  # asset -> trained state
        self._trained: set[str] = set()
        self._failed: set[str] = set()  # too few usable signals to model
        self._recent: dict[str, deque[Reading]] = defaultdict(lambda: deque(maxlen=window))
        self._streak: dict[str, int] = defaultdict(int)
        self._cooldown_left: dict[str, int] = defaultdict(int)

    def update(self, reading: Reading) -> list[Anomaly]:
        self._recent[reading.asset].append(reading)
        asset = reading.asset

        if asset in self._failed:
            return []
        if asset not in self._trained:
            self._buf[asset].append(dict(reading.values))
            if len(self._buf[asset]) >= self.baseline:
                self._train(asset)
            return []
        return self._score(asset, reading)

    # -- training -----------------------------------------------------------

    def _train(self, asset: str) -> None:
        import torch

        rows = self._buf[asset]
        # Signals present in every baseline row with real variance.
        common = set.intersection(*(set(r) for r in rows)) if rows else set()
        order = sorted(
            s for s in common if statistics.pstdev([r[s] for r in rows]) > self.min_base_std
        )
        if len(order) < 2:  # need at least a 2-D correlation to model
            self._failed.add(asset)
            self._buf.pop(asset, None)
            return

        mean = [statistics.fmean([r[s] for r in rows]) for s in order]
        std = [statistics.pstdev([r[s] for r in rows]) for s in order]
        x = torch.tensor(
            [[(r[s] - mean[i]) / std[i] for i, s in enumerate(order)] for r in rows],
            dtype=torch.float32,
        )

        torch.manual_seed(self.seed)
        d = len(order)
        latent = max(1, int(round(d * self.latent_frac)))
        hidden = max(latent + 1, d // 2)
        net = torch.nn.Sequential(
            torch.nn.Linear(d, hidden), torch.nn.Tanh(),
            torch.nn.Linear(hidden, latent), torch.nn.Tanh(),
            torch.nn.Linear(latent, hidden), torch.nn.Tanh(),
            torch.nn.Linear(hidden, d),
        )
        opt = torch.optim.Adam(net.parameters(), lr=self.lr)
        loss_fn = torch.nn.MSELoss()
        net.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            loss = loss_fn(net(x), x)
            loss.backward()
            opt.step()

        net.eval()
        with torch.no_grad():
            errs = ((net(x) - x) ** 2).mean(dim=1)
        errs_sorted = sorted(errs.tolist())
        k = min(len(errs_sorted) - 1, int(self.threshold_pct / 100.0 * len(errs_sorted)))
        threshold = errs_sorted[k] * self.margin

        self._model[asset] = {"net": net, "order": order, "mean": mean, "std": std,
                              "threshold": max(threshold, 1e-9)}
        self._trained.add(asset)
        self._buf.pop(asset, None)

    # -- scoring ------------------------------------------------------------

    def _reconstruction_error(self, asset: str, reading: Reading) -> tuple[float, float, str] | None:
        """Pure, side-effect-free forward pass: (error, threshold, trigger signal).

        Shared by `_score()` (the persistence/cooldown-gated path that decides
        whether to fire an Anomaly for the live agent) and `raw_score()` (the
        ungated path used to sweep thresholds for a precision-recall curve).
        Returns None if this asset is not trained or the reading is missing a
        required signal.
        """
        import torch

        m = self._model.get(asset)
        if m is None:
            return None
        order, mean, std = m["order"], m["mean"], m["std"]
        if any(s not in reading.values for s in order):
            return None

        x = torch.tensor(
            [[(reading.values[s] - mean[i]) / std[i] for i, s in enumerate(order)]],
            dtype=torch.float32,
        )
        with torch.no_grad():
            recon = m["net"](x)
        per_feat = ((recon - x) ** 2)[0]
        err = float(per_feat.mean())
        trigger = order[int(per_feat.argmax())]
        return err, m["threshold"], trigger

    def raw_score(self, asset: str, reading: Reading) -> float | None:
        """Continuous anomaly score for one reading, with no persistence or
        cooldown gating: `reconstruction_error / threshold`, so 1.0 sits
        exactly at the detector's own decision boundary. Returns None if the
        asset is not yet trained or the reading is missing a required signal.

        Used to build a precision-recall curve across many threshold
        multipliers post-hoc, without retraining the model per threshold, and
        without perturbing `update()`'s own persistence/cooldown state (this
        method never touches `_streak`/`_cooldown_left`).
        """
        result = self._reconstruction_error(asset, reading)
        if result is None:
            return None
        err, threshold, _trigger = result
        return err / threshold

    def reset_runtime(self) -> None:
        """Clear persistence/cooldown/window state, keep trained weights.

        For evaluating multiple independent HAI test files against one
        already-fitted model: without this, a streak or cooldown left over
        from the tail of one file would bleed into the start of the next
        unrelated file.
        """
        self._streak.clear()
        self._cooldown_left.clear()
        self._recent.clear()

    def _score(self, asset: str, reading: Reading) -> list[Anomaly]:
        result = self._reconstruction_error(asset, reading)
        if result is None:
            return []
        err, threshold, trigger = result

        if self._cooldown_left[asset] > 0:
            self._cooldown_left[asset] -= 1

        if err > threshold:
            self._streak[asset] += 1
        else:
            self._streak[asset] = 0

        if self._streak[asset] >= self.persistence and self._cooldown_left[asset] == 0:
            self._cooldown_left[asset] = self.cooldown
            self._streak[asset] = 0
            return [
                Anomaly(
                    asset=asset,
                    ts=reading.ts,
                    signal=trigger,
                    score=round(err / threshold, 2),  # reconstruction error, x over threshold
                    detector=self.name,
                    window=list(self._recent[asset]),
                )
            ]
        return []
