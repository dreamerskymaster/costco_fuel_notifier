"""email_thread.py
=================
Shared SMTP email threading and daily rate-change gating utilities.

Threading model
---------------
All pipelines share ``data/thread_state.json``. When a pipeline sends the
first email of the day it stores the ``Message-ID`` of that message. Every
subsequent email that day sets ``In-Reply-To`` and ``References`` to the
stored anchor, causing Gmail (and most IMAP clients) to collapse all same-day
emails into one thread.

Rate-change gate
----------------
``has_rate_changed(pipeline, value, threshold_pct)`` compares the current
numeric value against the last value stored for that pipeline.  Returns True
when the relative change >= ``threshold_pct`` OR when no prior value exists.

State file schema: data/thread_state.json
------------------------------------------
{
  "usd_inr": {
    "date": "2026-09-30",
    "message_id": "<abc123@fuel-notifier>",
    "references": "<abc123@fuel-notifier>",
    "last_rate": 83.42
  },
  "inr_usd": { ... },
  "gas": {
    "date": "2026-09-30",
    "message_id": "<def456@fuel-notifier>",
    "references": "<def456@fuel-notifier>",
    "last_price": 3.089
  }
}
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

_STATE_FILE = Path(__file__).parent / "data" / "thread_state.json"
_HOST = "fuel-notifier"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    """Load thread state; return empty dict on any read/parse error."""
    try:
        return json.loads(_STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(state: dict) -> None:
    """Persist thread state, creating parent dirs if needed."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _new_message_id() -> str:
    """Generate a unique RFC-2822-style Message-ID."""
    return f"<{uuid.uuid4().hex}@{_HOST}>"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_thread_headers(pipeline: str) -> dict[str, str]:
    """Return SMTP threading headers for the given pipeline.

    Returns an empty dict for the *first* email of the day (no In-Reply-To).
    For subsequent emails within the same calendar day returns::

        {"In-Reply-To": "<anchor-id>", "References": "<chain ...>"}

    Args:
        pipeline: Logical pipeline name, e.g. ``"usd_inr"``, ``"gas"``.

    Returns:
        Dict of header-name -> value; empty for the first email of the day.
    """
    today = date.today().isoformat()
    entry = _load().get(pipeline, {})
    if entry.get("date") == today and entry.get("message_id"):
        return {
            "In-Reply-To": entry["message_id"],
            "References": entry.get("references", entry["message_id"]),
        }
    return {}


def mark_sent(pipeline: str, message_id: str | None = None) -> str:
    """Record that an email was sent for ``pipeline`` today.

    On the first send of the day this creates the thread anchor.  On
    subsequent sends the anchor is preserved and the references chain
    is extended.

    Args:
        pipeline: Logical pipeline name.
        message_id: The Message-ID placed on the outgoing email, or None to
            auto-generate one.

    Returns:
        The Message-ID string that should be placed on the outgoing message.
    """
    today = date.today().isoformat()
    state = _load()
    entry = state.get(pipeline, {})
    mid = message_id or _new_message_id()

    if entry.get("date") != today:
        # First email of the day — become the thread anchor.
        state[pipeline] = {
            "date": today,
            "message_id": mid,
            "references": mid,
        }
    else:
        # Subsequent email — keep the original anchor, extend references.
        anchor = entry.get("message_id", mid)
        existing_refs = entry.get("references", anchor)
        extended_refs = existing_refs if mid in existing_refs else f"{existing_refs} {mid}"
        state[pipeline] = {
            "date": today,
            "message_id": anchor,
            "references": extended_refs,
        }
        # Carry over last_rate / last_price from before the merge.
        for carry_key in ("last_rate", "last_price"):
            if carry_key in entry:
                state[pipeline][carry_key] = entry[carry_key]

    _save(state)
    return mid


def record_rate(pipeline: str, value: float, key: str = "last_rate") -> None:
    """Store the numeric rate/price that was included in the last sent email.

    Args:
        pipeline: Logical pipeline name.
        value: Rate or price to persist.
        key: State key to use (``"last_rate"`` or ``"last_price"``).
    """
    state = _load()
    state.setdefault(pipeline, {})
    state[pipeline][key] = round(value, 6)
    _save(state)


def has_rate_changed(pipeline: str, current_value: float,
                     threshold_pct: float = 0.1,
                     key: str = "last_rate") -> bool:
    """Return True if the rate has moved enough to justify sending an email.

    The test is: ``abs(current - last) / abs(last) * 100 >= threshold_pct``.
    Always returns True when no prior value is stored.

    Args:
        pipeline: Logical pipeline name.
        current_value: The rate/price observed right now.
        threshold_pct: Minimum percentage move to trigger (default: 0.1%).
        key: Which state key to compare against.

    Returns:
        True if the change meets or exceeds the threshold.
    """
    last = _load().get(pipeline, {}).get(key)
    if last is None:
        return True
    if last == 0:
        return current_value != 0
    return abs(current_value - last) / abs(last) * 100.0 >= threshold_pct
