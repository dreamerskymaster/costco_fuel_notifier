"""Offline tests for when the US fuel digest may email. Run: python -m unittest test_gas_cadence"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import gas_cadence as gc

NJ = {"07002", "07608"}
MON = date(2026, 10, 12)


def history(days: int, price: float = 3.90, end: date = MON) -> list[dict]:
    return [{"date": (end - timedelta(days=i)).isoformat(), "zip": "07002", "name": "Costco", "price": f"{price:.3f}"}
            for i in range(1, days + 1)]


class CadenceTests(unittest.TestCase):
    def test_monday_with_recent_digest_and_no_low_is_silent(self):
        d = gc.decide("REGULAR", 3.95, history(20), NJ, {"last_digest_date": "2026-10-05"}, MON)
        self.assertFalse(d.send)
        self.assertIn("3 day(s)", d.note)

    def test_ten_day_interval(self):
        d = gc.decide("REGULAR", 3.95, history(20), NJ, {"last_digest_date": "2026-10-02"}, MON)
        self.assertEqual((d.send, d.reason), (True, "interval"))

    def test_new_low_sends_once(self):
        state = {"last_digest_date": "2026-10-10"}
        d = gc.decide("REGULAR", 3.85, history(20), NJ, state, MON)
        self.assertEqual((d.send, d.reason), (True, "low"))
        gc.mark_sent(state, d, "REGULAR", 3.85, MON)
        again = gc.decide("REGULAR", 3.85, history(20), NJ, state, MON + timedelta(days=1))
        self.assertFalse(again.send)

    def test_no_low_without_a_week_of_history(self):
        d = gc.decide("REGULAR", 3.50, history(3), NJ, {"last_digest_date": "2026-10-10"}, MON)
        self.assertFalse(d.send)

    def test_sub_cent_wobble_is_not_a_low(self):
        d = gc.decide("REGULAR", 3.898, history(20), NJ, {"last_digest_date": "2026-10-10"}, MON)
        self.assertFalse(d.send)

    def test_weekend_alert_once_per_day(self):
        fri = date(2026, 10, 9)
        state = {"last_digest_date": "2026-10-08"}
        d = gc.decide("FRIDAY_DEPARTURE", 3.95, [], NJ, state, fri)
        self.assertEqual((d.send, d.reason), (True, "weekend"))
        gc.mark_sent(state, d, "FRIDAY_DEPARTURE", 3.95, fri)
        self.assertFalse(gc.decide("FRIDAY_DEPARTURE", 3.95, [], NJ, state, fri).send)
        self.assertTrue(gc.decide("SUNDAY_RETURN", 3.95, [], NJ, state, fri + timedelta(days=2)).send)
        self.assertEqual(state["last_digest_date"], "2026-10-08")   # weekend alerts keep the 10-day clock

    def test_force_always_sends(self):
        self.assertTrue(gc.decide("REGULAR", None, [], NJ, {"last_digest_date": MON.isoformat()}, MON, force=True).send)


if __name__ == "__main__":
    unittest.main()
