"""When the US fuel digest is allowed to email. Two rules, nothing else:

    weekend   one Friday departure alert and one Sunday return alert, each at most
              once per day. NJ travel only happens on weekends.
    low       Monday-Friday: the cheapest Norwalk (06854) listed price is the lowest
              in the last LOW_LOOKBACK_DAYS (90; at least 7 days of history needed),
              and lower than the price of the last low alert.
    force     FORCE_EMAIL=true (manual run).

Prices are still checked and logged every 3 hours whether or not anything is sent.

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
HOME_ZIP = "06854"            # Norwalk
LOW_LOOKBACK_DAYS = 90
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


def daily_low(history: list[dict], zip_code: str, day: str) -> float | None:
    vals = [float(r["price"]) for r in history if r["zip"] == zip_code and r["date"] == day]
    return min(vals) if vals else None


def previous_low(history: list[dict], zip_code: str, today: date) -> tuple[float | None, int]:
    """Lowest daily listed price in `zip_code` before today within the lookback, and how many days that covers."""
    start = (today - timedelta(days=LOW_LOOKBACK_DAYS)).isoformat()
    by_day: dict[str, float] = {}
    for r in history:
        if r["zip"] == zip_code and start <= r["date"] < today.isoformat():
            by_day[r["date"]] = min(by_day.get(r["date"], 1e9), float(r["price"]))
    if not by_day:
        return None, 0
    return min(by_day.values()), len(by_day)


@dataclass
class Decision:
    send: bool
    reason: str               # "weekend" | "low" | "force" | "" when not sending
    note: str                 # one line for the log and the email


def decide(mode: str, home_price: float | None, history: list[dict], state: dict, today: date,
           force: bool = False) -> Decision:
    if force:
        return Decision(True, "force", "manual run")

    if mode in WEEKEND_MODES:
        if state.get("weekend_sent", {}).get(mode) == today.isoformat():
            return Decision(False, "", f"{mode} alert already sent today")
        return Decision(True, "weekend", mode)

    if today.weekday() >= 5:
        return Decision(False, "", "weekend outside the Friday/Sunday alert windows")

    if home_price is not None:
        low, days = previous_low(history, HOME_ZIP, today)
        last_alert = state.get("last_low_alert_price")
        if (low is not None and days >= LOW_MIN_HISTORY_DAYS and home_price < low - LOW_MARGIN_USD
                and (last_alert is None or home_price < last_alert - LOW_MARGIN_USD)):
            return Decision(True, "low", f"Norwalk ${home_price:.3f}/gal is the lowest in {days} logged days "
                                         f"(previous low ${low:.3f})")
        if low is not None and days < LOW_MIN_HISTORY_DAYS:
            return Decision(False, "", f"building history ({days}/{LOW_MIN_HISTORY_DAYS} days) before low alerts")
    return Decision(False, "", "no new Norwalk low")


def mark_sent(state: dict, decision: Decision, mode: str, home_price: float | None, today: date) -> dict:
    state["last_sent_date"] = today.isoformat()
    state["last_reason"] = decision.reason
    if decision.reason == "weekend":
        state.setdefault("weekend_sent", {})[mode] = today.isoformat()
    if decision.reason == "low" and home_price is not None:
        state["last_low_alert_price"] = round(home_price, 3)
    return state
