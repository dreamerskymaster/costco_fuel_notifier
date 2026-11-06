"""gcp/gas_compare/compare.py: parsing, summaries and the scorecard."""

import ast
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "gcp" / "gas_compare"))
import compare  # noqa: E402

NOW = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)  # 5:00 PM New York


def test_zip_list_matches_gas_tracker():
    tree = ast.parse((ROOT / "gas_tracker.py").read_text())
    zips = next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ZIP_CODES")
    assert list(compare.ZIP_LABELS) == zips


def test_gasbuddy_time_is_new_york_and_handles_new_year():
    fetched = datetime(2026, 11, 6, 2, 27, tzinfo=timezone.utc)  # Nov 5, 9:27 PM NY
    t = compare.parse_gasbuddy_time("Nov 05, 09:05 PM", fetched)
    assert t.isoformat() == "2026-11-05T21:05:00-05:00"
    jan = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert compare.parse_gasbuddy_time("Dec 31, 11:30 PM", jan).year == 2026
    assert compare.parse_gasbuddy_time("Unknown", fetched) is None


def test_google_rows_dedupe_and_parse_money():
    place = {"id": "a", "displayName": {"text": "Costco Gas Station"},
             "formattedAddress": "21 Goldsborough Dr, Bayonne, NJ 07002, USA",
             "fuelOptions": {"fuelPrices": [
                 {"type": "PREMIUM", "price": {"units": "4", "nanos": 450000000},
                  "updateTime": "2026-11-06T16:00:00Z"},
                 {"type": "REGULAR_UNLEADED", "price": {"units": "3", "nanos": 879000000},
                  "updateTime": "2026-11-06T16:00:00Z"}]}}
    bare = {"id": "b", "displayName": {"text": "Costco Gas Station"},
            "formattedAddress": "2 Teterboro Landing Dr, Teterboro, NJ 07608, USA"}
    rows = compare.google_stations({"07002": [place, bare], "07305": [place]})
    assert [(r["zip"], r["price"]) for r in rows] == [("07002", 3.879), ("07002", None)]


def test_summary_counts_fresh_and_costco():
    stations = [
        {"name": "Costco", "zip": "07002", "price": 3.87, "posted": "2026-11-06T20:00:00+00:00"},
        {"name": "Delta", "zip": "07047", "price": 3.80, "posted": "2026-11-05T12:00:00+00:00"},
        {"name": "Costco Gas Station", "zip": "07608", "price": None, "posted": None,
         "address": "Teterboro, NJ 07608"},
    ]
    s = compare.summarize(stations, NOW)
    assert (s["priced"], s["fresh"], s["zips_fresh"]) == (2, 1, 1)
    assert s["cheapest_fresh"] == {"name": "Costco", "zip": "07002", "price": 3.87}
    assert s["costco"]["Bayonne"] == {"price": 3.87, "age_h": 2.0}
    assert s["costco"]["Teterboro"] == {"listed_without_price": True}
    assert s["costco"]["New Rochelle"] is None


def _run(gb_fresh, gg_fresh, gb_costco_age, gg_costco_age, gb_ok=True):
    def src(fresh, age, ok):
        costco = {site: ({"price": 3.9, "age_h": age} if age is not None else None)
                  for site in compare.COSTCO_SITES.values()}
        return {"available": ok, "fresh": fresh, "zips_fresh": min(fresh, 11),
                "median_age_h": 5, "costco": costco}
    return {"gasbuddy": src(gb_fresh, gb_costco_age, gb_ok),
            "google": src(gg_fresh, gg_costco_age, True)}


def test_scorecard_picks_winners_and_counts_mac_outages():
    records = [_run(40, 60, 2, None), _run(0, 55, 30, 20, gb_ok=False)]
    card = {r["metric"]: r for r in compare.scorecard(records)}
    assert card["Runs with usable data (%)"]["gasbuddy"] == 50
    assert card["Runs with usable data (%)"]["winner"] == "Google"
    assert card["Costco Bayonne: fresh price (% of runs)"]["winner"] == "GasBuddy"
    assert "Costco" in compare.verdict(list(card.values()))


def test_email_renders_both_modes():
    records = [dict(_run(40, 60, 2, 14), at="2026-11-06T22:30:00+00:00")]
    for final in (False, True):
        subject, text, html = compare.build_email(
            records, date(2026, 11, 6), date(2026, 11, 6), date(2026, 11, 20), final)
        assert ("Final verdict" in subject) == final
        assert "Costco Teterboro" in html and "GasBuddy" in text
