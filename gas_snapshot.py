"""Hand-off file between the Mac fetcher and the cloud gas pipeline.

GasBuddy turns away GitHub's runners (Oct 2026), so the Mac fetches prices
(`gas_tracker.py --fetch-only`), writes data/gas_snapshot.json and pushes it. That push
starts the gas workflow, which reads the snapshot instead of fetching. Stdlib only, so
the daily watchdog can run it without installing anything.

    python gas_snapshot.py age                  # minutes since the last fetch (Mac scheduler)
    python gas_snapshot.py check --max-age-h 24 # exit 1 when the Mac has gone quiet (watchdog)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SNAPSHOT_PATH = Path(__file__).parent / "data" / "gas_snapshot.json"
MAX_AGE_H = 6          # the pipeline refuses older prices; the Mac fetches about every 3 h
CATCHUP_MAX_AGE_H = 12  # Friday/Sunday catch-up runs accept an older snapshot (Mac asleep)
PRICE_MAX_AGE_H = 12    # a single price older than this is never shown (Ajith's rule)
ET = ZoneInfo("America/New_York")


def posted_at(station: dict, fetched_at: datetime) -> datetime | None:
    """When GasBuddy says the price was posted. The Mac writes 'Nov 05, 09:05 PM' in ET, no year."""
    try:
        t = datetime.strptime(station.get("last_updated") or "", "%b %d, %I:%M %p")
    except ValueError:
        return None
    local = fetched_at.astimezone(ET)
    stamp = t.replace(year=local.year, tzinfo=ET)
    if stamp > local + timedelta(days=1):  # a December price read in January
        stamp = stamp.replace(year=local.year - 1)
    return stamp


def still_fresh(stations: list[dict], fetched_at: datetime, now: datetime) -> list[dict]:
    """Stations whose price is under PRICE_MAX_AGE_H old *now*, not just when the Mac fetched it."""
    keep = []
    for s in stations:
        posted = posted_at(s, fetched_at)
        if posted is not None and now - posted <= timedelta(hours=PRICE_MAX_AGE_H):
            keep.append(s)
    return keep


def save(stations: list[dict], path: Path | None = None, now: datetime | None = None) -> dict:
    path = Path(path or SNAPSHOT_PATH)
    snap = {"fetched_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "source": "mac", "stations": stations}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1) + "\n")
    return snap


def load(path: Path | None = None) -> dict | None:
    try:
        snap = json.loads(Path(path or SNAPSHOT_PATH).read_text())
        datetime.fromisoformat(snap["fetched_at"])
        return snap if isinstance(snap.get("stations"), list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def age_hours(snap: dict | None, now: datetime | None = None) -> float:
    """Hours since the snapshot was fetched; infinite when there is none."""
    if not snap:
        return float("inf")
    return ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(snap["fetched_at"])).total_seconds() / 3600


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("age")
    chk = sub.add_parser("check")
    chk.add_argument("--max-age-h", type=float, default=24)
    args = ap.parse_args(argv)

    snap = load()
    hours = age_hours(snap)
    if args.cmd == "age":
        print(10**9 if hours == float("inf") else int(hours * 60))
        return 0
    if hours > args.max_age_h:
        when = snap["fetched_at"] if snap else "never"
        print(f"Gas snapshot is stale: last Mac fetch {when} ({hours:.0f} h ago). "
              f"Is the Mac asleep or offline, or is GasBuddy turning it away too? See ~/Library/Logs/costco-fuel-notifier/gas-fetch.log on the Mac.")
        return 1
    print(f"Gas snapshot OK: fetched {snap['fetched_at']} ({hours:.1f} h ago), {len(snap['stations'])} stations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
