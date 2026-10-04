"""Should I fill up today? A week-ahead outlook for NY/NJ regular gasoline.

Retail pump prices follow wholesale gasoline with a lag of one to three weeks, and
fall more slowly than they rise ("rockets and feathers"). So last week's wholesale
move, and how wide the retail-over-wholesale margin is versus its own past year,
say a lot about where the pump price goes next week. That is the whole model:

    target   : does the Central Atlantic (PADD 1B: NY, NJ, PA, DE, MD) weekly retail
               average rise next week?   (logistic)   and by how many cents? (ridge)
    features : retail change over the last 1 and 2 weeks, New York Harbor wholesale
               change over 1, 2 and 4 weeks, and the margin's gap to its 52-week mean

Data: EIA weekly series, free and key-less, back to 1993. Walk-forward from 2008
(every prediction made by a model fitted only on earlier weeks) it called the
direction right ~77% of the time against ~56% for always guessing the usual
direction. The email prints that record beside every call.

Station-level prices from GasBuddy are logged to data/us_station_history.csv on
each run, so "is today's price low?" uses your own stations once a week of history
exists, and the regional EIA series until then.
"""

from __future__ import annotations

import bisect
import csv
import html as html_lib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import requests

import ml_core

EIA_URL = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx"
RETAIL_SERIES = "EMM_EPMR_PTE_R1Y_DPG"      # Weekly Central Atlantic (PADD 1B) regular retail, $/gal
WHOLESALE_SERIES = "EER_EPMRU_PF4_Y35NY_DPG"  # Weekly NY Harbor conventional regular spot, $/gal
CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_TTL_HOURS = 12
STATION_CSV = Path(__file__).parent / "data" / "us_station_history.csv"
STATION_FIELDS = ["date", "zip", "name", "price"]
FIRST_YEAR = 1997
EVAL_FROM = 2008
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


# --------------------------------------------------------------------------- data

def parse_eia_weekly(page: str) -> dict[date, float]:
    """EIA LeafHandler weekly table -> {week date: value}. Rows are 'YYYY-Mon' then (MM/DD, value) pairs."""
    out: dict[date, float] = {}
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = [html_lib.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if not cells or not re.match(r"\d{4}-[A-Z][a-z]{2}$", cells[0]):
            continue
        year, month = int(cells[0][:4]), _MONTHS[cells[0][5:]]
        for day, value in zip(cells[1::2], cells[2::2]):
            if not re.match(r"\d\d/\d\d$", day) or not value:
                continue
            mm, dd = int(day[:2]), int(day[3:])
            out[date(year + (1 if month == 12 and mm == 1 else 0), mm, dd)] = float(value)
    return dict(sorted(out.items()))


def fetch_eia_weekly(series: str) -> dict[date, float]:
    """Weekly EIA series, cached on disk; a failed fetch falls back to any cached copy."""
    path = CACHE_DIR / f"eia_{series}.json"
    cached = json.loads(path.read_text()) if path.exists() else None
    if cached and time.time() - cached["_at"] < CACHE_TTL_HOURS * 3600:
        return {date.fromisoformat(k): v for k, v in cached["data"].items()}
    try:
        r = requests.get(EIA_URL, params={"n": "PET", "s": series, "f": "W"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        data = parse_eia_weekly(r.text)
        if len(data) < 200:
            raise ValueError(f"only {len(data)} rows parsed")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"_at": time.time(), "data": {k.isoformat(): v for k, v in data.items()}}))
        return data
    except (requests.RequestException, ValueError) as exc:
        if cached:
            print(f"warning: EIA {series} fetch failed ({exc}); using cached copy", file=sys.stderr)
            return {date.fromisoformat(k): v for k, v in cached["data"].items()}
        raise


# --------------------------------------------------------------------------- model

def build_dataset(retail: dict[date, float], wholesale: dict[date, float]):
    """Features for every retail week; the last row (today's call) has no target."""
    rd = [d for d in retail if d.year >= FIRST_YEAR]
    wd = sorted(wholesale)
    R = np.array([retail[d] for d in rd])
    # Wholesale as known when the Monday retail survey is published: the week ending the Friday before.
    W = np.array([wholesale[wd[bisect.bisect_right(wd, d - timedelta(days=1)) - 1]] for d in rd])
    X, y, move, weeks = [], [], [], []
    for t in range(56, len(rd)):
        margin = R[t - 52:t] - W[t - 52:t]
        X.append([R[t] - R[t - 1], R[t - 1] - R[t - 2], W[t] - W[t - 1], W[t - 1] - W[t - 2],
                  W[t] - W[t - 4], (R[t] - W[t]) - margin.mean()])
        y.append(float(R[t + 1] > R[t]) if t + 1 < len(rd) else np.nan)
        move.append(R[t + 1] - R[t] if t + 1 < len(rd) else np.nan)
        weeks.append(rd[t])
    return np.array(X), np.array(y), np.array(move), weeks, R[56:]


@dataclass
class UsOutlook:
    week: date                   # latest EIA retail week the call is made from
    regional_price: float
    p_up: float
    expected_cents: float        # ridge forecast of next week's change, ¢/gal
    band_cents: float            # out-of-sample mean absolute error of that forecast
    confidence: str
    record: tuple[float, int] | None
    wf: ml_core.WalkForward
    regional_pct_52w: float      # share of the last 52 weeks priced at or above today's level
    station_pct: float | None    # same idea on your logged station prices, if enough history
    station_days: int
    verdict: str
    detail: str


def forecast(best_price: float | None = None, tank_gal: float = 18.5,
             retail: dict | None = None, wholesale: dict | None = None,
             station_history: list[dict] | None = None, best_station: str | None = None) -> UsOutlook:
    retail = retail or fetch_eia_weekly(RETAIL_SERIES)
    wholesale = wholesale or fetch_eia_weekly(WHOLESALE_SERIES)
    X, y, move, weeks, R = build_dataset(retail, wholesale)
    train = ~np.isnan(y)
    Xt, yt, mt = X[train], y[train], move[train]
    start = next(i for i, d in enumerate(weeks) if d.year >= EVAL_FROM)
    wf = ml_core.walk_forward(Xt, yt, start, step=4, l2=1.0)

    p_up = float(ml_core.predict_logit(wf.weights, wf.scaler(X[-1:]))[0])
    oos = np.full(len(mt), np.nan)
    for t in range(start, len(mt), 4):
        sc = ml_core.Scaler.fit(Xt[:t])
        oos[t:t + 4] = ml_core.predict_ridge(ml_core.fit_ridge(sc(Xt[:t]), mt[:t]), sc(Xt[t:t + 4]))
    mae = float(np.nanmean(np.abs(oos[start:] - mt[start:])))
    sc = ml_core.Scaler.fit(Xt)
    expected = float(ml_core.predict_ridge(ml_core.fit_ridge(sc(Xt), mt), sc(X[-1:]))[0])

    regional_pct = float(np.mean(R[-52:] >= R[-1]) * 100)
    station_pct, station_days = _station_percentile(station_history, best_station, best_price)

    confidence = ml_core.confidence_label(p_up, wf)
    verdict, detail = _verdict(p_up, expected * 100, confidence, tank_gal, station_pct, regional_pct)
    return UsOutlook(weeks[-1], float(R[-1]), p_up, expected * 100, mae * 100, confidence,
                     wf.track_record(p_up), wf, regional_pct, station_pct, station_days, verdict, detail)


def _station_percentile(history, station, price):
    if not history or not station or price is None:
        return None, 0
    cutoff = (date.today() - timedelta(days=60)).isoformat()
    by_day: dict[str, float] = {}
    for r in history:
        if r["name"] == station and r["date"] >= cutoff:
            by_day[r["date"]] = min(by_day.get(r["date"], 1e9), float(r["price"]))
    if len(by_day) < 7:
        return None, len(by_day)
    return float(np.mean(np.array(list(by_day.values())) >= price) * 100), len(by_day)


def _verdict(p_up, cents, confidence, tank_gal, station_pct, regional_pct):
    stake = abs(cents) / 100 * tank_gal
    level = station_pct if station_pct is not None else regional_pct
    small = " Small stakes either way." if stake < 1 else ""
    if p_up >= 0.6 and cents >= 1.5:
        verdict = "Fill up today rather than later — prices are likely to rise"
        detail = f"Expect about +{cents:.0f}¢/gal next week; a full {tank_gal:g}-gal fill now saves ~${stake:.2f}.{small}"
    elif p_up <= 0.4 and cents <= -1.5:
        verdict = "Buy only what you need — prices are likely to ease"
        detail = f"Expect about {cents:.0f}¢/gal next week; waiting on a full fill saves ~${stake:.2f}.{small}"
    else:
        verdict = "No strong signal — fill when convenient"
        detail = f"Expected move is small ({cents:+.0f}¢/gal); price and route matter more than timing this week."
    if level >= 70 and not verdict.startswith("Fill"):
        detail += " Today's price is already low versus recent weeks, so filling now is reasonable."
    if confidence == "Low":
        detail += " Low confidence: treat this as a lean, not a call."
    return verdict, detail


# --------------------------------------------------------------------------- station log

def record_stations(stations: list[dict], today: date | None = None) -> None:
    """Upsert today's cheapest listed price per (zip, station) into data/us_station_history.csv."""
    today_s = (today or date.today()).isoformat()
    keep = []
    if STATION_CSV.exists():
        cutoff = ((today or date.today()) - timedelta(days=370)).isoformat()   # rolling year keeps it small
        with STATION_CSV.open() as f:
            keep = [r for r in csv.DictReader(f) if r["date"] != today_s and r["date"] >= cutoff]
    todays: dict[tuple[str, str], float] = {}
    for s in stations:
        key = (s["zip"], s["name"])
        todays[key] = min(todays.get(key, 1e9), float(s["price"]))
    keep += [{"date": today_s, "zip": z, "name": n, "price": f"{p:.3f}"} for (z, n), p in todays.items()]
    keep.sort(key=lambda r: (r["date"], r["zip"], r["name"]))
    STATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    with STATION_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STATION_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(keep)


def load_station_history() -> list[dict]:
    if not STATION_CSV.exists():
        return []
    with STATION_CSV.open() as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- email copy

def outlook_lines(o: UsOutlook) -> list[str]:
    """Plain-text lines: verdict first, model record last. The HTML block renders the same content."""
    direction = "rise" if o.p_up >= 0.5 else "fall"
    p_dir = o.p_up if o.p_up >= 0.5 else 1 - o.p_up
    lines = [
        f"{o.verdict}.",
        f"Next week (NY/NJ average): {p_dir:.0%} chance prices {direction}, expected {o.expected_cents:+.1f}¢/gal "
        f"(typical error ±{o.band_cents:.1f}¢). Confidence: {o.confidence}.",
        o.detail,
    ]
    def tag(pct):
        return "low" if pct >= 70 else "high" if pct <= 30 else "about average"
    if o.station_pct is not None:
        lines.append(f"Is today's price low? It is {tag(o.station_pct)}: cheaper than or equal to "
                     f"{o.station_pct:.0f}% of this station's last {o.station_days} logged days.")
    else:
        lines.append(f"Is today's price low? Regionally it is {tag(o.regional_pct_52w)}: ${o.regional_price:.3f}/gal "
                     f"is cheaper than or equal to {o.regional_pct_52w:.0f}% of the last 52 weeks.")
    record = f" When it leaned this hard before it was right {o.record[0]:.0%} of {o.record[1]} weeks." if o.record else ""
    lines.append(f"Model record since {EVAL_FROM}: right {o.wf.hit_rate:.0%} of {o.wf.n} weeks vs "
                 f"{o.wf.base_hit_rate:.0%} for always guessing the usual direction.{record}")
    return lines


def outlook_html(o: UsOutlook) -> str:
    colour = {"Fill": "#047857", "Buy": "#b45309"}.get(o.verdict.split()[0], "#334155")
    lines = outlook_lines(o)
    rest = "".join(f"<div style='margin-top:4px;'>{html_lib.escape(line)}</div>" for line in lines[1:-1])
    return f"""
      <div style="padding:14px 18px;background-color:#f8fafc;border-bottom:1px solid #e2e8f0;font-size:12px;line-height:1.6;color:#334155;">
        <div style="font-weight:800;font-size:13px;margin-bottom:4px;color:#0f172a;">🔮 ML Fill-Up Outlook (EIA week of {o.week:%b %d})</div>
        <div style="font-weight:700;font-size:14px;color:{colour};">{html_lib.escape(lines[0])}</div>
        {rest}
        <div style="margin-top:6px;font-size:11px;color:#64748b;">{html_lib.escape(lines[-1])} Source: EIA weekly retail &amp; NY Harbor wholesale.</div>
      </div>"""


if __name__ == "__main__":
    o = forecast()
    print("\n".join(outlook_lines(o)))
    print("reliability:", [(f"{lo:.2f}-{hi:.2f}", f"{hit:.0%}", n) for lo, hi, hit, n in o.wf.reliability])
