"""Offline tests for the Mac -> cloud gas hand-off and the dashboard data. Run: python -m unittest test_gas_snapshot"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import gas_dashboard as gd
import gas_snapshot as gs

NOW = datetime(2026, 10, 29, 22, 30, tzinfo=timezone.utc)      # 6:30 PM ET


def station(name, zip_code, price, label=None, card="Citi Costco (4%)"):
    return {"name": name, "zip": zip_code, "city": "", "state": "", "area_label": label or zip_code,
            "distance": "N/A", "price": price, "formatted_price": f"${price:.2f}", "best_card": card,
            "net_price": round(price * 0.96, 2), "formatted_net_price": f"${price * 0.96:.2f}", "card_note": "",
            "stale": False, "last_updated": "Oct 29, 05:00 PM", "waze_link": "https://waze.com/x",
            "maps_link": "https://maps.google.com/x"}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "gas_snapshot.json"

    def test_round_trip_and_age(self):
        gs.save([station("Shell", "06854", 4.09)], self.path, now=NOW)
        snap = gs.load(self.path)
        self.assertEqual(snap["stations"][0]["name"], "Shell")
        self.assertAlmostEqual(gs.age_hours(snap, NOW + timedelta(hours=2)), 2.0)

    def test_missing_or_corrupt_snapshot_is_infinitely_old(self):
        self.assertEqual(gs.age_hours(gs.load(self.path)), float("inf"))
        self.path.write_text("{not json")
        self.assertIsNone(gs.load(self.path))
        self.path.write_text(json.dumps({"fetched_at": "yesterday", "stations": []}))
        self.assertIsNone(gs.load(self.path))

    def test_pipeline_refuses_stale_snapshot_without_fetching(self):
        import gas_tracker
        gs.save([station("Shell", "06854", 4.09)], self.path, now=datetime.now(timezone.utc) - timedelta(hours=7))
        with mock.patch.dict(os.environ, {"GAS_SNAPSHOT": str(self.path)}), \
                mock.patch.object(gas_tracker, "fetch_gas_prices") as fetch, \
                mock.patch.object(gas_tracker, "log_to_sheets") as sheets:
            self.assertEqual(asyncio.run(gas_tracker.main()), 1)
        fetch.assert_not_called()
        sheets.assert_not_called()

    def test_old_prices_never_logged_or_shown(self):
        import gas_tracker
        old = station("Shell", "06854", 4.09)
        old["stale"] = True
        gs.save([old], self.path)
        with mock.patch.dict(os.environ, {"GAS_SNAPSHOT": str(self.path)}), \
                mock.patch.object(gas_tracker, "log_to_sheets") as sheets, \
                mock.patch.object(gas_tracker.gas_dashboard, "write") as board:
            self.assertEqual(asyncio.run(gas_tracker.main()), 0)       # overnight lull is not a failure
        sheets.assert_not_called()
        board.assert_not_called()

    def test_watchdog_exit_codes(self):
        with mock.patch.object(gs, "SNAPSHOT_PATH", self.path):
            self.assertEqual(gs.main(["check", "--max-age-h", "24"]), 1)          # never fetched
            gs.save([station("Shell", "06854", 4.09)], self.path)
            self.assertEqual(gs.main(["check", "--max-age-h", "24"]), 0)


class DashboardTests(unittest.TestCase):
    def history(self):
        rows = []
        for i in range(1, 100):
            d = (date(2026, 10, 29) - timedelta(days=i)).isoformat()
            rows += [{"date": d, "zip": "06854", "name": "Shell", "price": f"{4.20 + (i % 5) / 100:.3f}"},
                     {"date": d, "zip": "07002", "name": "Costco", "price": "3.890"},
                     {"date": d, "zip": "07002", "name": "BP", "price": "3.500"}]          # not Costco: excluded
        return rows

    def test_board_contents(self):
        stations = [station("Dave's", "06854", 4.09, "Norwalk (Home)"), station("Shell", "06854", 4.25, "Norwalk (Home)"),
                    station("Costco", "07002", 3.85, "Bayonne (Costco)")]
        board = gd.build(stations, NOW, self.history(), zip_order=["06854", "07002"],
                         prediction_lines=["Tomorrow: $4.08"], decision_note="no new low")
        self.assertEqual(board["best"]["name"], "Costco")
        self.assertEqual(board["home"]["cheapest"]["name"], "Dave's")
        self.assertEqual(board["home"]["low_90d"]["price"], 4.20)
        self.assertEqual(len(board["trend"]["norwalk"]), gd.TREND_DAYS)
        self.assertTrue(all(p[1] == 3.89 for p in board["trend"]["nj_costco"]))
        self.assertEqual([a["label"] for a in board["areas"]], ["Norwalk (Home)", "Bayonne (Costco)"])
        self.assertEqual(board["fetched_at"], "2026-10-29T22:30:00+00:00")
        costco = {c["zip"]: c for c in board["costco"]}
        self.assertEqual(costco["07002"]["net_price"], round(3.85 * 0.96, 2))
        self.assertNotIn("price", costco["10801"])                       # no Costco reported there

    def test_email_copy_drops_every_address(self):
        html = "<p>Commute Edition for me@northeastern.edu</p><p>from bot@gmail.com</p><p>$3.77 at Costco</p>"
        out = gd.scrub(html, private=["me@northeastern.edu"])
        self.assertNotIn("@", out)
        self.assertIn("$3.77 at Costco", out)

    def test_areas_keep_top_five_by_net_price(self):
        stations = [station(f"S{i}", "06854", 4.00 + i / 100, "Norwalk") for i in range(8, 0, -1)]
        area = gd.build(stations, NOW, [], zip_order=["06854"])["areas"][0]
        self.assertEqual([s["name"] for s in area["stations"]], ["S1", "S2", "S3", "S4", "S5"])


if __name__ == "__main__":
    unittest.main()
