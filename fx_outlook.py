"""Will USD/INR go up or down? A direction-and-range outlook with an honest record.

A logistic model on the pair's own momentum, stretch versus its 60-day mean and
recent volatility estimates P(USD/INR is higher in ~2 weeks and ~1 month). It is
evaluated walk-forward on non-overlapping windows, and the email prints that
record beside the probability.

What the record says (ECB data 2005-2026, scored from 2012): direction is close to
unforecastable. No horizon from a week to three months beat "the rupee usually
weakens" by a meaningful margin, so confidence is reported as Low and the
probability stays near the base rate. That agrees with the remittance backtest in
CLAUDE.md (timing is worth little; provider choice is worth ~8x more).

What does work is the range: the 80% band built from the last three years of
same-length moves contained the outcome ~81% of the time at 2 weeks and ~80% at a
month. So the email leads with the range and the base rate, and only calls a
direction when the model has earned it.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass

import numpy as np

import ml_core

HORIZONS = {"2 weeks": 10, "1 month": 22}   # trading days
RANGE_LOOKBACK = 750                        # ~3 years of daily closes
EVAL_FROM_ROW = 500                         # first scored row, after two years of training


def _features(L: np.ndarray, t: int) -> list[float]:
    r = np.diff(L[t - 60:t + 1])
    return [L[t] - L[t - 5], L[t] - L[t - 22], L[t] - L[t - 66],
            (L[t] - L[t - 60:t + 1].mean()) / (r.std() * np.sqrt(60) + 1e-12),
            r[-20:].std(), L[t] - L[t - 130:t + 1].max()]


@dataclass
class HorizonView:
    label: str
    days: int
    p_up: float
    low: float                 # 10th percentile rate
    high: float                # 90th percentile rate
    base_rate: float           # share of past windows in which USD/INR rose
    confidence: str
    wf: ml_core.WalkForward


@dataclass
class FxOutlook:
    spot: float
    views: list[HorizonView]


def forecast(closes: list[float]) -> FxOutlook | None:
    """Outlook from a daily close series (oldest first). None if there is too little history."""
    L = np.log(np.asarray(closes, float))
    if len(L) < 130 + EVAL_FROM_ROW + 60:
        return None
    views = []
    for label, H in HORIZONS.items():
        rows = range(130, len(L) - H)
        X = np.array([_features(L, t) for t in rows])
        y = np.array([float(L[t + H] > L[t]) for t in rows])
        start = EVAL_FROM_ROW
        wf = ml_core.walk_forward(X, y, start, step=H, l2=5.0, sample=np.arange(start, len(y), H))
        p_model = float(ml_core.predict_logit(wf.weights, wf.scaler(np.array([_features(L, len(L) - 1)])))[0])
        # Without an edge, report the base rate rather than the model's noise.
        base = float(y[-RANGE_LOOKBACK:].mean())
        p_up = p_model if wf.has_edge else base
        moves = L[H:][-RANGE_LOOKBACK:] - L[:-H][-RANGE_LOOKBACK:]
        lo, hi = np.quantile(moves, [0.1, 0.9])
        views.append(HorizonView(label, H, p_up, float(np.exp(L[-1] + lo)), float(np.exp(L[-1] + hi)),
                                 base, ml_core.confidence_label(p_up, wf), wf))
    return FxOutlook(float(np.exp(L[-1])), views)


def outlook_lines(o: FxOutlook, amount_usd: float, direction: str = "usd_to_inr") -> list[str]:
    """Email lines. `direction` frames the advice: who gains when USD/INR rises."""
    lines = []
    for v in o.views:
        lean = "higher" if v.p_up >= 0.5 else "lower"
        p = v.p_up if v.p_up >= 0.5 else 1 - v.p_up
        spread_inr = amount_usd * (v.high - v.low) / 2
        lines.append(
            f"In {v.label}: {p:.0%} chance USD/INR is {lean} than {o.spot:.2f}; 80% range "
            f"{v.low:.2f}–{v.high:.2f} (±₹{spread_inr:,.0f} on ${amount_usd:,.0f}). Confidence: {v.confidence}."
        )
    v = o.views[0]
    if not v.wf.has_edge:
        if direction == "usd_to_inr":
            gist = ("The only dependable pattern is the rupee's slow slide, which slightly favours waiting, "
                    "but the range above dwarfs it. Pick the best provider and send on schedule.")
        else:
            gist = ("The rupee's slow slide means dollars tend to get dearer, not cheaper, so waiting "
                    "for a better rate usually costs money. Send on day one.")
        lines.append(gist)
    lines.append(
        f"Model record (non-overlapping {v.label} windows): right {v.wf.hit_rate:.0%} of {v.wf.n} vs "
        f"{v.wf.base_hit_rate:.0%} for always guessing the usual direction — "
        + ("a real edge." if v.wf.has_edge else "no edge, so the probability shown is the base rate.")
    )
    return lines


def outlook_html(o: FxOutlook, amount_usd: float, direction: str = "usd_to_inr", muted: str = "#64748b") -> str:
    lines = outlook_lines(o, amount_usd, direction)
    body = "".join(f"<div style='margin:0 0 6px 0;'>{html_lib.escape(line)}</div>" for line in lines[:-1])
    return (f"{body}<div style='margin-top:6px;font-size:11px;color:{muted};'>{html_lib.escape(lines[-1])} "
            f"Ranges come from the last three years of same-length moves and held ~80% of outcomes in testing.</div>")
