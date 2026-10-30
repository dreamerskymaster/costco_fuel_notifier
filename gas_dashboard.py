"""Data for the phone dashboard (site/index.html on Cloudflare Pages).

Every gas run rewrites data/gas_dashboard.json. The page is static and reads that file
straight from GitHub, so a fresh push shows up without redeploying the site.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DASHBOARD_JSON = Path(__file__).parent / "data" / "gas_dashboard.json"
HOME_ZIP = "06854"
NJ_COSTCO_ZIPS = ("07002", "07608")    # Bayonne, Teterboro
TREND_DAYS = 90
TOP_PER_AREA = 5
STATION_FIELDS = ("name", "price", "net_price", "best_card", "card_note", "last_updated", "stale",
                  "maps_link", "waze_link")


def _daily_lows(history: list[dict], keep) -> list[list]:
    lows: dict[str, float] = {}
    for r in history:
        if keep(r):
            lows[r["date"]] = min(lows.get(r["date"], 1e9), float(r["price"]))
    return [[d, round(p, 3)] for d, p in sorted(lows.items())]


def _station(s: dict) -> dict:
    return {k: s.get(k) for k in STATION_FIELDS}


def build(stations: list[dict], fetched_at: datetime, history: list[dict], *, zip_order: list[str] = (),
          outlook_lines: list[str] = (), prediction_lines: list[str] = (), decision_note: str = "",
          today: date | None = None) -> dict:
    today = today or fetched_at.astimezone(ET).date()
    since = (today - timedelta(days=TREND_DAYS)).isoformat()
    norwalk = [p for p in _daily_lows(history, lambda r: r["zip"] == HOME_ZIP) if p[0] >= since]
    nj_costco = [p for p in _daily_lows(history, lambda r: r["zip"] in NJ_COSTCO_ZIPS and "costco" in r["name"].lower())
                 if p[0] >= since]

    home = [s for s in stations if s["zip"] == HOME_ZIP]
    cheapest_home = min(home, key=lambda s: s["price"]) if home else None
    low = min(norwalk, key=lambda p: p[1]) if norwalk else None

    order = {z: i for i, z in enumerate(zip_order)}
    areas: dict[str, dict] = {}
    for s in sorted(stations, key=lambda s: (order.get(s["zip"], 99), s["net_price"])):
        area = areas.setdefault(s["area_label"], {"label": s["area_label"], "zip": s["zip"], "stations": []})
        if len(area["stations"]) < TOP_PER_AREA:
            area["stations"].append(_station(s))

    best = min(stations, key=lambda s: s["net_price"])
    return {
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "best": {**_station(best), "area": best["area_label"]},
        "home": {
            "cheapest": _station(cheapest_home) if cheapest_home else None,
            "low_90d": {"date": low[0], "price": low[1]} if low else None,
            "decision_note": decision_note,
        },
        "trend": {"norwalk": norwalk, "nj_costco": nj_costco},
        "areas": list(areas.values()),
        "prediction_lines": list(prediction_lines),
        "outlook_lines": list(outlook_lines),
    }


def write(stations: list[dict], fetched_at: datetime, history: list[dict], path: Path | None = None, **kw) -> dict:
    board = build(stations, fetched_at, history, **kw)
    path = Path(path or DASHBOARD_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False) + "\n")
    return board
