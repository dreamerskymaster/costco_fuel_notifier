"""Friday/Sunday catch-up: the weekend alert goes out even when the Mac slept through the window.
Run: python -m unittest test_weekend_catchup"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import gas_cadence
import gas_snapshot as gs
import gas_tracker

# Friday 2026-11-06, 12:30 PM ET; the Mac last pushed at 6:28 AM ET and then slept.
NOW = datetime(2026, 11, 6, 17, 30, tzinfo=timezone.utc)
FETCHED = datetime(2026, 11, 6, 11, 28, tzinfo=timezone.utc)


def station(name, zip_code, price, posted):
    return {"name": name, "zip": zip_code, "city": "", "state": "NJ" if zip_code.startswith("07") else "CT",
            "area_label": zip_code, "distance": "N/A", "price": price, "formatted_price": f"${price:.2f}",
            "best_card": "x", "net_price": price, "formatted_net_price": f"${price:.2f}", "card_note": "",
            "stale": False, "last_updated": posted, "waze_link": "w", "maps_link": "m"}


class StillFreshTests(unittest.TestCase):
    def test_age_is_measured_at_send_time(self):
        stations = [station("Costco", "07002", 3.87, "Nov 06, 05:10 AM"),   # 7.3 h old at 12:30 PM
                    station("Shell", "06854", 4.20, "Nov 05, 09:05 PM"),    # 15.4 h old: hidden
                    station("Odd", "06854", 4.10, "Unknown")]               # no time: hidden
        kept = gs.still_fresh(stations, FETCHED, NOW)
        self.assertEqual([s["name"] for s in kept], ["Costco"])

    def test_new_year_rollover(self):
        jan = datetime(2027, 1, 1, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(gs.posted_at(station("a", "07002", 3, "Dec 31, 11:30 PM"), jan).year, 2026)


class CatchupTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.snap = d / "gas_snapshot.json"
        self.state = d / "gas_state.json"
        gs.save([station("Costco", "07002", 3.87, "Nov 06, 05:10 AM"),
                 station("Exxon", "06854", 4.14, "Nov 06, 06:00 AM"),
                 station("Shell", "06854", 4.20, "Nov 05, 09:05 PM")], self.snap, now=FETCHED)
        self.state.write_text(json.dumps({"weekend_sent": {"FRIDAY_DEPARTURE": "2026-10-30"}}))
        self.patches = [mock.patch.object(gas_cadence, "STATE_JSON", self.state),
                        mock.patch.object(gas_tracker.us_fuel_forecast, "forecast", side_effect=RuntimeError),
                        mock.patch.object(gas_tracker.us_fuel_forecast, "load_station_history", return_value=[])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_catchup(self, mode="FRIDAY_DEPARTURE", now=NOW, sent=True):
        with mock.patch.object(gas_tracker, "get_commute_mode", return_value=mode), \
             mock.patch.object(gas_tracker, "send_email_smtp", return_value=sent) as send, \
             mock.patch.object(gas_tracker.us_fuel_forecast, "record_stations") as record, \
             mock.patch.object(gas_tracker, "log_to_sheets") as sheets:
            rc = gas_tracker.weekend_catchup(str(self.snap), now=now)
        return rc, send, record, sheets

    def test_sends_friday_alert_from_older_snapshot_without_relogging(self):
        rc, send, record, sheets = self.run_catchup()
        self.assertEqual(rc, 0)
        send.assert_called_once()
        subject, text, html = send.call_args.args
        self.assertIn("asleep", text)
        self.assertNotIn("Shell", text)            # the 15 h old price stays hidden
        record.assert_not_called()
        sheets.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())["weekend_sent"]["FRIDAY_DEPARTURE"], "2026-11-06")

    def test_only_once_per_day(self):
        self.run_catchup()
        rc, send, _, _ = self.run_catchup()
        self.assertEqual(rc, 0)
        send.assert_not_called()

    def test_skips_outside_weekend_windows(self):
        rc, send, _, _ = self.run_catchup(mode="REGULAR")
        self.assertEqual(rc, 0)
        send.assert_not_called()

    def test_refuses_snapshot_over_twelve_hours(self):
        late = datetime(2026, 11, 6, 23, 45, tzinfo=timezone.utc)   # 12.3 h after the fetch
        rc, send, _, _ = self.run_catchup(now=late)
        self.assertEqual(rc, 1)
        send.assert_not_called()

    def test_failed_send_does_not_mark_sent(self):
        rc, _, _, _ = self.run_catchup(sent=False)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(self.state.read_text())["weekend_sent"]["FRIDAY_DEPARTURE"], "2026-10-30")


if __name__ == "__main__":
    unittest.main()
