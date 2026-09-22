"""Configuration, read from the environment with sane defaults.

Empty GitHub Actions secrets arrive as "" rather than being absent, which
breaks the obvious `os.environ.get("X", default)` pattern. Every read here
strips and validates instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
HISTORY_CSV = DATA_DIR / "history.csv"
STATE_JSON = DATA_DIR / "state.json"
EVENTS_JSON = DATA_DIR / "events.json"


def _text(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _number(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _integer(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw.isdigit() else default


def _emails(*names: str) -> list[str]:
    """First populated variable wins; commas split multiple recipients."""
    for name in names:
        raw = _text(name)
        if raw:
            return [part.strip() for part in raw.split(",") if part.strip()]
    return []


@dataclass
class Config:
    # --- transfer profile ---
    send_amount: float = field(default_factory=lambda: _number("SEND_AMOUNT_USD", 3000.0))
    payday_day: int = field(default_factory=lambda: _integer("PAYDAY_DAY", 15))
    flex_days: int = field(default_factory=lambda: _integer("FLEX_DAYS", 14))
    current_provider: str = field(default_factory=lambda: _text("CURRENT_PROVIDER", "Wise"))

    # --- model knobs ---
    risk_aversion: float = field(default_factory=lambda: _number("RISK_AVERSION", 3.0))
    drift_shrink: float = field(default_factory=lambda: _number("DRIFT_SHRINK", 0.5))

    # --- alerting ---
    alert_percentile: float = field(default_factory=lambda: _number("ALERT_PERCENTILE", 85.0))
    alert_cooldown_days: int = field(default_factory=lambda: _integer("ALERT_COOLDOWN_DAYS", 3))

    # --- email ---
    sender_email: str = field(default_factory=lambda: _text("SENDER_EMAIL"))
    sender_password: str = field(default_factory=lambda: _text("SENDER_PASSWORD"))
    receivers: list[str] = field(default_factory=lambda: _emails("FX_RECEIVER_EMAIL", "RECEIVER_EMAIL"))
    smtp_server: str = field(default_factory=lambda: _text("SMTP_SERVER", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _integer("SMTP_PORT", 587))

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.sender_email:
            problems.append("SENDER_EMAIL is not set")
        if not self.sender_password:
            problems.append("SENDER_PASSWORD is not set")
        if not self.receivers:
            problems.append("FX_RECEIVER_EMAIL / RECEIVER_EMAIL is not set")
        if not 1 <= self.payday_day <= 28:
            problems.append(f"PAYDAY_DAY must be 1-28, got {self.payday_day}")
        if self.flex_days < 0:
            problems.append("FLEX_DAYS cannot be negative")
        return problems


def load_events() -> list[dict]:
    """Policy dates that can move the pair inside a send window."""
    if not EVENTS_JSON.exists():
        return []
    try:
        return json.loads(EVENTS_JSON.read_text()).get("events", [])
    except (json.JSONDecodeError, OSError):
        return []
