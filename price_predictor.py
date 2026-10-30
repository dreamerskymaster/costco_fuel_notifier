"""Tomorrow's Norwalk gas price, with a confidence score, graded the next day.

Every day the tracker:
  1. grades yesterday's prediction against today's actual cheapest Norwalk price
     (error in cents, inside the range or not, direction right or not, and a
     one-line "what went wrong");
  2. relearns from its graded history (the last 30 day-over-day moves);
  3. makes tomorrow's prediction:
       point      = today's price. The cheapest Norwalk price moves in steps: it sat
                    still on 62% of days (Oct 5-26, 2026) and otherwise jumped 6-10¢.
                    Walk-forward over 21 graded days, "same as today" beat the old
                    today + EIA-weekly/7 + bias model: average miss 3.05¢ vs 3.60¢,
                    direction right 52% vs 33%. Any share of the EIA trend made it worse.
       direction  = "flat", the most common day.
       range      = ± the 90th percentile of recent |daily moves| (at least 2¢), so it
                    covers the typical jump; old error quantiles covered 57%, this ~75%.
       confidence = share of recent days that moved less than 2¢.
     Re-run the backtest once a few months of history exist; with more data a
     direction call (e.g. from the EIA weekly model) may start to pay.

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
RANGE_WINDOW, MIN_GRADED = 30, 10
RANGE_QUANTILE, MIN_HALF_RANGE_C = 0.90, 2.0
PRIOR_HALF_RANGE_C, PRIOR_CONFIDENCE = 6.0, 0.60    # until 10 days are graded (Oct 2026 jumps were 6-10¢)


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


def learn(rows: list[dict]) -> Learned:
    done = [r for r in rows if r["actual"]]
    errs = np.array([float(r["error_c"]) for r in done])
    if len(errs) < MIN_GRADED:
        return Learned(0.0, -PRIOR_HALF_RANGE_C, PRIOR_HALF_RANGE_C, PRIOR_CONFIDENCE, len(errs),
                       float(np.abs(errs).mean()) if len(errs) else None, None, None)
    last = done[-RANGE_WINDOW:]
    moves = np.array([(float(r["actual"]) - float(r["today_price"])) * 100 for r in last])
    half = max(float(np.quantile(np.abs(moves), RANGE_QUANTILE)), MIN_HALF_RANGE_C)
    return Learned(
        bias_c=0.0, lo_c=-half, hi_c=half,
        confidence=float(np.mean(np.abs(moves) < CLOSE_C)),
        graded=len(errs), mae_c=float(np.abs(errs[-RANGE_WINDOW:]).mean()),
        in_range_rate=float(np.mean([r["in_range"] == "True" for r in last])),
        direction_rate=float(np.mean([r["dir_pred"] == r["dir_actual"] for r in last])),
    )


def predict(rows: list[dict], today: date, today_price: float, weekly_expected_c: float | None = None) -> dict:
    """Add (or replace) today's prediction for tomorrow. The EIA weekly move is not used (see the docstring)."""
    lr = learn(rows)
    point = today_price
    row = {
        "made_on": today.isoformat(), "target": (today + timedelta(days=1)).isoformat(),
        "today_price": f"{today_price:.3f}", "predicted": f"{point:.3f}",
        "low": f"{point + lr.lo_c / 100:.3f}", "high": f"{point + lr.hi_c / 100:.3f}",
        "confidence": f"{lr.confidence:.2f}", "bias_c": "0.00", "dir_pred": "flat",
        "actual": "", "error_c": "", "in_range": "", "dir_actual": "", "note": "",
    }
    rows[:] = [r for r in rows if r["made_on"] != row["made_on"]] + [row]
    return row


def run_daily(today: date, today_price: float | None, weekly_expected_c: float | None) -> tuple[dict | None, dict | None, Learned]:
    """Grade today's target, make tomorrow's prediction (first run of the day only), persist."""
    rows = load()
    graded = grade(rows, today, today_price) if today_price is not None else None
    made = next((r for r in rows if r["made_on"] == today.isoformat()), None)
    if made is None and today_price is not None:
        made = predict(rows, today, today_price, weekly_expected_c)
    _save(rows)
    return graded, made, learn(rows)


def summary_lines(graded: dict | None, made: dict | None, lr: Learned) -> list[str]:
    lines = []
    if graded:
        lines.append(f"Yesterday's call: ${float(graded['predicted']):.3f} → actual ${float(graded['actual']):.3f}. "
                     f"{graded['note']}.")
    if made:
        lines.append(f"Tomorrow (Norwalk cheapest): ${float(made['predicted']):.3f}, likely range "
                     f"${float(made['low']):.3f}–${float(made['high']):.3f}, "
                     f"{float(made['confidence']):.0%} chance it stays within 2¢ (recent days that did).")
    if lr.graded >= MIN_GRADED:
        lines.append(f"Last {min(lr.graded, RANGE_WINDOW)} days: average miss {lr.mae_c:.1f}¢, inside range "
                     f"{lr.in_range_rate:.0%}, direction right {lr.direction_rate:.0%}. "
                     f"Range now ±{lr.hi_c:.0f}¢, sized from recent daily jumps.")
    else:
        lines.append(f"Learning: {lr.graded}/{MIN_GRADED} days graded so far; confidence uses a 60% prior until then.")
    return lines
