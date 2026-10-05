"""Tomorrow's Norwalk gas price, with a confidence score, graded the next day.

Every day the tracker:
  1. grades yesterday's prediction against today's actual cheapest Norwalk price
     (error in cents, inside the range or not, direction right or not, and a
     one-line "what went wrong");
  2. relearns from its graded history:
       bias  - the base model's average miss over the last 14 days (before any
               correction, shrunk while there are few days) is added to the next
               forecast, so a model that keeps guessing low corrects itself;
       range - the 80% range is the 10th-90th percentile of the last 30 errors,
               so it widens after misses and tightens when it is right;
  3. makes tomorrow's prediction:
       point      = today + (EIA week-ahead expected move)/7 + bias
       confidence = share of recent days the actual landed within ±2¢ of the
                    prediction (a prior of 60% until 10 days are graded).

Everything is stored in data/predictions.csv (committed by the workflow) and the
graded row is appended to the Google Sheet's "Predictions" tab.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

PRED_CSV = Path(__file__).parent / "data" / "predictions.csv"
FIELDS = ["made_on", "target", "today_price", "predicted", "low", "high", "confidence",
          "bias_c", "dir_pred", "actual", "error_c", "in_range", "dir_actual", "note"]
FLAT_C = 1.0            # moves under 1¢ count as "flat"
CLOSE_C = 2.0           # "within ±2¢" defines the confidence score
BIAS_WINDOW, RANGE_WINDOW, MIN_GRADED = 14, 30, 10
PRIOR_HALF_RANGE_C, PRIOR_CONFIDENCE = 3.0, 0.60


def load() -> list[dict]:
    if not PRED_CSV.exists():
        return []
    with PRED_CSV.open() as f:
        return list(csv.DictReader(f))


def _save(rows: list[dict]) -> None:
    PRED_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PRED_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _direction(change_c: float) -> str:
    return "flat" if abs(change_c) < FLAT_C else ("up" if change_c > 0 else "down")


def grade(rows: list[dict], today: date, actual: float) -> dict | None:
    """Fill in the actual for the prediction that targeted today (once). Returns the graded row."""
    for r in rows:
        if r["target"] == today.isoformat() and not r["actual"]:
            err = (actual - float(r["predicted"])) * 100
            in_range = float(r["low"]) <= actual <= float(r["high"])
            dir_actual = _direction((actual - float(r["today_price"])) * 100)
            if in_range:
                note = f"Good: off by {err:+.1f}¢, inside the range"
            elif err > 0:
                note = f"Missed high: price came in {err:.1f}¢ above the forecast (rose more / fell less than expected)"
            else:
                note = f"Missed low: price came in {-err:.1f}¢ below the forecast (fell more / rose less than expected)"
            if dir_actual != r["dir_pred"]:
                note += f"; called {r['dir_pred']}, was {dir_actual}"
            r.update(actual=f"{actual:.3f}", error_c=f"{err:.2f}", in_range=str(in_range), dir_actual=dir_actual, note=note)
            return r
    return None


@dataclass
class Learned:
    bias_c: float
    lo_c: float
    hi_c: float
    confidence: float
    graded: int
    mae_c: float | None
    in_range_rate: float | None
    direction_rate: float | None


def _bias(done: list[dict]) -> float:
    """Average miss of the uncorrected model (error + the correction that was applied), shrunk toward 0."""
    raw = [float(r["error_c"]) + float(r.get("bias_c") or 0) for r in done[-BIAS_WINDOW:]]
    return float(np.mean(raw)) * len(raw) / (len(raw) + 4) if raw else 0.0


def learn(rows: list[dict]) -> Learned:
    done = [r for r in rows if r["actual"]]
    errs = np.array([float(r["error_c"]) for r in done])
    bias = _bias(done)
    if len(errs) < MIN_GRADED:
        return Learned(bias, -PRIOR_HALF_RANGE_C, PRIOR_HALF_RANGE_C, PRIOR_CONFIDENCE, len(errs),
                       float(np.abs(errs).mean()) if len(errs) else None, None, None)
    recent = errs[-RANGE_WINDOW:]
    centred = recent - float(np.mean(errs[-BIAS_WINDOW:]))      # spread around the remaining miss
    lo, hi = np.quantile(centred, [0.1, 0.9])
    lo, hi = min(lo, -1.0), max(hi, 1.0)                    # never claim sub-cent precision
    last = done[-RANGE_WINDOW:]
    return Learned(
        bias_c=bias, lo_c=float(lo), hi_c=float(hi),
        confidence=float(np.mean(np.abs(centred) <= CLOSE_C)),
        graded=len(errs), mae_c=float(np.abs(recent).mean()),
        in_range_rate=float(np.mean([r["in_range"] == "True" for r in last])),
        direction_rate=float(np.mean([r["dir_pred"] == r["dir_actual"] for r in last])),
    )


def predict(rows: list[dict], today: date, today_price: float, weekly_expected_c: float) -> dict:
    """Add (or replace) today's prediction for tomorrow."""
    lr = learn(rows)
    drift_c = weekly_expected_c / 7
    point = today_price + (drift_c + lr.bias_c) / 100
    row = {
        "made_on": today.isoformat(), "target": (today + timedelta(days=1)).isoformat(),
        "today_price": f"{today_price:.3f}", "predicted": f"{point:.3f}",
        "low": f"{point + lr.lo_c / 100:.3f}", "high": f"{point + lr.hi_c / 100:.3f}",
        "confidence": f"{lr.confidence:.2f}", "bias_c": f"{lr.bias_c:.2f}", "dir_pred": _direction(drift_c + lr.bias_c),
        "actual": "", "error_c": "", "in_range": "", "dir_actual": "", "note": "",
    }
    rows[:] = [r for r in rows if r["made_on"] != row["made_on"]] + [row]
    return row


def run_daily(today: date, today_price: float | None, weekly_expected_c: float | None) -> tuple[dict | None, dict | None, Learned]:
    """Grade today's target, make tomorrow's prediction (first run of the day only), persist."""
    rows = load()
    graded = grade(rows, today, today_price) if today_price is not None else None
    made = next((r for r in rows if r["made_on"] == today.isoformat()), None)
    if made is None and today_price is not None and weekly_expected_c is not None:
        made = predict(rows, today, today_price, weekly_expected_c)
    _save(rows)
    return graded, made, learn(rows)


def summary_lines(graded: dict | None, made: dict | None, lr: Learned) -> list[str]:
    lines = []
    if graded:
        lines.append(f"Yesterday's call: ${float(graded['predicted']):.3f} → actual ${float(graded['actual']):.3f}. "
                     f"{graded['note']}.")
    if made:
        lines.append(f"Tomorrow (Norwalk cheapest): ${float(made['predicted']):.3f}, 80% range "
                     f"${float(made['low']):.3f}–${float(made['high']):.3f}, "
                     f"{float(made['confidence']):.0%} confident it lands within ±2¢ ({made['dir_pred']}).")
    if lr.graded >= MIN_GRADED:
        lines.append(f"Last {min(lr.graded, RANGE_WINDOW)} days: average miss {lr.mae_c:.1f}¢, inside range "
                     f"{lr.in_range_rate:.0%}, direction right {lr.direction_rate:.0%}. "
                     f"Self-correction now {lr.bias_c:+.1f}¢.")
    else:
        lines.append(f"Learning: {lr.graded}/{MIN_GRADED} days graded so far; confidence uses a 60% prior until then.")
    return lines
