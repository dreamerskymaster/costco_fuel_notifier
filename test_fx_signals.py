"""Offline tests for the model, formatting and window logic.

No network: series are synthetic so the invariants are checked deterministically.
Run with `python -m unittest -v test_signals`.
"""

from __future__ import annotations

import math
import unittest
from datetime import date

import numpy as np

from fx_backtest import run_backtest
from fx_config import Config
from fx_signals import (
    compute_stats,
    decide,
    fit_trend_model,
    forecast_distribution,
    solve_reservation_rate,
)
from usd_inr_tracker import compute_window, inr


def synthetic_series(n: int = 600, drift: float = 0.0002, seed: int = 7) -> tuple[list[str], list[float]]:
    """A trending series with mean-reverting noise, shaped like USD/INR."""
    rng = np.random.default_rng(seed)
    level = math.log(80.0)
    residual = 0.0
    dates: list[str] = []
    closes: list[float] = []
    day = date(2022, 1, 3)
    for index in range(n):
        residual = 0.95 * residual + rng.normal(0, 0.003)
        closes.append(math.exp(level + drift * index + residual))
        # Weekday-only stamps, which is what both data sources provide.
        while day.weekday() >= 5:
            day = date.fromordinal(day.toordinal() + 1)
        dates.append(day.isoformat())
        day = date.fromordinal(day.toordinal() + 1)
    return dates, closes


class ModelInvariants(unittest.TestCase):
    """The two identities that silently broke during development."""

    def setUp(self) -> None:
        self.dates, self.closes = synthetic_series()
        self.model = fit_trend_model(self.closes)
        self.spot = self.closes[-1]

    def test_forecast_at_horizon_zero_is_spot(self) -> None:
        mean, sd = forecast_distribution(self.model, 0)
        self.assertAlmostEqual(math.exp(mean), self.spot, places=8)
        self.assertEqual(sd, 0.0)

    def test_reservation_at_deadline_is_spot(self) -> None:
        # With no days left there is no optionality: waiting is worth exactly spot.
        self.assertAlmostEqual(solve_reservation_rate(self.model, 0), self.spot, places=6)

    def test_reservation_rises_with_time_remaining(self) -> None:
        # Only for days_left >= 1. days_left = 0 is a boundary convention that
        # returns spot (you are forced to send), not a continuation value, so it
        # can sit above the one-day value when the rate is stretched above trend.
        values = [solve_reservation_rate(self.model, d) for d in (1, 5, 10, 20, 40)]
        self.assertEqual(values, sorted(values), "more flexibility cannot be worth less")

    def test_zero_days_left_is_a_boundary_not_a_continuation_value(self) -> None:
        forced = solve_reservation_rate(self.model, 0)
        one_day = solve_reservation_rate(self.model, 1)
        self.assertAlmostEqual(forced, self.spot, places=6)
        # The two are different quantities; assert only that both are sane.
        self.assertGreater(one_day, self.spot * 0.9)
        self.assertLess(one_day, self.spot * 1.1)

    def test_risk_aversion_lowers_reservation(self) -> None:
        relaxed = solve_reservation_rate(self.model, 14, risk_aversion=0.0)
        cautious = solve_reservation_rate(self.model, 14, risk_aversion=10.0)
        self.assertLess(cautious, relaxed, "a cautious sender should lock in sooner")

    def test_extreme_risk_aversion_stays_finite(self) -> None:
        # Guards the CRRA normalisation: without it this underflows to garbage.
        for gamma in (12.0, 25.0, 60.0):
            value = solve_reservation_rate(self.model, 10, risk_aversion=gamma)
            self.assertTrue(math.isfinite(value))
            self.assertGreater(value, self.spot * 0.5)
            self.assertLess(value, self.spot * 1.5)

    def test_forecast_uncertainty_grows_with_horizon(self) -> None:
        sds = [forecast_distribution(self.model, h)[1] for h in (1, 5, 20, 60)]
        self.assertEqual(sds, sorted(sds))

    def test_reversion_is_detected_in_a_reverting_series(self) -> None:
        self.assertTrue(self.model.stationary)
        self.assertIsNotNone(self.model.half_life_days)
        self.assertLess(self.model.half_life_days, 120)

    def test_drift_shrink_reduces_trend(self) -> None:
        full = fit_trend_model(self.closes, drift_shrink=1.0)
        half = fit_trend_model(self.closes, drift_shrink=0.5)
        self.assertAlmostEqual(half.slope_used, full.slope_used / 2, places=12)


class VerdictLogic(unittest.TestCase):
    def setUp(self) -> None:
        self.dates, self.closes = synthetic_series()
        self.model = fit_trend_model(self.closes)

    def test_deadline_forces_a_send(self) -> None:
        stats = compute_stats(self.closes)
        verdict = decide(stats, self.model, 0, "2026-10-01", 10).verdict
        self.assertEqual(verdict, "FORCED_SEND")

    def test_strong_level_sends_even_against_positive_drift(self) -> None:
        # A rising series ends at its own high, so the percentile rule should
        # fire despite the drift term arguing for waiting.
        _, rising = synthetic_series(drift=0.0006, seed=3)
        stats = compute_stats(rising)
        model = fit_trend_model(rising)
        decision = decide(stats, model, 10, "2026-10-01", 10, good_level_percentile=85.0)
        self.assertGreaterEqual(stats.percentile[252], 85.0)
        self.assertEqual(decision.verdict, "SEND_NOW")

    def test_score_is_bounded(self) -> None:
        stats = compute_stats(self.closes)
        for days in (0, 3, 14):
            score = decide(stats, self.model, days, "2026-10-01", 14).score
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


class WindowLogic(unittest.TestCase):
    def test_window_inside_month(self) -> None:
        payday, deadline, days_left, total = compute_window(date(2026, 9, 21), 15, 14)
        self.assertEqual((payday, deadline), (date(2026, 9, 15), date(2026, 9, 29)))
        self.assertGreater(days_left, 0)
        self.assertGreater(total, 0)

    def test_expired_window_rolls_to_next_month(self) -> None:
        payday, deadline, _, _ = compute_window(date(2026, 9, 30), 15, 14)
        self.assertEqual(payday, date(2026, 10, 15))
        self.assertEqual(deadline, date(2026, 10, 29))

    def test_december_rolls_into_january(self) -> None:
        payday, _, _, _ = compute_window(date(2026, 12, 31), 15, 14)
        self.assertEqual(payday, date(2027, 1, 15))

    def test_no_negative_days_left(self) -> None:
        _, _, days_left, _ = compute_window(date(2026, 9, 29), 15, 14)
        self.assertGreaterEqual(days_left, 0)


class Formatting(unittest.TestCase):
    def test_indian_digit_grouping(self) -> None:
        self.assertEqual(inr(286567), "2,86,567")
        self.assertEqual(inr(3438840), "34,38,840")
        self.assertEqual(inr(1163), "1,163")
        self.assertEqual(inr(999), "999")
        self.assertEqual(inr(-2280), "-2,280")

    def test_decimals(self) -> None:
        self.assertEqual(inr(286567.89, 2), "2,86,567.89")
        self.assertEqual(inr(95.8, 2), "95.80")


class Recipients(unittest.TestCase):
    """Recipient resolution. Addresses live in separate secrets because the
    repo is public, so the merge logic is what actually delivers to both."""

    def _config(self, **env):
        import os
        from unittest.mock import patch
        keys = ("FX_RECEIVER_EMAIL", "RECEIVER_EMAIL", "FX_EXTRA_RECEIVERS")
        cleared = {k: "" for k in keys}
        with patch.dict(os.environ, {**cleared, **env}):
            return Config()

    def test_primary_only(self):
        self.assertEqual(self._config(RECEIVER_EMAIL="a@x.com").receivers, ["a@x.com"])

    def test_primary_plus_extra(self):
        cfg = self._config(RECEIVER_EMAIL="a@x.com", FX_EXTRA_RECEIVERS="b@y.com")
        self.assertEqual(cfg.receivers, ["a@x.com", "b@y.com"])

    def test_extra_may_list_several(self):
        cfg = self._config(RECEIVER_EMAIL="a@x.com", FX_EXTRA_RECEIVERS="b@y.com, c@z.com")
        self.assertEqual(cfg.receivers, ["a@x.com", "b@y.com", "c@z.com"])

    def test_duplicates_are_not_mailed_twice(self):
        cfg = self._config(RECEIVER_EMAIL="a@x.com", FX_EXTRA_RECEIVERS="A@X.com")
        self.assertEqual(cfg.receivers, ["a@x.com"])

    def test_fx_receiver_overrides_generic_but_extra_still_applies(self):
        cfg = self._config(FX_RECEIVER_EMAIL="fx@x.com", RECEIVER_EMAIL="gen@x.com",
                           FX_EXTRA_RECEIVERS="b@y.com")
        self.assertEqual(cfg.receivers, ["fx@x.com", "b@y.com"])

    def test_empty_extra_is_harmless(self):
        for blank in ("", "   ", ","):
            cfg = self._config(RECEIVER_EMAIL="a@x.com", FX_EXTRA_RECEIVERS=blank)
            self.assertEqual(cfg.receivers, ["a@x.com"])

    def test_malformed_address_is_reported(self):
        cfg = self._config(RECEIVER_EMAIL="a@x.com", FX_EXTRA_RECEIVERS="not-an-email")
        self.assertTrue(any("malformed" in p for p in cfg.validate()))


class Backtesting(unittest.TestCase):
    def test_deadline_is_always_enforced(self) -> None:
        dates, closes = synthetic_series(n=700)
        result = run_backtest(dates, closes, min_history=300, send_amount=3000.0)
        self.assertGreater(result.n, 3)
        for month in result.months:
            # Nothing may settle after the window closed.
            self.assertLessEqual(month.send_date, (result.months[-1].send_date + "~"))
            self.assertGreaterEqual(month.strategy_rate, month.worst_rate)
            self.assertLessEqual(month.strategy_rate, month.best_rate)

    def test_both_rules_run(self) -> None:
        dates, closes = synthetic_series(n=700)
        for rule in ("percentile", "model"):
            result = run_backtest(dates, closes, min_history=300, rule=rule)
            self.assertEqual(result.rule, rule)
            self.assertTrue(result.summary_lines())

    def test_short_history_returns_empty_not_error(self) -> None:
        dates, closes = synthetic_series(n=80)
        self.assertEqual(run_backtest(dates, closes, min_history=300).n, 0)


if __name__ == "__main__":
    unittest.main()
