"""Timing for the reverse direction: INR -> USD, where a *low* USD/INR is good.

The USD->INR pipeline learned that timing is nearly worthless at remittance
size. This module re-asks the question the other way round, for a monthly
dollar amount bought with rupees, so the INR->USD brief can print its own
track record instead of borrowing a conclusion measured on the opposite side.

The rule is deliberately simple and has no lookahead: inside the send window,
buy on the first day the rate sits in the cheapest `threshold` percent of the
trailing `lookback` closes; if that never happens, buy on the deadline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np


@dataclass
class OutboundMonth:
    month: str
    send_date: str
    strategy_rate: float
    naive_rate: float
    best_rate: float     # lowest USD/INR in the window: fewest rupees per dollar
    worst_rate: float
    fired_early: bool

    def inr_saved(self, usd: float) -> float:
        """Rupees saved versus buying on payday. Positive means the rule helped."""
        return usd * (self.naive_rate - self.strategy_rate)


@dataclass
class OutboundBacktest:
    months: list[OutboundMonth] = field(default_factory=list)
    usd_amount: float = 1000.0
    threshold: float = 15.0

    @property
    def n(self) -> int:
        return len(self.months)

    @property
    def saved_per_month(self) -> float:
        return sum(m.inr_saved(self.usd_amount) for m in self.months) / self.n if self.n else 0.0

    @property
    def hindsight_per_month(self) -> float:
        """The ceiling: buying at each window's lowest rate, knowable only afterwards."""
        if not self.n:
            return 0.0
        return sum(self.usd_amount * (m.naive_rate - m.best_rate) for m in self.months) / self.n

    @property
    def wins(self) -> int:
        return sum(1 for m in self.months if m.strategy_rate < m.naive_rate)

    @property
    def losses(self) -> int:
        return sum(1 for m in self.months if m.strategy_rate > m.naive_rate)

    @property
    def t_stat(self) -> float | None:
        """Is the average saving distinguishable from zero? |t| < 2 means no."""
        if self.n < 3:
            return None
        savings = np.array([m.inr_saved(self.usd_amount) for m in self.months])
        spread = savings.std(ddof=1)
        return None if spread == 0 else float(savings.mean() / (spread / np.sqrt(self.n)))


def trailing_percentile(closes: list[float], index: int, lookback: int) -> float:
    """Share of the trailing window priced *above* today. 100 = cheapest dollar seen."""
    start = max(0, index - lookback + 1)
    window = np.asarray(closes[start : index + 1], dtype=float)
    return float(100.0 * (window > closes[index]).sum() / len(window))


def cheapness_percentile(closes: list[float], lookback: int = 90) -> float:
    """Today's cheapness in the trailing window; high means the dollar is cheap."""
    return trailing_percentile(closes, len(closes) - 1, lookback)


def run_outbound_backtest(
    dates: list[str],
    closes: list[float],
    payday: int,
    flex_days: int,
    usd_amount: float,
    threshold: float = 15.0,
    lookback: int = 90,
) -> OutboundBacktest:
    """Walk forward month by month over every complete window in the history.

    `threshold` is the cheapest-percent band: 15 means "fire when today is
    cheaper than 85% of the trailing window".
    """
    result = OutboundBacktest(usd_amount=usd_amount, threshold=threshold)
    if len(dates) < lookback + 30:
        return result

    parsed = [date.fromisoformat(d) for d in dates]
    first, last = parsed[lookback], parsed[-1]
    cursor = date(first.year, first.month, 1)
    while True:
        start = cursor.replace(day=payday)
        deadline = start + timedelta(days=flex_days)
        if deadline > last:
            break
        idx = [i for i, d in enumerate(parsed) if start <= d <= deadline and i >= lookback]
        if idx:
            window = [closes[i] for i in idx]
            chosen = idx[-1]
            fired = False
            for i in idx:
                if trailing_percentile(closes, i, lookback) >= 100.0 - threshold:
                    chosen, fired = i, True
                    break
            result.months.append(
                OutboundMonth(
                    month=start.strftime("%Y-%m"),
                    send_date=dates[chosen],
                    strategy_rate=closes[chosen],
                    naive_rate=window[0],
                    best_rate=min(window),
                    worst_rate=max(window),
                    fired_early=fired and chosen != idx[-1],
                )
            )
        month = cursor.month + 1
        cursor = date(cursor.year + (month > 12), 1 if month > 12 else month, 1)
    return result
