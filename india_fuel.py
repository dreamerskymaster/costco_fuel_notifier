"""Live Indian retail fuel prices, plus a small daily history kept in the repo.

`priceapi.indiatoday.in` stopped answering in 2026 and every caller silently fell
back to hard-coded numbers (Mumbai petrol shown at ₹104.21 while pumps charged
₹111.21). Sources now, both free and key-less:

    goodreturns.in   today, the last 10 days and per-month first/last prices
    bankbazaar.com   today only (fallback; also the only one with Goa)

A value outside a plausible band, or diesel priced above petrol, is rejected and
the caller is told the price is unverified rather than shown a wrong number.
"""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
HISTORY_CSV = Path(__file__).parent / "data" / "india_fuel_history.csv"
HISTORY_FIELDS = ["date", "city", "petrol", "diesel", "source"]
PLAUSIBLE = {"petrol": (85.0, 140.0), "diesel": (75.0, 130.0)}

# Cities goodreturns covers; anything else goes straight to bankbazaar.
GOODRETURNS = {"mumbai", "pune", "thane", "nagpur", "nashik"}


@dataclass
class CityPrice:
    city: str
    fuel: str
    price: float
    source: str
    daily: list[tuple[date, float]] = field(default_factory=list)          # newest first
    months: list[tuple[date, float, float]] = field(default_factory=list)  # (month, first, last), newest first


def _text(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    r.encoding = "utf-8"          # bankbazaar omits the charset, and ₹ then decodes as Latin-1
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", r.text, flags=re.S)
    body = html.unescape(re.sub(r"<[^>]+>", "|", body))
    return re.sub(r"\s+", " ", re.sub(r"(\|\s*)+", "|", body))


def _plausible(fuel: str, value: float | None) -> bool:
    lo, hi = PLAUSIBLE[fuel]
    return value is not None and lo <= value <= hi


def parse_goodreturns(text: str, city: str, fuel: str) -> CityPrice | None:
    daily = [
        (datetime.strptime(d, "%B %d, %Y").date(), float(p))
        for d, p in re.findall(r"\|(\w+ \d{1,2}, 20\d\d)\|₹ ?([\d.]+)\|", text)
    ]
    months = [
        (datetime.strptime(m, "%B %Y").date(), float(first), float(last))
        for m, first, last in re.findall(
            r"Rate in [\w ]+, (\w+ 20\d\d)\|Details\|Price\|1\|st\|\w+ ?\|₹ ?([\d.]+)\|\d+\|\w+\|\w+ ?\|₹ ?([\d.]+)",
            text,
        )
    ]
    if not daily or not _plausible(fuel, daily[0][1]):
        return None
    return CityPrice(city, fuel, daily[0][1], "goodreturns.in", daily, months)


def parse_bankbazaar(text: str, city: str, fuel: str) -> CityPrice | None:
    m = re.search(rf"Today[’']s {fuel} Price in [\w ]+\|₹ ?([\d.]+)", text, re.I)
    value = float(m.group(1)) if m else None
    if not _plausible(fuel, value):
        return None
    return CityPrice(city, fuel, value, "bankbazaar.com")


def fetch(city: str, fuel: str) -> CityPrice | None:
    """Today's price for one city and fuel ('petrol'/'diesel'), or None if no source is believable."""
    slug = city.lower().replace(" ", "-")
    if slug in GOODRETURNS:
        text = _text(f"https://www.goodreturns.in/{fuel}-price-in-{slug}.html")
        found = parse_goodreturns(text, city, fuel) if text else None
        if found:
            return found
    text = _text(f"https://www.bankbazaar.com/fuel/{fuel}-price-{slug}.html")
    return parse_bankbazaar(text, city, fuel) if text else None


def fetch_city(city: str) -> dict[str, CityPrice]:
    """{'petrol': CityPrice, 'diesel': CityPrice}, dropping whichever is missing or implausible.

    Diesel has been cheaper than petrol in every Indian city for years, so a diesel
    quote above petrol is a scraping glitch (bankbazaar showed Goa diesel at ₹113.51).
    """
    out = {fuel: p for fuel in ("petrol", "diesel") if (p := fetch(city, fuel))}
    if "petrol" in out and "diesel" in out and out["diesel"].price > out["petrol"].price + 2:
        out.pop("diesel")
    return out


def load_history(city: str | None = None) -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open() as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if city is None or r["city"] == city]


def record(city: str, prices: dict[str, CityPrice], today: date | None = None) -> None:
    """Upsert today's row for `city` into data/india_fuel_history.csv."""
    if not prices:
        return
    today = today or date.today()
    rows = [r for r in load_history() if not (r["date"] == today.isoformat() and r["city"] == city)]
    rows.append({
        "date": today.isoformat(),
        "city": city,
        "petrol": f"{prices['petrol'].price:.2f}" if "petrol" in prices else "",
        "diesel": f"{prices['diesel'].price:.2f}" if "diesel" in prices else "",
        "source": "+".join(sorted({p.source for p in prices.values()})),
    })
    rows.sort(key=lambda r: (r["date"], r["city"]))
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- outlook

REVISION_RS = 0.50     # moves smaller than this are local tax/dealer noise, not an oil-company revision


@dataclass
class IndiaOutlook:
    city: str
    fuel: str
    price: float
    stable_days: int | None          # days at today's price in the 10-day table (10 = at least 10)
    last_revision: date | None       # month of the last revision >= REVISION_RS
    revision_rs: float | None        # size of that revision
    months_seen: int
    months_with_revision: int
    crude_change_pct: float | None   # Brent in rupees now vs the month of the last revision
    lines: list[str]


def outlook(cp: CityPrice, brent_usd: dict[date, float] | None = None,
            usdinr: dict[str, float] | None = None, today: date | None = None) -> IndiaOutlook:
    """Rule-based: Indian pump prices are administered and change a few times a year,
    far too rarely to train a model on, so the pressure gauge is crude oil in rupees."""
    today = today or date.today()
    stable = None
    if cp.daily:
        stable = next((i for i, (_, p) in enumerate(cp.daily) if abs(p - cp.price) >= REVISION_RS), len(cp.daily))
    revs = [(m, last - first) for m, first, last in cp.months if abs(last - first) >= REVISION_RS]
    last_rev, rev_rs = (revs[0] if revs else (None, None))

    crude = None
    if brent_usd and usdinr and last_rev:
        def inr_brent(start: date, end: date) -> float | None:
            vals = [v * _rate_on(usdinr, d) for d, v in brent_usd.items() if start <= d <= end and _rate_on(usdinr, d)]
            return sum(vals) / len(vals) if vals else None
        month_end = date(last_rev.year + last_rev.month // 12, last_rev.month % 12 + 1, 1) - timedelta(days=1)
        then = inr_brent(last_rev, month_end)
        now = inr_brent(today - timedelta(days=21), today)
        crude = (now / then - 1) * 100 if then and now else None

    label = cp.fuel.capitalize()
    lines = []
    if stable is not None and stable >= len(cp.daily):
        lines.append(f"{label} has held at ₹{cp.price:.2f} for at least {stable} days. Prices here are set by the "
                     "oil companies, so there is no cheaper day to wait for: fill whenever you need to.")
    elif stable is not None:
        lines.append(f"{label} changed {stable} day(s) ago and is now ₹{cp.price:.2f}.")
    if cp.months:
        p_week = 1 - (1 - len(revs) / len(cp.months)) ** (7 / 30)
        rev_txt = (f" Last revision: {'+' if rev_rs > 0 else '−'}₹{abs(rev_rs):.2f} in {last_rev:%B %Y}."
                   if last_rev else "")
        lines.append(f"Chance of a revision in the next 7 days: about {p_week:.0%} "
                     f"({len(revs)} revision(s) in the last {len(cp.months)} months).{rev_txt}")
    if crude is not None:
        if crude >= 8:
            gauge = "the pressure is upward: if prices are revised, a hike is likelier than a cut, so keep the tank fuller"
        elif crude <= -8:
            gauge = "there is room for a cut, though oil companies often hold prices to recover past losses"
        else:
            gauge = "there is no strong pressure either way"
        lines.append(f"Crude oil in rupees is {crude:+.0f}% versus {last_rev:%B %Y}, so {gauge}.")
    lines.append("Rule-based outlook, not ML: pump prices change too rarely to train a model on.")
    return IndiaOutlook(cp.city, cp.fuel, cp.price, stable, last_rev, rev_rs,
                        len(cp.months), len(revs), crude, lines)


def _rate_on(usdinr: dict[str, float], d: date) -> float | None:
    for back in range(7):
        v = usdinr.get((d - timedelta(days=back)).isoformat())
        if v:
            return v
    return None


def outlook_html(o: IndiaOutlook) -> str:
    body = "".join(f"• {html.escape(line)}<br>" for line in o.lines[:-1])
    return f"""
          <div style="padding: 14px 18px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; color: #334155; font-size: 12px; line-height: 1.6;">
            <div style="font-weight: 700; font-size: 13px; color: #0f172a; margin-bottom: 4px;">🔮 Should you fill up today? ({o.city} {o.fuel})</div>
            {body}
            <div style="font-size: 11px; color: #64748b; margin-top: 4px;">{html.escape(o.lines[-1])} Source: goodreturns.in; crude from EIA Brent and ECB USD/INR.</div>
          </div>"""
