"""When the US fuel digest is allowed to email.

Prices are checked (and logged) every 3 hours, but an email goes out only when:

    weekend   one Friday departure alert and one Sunday return alert, each at most
              once per day. NJ travel only happens on weekends.
    low       the cheapest NJ net price (where the fill-ups happen) is a new low:
              below every logged day of the last LOW_LOOKBACK_DAYS, and below the
              price of the last low-price email.
    interval  INTERVAL_DAYS have passed since the last weekday digest (routine or low);
              weekend alerts don't reset this clock.
    force     FORCE_EMAIL=true (manual run).

State lives in data/gas_state.json, which the workflow commits. The old gate kept
its memory in data/thread_state.json, which is git-ignored, so every run started
blank, thought the price had moved, and emailed (Monday mornings included).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

STATE_JSON = Path(__file__).parent / "data" / "gas_state.json"
INTERVAL_DAYS = 10
LOW_LOOKBACK_DAYS = 60
LOW_MIN_HISTORY_DAYS = 7      # don't call anything a "low" until a week of history exists
LOW_MARGIN_USD = 0.005        # sub-cent wobbles are not a new low
WEEKEND_MODES = ("FRIDAY_DEPARTURE", "SUNDAY_RETURN")


def load_state() -> dict:
    try:
        return json.loads(STATE_JSON.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def previous_low(history: list[dict], zips: set[str], today: date) -> tuple[float | None, int]:
    """Lowest daily NJ listed price before today within the lookback, and how many days that covers."""
    start = (today - timedelta(days=LOW_LOOKBACK_DAYS)).isoformat()
    by_day: dict[str, float] = {}
    for r in history:
        if r["zip"] in zips and start <= r["date"] < today.isoformat():
            by_day[r["date"]] = min(by_day.get(r["date"], 1e9), float(r["price"]))
    if not by_day:
        return None, 0
    return min(by_day.values()), len(by_day)


@dataclass
class Decision:
    send: bool
    reason: str               # "weekend" | "low" | "interval" | "force" | "" when not sending
    note: str                 # one line for the log and the email


def decide(mode: str, nj_price: float | None, history: list[dict], nj_zips: set[str],
           state: dict, today: date, force: bool = False) -> Decision:
    if force:
        return Decision(True, "force", "manual run")

    if mode in WEEKEND_MODES:
        if state.get("weekend_sent", {}).get(mode) == today.isoformat():
            return Decision(False, "", f"{mode} alert already sent today")
        return Decision(True, "weekend", mode)

    if nj_price is not None:
        low, days = previous_low(history, nj_zips, today)
        last_alert = state.get("last_low_alert_price")
        if (low is not None and days >= LOW_MIN_HISTORY_DAYS and nj_price < low - LOW_MARGIN_USD
                and (last_alert is None or nj_price < last_alert - LOW_MARGIN_USD)):
            return Decision(True, "low", f"NJ ${nj_price:.3f}/gal is the lowest in {days} logged days "
                                         f"(previous low ${low:.3f})")

    last = state.get("last_digest_date")
    if last is None or (today - date.fromisoformat(last)).days >= INTERVAL_DAYS:
        return Decision(True, "interval", f"{INTERVAL_DAYS}-day digest" if last else "first digest")
    days_left = INTERVAL_DAYS - (today - date.fromisoformat(last)).days
    return Decision(False, "", f"no new NJ low; next routine digest in {days_left} day(s)")


def mark_sent(state: dict, decision: Decision, mode: str, nj_price: float | None, today: date) -> dict:
    """Weekend alerts run on their own track; only weekday digests restart the 10-day clock."""
    state["last_reason"] = decision.reason
    if decision.reason == "weekend":
        state.setdefault("weekend_sent", {})[mode] = today.isoformat()
    else:
        state["last_digest_date"] = today.isoformat()
    if decision.reason == "low" and nj_price is not None:
        state["last_low_alert_price"] = round(nj_price, 3)
    return state
