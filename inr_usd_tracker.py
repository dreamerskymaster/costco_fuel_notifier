#!/usr/bin/env python3
"""INR -> USD brief: which Credila route lands a monthly dollar amount in Chase cheapest.

Run modes
  digest   - email only when there is a reason: the send window opens, the
             weekly brief is due, or the window is about to close
  force    - email now regardless (manual workflow runs)
  dry-run  - render inr_usd_preview.html and print the summary; send nothing

The ordering mirrors what the data says. Route choice and fee handling are
certain savings; timing is not. A walk-forward over five years of ECB closes
found that waiting for a cheap dollar inside a two-week window *lost* money on
average versus sending on the window's first day, because the rupee drifts
weaker. So the brief leads with the route, tells her to send early, and prints
the timing rule's own record so it never quietly overclaims.
"""

from __future__ import annotations

import argparse
import csv
import json
import smtplib
import ssl
import sys
from dataclasses import replace
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import email_thread  # shared threading + rate-change gating
import fx_data
from fx_config import DATA_DIR, OUTBOUND_HISTORY_CSV, OUTBOUND_STATE_JSON, OutboundConfig
from fx_outbound import (
    CardRate,
    Route,
    RouteCost,
    break_even_markup,
    cost_to_land,
    fetch_card_rate,
    fetch_wise_benchmark,
    implied_markup_pct,
)
from fx_outbound_timing import OutboundBacktest, cheapness_percentile, run_outbound_backtest
from usd_inr_tracker import BAD, GOOD, INK, MUTED, WARN, _card, compute_window, inr, signed_pct

ACCENT = "#1d4ed8"  # Chase-adjacent blue, so the two briefs are told apart at a glance

HISTORY_FIELDS = [
    "date", "mid", "hsbc_tt_sell", "iob_tt_sell", "wire_total_inr", "globalpay_total_inr",
    "globalpay_breakeven_rate", "wise_total_inr",
    "best_route", "cheapness_pct", "days_left", "reason",
]


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

def build_routes(config: OutboundConfig, mid: float, hsbc: CardRate | None) -> tuple[list[Route], list[str]]:
    """The routes she can actually use. The wire's markup comes from HSBC's live card rate."""
    notes: list[str] = []
    if hsbc:
        wire_markup = 100.0 * (hsbc.tt_sell / mid - 1.0)
        notes.append(f"Wire rate: HSBC India TT-selling card rate ₹{hsbc.tt_sell:.2f} ({hsbc.updated}).")
    else:
        wire_markup = config.wire_markup_pct
        notes.append(f"HSBC card-rate page unreadable today; wire modelled at mid + {wire_markup:.2f}%.")

    chase = config.chase_incoming_wire_usd
    wire_our = Route(
        name="Credila wire · OUR",
        markup_pct=wire_markup,
        fee_inr=config.wire_our_fee_inr,
        usd_deductions=chase,
        notes=[
            f"Tick <b>OUR</b> on the HSBC remittance: ₹{inr(config.wire_our_fee_inr)} + GST up front instead of "
            f"~${config.wire_correspondent_usd:.0f} shaved off by correspondent banks.",
        ],
    )
    wire_sha = replace(
        wire_our,
        name="Credila wire · SHA",
        fee_inr=0.0,
        usd_deductions=config.wire_correspondent_usd + chase,
        notes=[],
    )
    globalpay = Route(
        name="Credila Global Pay",
        markup_pct=config.globalpay_markup_pct,
        fee_inr=config.globalpay_fee_inr,
        usd_deductions=config.globalpay_correspondent_usd + chase,
        notes=[],
    )

    for route, receipt, label in (
        (wire_our, config.wire_receipt, "WIRE_RECEIPT"),
        (globalpay, config.globalpay_receipt, "GLOBALPAY_RECEIPT"),
    ):
        if not receipt:
            continue
        day, inr_debited, usd_received = receipt
        mid_then = _mid_on(day)
        if mid_then is None:
            notes.append(f"{label}: no mid-market close found for {day}; receipt ignored.")
            continue
        implied = implied_markup_pct(inr_debited, usd_received, mid_then, route)
        notes.append(
            f"{label} ({day}: ₹{inr(inr_debited)} → ${usd_received:,.2f}, mid {mid_then:.3f}) "
            f"implies a {implied:.2f}% markup."
        )
        if route is globalpay:
            # Global Pay has no public rate, so a real receipt beats the guess.
            globalpay.markup_pct = implied
    return [wire_our, wire_sha, globalpay], notes


_HISTORY_CACHE: fx_data.Series | None = None


def _history() -> fx_data.Series:
    global _HISTORY_CACHE
    if _HISTORY_CACHE is None:
        _HISTORY_CACHE = fx_data.fetch_pair_history(years=5)
    return _HISTORY_CACHE


def _mid_on(day: str) -> float | None:
    """Mid-market close on `day`, or the nearest earlier business day."""
    series = _history()
    candidates = [c for d, c in zip(series.dates, series.closes) if d <= day]
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# SMTP sender — separate pipeline key so inr_usd threads independently
# ---------------------------------------------------------------------------

def _send_email_inr_usd(config: OutboundConfig, subject: str, html: str) -> None:
    """Send the INR→USD brief via SMTP, threading same-day messages together.

    Uses the ``'inr_usd'`` pipeline key in ``data/thread_state.json`` so that
    these emails thread separately from the USD→INR pipeline.
    """
    thread_headers = email_thread.get_thread_headers("inr_usd")
    mid = email_thread.mark_sent("inr_usd")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = config.sender_email
    message["To"] = ", ".join(config.receivers)
    message["Message-ID"] = mid
    for header_name, header_value in thread_headers.items():
        message[header_name] = header_value
    message.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=30) as server:
        server.starttls(context=context)
        server.login(config.sender_email, config.sender_password)
        server.sendmail(config.sender_email, config.receivers, message.as_string())
    thread_note = "(new thread)" if not thread_headers else "(threaded reply)"
    print(f"INR→USD email sent to {len(config.receivers)} recipient(s) {thread_note}")


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(OUTBOUND_STATE_JSON.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOUND_STATE_JSON.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def append_history(row: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not OUTBOUND_HISTORY_CSV.exists()
    with OUTBOUND_HISTORY_CSV.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in HISTORY_FIELDS})


# --------------------------------------------------------------------------
# when to email
# --------------------------------------------------------------------------

def send_reason(today: date, config: OutboundConfig, state: dict) -> str | None:
    """Why today deserves an email, or None. At most one email per day.

    Window open  - first day of the send window: the backtest's best time to send.
    Closing      - two days before the deadline, as a last reminder.
    Weekly       - the configured weekday, so the numbers never go stale.
    """
    if state.get("last_sent") == today.isoformat():
        return None
    start, deadline, _, _ = compute_window(today, config.window_start, config.flex_days)
    if today == start:
        return "window-open"
    if today == deadline - timedelta(days=2):
        return "closing"
    if today.weekday() == config.digest_weekday:
        return "weekly"
    return None


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------

def _route_table(costs: list[RouteCost], wise: dict | None, usd: float, mid: float) -> str:
    best = min(c.total_inr for c in costs)
    rows = []
    for cost in sorted(costs, key=lambda c: c.total_inr):
        gap = cost.total_inr - best
        colour = GOOD if gap == 0 else INK
        rows.append(
            f"<tr style='border-top:1px solid #e5e7eb;'>"
            f"<td style='padding:8px 4px;color:{colour};'><b>{cost.route.name}</b><br>"
            f"<span style='font-size:11px;color:{MUTED};'>rate ₹{cost.rate:.3f} "
            f"(+{cost.route.markup_pct:.2f}%) · fees ₹{inr(cost.fees_inr)} · GST ₹{inr(cost.gst_inr)}"
            f"{f' · ${cost.route.usd_deductions:.0f} deducted en route' if cost.route.usd_deductions else ''}</span></td>"
            f"<td align='right' style='padding:8px 4px;color:{colour};'><b>₹{inr(cost.total_inr)}</b><br>"
            f"<span style='font-size:11px;color:{MUTED};'>{cost.cost_pct:.2f}% over mid"
            f"{'' if gap == 0 else f' · +₹{inr(gap)}'}</span></td></tr>"
        )
    rows.append(
        f"<tr style='border-top:1px solid #e5e7eb;color:{MUTED};font-size:12px;'>"
        f"<td style='padding:8px 4px;'>Mid-market (no provider pays this)</td>"
        f"<td align='right' style='padding:8px 4px;'>₹{inr(usd * mid)}</td></tr>"
    )
    if wise:
        rows.append(
            f"<tr style='color:{MUTED};font-size:12px;'>"
            f"<td style='padding:4px 4px 8px 4px;'>Wise, live quote — yardstick only "
            f"(needs an India-resident sender)</td>"
            f"<td align='right' style='padding:4px 4px 8px 4px;'>₹{inr(wise['total_inr'])}</td></tr>"
        )
    return (
        f"<div style='font-size:12px;color:{MUTED};margin-bottom:4px;'>"
        f"Rupees drawn from the loan to land exactly <b>${usd:,.0f}</b> in Chase (Global Pay row is an estimate)</div>"
        f"<table width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;'>{''.join(rows)}</table>"
    )


def build_html(
    config: OutboundConfig,
    spot: fx_data.SpotQuote,
    mid: float,
    costs: list[RouteCost],
    wise: dict | None,
    iob: CardRate | None,
    breakeven_rate: float,
    backtest: OutboundBacktest,
    cheapness: float,
    window: tuple[date, date, int, int],
    calibration: list[str],
    reason: str,
) -> str:
    start, deadline, days_left, _ = window
    by_name = {c.route.name: c for c in costs}
    wires = [c for c in costs if c.route.name.startswith("Credila wire")]
    best_wire = min(wires, key=lambda c: c.total_inr)
    worst_wire = max(wires, key=lambda c: c.total_inr)
    globalpay = by_name["Credila Global Pay"]
    today = date.today()
    in_window = start <= today <= deadline

    if in_window:
        when = (
            f"<b>Send now.</b> You are inside the window ({start:%b %d} – {deadline:%b %d}, "
            f"{days_left} business day{'' if days_left == 1 else 's'} left); historically, waiting for a better rate lost money."
        )
        colour = BAD if reason == "closing" else GOOD
    else:
        when = f"<b>Next window opens {start:%a %b %d}.</b> Send on day one; waiting has not paid off historically."
        colour = ACCENT

    decision = f"""
      <div style="font:700 19px/1.35 -apple-system,sans-serif;color:{colour};">
        Open Global Pay first. If its rate is ₹{breakeven_rate:.2f} per dollar or better{f' with its ₹{inr(config.globalpay_fee_inr)} fee' if config.globalpay_fee_inr else ' and it adds no separate fee'}, use it.
        Otherwise wire from HSBC with charges set to OUR.</div>
      <div style="margin-top:8px;">Wire (OUR) costs <b>₹{inr(best_wire.total_inr)}</b> to land
      ${config.usd_amount:,.0f}: HSBC's card rate is {best_wire.route.markup_pct:.2f}% over mid-market, plus fees.
      Global Pay at an assumed +{globalpay.route.markup_pct:.2f}% would cost ₹{inr(globalpay.total_inr)}.
      Choosing OUR over SHA on the wire saves about ₹{inr(worst_wire.total_inr - best_wire.total_inr)}.</div>
      <div style="margin-top:8px;color:{MUTED};font-size:13px;">{when}</div>"""

    checklist = "".join(f"<li style='margin-bottom:4px;'>{note}</li>" for c in costs for note in c.route.notes)
    checklist += (
        "<li style='margin-bottom:4px;'>Send in <b>USD</b>, never INR. Chase converts incoming rupees at its own spread.</li>"
        "<li style='margin-bottom:4px;'>Loan-funded education remittances carry <b>0% TCS</b>. "
        "Make sure it is filed under education (purpose code S0305) against the Credila loan.</li>"
    )
    if iob:
        iob_markup = 100.0 * (iob.tt_sell / mid - 1.0)
        iob_saving = config.usd_amount * mid * (best_wire.route.markup_pct - iob_markup) / 100.0
        checklist += (
            f"<li style='margin-bottom:4px;'>IOB's card rate today is <b>₹{iob.tt_sell:.2f}</b> "
            f"(+{iob_markup:.2f}%), against HSBC's +{best_wire.route.markup_pct:.2f}%. If Credila can disburse to your IOB "
            f"account and IOB will wire for you, the rate alone saves ~₹{inr(iob_saving)}. Ask Credila.</li>"
        )
    checklist += (
        f"<li>Today's mid-market is <b>₹{mid:.3f}</b>. Each 0.1% of markup costs about "
        f"₹{inr(config.usd_amount * mid * 0.001)} on ${config.usd_amount:,.0f}.</li>"
    )

    t = backtest.t_stat
    track = (
        f"Over {backtest.n} past monthly windows, waiting for a cheap dollar (the cheapest {backtest.threshold:.0f}% "
        f"of the last 90 days, otherwise the deadline) <b>cost ₹{inr(-backtest.saved_per_month)} more per month</b> "
        f"than sending on day one ({backtest.wins} months better, {backtest.losses} worse"
        f"{f', t = {t:.1f}' if t is not None else ''}). Even perfect hindsight would have saved only "
        f"₹{inr(backtest.hindsight_per_month)} a month. Today the dollar is cheaper than "
        f"{cheapness:.0f}% of the last 90 days."
    ) if backtest.n else "Not enough history to score a timing rule."

    assumptions = (
        f"Global Pay (WSFx) publishes no markup, so +{config.globalpay_markup_pct:.2f}% is a guess. "
        f"Correspondent deductions on SHA are ~${config.wire_correspondent_usd:.0f}; Chase takes "
        f"${config.chase_incoming_wire_usd:.0f} per incoming wire. GST on conversion follows CGST Rule 32(2)(b). "
        "After a transfer, save the receipt as the <code>GLOBALPAY_RECEIPT</code> or <code>WIRE_RECEIPT</code> "
        "repo variable (<code>YYYY-MM-DD,inr_debited,usd_received</code>). A Global Pay receipt replaces the guess; "
        "a wire receipt shows what HSBC actually charged."
    )
    if calibration:
        assumptions = "<br>".join(calibration) + "<br>" + assumptions

    cards = [
        _card("The decision", decision, colour),
        _card("Route comparison", _route_table(costs, wise, config.usd_amount, mid), ACCENT),
        _card("Before you confirm", f"<ul style='margin:0;padding-left:18px;'>{checklist}</ul>", WARN),
        _card("Does timing help? (its own track record)", track, MUTED),
        _card("Assumptions", f"<span style='font-size:12px;color:{MUTED};'>{assumptions}</span>", MUTED),
    ]

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>INR → USD transfer brief</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:18px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
  <tr><td style="padding:0 0 16px 0;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:{ACCENT};border-radius:10px;">
      <tr><td style="padding:18px;text-align:center;color:#ffffff;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
        <div style="font:600 13px/1.4 -apple-system,sans-serif;opacity:.85;letter-spacing:.5px;">INR → USD · CREDILA → CHASE</div>
        <div style="font:700 40px/1.1 -apple-system,sans-serif;margin:8px 0 2px 0;">₹{mid:.3f}</div>
        <div style="font:400 13px/1.4 -apple-system,sans-serif;opacity:.9;">
          {('day ' + signed_pct(spot.day_change_pct)) if spot.day_change_pct is not None else 'mid-market'}
          &nbsp;·&nbsp; ${config.usd_amount:,.0f} = ₹{inr(config.usd_amount * mid)} at mid-market
        </div>
        <div style="font:400 11px/1.4 -apple-system,sans-serif;opacity:.75;margin-top:6px;">
          {spot.source} &nbsp;·&nbsp; {spot.as_of.strftime('%Y-%m-%d %H:%M UTC')}
        </div>
      </td></tr>
    </table>
  </td></tr>
  {''.join(cards)}
  <tr><td style="padding:0 0 18px 0;font:400 11px/1.5 -apple-system,sans-serif;color:{MUTED};text-align:center;">
    Estimates, not quotes. Credila's confirmed rate and fees are what count — check them on the payment screen.
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("digest", "force", "dry-run"), default="digest")
    args = parser.parse_args(argv)

    config = OutboundConfig()
    today = date.today()
    problems = config.validate()
    if problems and args.mode != "dry-run":
        print("Configuration errors:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    state = load_state()
    reason = {"force": "manual", "dry-run": "preview"}.get(args.mode) or send_reason(today, config, state)
    if reason is None:
        print("no email due today — window not opening/closing and not the weekly day.")
        return 0

    spot = fx_data.fetch_spot()
    series = _history()
    closes = list(series.closes)
    if series.dates and series.dates[-1] == today.isoformat():
        closes[-1] = spot.rate
    else:
        closes.append(spot.rate)
    mid = spot.rate

    hsbc = fetch_card_rate("HSBC India")
    iob = fetch_card_rate("IOB")
    routes, calibration = build_routes(config, mid, hsbc)
    costs = [cost_to_land(route, config.usd_amount, mid) for route in routes]
    best_wire = min(costs[:2], key=lambda c: c.total_inr)
    breakeven = break_even_markup(routes[2], best_wire.total_inr, config.usd_amount, mid)
    breakeven_rate = mid * (1.0 + breakeven / 100.0)
    wise = fetch_wise_benchmark(config.usd_amount)
    backtest = run_outbound_backtest(
        series.dates, series.closes, config.window_start, config.flex_days, config.usd_amount
    )
    cheapness = cheapness_percentile(closes)
    window = compute_window(today, config.window_start, config.flex_days)

    best = min(costs, key=lambda c: c.total_inr)
    summary = " | ".join(
        [f"mid {mid:.3f}"]
        + [f"{c.route.name} ₹{inr(c.total_inr)} ({c.cost_pct:.2f}%)" for c in costs]
        + [f"Global Pay break-even ₹{breakeven_rate:.2f}"]
        + ([f"IOB TT ₹{iob.tt_sell:.2f}"] if iob else [])
        + ([f"Wise yardstick ₹{inr(wise['total_inr'])}"] if wise else [])
    )
    html = build_html(
        config, spot, mid, costs, wise, iob, breakeven_rate, backtest, cheapness, window, calibration, reason
    )

    if args.mode == "dry-run":
        Path("inr_usd_preview.html").write_text(html)
        print(summary)
        print(f"timing: rule {-backtest.saved_per_month:+.0f} ₹/mo vs day-one over {backtest.n} windows; "
              f"hindsight ceiling ₹{backtest.hindsight_per_month:.0f}/mo")
        print("\n".join(f"  · {line}" for line in calibration))
        print("wrote inr_usd_preview.html")
        return 0

    # --- rate-change gate (0.1% mid movement required; force/manual bypasses) ---
    if args.mode != "force" and not email_thread.has_rate_changed(
        "inr_usd", mid, threshold_pct=0.1, key="last_rate"
    ):
        print(
            f"Rate gate: mid ₹{mid:.3f} has not moved >= 0.1% since last email — skipping."
        )
        state["last_sent"] = today.isoformat()
        save_state(state)
        return 0

    tag = {"window-open": "SEND NOW", "closing": "LAST CALL", "weekly": "WEEKLY", "manual": "BRIEF"}[reason]
    subject = f"INR→USD ₹{mid:.2f} · {tag} · Global Pay ≤ ₹{breakeven_rate:.2f} else wire OUR ₹{inr(best_wire.total_inr)}"
    _send_email_inr_usd(config, subject, html)

    append_history(
        {
            "date": today.isoformat(),
            "mid": round(mid, 4),
            "wire_total_inr": round(best_wire.total_inr, 2),
            "globalpay_total_inr": round(costs[2].total_inr, 2),
            "globalpay_breakeven_rate": round(breakeven_rate, 4),
            "hsbc_tt_sell": hsbc.tt_sell if hsbc else "",
            "iob_tt_sell": iob.tt_sell if iob else "",
            "wise_total_inr": round(wise["total_inr"], 2) if wise else "",
            "best_route": best.route.name,
            "cheapness_pct": round(cheapness, 1),
            "days_left": window[2],
            "reason": reason,
        }
    )
    state["last_sent"] = today.isoformat()
    save_state(state)
    # Persist mid rate for next run's change gate.
    email_thread.record_rate("inr_usd", mid, key="last_rate")
    print(f"sent ({reason}) to {len(config.receivers)} recipient(s) — {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
