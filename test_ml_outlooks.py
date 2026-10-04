"""Offline tests for the ML/outlook layer: no network, synthetic or fixture data only.

Run with `python -m unittest -v test_ml_outlooks`.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

import fx_outlook
import india_fuel
import ml_core
import us_fuel_forecast


class MlCoreTests(unittest.TestCase):
    def test_logit_learns_a_real_signal_out_of_sample(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(1500, 3))
        y = (X[:, 0] * 2 + rng.normal(size=1500) > 0).astype(float)
        wf = ml_core.walk_forward(X, y, start=300, step=50)
        self.assertTrue(wf.has_edge)
        self.assertGreater(wf.hit_rate, 0.75)
        self.assertGreater(wf.skill, 0.2)

    def test_noise_is_reported_as_no_edge(self):
        rng = np.random.default_rng(2)
        X = rng.normal(size=(1500, 4))
        y = (rng.random(1500) < 0.6).astype(float)
        wf = ml_core.walk_forward(X, y, start=300, step=50)
        self.assertFalse(wf.has_edge)
        self.assertEqual(ml_core.confidence_label(0.9, wf), "Low")

    def test_walk_forward_never_predicts_before_start(self):
        rng = np.random.default_rng(3)
        X, y = rng.normal(size=(400, 2)), (rng.random(400) < 0.5).astype(float)
        wf = ml_core.walk_forward(X, y, start=200, step=25)
        self.assertTrue(np.isnan(wf.prob[:200]).all())
        self.assertFalse(np.isnan(wf.prob[200:]).any())


EIA_FIXTURE = """
<tr><td class='B6'>&nbsp;&nbsp;2025-Dec</td><td class='B5'>12/22&nbsp;</td><td class='B3'>3.100&nbsp;&nbsp;</td>
<td class='B5'>12/29&nbsp;</td><td class='B3'>3.120&nbsp;&nbsp;</td><td class='B5'>01/05&nbsp;</td><td class='B3'>3.150&nbsp;&nbsp;</td>
<td class='B5'>&nbsp;</td><td class='B3'>&nbsp;&nbsp;</td></tr>
<tr><td class='B6'>&nbsp;&nbsp;2026-Jan</td><td class='B5'>01/12&nbsp;</td><td class='B3'>3.200&nbsp;&nbsp;</td></tr>
"""


class UsFuelTests(unittest.TestCase):
    def test_eia_parser_handles_the_year_boundary(self):
        data = us_fuel_forecast.parse_eia_weekly(EIA_FIXTURE)
        self.assertEqual(list(data), [date(2025, 12, 22), date(2025, 12, 29), date(2026, 1, 5), date(2026, 1, 12)])
        self.assertEqual(data[date(2026, 1, 5)], 3.150)

    def _synthetic(self, weeks=900, seed=4):
        """Retail follows wholesale with a one-week lag, so the model has something real to find."""
        rng = np.random.default_rng(seed)
        w = 2.0 + np.cumsum(rng.normal(0, 0.05, weeks))
        r = np.empty(weeks)
        r[0] = w[0] + 0.6
        for t in range(1, weeks):
            r[t] = r[t - 1] + 0.6 * (w[t - 1] + 0.6 - r[t - 1]) + rng.normal(0, 0.01)
        start = date(2000, 1, 3)
        retail = {start + timedelta(weeks=i): float(r[i]) for i in range(weeks)}
        wholesale = {start + timedelta(weeks=i, days=-3): float(w[i]) for i in range(weeks)}
        return retail, wholesale

    def test_forecast_finds_the_lag_and_keeps_its_record(self):
        retail, wholesale = self._synthetic()
        o = us_fuel_forecast.forecast(retail=retail, wholesale=wholesale)
        self.assertTrue(o.wf.has_edge)
        self.assertGreater(o.wf.hit_rate, 0.7)
        self.assertIn(o.confidence, {"Low", "Medium", "High"})
        lines = us_fuel_forecast.outlook_lines(o)
        self.assertTrue(lines[-1].startswith("Model record since"))
        self.assertIn("ML Fill-Up Outlook", us_fuel_forecast.outlook_html(o))

    def test_verdict_is_proportionate(self):
        verdict, detail = us_fuel_forecast._verdict(0.8, 2.0, "Medium", 18.5, None, 20)
        self.assertTrue(verdict.startswith("Fill up today"))
        self.assertIn("Small stakes", detail)
        verdict, _ = us_fuel_forecast._verdict(0.55, 0.5, "Low", 18.5, None, 50)
        self.assertTrue(verdict.startswith("No strong signal"))
        verdict, _ = us_fuel_forecast._verdict(0.2, -4.0, "High", 18.5, None, 50)
        self.assertTrue(verdict.startswith("Buy only what you need"))


GOODRETURNS_FIXTURE = (
    "|Last 10 Days Petrol Rate in Mumbai|Date|Price|Price Change|October 2, 2026|₹111.21|0.00 "
    "|October 1, 2026|₹111.21|0.00 |September 30, 2026|₹111.21|0.00 |"
    "Petrol Rate in Mumbai, September 2026|Details|Price|1|st|September |₹ 111.21|30|th|September |₹ 111.21|"
    "Petrol Rate in Mumbai, May 2026|Details|Price|1|st|May |₹ 103.54|31|st|May |₹ 111.21|"
    "Petrol Rate in Mumbai, April 2026|Details|Price|1|st|April |₹ 103.54|30|th|April |₹ 103.54|"
)


class IndiaFuelTests(unittest.TestCase):
    def test_goodreturns_parse(self):
        cp = india_fuel.parse_goodreturns(GOODRETURNS_FIXTURE, "Mumbai", "petrol")
        self.assertEqual(cp.price, 111.21)
        self.assertEqual(cp.daily[0], (date(2026, 10, 2), 111.21))
        self.assertEqual(cp.months[1], (date(2026, 5, 1), 103.54, 111.21))

    def test_implausible_values_are_rejected(self):
        self.assertIsNone(india_fuel.parse_bankbazaar("|Today's Diesel Price in Goa|₹ 13.69|", "Goa", "diesel"))
        ok = india_fuel.parse_bankbazaar("|Today’s Petrol Price in Goa|₹ 104.06|", "Goa", "petrol")
        self.assertEqual(ok.price, 104.06)

    def test_outlook_reports_stability_revision_and_crude_pressure(self):
        cp = india_fuel.parse_goodreturns(GOODRETURNS_FIXTURE, "Mumbai", "petrol")
        brent = {date(2026, 5, 8): 100.0, date(2026, 5, 22): 100.0, date(2026, 9, 25): 115.0}
        fx = {"2026-05-08": 90.0, "2026-05-22": 90.0, "2026-09-25": 90.0}
        o = india_fuel.outlook(cp, brent, fx, today=date(2026, 10, 3))
        self.assertEqual(o.revision_rs, 111.21 - 103.54)
        self.assertAlmostEqual(o.crude_change_pct, 15.0, places=1)
        text = " ".join(o.lines)
        self.assertIn("no cheaper day to wait for", text)
        self.assertIn("pressure is upward", text)
        self.assertIn("not ML", o.lines[-1])


class FxOutlookTests(unittest.TestCase):
    def _walk(self, n=1300, drift=0.0001, seed=5):
        rng = np.random.default_rng(seed)
        return list(80 * np.exp(np.cumsum(rng.normal(drift, 0.003, n))))

    def test_random_walk_falls_back_to_the_base_rate(self):
        o = fx_outlook.forecast(self._walk())
        for v in o.views:
            if not v.wf.has_edge:
                self.assertEqual(v.p_up, v.base_rate)
                self.assertEqual(v.confidence, "Low")
            self.assertLess(v.low, o.spot)
            self.assertGreater(v.high, o.spot)

    def test_lines_frame_advice_by_direction(self):
        o = fx_outlook.forecast(self._walk())
        out = " ".join(fx_outlook.outlook_lines(o, 3000, "usd_to_inr"))
        inbound = " ".join(fx_outlook.outlook_lines(o, 1000, "inr_to_usd"))
        self.assertIn("80% range", out)
        if not o.views[0].wf.has_edge:
            self.assertIn("send on schedule", out)
            self.assertIn("Send on day one", inbound)

    def test_short_history_returns_none(self):
        self.assertIsNone(fx_outlook.forecast(self._walk(n=300)))


if __name__ == "__main__":
    unittest.main()
