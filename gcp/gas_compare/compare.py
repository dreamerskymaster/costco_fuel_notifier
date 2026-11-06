#!/usr/bin/env python3
"""Two-week side-by-side test: Google Maps fuel prices vs the Mac's GasBuddy fetch.

Runs as the Cloud Run job `gas-compare` (project ajith-automations) at 7:30 AM and
5:30 PM New York time. Each run:

  1. reads the Mac's latest GasBuddy snapshot (data/gas_snapshot.json on GitHub),
  2. asks Google Places for gas stations with prices in the same 11 corridor ZIPs
     (11 Text Search calls; a quota caps the project at 30 a day, inside the free tier),
  3. stores a summary of both in gs://$STATE_BUCKET/runs/, and
  4. after noon, emails one comparison per day to TO_EMAIL, plus a final verdict
     on the last day. After the test window it does nothing.

It sends no gas alerts and changes nothing in the real pipeline (gas_tracker.py).
Only the standard library is used, so the container stays tiny.

Environment:
  STATE_BUCKET          bucket for run summaries (required)
  TEST_START, TEST_END  ISO dates of the window (defaults 2026-11-06 .. 2026-11-20)
  SMTP_USER, SMTP_PASSWORD, TO_EMAIL   from the gas-compare-env secret
  GOOGLE_CLOUD_PROJECT  project billed for Places calls (default ajith-automations)
  GCP_ACCESS_TOKEN      only for running on a laptop (gcloud auth print-access-token)
"""

from __future__ import annotations

import json
import os
import smtplib
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# Same corridor as gas_tracker.ZIP_CODES (a test keeps them in sync).
ZIP_LABELS = {
    "06854": "Norwalk (Home)",
    "06901": "Stamford (I-95)",
    "10801": "New Rochelle (Costco)",
    "07657": "Ridgefield (Rt 46 / GWB Exit)",
    "07608": "Teterboro (Costco)",
    "07601": "Hackensack (Rt 17)",
    "07047": "North Bergen (Tonnelle Ave)",
    "07032": "Kearny (Rt 7)",
    "07306": "Jersey City (Westside Ave)",
    "07305": "Jersey City (Route 440)",
    "07002": "Bayonne (Costco)",
}
COSTCO_SITES = {"07608": "Teterboro", "07002": "Bayonne", "10801": "New Rochelle"}
FRESH_HOURS = 12  # the real pipeline hides prices older than this
SNAPSHOT_MAX_AGE_HOURS = 6  # the real pipeline refuses older Mac snapshots
SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/dreamerskymaster/costco_fuel_notifier/main/"
    "data/gas_snapshot.json"
)
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = (
    "places.id,places.displayName,places.formattedAddress,places.fuelOptions.fuelPrices"
)
GCS = "https://storage.googleapis.com"
METADATA = "http://metadata.google.internal/computeMetadata/v1"
CONSOLE = "https://console.cloud.google.com/run/jobs/details/us-central1/gas-compare/executions?project=ajith-automations"


def log(msg: str) -> None:
    print(f"[gas-compare] {msg}", flush=True)


# -- HTTP helpers ----------------------------------------------------------------


def http(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
         timeout: int = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def access_token() -> str:
    if os.environ.get("GCP_ACCESS_TOKEN"):
        return os.environ["GCP_ACCESS_TOKEN"]
    status, body = http(
        "GET", f"{METADATA}/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"}, timeout=10,
    )
    if status != 200:
        raise RuntimeError(f"metadata token: HTTP {status}")
    return json.loads(body)["access_token"]


class Bucket:
    def __init__(self, name: str, token: str):
        self.name, self.auth = name, {"Authorization": f"Bearer {token}"}

    def _url(self, obj: str) -> str:
        return f"{GCS}/storage/v1/b/{self.name}/o/{urllib.parse.quote(obj, safe='')}"

    def get(self, obj: str) -> bytes | None:
        status, body = http("GET", self._url(obj) + "?alt=media", headers=self.auth)
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"read gs://{self.name}/{obj}: HTTP {status}")
        return body

    def put(self, obj: str, data: bytes, content_type: str = "application/json") -> None:
        q = urllib.parse.urlencode({"uploadType": "media", "name": obj})
        status, body = http(
            "POST", f"{GCS}/upload/storage/v1/b/{self.name}/o?{q}", body=data,
            headers={**self.auth, "Content-Type": content_type},
        )
        if status != 200:
            raise RuntimeError(f"write gs://{self.name}/{obj}: HTTP {status} {body[:200]!r}")

    def list(self, prefix: str) -> list[str]:
        names, token = [], None
        while True:
            q = {"prefix": prefix, "fields": "items(name),nextPageToken"}
            if token:
                q["pageToken"] = token
            status, body = http(
                "GET", f"{GCS}/storage/v1/b/{self.name}/o?{urllib.parse.urlencode(q)}",
                headers=self.auth,
            )
            if status != 200:
                raise RuntimeError(f"list gs://{self.name}/{prefix}: HTTP {status}")
            d = json.loads(body)
            names += [i["name"] for i in d.get("items", [])]
            token = d.get("nextPageToken")
            if not token:
                return sorted(names)


# -- the two sources -----------------------------------------------------------------


def parse_gasbuddy_time(text: str, fetched_at: datetime) -> datetime | None:
    """gas_tracker writes 'Nov 05, 09:05 PM' in Mac (New York) time, without a year."""
    try:
        t = datetime.strptime(text, "%b %d, %I:%M %p")
    except (TypeError, ValueError):
        return None
    local = fetched_at.astimezone(NY)
    stamp = t.replace(year=local.year, tzinfo=NY)
    if stamp > local + timedelta(days=1):  # a December price read in January
        stamp = stamp.replace(year=local.year - 1)
    return stamp


def gasbuddy_stations(snapshot: dict) -> list[dict]:
    fetched = datetime.fromisoformat(snapshot["fetched_at"])
    out = []
    for s in snapshot.get("stations", []):
        posted = parse_gasbuddy_time(s.get("last_updated", ""), fetched)
        out.append({"name": s["name"], "zip": s["zip"], "price": float(s["price"]),
                    "posted": posted.isoformat() if posted else None})
    return out


def money(m: dict) -> float:
    return int(m.get("units", 0) or 0) + (m.get("nanos", 0) or 0) / 1e9


def google_stations(places_by_zip: dict[str, list[dict]]) -> list[dict]:
    """One row per place (first ZIP it appeared under); price None when Google has none."""
    seen, out = set(), []
    for zip_code, places in places_by_zip.items():
        for p in places:
            if p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            regular = next(
                (f for f in (p.get("fuelOptions") or {}).get("fuelPrices", [])
                 if f.get("type") == "REGULAR_UNLEADED"),
                None,
            )
            out.append({
                "name": (p.get("displayName") or {}).get("text", "?"),
                "zip": zip_code,
                "address": p.get("formattedAddress", ""),
                "price": round(money(regular["price"]), 3) if regular else None,
                "posted": regular.get("updateTime") if regular else None,
            })
    return out


def fetch_google(token: str, project: str) -> dict[str, list[dict]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project,
        "X-Goog-FieldMask": PLACES_FIELDS,
        "Content-Type": "application/json",
    }
    results = {}
    for zip_code in ZIP_LABELS:
        body = json.dumps({"textQuery": f"gas station {zip_code}", "includedType": "gas_station",
                           "strictTypeFiltering": True, "pageSize": 20}).encode()
        status, raw = http("POST", PLACES_URL, headers=headers, body=body)
        if status != 200:
            raise RuntimeError(f"Places {zip_code}: HTTP {status} {raw[:200]!r}")
        results[zip_code] = json.loads(raw).get("places", [])
    return results


# -- comparing ---------------------------------------------------------------------


def age_hours(posted: str | None, now: datetime) -> float | None:
    if not posted:
        return None
    return (now - datetime.fromisoformat(posted.replace("Z", "+00:00"))).total_seconds() / 3600


def summarize(stations: list[dict], now: datetime) -> dict:
    priced = [dict(s, age=age_hours(s["posted"], now)) for s in stations if s["price"]]
    fresh = [s for s in priced if s["age"] is not None and s["age"] <= FRESH_HOURS]
    ages = [s["age"] for s in priced if s["age"] is not None]
    cheapest = min(fresh, key=lambda s: s["price"], default=None)
    costco = {}
    for zip_code, site in COSTCO_SITES.items():
        hits = [s for s in stations if "costco" in s["name"].lower()
                and (s["zip"] == zip_code or zip_code in s.get("address", ""))]
        with_price = [dict(s, age=age_hours(s["posted"], now)) for s in hits if s["price"]]
        best = min(with_price, key=lambda s: s["age"] if s["age"] is not None else 1e9,
                   default=None)
        costco[site] = (
            {"price": best["price"], "age_h": round(best["age"], 1) if best["age"] is not None
             else None} if best else ({"listed_without_price": True} if hits else None)
        )
    return {
        "stations": len(stations),
        "priced": len(priced),
        "fresh": len(fresh),
        "zips_fresh": len({s["zip"] for s in fresh}),
        "median_age_h": round(statistics.median(ages), 1) if ages else None,
        "cheapest_fresh": (
            {"name": cheapest["name"], "zip": cheapest["zip"], "price": cheapest["price"]}
            if cheapest else None
        ),
        "costco": costco,
    }


def costco_fresh(summary: dict | None, site: str) -> bool:
    c = (summary or {}).get("costco", {}).get(site) or {}
    return c.get("age_h") is not None and c["age_h"] <= FRESH_HOURS


def scorecard(records: list[dict]) -> list[dict]:
    """Rows of (metric, gasbuddy, google, winner) across all runs so far."""
    def runs(src):
        return [r[src] for r in records]

    def avg(src, key):
        vals = [s[key] for s in runs(src) if s.get("available") and s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def rate(src, pred):
        rs = runs(src)
        return round(100 * sum(1 for s in rs if pred(s)) / len(rs)) if rs else None

    rows = [
        ("Runs with usable data (%)", rate("gasbuddy", lambda s: s.get("available")),
         rate("google", lambda s: s.get("available")), "high"),
        ("Stations with a price under 12 h old (avg)", avg("gasbuddy", "fresh"),
         avg("google", "fresh"), "high"),
        ("ZIPs (of 11) with a fresh price (avg)", avg("gasbuddy", "zips_fresh"),
         avg("google", "zips_fresh"), "high"),
        ("Median price age, hours", avg("gasbuddy", "median_age_h"),
         avg("google", "median_age_h"), "low"),
    ]
    for site in COSTCO_SITES.values():
        rows.append((f"Costco {site}: fresh price (% of runs)",
                     rate("gasbuddy", lambda s, x=site: s.get("available") and costco_fresh(s, x)),
                     rate("google", lambda s, x=site: s.get("available") and costco_fresh(s, x)),
                     "high"))
    out = []
    for metric, gb, gg, better in rows:
        if gb is None or gg is None or gb == gg:
            winner = "tie" if gb == gg and gb is not None else "—"
        elif better == "high":
            winner = "GasBuddy" if gb > gg else "Google"
        else:
            winner = "GasBuddy" if gb < gg else "Google"
        out.append({"metric": metric, "gasbuddy": gb, "google": gg, "winner": winner})
    return out


def verdict(card: list[dict]) -> str:
    gb = sum(1 for r in card if r["winner"] == "GasBuddy")
    gg = sum(1 for r in card if r["winner"] == "Google")
    costco_rows = [r for r in card if r["metric"].startswith("Costco")]
    costco_gb = sum(1 for r in costco_rows if r["winner"] == "GasBuddy")
    costco_gg = sum(1 for r in costco_rows if r["winner"] == "Google")
    lead = "GasBuddy (Mac)" if gb > gg else "Google Maps" if gg > gb else "Neither source"
    costco = ("GasBuddy" if costco_gb > costco_gg else "Google" if costco_gg > costco_gb
              else "neither")
    return (f"{lead} leads on {max(gb, gg)} of {len(card)} measures; "
            f"on Costco prices specifically, {costco} is ahead.")


# -- email ---------------------------------------------------------------------------


def fmt(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


def costco_cell(s: dict | None, site: str) -> str:
    c = (s or {}).get("costco", {}).get(site)
    if not c:
        return "not listed"
    if c.get("listed_without_price"):
        return "listed, no price"
    age = c.get("age_h")
    return f"${c['price']:.2f} ({fmt(age, ' h')} old)"


def build_email(records: list[dict], today: date, start: date, end: date, final: bool):
    latest = records[-1]
    day_n = (today - start).days + 1
    total = (end - start).days + 1
    card = scorecard(records)
    head = ("Final verdict" if final else f"Day {day_n} of {total}")
    subject = f"⛽ Gas price source test: {head} (Google Maps vs GasBuddy)"

    th = 'style="text-align:left;padding:6px 10px;border-bottom:1px solid #e2e8f0;"'
    td = 'style="padding:6px 10px;border-bottom:1px solid #f1f5f9;"'

    def table(header, rows):
        h = "".join(f"<th {th}>{escape(c)}</th>" for c in header)
        b = "".join("<tr>" + "".join(f"<td {td}>{escape(str(c))}</td>" for c in r) + "</tr>"
                    for r in rows)
        return f'<table style="border-collapse:collapse;font-size:13px;">{h and "<tr>"+h+"</tr>"}{b}</table>'

    gbs, ggs = latest["gasbuddy"], latest["google"]
    run_at = datetime.fromisoformat(latest["at"]).astimezone(NY).strftime("%b %d, %I:%M %p")
    snap_note = (f"Mac snapshot was {fmt(gbs.get('snapshot_age_h'), ' h')} old"
                 + ("" if gbs.get("available") else " (too old: the real email would skip it)"))
    latest_rows = [
        ("Stations with any price", gbs.get("priced"), ggs.get("priced")),
        ("…with a price under 12 h old", gbs.get("fresh"), ggs.get("fresh")),
        ("ZIPs (of 11) with a fresh price", gbs.get("zips_fresh"), ggs.get("zips_fresh")),
        ("Median price age", fmt(gbs.get("median_age_h"), " h"), fmt(ggs.get("median_age_h"), " h")),
        *[(f"Costco {site}", costco_cell(gbs, site), costco_cell(ggs, site))
          for site in COSTCO_SITES.values()],
        ("Cheapest fresh price",
         *(f"${c['price']:.2f} {c['name']} ({c['zip']})" if (c := s.get("cheapest_fresh")) else "—"
           for s in (gbs, ggs))),
    ]
    card_rows = [(r["metric"], fmt(r["gasbuddy"]), fmt(r["google"]), r["winner"]) for r in card]

    intro = (
        "The two-week test is over. Here is how the two sources compared across every run."
        if final else
        "Your normal gas emails are unchanged. This test only compares where the prices come from."
    )
    tail = (
        "<p><b>What to do now:</b> reply to Claude with which source you want. If Google wins, the "
        "Mac is no longer needed for gas: the fetch moves into the cloud pipeline. If GasBuddy "
        "wins, nothing changes. The test has stopped calling Google; delete it with "
        "<code>gcloud scheduler jobs delete gas-compare-twice-daily --location us-central1 "
        "--project ajith-automations</code>.</p>"
        if final else
        f"<p style='color:#64748b'>Test runs {start:%b %d}–{end:%b %d}, then sends a final verdict "
        "and stops by itself.</p>"
    )
    html = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:680px;color:#0f172a">
<h2 style="margin:0 0 4px">{escape(head)}: which gas price source is better?</h2>
<p style="margin:0 0 14px;color:#475569">{escape(intro)}</p>
<h3 style="margin:16px 0 6px">So far ({len(records)} runs)</h3>
<p style="margin:0 0 8px"><b>{escape(verdict(card))}</b></p>
{table(["Measure", "GasBuddy (Mac)", "Google Maps", "Better"], card_rows)}
<h3 style="margin:18px 0 6px">Latest run, {escape(run_at)}</h3>
<p style="margin:0 0 8px;color:#475569">{escape(snap_note)}.</p>
{table(["", "GasBuddy (Mac)", "Google Maps"], latest_rows)}
{tail}
<p style="color:#94a3b8;font-size:11px">Prices from Google Maps and GasBuddy. Run history:
<a href="{CONSOLE}">Cloud Run job gas-compare</a> (project ajith-automations).</p></div>"""
    text = (f"{head}: {verdict(card)}\n\n"
            + "\n".join(f"{m}: GasBuddy {g} | Google {o} -> {w}" for m, g, o, w in card_rows))
    return subject, text, html


def send_email(subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ["SMTP_USER"], os.environ["TO_EMAIL"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


# -- the run -------------------------------------------------------------------------


def load_dotenv_secret() -> None:
    """GAS_COMPARE_ENV (from Secret Manager) holds KEY=VALUE lines; fill missing env vars."""
    for line in os.environ.get("GAS_COMPARE_ENV", "").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run(args: list[str]) -> int:
    load_dotenv_secret()
    now = datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    start = date.fromisoformat(os.environ.get("TEST_START", "2026-11-06"))
    end = date.fromisoformat(os.environ.get("TEST_END", "2026-11-20"))
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "ajith-automations")
    token = access_token()
    bucket = Bucket(os.environ["STATE_BUCKET"], token)

    if today > end:
        log(f"test window ended {end}; nothing to do")
        return 0

    record = {"at": now.isoformat(timespec="seconds")}
    status, raw = http("GET", SNAPSHOT_URL)
    if status == 200:
        snap = json.loads(raw)
        snap_age = (now - datetime.fromisoformat(snap["fetched_at"])).total_seconds() / 3600
        gb = summarize(gasbuddy_stations(snap), now)
        gb.update(available=snap_age <= SNAPSHOT_MAX_AGE_HOURS, snapshot_age_h=round(snap_age, 1))
    else:
        gb = {"available": False, "error": f"snapshot HTTP {status}"}
    record["gasbuddy"] = gb

    try:
        gg = summarize(google_stations(fetch_google(token, project)), now)
        gg["available"] = gg["fresh"] > 0
    except Exception as exc:
        gg = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    record["google"] = gg

    stamp = now.astimezone(NY).strftime("%Y-%m-%dT%H%M")
    bucket.put(f"runs/{stamp}.json", json.dumps(record, indent=1).encode())
    log(f"GasBuddy fresh={gb.get('fresh')} available={gb.get('available')} | "
        f"Google fresh={gg.get('fresh')} available={gg.get('available')} "
        f"{gg.get('error', '')}")

    final = today == end
    want_email = "--no-email" not in args and now.astimezone(NY).hour >= 12
    marker = f"emails/{today.isoformat()}"
    if want_email and bucket.get(marker) is None:
        records = [json.loads(bucket.get(n) or b"{}") for n in bucket.list("runs/")]
        records = [r for r in records if "gasbuddy" in r and "google" in r]
        subject, text, html = build_email(records, today, start, end, final)
        send_email(subject, text, html)
        bucket.put(marker, b"sent", "text/plain")
        log(f"emailed: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
