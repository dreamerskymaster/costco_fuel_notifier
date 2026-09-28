"""Offline tests for the INR -> USD brief. No network: series are synthetic."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from fx_config import OutboundConfig
from fx_outbound import Route, break_even_markup, cost_to_land, gst_on_conversion, implied_markup_pct, parse_card_rate
from fx_outbound_timing import cheapness_percentile, run_outbound_backtest
from inr_usd_tracker import send_reason


def weekday_series(closes: list[float], start: date = date(2022, 1, 3)) -> list[str]:
    dates, day = [], start
    for _ in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        dates.append(day.isoformat())
        day += timedelta(days=1)
    return dates


class GstTests(unittest.TestCase):
    def test_slabs(self):
        self.assertAlmostEqual(gst_on_conversion(10_000), 0.18 * 250)          # minimum
        self.assertAlmostEqual(gst_on_conversion(96_000), 0.18 * 960)
        self.assertAlmostEqual(gst_on_conversion(200_000), 0.18 * 1_500)
        self.assertAlmostEqual(gst_on_conversion(1e9), 0.18 * 60_000)          # cap


class RouteCostTests(unittest.TestCase):
    def test_zero_cost_route_is_mid_market(self):
        route = Route("ideal", markup_pct=0.0, fee_inr=0.0, conversion_gst=False)
        cost = cost_to_land(route, 1000, 95.0)
        self.assertAlmostEqual(cost.total_inr, 95_000)
        self.assertAlmostEqual(cost.extra_vs_mid, 0.0)

    def test_deductions_are_grossed_up(self):
        route = Route("wire", markup_pct=0.0, fee_inr=0.0, conversion_gst=False, usd_deductions=30)
        self.assertAlmostEqual(cost_to_land(route, 1000, 95.0).total_inr, 1030 * 95.0)

    def test_markup_and_fees_add_up(self):
        route = Route("x", markup_pct=1.0, fee_inr=1000, fee_gst=True, conversion_gst=True)
        cost = cost_to_land(route, 1000, 100.0)
        converted = 1000 * 101.0
        expected = converted + 1000 + 180 + gst_on_conversion(converted)
        self.assertAlmostEqual(cost.total_inr, expected)

    def test_implied_markup_round_trips(self):
        for route in (
            Route("wire", markup_pct=0.8, fee_inr=1200, usd_deductions=30),
            Route("gp", markup_pct=1.7, fee_inr=0, usd_deductions=0),
        ):
            cost = cost_to_land(route, 1000, 95.5)
            recovered = implied_markup_pct(cost.total_inr, 1000, 95.5, route)
            self.assertAlmostEqual(recovered, route.markup_pct, places=3)


class CardRateTests(unittest.TestCase):
    HSBC = (
        "<p>Updated on: 25 Sep 2026, 10:10am IST</p><table><tr><td>Great Britain Pound (GBP)</td><td>123.54</td>"
        "<td>129.87</td><td>123.54</td><td>129.87</td></tr><tr><td>United States Dollar (USD)</td><td>94.08</td>"
        "<td>97.75</td><td>94.08</td><td>97.75</td><td>94.08</td><td>97.75</td></tr></table>"
    )
    IOB = (
        "<b>CARD RATES - </b><b>25.09.2026 </b>updated at <b>10.35 AM </b><table><tr><td>1</td><td>USD</td>"
        "<td>95.64</td><td>96.21</td><td>95.59</td><td>96.26</td></tr></table>"
    )

    def test_hsbc_tt_sell(self):
        rate = parse_card_rate("HSBC India", self.HSBC)
        self.assertEqual(rate.tt_sell, 97.75)
        self.assertIn("25 Sep 2026", rate.updated)

    def test_iob_tt_sell(self):
        self.assertEqual(parse_card_rate("IOB", self.IOB).tt_sell, 96.21)

    def test_layout_change_returns_none(self):
        self.assertIsNone(parse_card_rate("IOB", "<td>USD</td><td>Buy</td><td>Sell</td>"))
        self.assertIsNone(parse_card_rate("HSBC India", "<p>maintenance</p>"))


class BreakEvenTests(unittest.TestCase):
    def test_break_even_matches_target(self):
        wire = Route("wire", markup_pct=1.9, fee_inr=1200, usd_deductions=15)
        target = cost_to_land(wire, 1000, 96.0).total_inr
        gp = Route("gp", markup_pct=1.0, fee_inr=0, usd_deductions=15)
        m = break_even_markup(gp, target, 1000, 96.0)
        self.assertGreater(m, 1.9)  # no fee, so it can afford a wider spread
        self.assertAlmostEqual(cost_to_land(Route("gp", m, 0, usd_deductions=15), 1000, 96.0).total_inr, target, places=2)


class TimingTests(unittest.TestCase):
    def test_cheapness_percentile(self):
        self.assertEqual(cheapness_percentile([90, 91, 92, 89], lookback=4), 75.0)  # cheapest dollar
        self.assertEqual(cheapness_percentile([89, 90, 91, 92], lookback=4), 0.0)   # dearest dollar

    def test_depreciating_rupee_punishes_waiting(self):
        closes = [80 + 0.01 * i for i in range(700)]  # steady rupee weakening
        dates = weekday_series(closes)
        result = run_outbound_backtest(dates, closes, payday=15, flex_days=14, usd_amount=1000)
        self.assertGreater(result.n, 20)
        # A monotone rise never looks cheap, so the rule always waits for the
        # deadline and always pays more than sending on day one.
        self.assertLess(result.saved_per_month, 0)
        self.assertEqual(result.wins, 0)

    def test_no_lookahead_when_short(self):
        self.assertEqual(run_outbound_backtest(["2024-01-01"], [83.0], 15, 14, 1000).n, 0)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.config = OutboundConfig()
        self.config.window_start, self.config.flex_days, self.config.digest_weekday = 15, 14, 0

    def test_window_open_and_closing(self):
        self.assertEqual(send_reason(date(2026, 10, 15), self.config, {}), "window-open")
        self.assertEqual(send_reason(date(2026, 10, 27), self.config, {}), "closing")

    def test_weekly_and_quiet(self):
        self.assertEqual(send_reason(date(2026, 10, 5), self.config, {}), "weekly")   # a Monday
        self.assertIsNone(send_reason(date(2026, 10, 7), self.config, {}))            # a Wednesday

    def test_one_email_per_day(self):
        self.assertIsNone(send_reason(date(2026, 10, 15), self.config, {"last_sent": "2026-10-15"}))

    def test_no_fallback_recipient(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("INR_USD_RECEIVER_EMAIL", "RECEIVER_EMAIL")}
        os.environ["RECEIVER_EMAIL"] = "owner@example.com"
        try:
            self.assertEqual(OutboundConfig().receivers, [])
        finally:
            os.environ.pop("RECEIVER_EMAIL")
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
