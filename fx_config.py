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


def _recipients() -> list[str]:
    """Primary recipient(s) plus any additional ones, de-duplicated.

    Additional recipients live in their own variable rather than being appended
    to the primary one. This repo is public, so an address must never appear in
    a workflow file; keeping them in separate secrets means the workflow only
    ever references secret names. Matching is case-insensitive so the same
    address listed in both places is not mailed twice.
    """
    people = _emails("FX_RECEIVER_EMAIL", "RECEIVER_EMAIL") + _emails("FX_EXTRA_RECEIVERS")
    seen: set[str] = set()
    unique: list[str] = []
    for address in people:
        key = address.lower()
        if key not in seen:
            seen.add(key)
            unique.append(address)
    return unique


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
    receivers: list[str] = field(default_factory=_recipients)
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
        bad = [a for a in self.receivers if "@" not in a or a.startswith("@") or a.endswith("@")]
        if bad:
            problems.append(f"recipient address looks malformed: {', '.join(bad)}")
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


# --------------------------------------------------------------------------
# INR -> USD (Credila education loan to a US Chase account)
# --------------------------------------------------------------------------

OUTBOUND_HISTORY_CSV = DATA_DIR / "inr_usd_history.csv"
OUTBOUND_STATE_JSON = DATA_DIR / "inr_usd_state.json"


def _receipt(name: str) -> tuple[str, float, float] | None:
    """Parse "YYYY-MM-DD,inr_debited,usd_received" from a real transfer receipt."""
    parts = [p.strip().replace("_", "") for p in _text(name).split(",")]
    if len(parts) != 3:
        return None
    try:
        return parts[0], float(parts[1]), float(parts[2])
    except ValueError:
        return None


@dataclass
class OutboundConfig:
    """Settings for the INR->USD brief. Separate recipients from the USD->INR one.

    There is deliberately no fallback to RECEIVER_EMAIL: this brief belongs to
    someone else, and silently mailing the repo owner instead would hide a
    missing secret rather than surface it.
    """

    usd_amount: float = field(default_factory=lambda: _number("INR_USD_AMOUNT", 1000.0))
    window_start: int = field(default_factory=lambda: _integer("INR_USD_WINDOW_START", 15))
    flex_days: int = field(default_factory=lambda: _integer("INR_USD_FLEX_DAYS", 14))
    digest_weekday: int = field(default_factory=lambda: _integer("INR_USD_DIGEST_WEEKDAY", 0))  # Monday

    # Credila wire: money sits in the HSBC India account opened at loan signing
    # and leaves by SWIFT at HSBC's live TT-selling card rate. The markup below
    # is only the fallback for when that page cannot be read.
    wire_markup_pct: float = field(default_factory=lambda: _number("WIRE_MARKUP_PCT", 2.0))
    # HSBC: app remittances carry no fee; ticking OUR (sender pays every bank
    # in the chain) costs ₹1,200 + GST. With SHA, correspondents shave dollars.
    wire_our_fee_inr: float = field(default_factory=lambda: _number("WIRE_OUR_FEE_INR", 1200.0))
    wire_correspondent_usd: float = field(default_factory=lambda: _number("WIRE_CORRESPONDENT_USD", 20.0))
    # Chase: $15 per incoming international wire, College Checking included.
    chase_incoming_wire_usd: float = field(default_factory=lambda: _number("CHASE_INCOMING_WIRE_USD", 15.0))
    wire_receipt: tuple | None = field(default_factory=lambda: _receipt("WIRE_RECEIPT"))

    # Global Pay (WSFx GlobalPay, Credila's forex partner): "market-linked"
    # rate with no published markup, so this is a guess until a receipt or an
    # in-app quote replaces it. Assumed to arrive as a SWIFT wire, so Chase's
    # incoming fee applies.
    globalpay_markup_pct: float = field(default_factory=lambda: _number("GLOBALPAY_MARKUP_PCT", 1.0))
    globalpay_fee_inr: float = field(default_factory=lambda: _number("GLOBALPAY_FEE_INR", 0.0))
    globalpay_correspondent_usd: float = field(default_factory=lambda: _number("GLOBALPAY_CORRESPONDENT_USD", 0.0))
    globalpay_receipt: tuple | None = field(default_factory=lambda: _receipt("GLOBALPAY_RECEIPT"))

    sender_email: str = field(default_factory=lambda: _text("SENDER_EMAIL"))
    sender_password: str = field(default_factory=lambda: _text("SENDER_PASSWORD"))
    receivers: list[str] = field(default_factory=lambda: _emails("INR_USD_RECEIVER_EMAIL"))
    smtp_server: str = field(default_factory=lambda: _text("SMTP_SERVER", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _integer("SMTP_PORT", 587))

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.sender_email:
            problems.append("SENDER_EMAIL is not set")
        if not self.sender_password:
            problems.append("SENDER_PASSWORD is not set")
        if not self.receivers:
            problems.append("INR_USD_RECEIVER_EMAIL is not set")
        bad = [a for a in self.receivers if "@" not in a or a.startswith("@") or a.endswith("@")]
        if bad:
            problems.append(f"recipient address looks malformed: {', '.join(bad)}")
        if not 1 <= self.window_start <= 28:
            problems.append(f"INR_USD_WINDOW_START must be 1-28, got {self.window_start}")
        if self.flex_days < 0:
            problems.append("INR_USD_FLEX_DAYS cannot be negative")
        return problems
