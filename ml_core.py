"""Small, auditable ML core shared by the fuel and FX outlooks.

Everything is numpy: an L2-regularised logistic regression (Newton/IRLS), a ridge
regression, an expanding-window walk-forward evaluator and a reliability table.
Nothing here ever sees the future: each block of predictions is made by a model
fitted only on rows before it, with standardisation statistics from those rows.

The point of the walk-forward numbers is the email copy. A probability is only
printed with the model's own out-of-sample record next to it, and a model that
does not beat the base rate is reported as having no edge rather than dressed up.

Matrix products are written as elementwise sums on purpose: macOS Accelerate's
BLAS raises spurious floating-point warnings from `@` (see CLAUDE.md landmines).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _dot(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    return (X * w).sum(axis=1)


def _gram(X: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return np.einsum("ni,nj,n->ij", X, X, weights)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def _with_bias(X: np.ndarray) -> np.ndarray:
    return np.concatenate([np.ones((len(X), 1)), X], axis=1)


def fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 60) -> np.ndarray:
    """Weights (bias first) of an L2 logistic regression; the bias is not penalised."""
    X1 = _with_bias(X)
    w = np.zeros(X1.shape[1])
    penalty = np.full(len(w), float(l2))
    penalty[0] = 0.0
    for _ in range(iters):
        p = _sigmoid(_dot(X1, w))
        grad = (X1 * (p - y)[:, None]).sum(axis=0) + penalty * w
        hess = _gram(X1, p * (1 - p)) + np.diag(penalty) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def predict_logit(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return _sigmoid(_dot(_with_bias(X), w))


def fit_ridge(X: np.ndarray, y: np.ndarray, l2: float = 1.0) -> np.ndarray:
    X1 = _with_bias(X)
    penalty = np.full(X1.shape[1], float(l2))
    penalty[0] = 0.0
    return np.linalg.solve(_gram(X1, np.ones(len(X1))) + np.diag(penalty), (X1 * y[:, None]).sum(axis=0))


def predict_ridge(b: np.ndarray, X: np.ndarray) -> np.ndarray:
    return _dot(_with_bias(X), b)


@dataclass
class Scaler:
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, X: np.ndarray) -> "Scaler":
        return cls(X.mean(axis=0), X.std(axis=0) + 1e-12)

    def __call__(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.std


@dataclass
class WalkForward:
    """Out-of-sample record of a classifier, plus the final model for today's call."""

    prob: np.ndarray                 # OOS P(y=1) per row, NaN before `start`
    y: np.ndarray
    n: int
    hit_rate: float                  # share of OOS calls (p>0.5) that were right
    base_hit_rate: float             # best constant guess over the same rows
    brier: float
    brier_base: float                # climatology: expanding mean of y known at the time
    skill: float                     # 1 - brier/brier_base; > 0 means better than base rate
    reliability: list[tuple[float, float, float, int]] = field(default_factory=list)  # (lo, hi, hit, n)
    weights: np.ndarray | None = None
    scaler: Scaler | None = None

    @property
    def has_edge(self) -> bool:
        return self.skill > 0.02 and self.hit_rate > self.base_hit_rate

    def track_record(self, p: float) -> tuple[float, int] | None:
        """How often the model was right when it last said something like `p`."""
        for lo, hi, hit, n in self.reliability:
            if lo <= p < hi or (hi == 1.0 and p == 1.0):
                return (hit, n) if n >= 15 else None
        return None


RELIABILITY_EDGES = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0)


def walk_forward(X: np.ndarray, y: np.ndarray, start: int, step: int = 4, l2: float = 1.0,
                 sample: np.ndarray | None = None) -> WalkForward:
    """Expanding-window evaluation; refits every `step` rows.

    `sample` optionally restricts scoring to a subset of rows (e.g. non-overlapping
    horizons) so overlapping targets do not inflate the record.
    """
    prob = np.full(len(y), np.nan)
    for t in range(start, len(y), step):
        scaler = Scaler.fit(X[:t])
        w = fit_logit(scaler(X[:t]), y[:t], l2)
        prob[t:t + step] = predict_logit(w, scaler(X[t:t + step]))

    rows = np.arange(start, len(y)) if sample is None else np.asarray([i for i in sample if i >= start])
    p, yy = prob[rows], y[rows]
    clim = np.array([y[:i].mean() for i in rows])
    brier, brier_base = float(np.mean((p - yy) ** 2)), float(np.mean((clim - yy) ** 2))
    calls = p > 0.5
    reliability = []
    for lo, hi in zip(RELIABILITY_EDGES[:-1], RELIABILITY_EDGES[1:]):
        m = (p >= lo) & ((p < hi) | (hi == 1.0))
        if m.any():
            # "right" means the side the model leaned to happened
            right = np.where(p[m] > 0.5, yy[m], 1 - yy[m])
            reliability.append((lo, hi, float(right.mean()), int(m.sum())))

    scaler = Scaler.fit(X)
    return WalkForward(
        prob=prob, y=y, n=len(rows),
        hit_rate=float(np.mean(calls == (yy == 1))),
        base_hit_rate=float(max(yy.mean(), 1 - yy.mean())),
        brier=brier, brier_base=brier_base,
        skill=1 - brier / brier_base if brier_base else 0.0,
        reliability=reliability,
        weights=fit_logit(scaler(X), y, l2), scaler=scaler,
    )


def confidence_label(p: float, wf: WalkForward | None) -> str:
    """'High' / 'Medium' / 'Low' from how far p is from a coin flip and whether the model has an edge."""
    if wf is None or not wf.has_edge:
        return "Low"
    lean = abs(p - 0.5)
    record = wf.track_record(p)
    if lean >= 0.25 and (record is None or record[0] >= 0.75):
        return "High"
    if lean >= 0.12:
        return "Medium"
    return "Low"
