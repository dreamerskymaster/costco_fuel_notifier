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
        _, made, _ = pp.run_daily(d0, 3.900, 7.0)            # same as today; the EIA week is not used
        self.assertAlmostEqual(float(made["predicted"]), 3.900, places=3)
        self.assertEqual(made["dir_pred"], "flat")
        _, again, _ = pp.run_daily(d0, 3.950, 7.0)           # later run same day keeps the morning call
        self.assertEqual(again["predicted"], made["predicted"])
        graded, _, lr = pp.run_daily(d0 + timedelta(days=1), 3.990, 7.0)
        self.assertAlmostEqual(float(graded["error_c"]), 9.0, places=1)
        self.assertIn("Missed high", graded["note"])
        self.assertEqual(lr.graded, 1)

    def test_range_is_sized_from_recent_jumps(self):
        day, price = date(2026, 9, 1), 4.30
        steps = [0, 0, -6, 0, 0, 6, 0, -8, 0, 0, 0, 8, 0, -10, 0, 0]       # Oct-2026-like step moves, in ¢
        for step in steps:
            pp.run_daily(day, price, None)
            day, price = day + timedelta(days=1), round(price + step / 100, 3)
        lr = pp.learn(pp.load())
        self.assertGreaterEqual(lr.hi_c, 6.0)                 # covers a typical 6-10¢ jump
        self.assertEqual(lr.lo_c, -lr.hi_c)
        self.assertAlmostEqual(lr.confidence, 10 / 15, places=2)   # share of graded days that stayed put
        _, made, _ = pp.run_daily(day, price, None)
        self.assertIn("sized from recent daily jumps", " ".join(pp.summary_lines(None, made, lr)))

if __name__ == "__main__":
    unittest.main()
