"""Offline tests for email cadence and the self-graded predictor. Run: python -m unittest test_gas_cadence"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import gas_cadence as gc
import price_predictor as pp

MON = date(2026, 10, 12)


def history(days: int, price: float = 3.90, end: date = MON, zip_code: str = "06854") -> list[dict]:
    return [{"date": (end - timedelta(days=i)).isoformat(), "zip": zip_code, "name": "Shell", "price": f"{price:.3f}"}
            for i in range(1, days + 1)]


class CadenceTests(unittest.TestCase):
    def test_weekday_without_new_low_is_silent(self):
        self.assertFalse(gc.decide("REGULAR", 3.95, history(30), {}, MON).send)

    def test_norwalk_low_sends_once(self):
        state = {}
        d = gc.decide("REGULAR", 3.85, history(30), state, MON)
        self.assertEqual((d.send, d.reason), (True, "low"))
        gc.mark_sent(state, d, "REGULAR", 3.85, MON)
        self.assertFalse(gc.decide("REGULAR", 3.85, history(30), state, MON + timedelta(days=1)).send)

    def test_nj_prices_do_not_trigger_weekday_low(self):
        h = history(30) + history(30, price=3.50, zip_code="07002")
        self.assertFalse(gc.decide("REGULAR", 3.92, h, {}, MON).send)   # cheap NJ history neither blocks nor triggers

    def test_no_low_without_a_week_of_history(self):
        d = gc.decide("REGULAR", 3.50, history(3), {}, MON)
        self.assertFalse(d.send)
        self.assertIn("building history", d.note)

    def test_sub_cent_wobble_is_not_a_low(self):
        self.assertFalse(gc.decide("REGULAR", 3.898, history(30), {}, MON).send)

    def test_saturday_low_is_silent(self):
        sat = date(2026, 10, 10)
        self.assertFalse(gc.decide("REGULAR", 3.50, history(30, end=sat), {}, sat).send)

    def test_weekend_alert_once_per_day(self):
        fri, state = date(2026, 10, 9), {}
        d = gc.decide("FRIDAY_DEPARTURE", 3.95, [], state, fri)
        self.assertEqual((d.send, d.reason), (True, "weekend"))
        gc.mark_sent(state, d, "FRIDAY_DEPARTURE", 3.95, fri)
        self.assertFalse(gc.decide("FRIDAY_DEPARTURE", 3.95, [], state, fri).send)
        self.assertTrue(gc.decide("SUNDAY_RETURN", 3.95, [], state, fri + timedelta(days=2)).send)

    def test_force_always_sends(self):
        self.assertTrue(gc.decide("REGULAR", None, [], {}, MON, force=True).send)


class PredictorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        pp.PRED_CSV = Path(self.tmp.name) / "predictions.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_predicts_once_a_day_and_grades_next_day(self):
        d0 = date(2026, 10, 5)
        _, made, _ = pp.run_daily(d0, 3.900, 7.0)            # +7¢/week -> +1¢ tomorrow
        self.assertAlmostEqual(float(made["predicted"]), 3.910, places=3)
        _, again, _ = pp.run_daily(d0, 3.950, 7.0)           # later run same day keeps the morning call
        self.assertEqual(again["predicted"], made["predicted"])
        graded, _, lr = pp.run_daily(d0 + timedelta(days=1), 3.990, 7.0)
        self.assertAlmostEqual(float(graded["error_c"]), 8.0, places=1)
        self.assertIn("Missed high", graded["note"])
        self.assertEqual(lr.graded, 1)

    def test_learns_a_persistent_bias_and_widens_after_misses(self):
        day, price = date(2026, 9, 1), 3.50
        for _ in range(25):                                   # real price rises 3¢/day; model expects flat
            pp.run_daily(day, price, 0.0)
            day, price = day + timedelta(days=1), price + 0.03
        lr = pp.learn(pp.load())
        self.assertGreater(lr.bias_c, 2.0)                    # it has learned most of the 3¢/day drift
        self.assertLess(abs(float(pp.load()[-2]["error_c"])), 1.5)   # and its recent misses have shrunk
        _, made, _ = pp.run_daily(day, price, 0.0)
        self.assertGreater(float(made["predicted"]), price)
        self.assertIn("Self-correction", " ".join(pp.summary_lines(None, made, lr)))


if __name__ == "__main__":
    unittest.main()
