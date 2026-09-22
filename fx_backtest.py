"""Walk-forward validation of the send/hold rule against just sending on payday.

A model that cannot beat "send on the 15th and forget it" is not worth acting
on, so the engine is scored honestly here. Two rules are enforced:

  * No lookahead. On each simulated day the model refits using only closes up
    to and including that day, exactly as it would in production.
  * A hard deadline. If the rule never fires, the money goes out on the last
    day of the window, including at a worse rate. Strategies that quietly wait
    forever are not strategies.

The headline number is INR gained or lost per month versus payday-sending, on
the user's actual transfer size. Capture ratio says where in the window's range
the rule landed: 100% is the best rate that was available, 0% the worst.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from fx_signals import TRADING_DAYS, fit_trend_model, solve_reservation_rate


@dataclass
class MonthResult:
    month: str
    send_date: str
    strategy_rate: float
    naive_rate: float
    best_rate: float
    worst_rate: float
    days_waited: int
    fired_early: bool

    @property
    def gain_pct(self) -> float:
        return 100.0 * (self.strategy_rate / self.naive_rate - 1.0)

    @property
    def capture_ratio(self) -> float:
        span = self.best_rate - self.worst_rate
        if span <= 0:
            return 100.0
        return 100.0 * (self.strategy_rate - self.worst_rate) / span


@dataclass
class BacktestResult:
    months: list[MonthResult] = field(default_factory=list)
    send_amount: float = 3000.0
    rule: str = "percentile"

    @property
    def n(self) -> int:
        return len(self.months)

    @property
    def mean_gain_pct(self) -> float:
        return sum(m.gain_pct for m in self.months) / self.n if self.n else 0.0

    @property
    def total_inr_gain(self) -> float:
        return sum(self.send_amount * (m.strategy_rate - m.naive_rate) for m in self.months)

    @property
    def inr_gain_per_month(self) -> float:
        return self.total_inr_gain / self.n if self.n else 0.0

    @property
    def wins(self) -> int:
        return sum(1 for m in self.months if m.strategy_rate > m.naive_rate)

    @property
    def losses(self) -> int:
        return sum(1 for m in self.months if m.strategy_rate < m.naive_rate)

    @property
    def mean_capture_ratio(self) -> float:
        return sum(m.capture_ratio for m in self.months) / self.n if self.n else 0.0

    @property
    def naive_capture_ratio(self) -> float:
        """Where payday-sending lands in the window, for a fair comparison."""
        if not self.n:
            return 0.0
        total = 0.0
        for m in self.months:
            span = m.best_rate - m.worst_rate
            total += 100.0 if span <= 0 else 100.0 * (m.naive_rate - m.worst_rate) / span
        return total / self.n

    @property
    def worst_month(self) -> MonthResult | None:
        return min(self.months, key=lambda m: m.gain_pct) if self.months else None

    @property
    def best_month(self) -> MonthResult | None:
        return max(self.months, key=lambda m: m.gain_pct) if self.months else None

    def summary_lines(self) -> list[str]:
        if not self.n:
            return ["Not enough history to backtest."]
        label = "percentile" if self.rule == "percentile" else "model-reservation"
        lines = [
            f"{self.n} monthly windows tested walk-forward, no lookahead ({label} rule).",
            f"Average vs sending on payday: {self.mean_gain_pct:+.3f}% "
            f"({self.inr_gain_per_month:+,.0f} INR per month on ${self.send_amount:,.0f}).",
            f"Beat payday-sending in {self.wins}/{self.n} months, lost in {self.losses}.",
            f"Captured {self.mean_capture_ratio:.0f}% of each window's range "
            f"versus {self.naive_capture_ratio:.0f}% for payday-sending.",
        ]
        if self.worst_month:
            lines.append(
                f"Worst month {self.worst_month.month}: {self.worst_month.gain_pct:+.2f}% "
                f"({self.send_amount * (self.worst_month.strategy_rate - self.worst_month.naive_rate):+,.0f} INR)."
            )
        return lines


def _first_index_on_or_after(dates: list[str], target: date) -> int | None:
    for index, raw in enumerate(dates):
        if datetime.strptime(raw, "%Y-%m-%d").date() >= target:
            return index
    return None


def _percentile_rank(closes: list[float], index: int, window: int = TRADING_DAYS) -> float:
    """Where closes[index] sits in its own trailing window, 0-100."""
    history = np.asarray(closes[max(0, index - window + 1) : index + 1])
    return float(100.0 * (history < closes[index]).sum() / len(history))


def run_backtest(
    dates: list[str],
    closes: list[float],
    payday: int = 15,
    flex_days: int = 14,
    risk_aversion: float = 3.0,
    min_history: int = 300,
    send_amount: float = 3000.0,
    rule: str = "percentile",
    good_level_percentile: float = 85.0,
) -> BacktestResult:
    """Replay every complete monthly window that has enough prior history.

    `rule` selects what is being scored: "percentile" is the rule the notifier
    actually deploys, "model" is the optimal-stopping reservation rule, kept so
    the two can be compared honestly rather than asserted.
    """
    result = BacktestResult(send_amount=send_amount, rule=rule)
    if len(closes) <= min_history + 5:
        return result

    parsed = [datetime.strptime(raw, "%Y-%m-%d").date() for raw in dates]
    months = sorted({(d.year, d.month) for d in parsed})

    for year, month in months:
        try:
            payday_date = date(year, month, payday)
        except ValueError:
            continue

        start = _first_index_on_or_after(dates, payday_date)
        if start is None or start < min_history:
            continue

        deadline_date = payday_date + timedelta(days=flex_days)
        window = [i for i in range(start, len(dates)) if parsed[i] <= deadline_date]
        if len(window) < 3:
            continue  # incomplete or still-open window
        if parsed[window[-1]] < deadline_date and window[-1] == len(dates) - 1:
            continue  # window runs past the end of available data

        last = window[-1]
        chosen_index = last
        fired_early = False

        for index in window:
            days_left = last - index
            if days_left <= 0:
                break
            if rule == "percentile":
                fires = _percentile_rank(closes, index) >= good_level_percentile
            else:
                try:
                    # Refit on history available at that moment only.
                    model = fit_trend_model(closes[: index + 1])
                except ValueError:
                    continue
                fires = closes[index] >= solve_reservation_rate(model, days_left, risk_aversion)
            if fires:
                chosen_index = index
                fired_early = True
                break

        window_rates = [closes[i] for i in window]
        result.months.append(
            MonthResult(
                month=f"{year}-{month:02d}",
                send_date=dates[chosen_index],
                strategy_rate=closes[chosen_index],
                naive_rate=closes[window[0]],
                best_rate=max(window_rates),
                worst_rate=min(window_rates),
                days_waited=(parsed[chosen_index] - parsed[window[0]]).days,
                fired_early=fired_early,
            )
        )

    return result
