#!/usr/bin/env python3
"""USD to INR remittance notifier: what to send with, and whether today is the day.

Run modes
    digest  - the full daily email (default)
    alert   - stay silent unless something is genuinely worth interrupting for
    dry-run - render to preview.html and print a summary, send nothing

The ordering of this report reflects what the backtest actually found. Provider
selection is a certain saving of roughly a thousand rupees a month on a $3,000
transfer; the timing rule, measured walk-forward over 45 monthly windows, is
worth about sixteen rupees and is statistically indistinguishable from zero. So
the provider call leads, and the timing call is reported with its own track
record attached so it can never quietly overclaim.
"""

from __future__ import annotations

import argparse
import csv
import json
import smtplib
import ssl
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from statistics import median

import numpy as np

import fx_data
import fx_providers as provider_module
from fx_backtest import BacktestResult, run_backtest
from fx_config import DATA_DIR, HISTORY_CSV, STATE_JSON, Config, load_events
from fx_signals import Decision, Stats, TrendModel, compute_stats, decide, fit_trend_model

HISTORY_FIELDS = [
    "date",
    "spot",
    "verdict",
    "score",
    "reservation_rate",
    "edge_pct",
    "best_provider",
    "best_received_inr",
    "current_provider_received_inr",
    "days_left",
]

GOOD = "#15803d"
BAD = "#b91c1c"
WARN = "#b45309"
ACCENT = "#6b21a8"
INK = "#1f2937"
MUTED = "#6b7280"


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

def inr(amount: float, decimals: int = 0) -> str:
    """Format with Indian digit grouping: 286567 -> 2,86,567."""
    negative = amount < 0
    rounded = f"{abs(amount):.{decimals}f}"
    digits, _, fraction_digits = rounded.partition(".")
    fraction = f".{fraction_digits}" if fraction_digits else ""
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{'-' if negative else ''}{digits}{fraction}"


def signed_pct(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value:+.{decimals}f}%"


# --------------------------------------------------------------------------
# send window
# --------------------------------------------------------------------------

def compute_window(today: date, payday_day: int, flex_days: int) -> tuple[date, date, int, int]:
    """Resolve the active send window.

    Returns (payday, deadline, business days left, business days in window).
    Once a window has closed, the next month's window becomes the active one,
    so the tool never sits in a permanently expired state.
    """
    payday = today.replace(day=payday_day)
    deadline = payday + timedelta(days=flex_days)
    if today > deadline:
        month = payday.month + 1
        year = payday.year + (month > 12)
        payday = date(year, 1 if month > 12 else month, payday_day)
        deadline = payday + timedelta(days=flex_days)

    reference = max(today, payday)
    days_left = int(np.busday_count(reference, deadline)) if deadline > reference else 0
    total_window = max(int(np.busday_count(payday, deadline)), 1)
    return payday, deadline, max(days_left, 0), total_window


# --------------------------------------------------------------------------
# persistence: CSV history plus a small JSON state file, both committed by CI
# --------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_JSON.exists():
        return {}
    try:
        return json.loads(STATE_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def append_history(row: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not HISTORY_CSV.exists()
    with HISTORY_CSV.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in HISTORY_FIELDS})


def record_markups(state: dict, report: provider_module.ProviderReport, today: str) -> dict[str, float]:
    """Keep a rolling markup history per provider and return trailing medians.

    This is what makes an expired promotional rate visible: a provider whose
    spread has widened well past its own norm gets flagged rather than silently
    costing a transfer.
    """
    tracked = state.setdefault("markup_history", {})
    baselines: dict[str, float] = {}
    for view in report.views:
        key = view.quote.name.strip().lower()
        entries = tracked.setdefault(key, [])
        if not any(entry.get("date") == today for entry in entries):
            entries.append({"date": today, "markup": round(view.quote.markup_pct, 4)})
        del entries[:-60]  # keep roughly two months of observations
        prior = [entry["markup"] for entry in entries if entry.get("date") != today]
        if len(prior) >= 5:
            baselines[key] = float(median(prior))
    return baselines


# --------------------------------------------------------------------------
# alerting
# --------------------------------------------------------------------------

def alert_reasons(
    stats: Stats,
    decision: Decision,
    report: provider_module.ProviderReport,
    config: Config,
    state: dict,
    today: date,
) -> list[str]:
    """Conditions worth interrupting for. Empty list means stay quiet."""
    reasons: list[str] = []

    percentile_1y = stats.percentile.get(252)
    if percentile_1y is not None and percentile_1y >= config.alert_percentile:
        reasons.append(
            f"Rate is better than {percentile_1y:.0f}% of the last 12 months "
            f"(alert threshold {config.alert_percentile:.0f}%)."
        )
    if stats.spot >= stats.high_52w:
        reasons.append(f"New 52-week high for USD/INR at {stats.spot:.3f}.")
    if decision.days_left <= 1:
        reasons.append(f"Send deadline {decision.deadline} is here; this window closes.")
    for warning in report.warnings:
        if "promotional" in warning:
            reasons.append(warning)

    if not reasons:
        return []

    # Respect a cooldown so a sustained good level does not mail every run.
    last_raw = state.get("last_alert_date")
    if last_raw:
        try:
            last = datetime.strptime(last_raw, "%Y-%m-%d").date()
            if (today - last).days < config.alert_cooldown_days and decision.days_left > 1:
                return []
        except ValueError:
            pass
    return reasons


# --------------------------------------------------------------------------
# email rendering
# --------------------------------------------------------------------------

def _card(title: str, body: str, colour: str = ACCENT) -> str:
    return f"""
    <tr><td style="padding:0 0 18px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
        <tr><td style="background:{colour};color:#ffffff;padding:10px 14px;font:600 13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;letter-spacing:.3px;">{title}</td></tr>
        <tr><td style="padding:14px;background:#ffffff;font:400 14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:{INK};">{body}</td></tr>
      </table>
    </td></tr>"""


def _range_bar(stats: Stats) -> str:
    span = stats.high_52w - stats.low_52w
    position = 0.0 if span <= 0 else max(0.0, min(1.0, (stats.spot - stats.low_52w) / span))
    filled = int(round(position * 100))
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 4px 0;">
      <tr>
        <td style="background:{GOOD};height:10px;width:{filled}%;border-radius:5px 0 0 5px;"></td>
        <td style="background:#e5e7eb;height:10px;width:{100 - filled}%;border-radius:0 5px 5px 0;"></td>
      </tr>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr style="font:400 11px/1.3 -apple-system,sans-serif;color:{MUTED};">
        <td align="left">52w low {stats.low_52w:.2f}</td>
        <td align="right">52w high {stats.high_52w:.2f}</td>
      </tr>
    </table>"""


def _provider_table(report: provider_module.ProviderReport, config: Config) -> str:
    if not report.views:
        return f"<span style='color:{WARN};'>Provider comparison feed unavailable this run.</span>"
    rows = []
    for index, view in enumerate(report.views):
        quote = view.quote
        highlight = "background:#f0fdf4;" if index == 0 else ""
        flag = " 🏆" if index == 0 else ""
        if view.promo_expired:
            flag += " ⚠️"
        name = f"{view.display_name}{flag}"
        if view.display_name.lower().startswith(config.current_provider.lower()[:5]):
            name += " <span style='color:" + MUTED + ";'>(yours)</span>"
        delta = "—" if index == 0 else f"-{inr(abs(view.inr_vs_best))}"
        rows.append(
            f"""<tr style="{highlight}">
              <td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;">{name}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;" align="right">{quote.rate:.3f}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;" align="right">${quote.fee:,.2f}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;" align="right">{view.total_cost_pct:.2f}%</td>
              <td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;font-weight:600;" align="right">₹{inr(quote.received_inr)}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;color:{BAD};" align="right">{delta}</td>
            </tr>"""
        )
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="font:400 12px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;">
      <tr style="background:#f9fafb;font-weight:600;color:{MUTED};">
        <td style="padding:6px 8px;">Provider</td>
        <td style="padding:6px 8px;" align="right">Rate</td>
        <td style="padding:6px 8px;" align="right">Fee</td>
        <td style="padding:6px 8px;" align="right">All-in</td>
        <td style="padding:6px 8px;" align="right">You get</td>
        <td style="padding:6px 8px;" align="right">vs best</td>
      </tr>
      {''.join(rows)}
    </table>
    <p style="margin:10px 0 0 0;font:400 11px/1.5 -apple-system,sans-serif;color:{MUTED};">
      "All-in" is the total cost versus a zero-fee mid-market transfer, so spread and fee are
      compared on one number. Quotes come from Wise's public comparison feed and are collected
      on their cadence, not live to the second &mdash; confirm in-app before sending.
    </p>"""


def build_html(
    spot: fx_data.SpotQuote,
    stats: Stats,
    model: TrendModel,
    decision: Decision,
    report: provider_module.ProviderReport,
    backtest: BacktestResult,
    context: dict,
    events: list[dict],
    config: Config,
    alerts: list[str],
) -> str:
    verdict_colour = {"SEND_NOW": GOOD, "HOLD": WARN, "FORCED_SEND": BAD}[decision.verdict]
    verdict_text = {
        "SEND_NOW": "Send now",
        "HOLD": "Hold for a better level",
        "FORCED_SEND": "Send today — window closing",
    }[decision.verdict]

    best = report.best
    mine = report.find(config.current_provider)
    cards: list[str] = []

    if alerts:
        cards.append(
            _card(
                "⚡ WHY YOU ARE GETTING THIS NOW",
                "".join(f"<div style='margin:0 0 6px 0;'>• {reason}</div>" for reason in alerts),
                BAD,
            )
        )

    # --- 1. the certain saving, which is the whole point ---
    if best:
        switch_note = ""
        if mine and mine is not best:
            gap = best.quote.received_inr - mine.quote.received_inr
            if gap > 0:
                switch_note = (
                    f"<div style='margin-top:10px;padding:10px;background:#f0fdf4;border-radius:6px;'>"
                    f"Switching from <b>{mine.display_name}</b> to <b>{best.display_name}</b> adds "
                    f"<b style='color:{GOOD};'>₹{inr(gap)}</b> on this transfer "
                    f"(₹{inr(gap * 12)} a year at ${config.send_amount:,.0f}/month). "
                    f"This is a certain gain, unlike waiting for a better rate.</div>"
                )
        elif mine is best:
            switch_note = (
                f"<div style='margin-top:10px;padding:10px;background:#f0fdf4;border-radius:6px;'>"
                f"<b>{mine.display_name}</b> is already the best of the {len(report.views)} providers "
                f"quoted. Nothing to change.</div>"
            )
        cards.append(
            _card(
                "① BEST WAY TO SEND — decided, not predicted",
                f"""<div style="font:700 26px/1.2 -apple-system,sans-serif;color:{INK};">
                      {best.display_name} → ₹{inr(best.quote.received_inr)}
                    </div>
                    <div style="color:{MUTED};margin-top:4px;">
                      on ${config.send_amount:,.0f} &nbsp;·&nbsp; rate {best.quote.rate:.3f} &nbsp;·&nbsp;
                      fee ${best.quote.fee:,.2f} &nbsp;·&nbsp; all-in cost {best.total_cost_pct:.2f}%
                    </div>
                    <div style="color:{MUTED};margin-top:6px;">
                      Worst provider quoted would deliver ₹{inr(report.worst.quote.received_inr)} —
                      a spread of <b>₹{inr(report.spread_inr)}</b> on the same dollars.
                    </div>
                    {switch_note}""",
                GOOD,
            )
        )

    # --- 2. the timing call, with its measured track record attached ---
    events_in_window = [
        event
        for event in events
        if decision.deadline >= event.get("date", "") >= date.today().isoformat()
    ]
    event_note = ""
    if events_in_window:
        listed = "; ".join(f"{e['date']} {e['label']}" for e in events_in_window)
        event_note = (
            f"<div style='margin-top:8px;color:{WARN};'>Inside this window: {listed}. "
            f"Policy days are the most likely source of a sharp move either way.</div>"
        )
    cards.append(
        _card(
            "② TIMING — take a strong level, do not forecast",
            f"""<div style="font:700 22px/1.2 -apple-system,sans-serif;color:{verdict_colour};">
                  {verdict_text}
                </div>
                <div style="margin-top:8px;">
                  Spot <b>{stats.spot:.3f}</b> versus a risk-adjusted value of waiting of
                  <b>{decision.reservation_rate:.3f}</b> ({signed_pct(decision.edge_pct)}).
                </div>
                <div style="color:{MUTED};margin-top:6px;">
                  {decision.days_left} business day(s) of flexibility left, deadline
                  <b>{decision.deadline}</b>. Model median at deadline
                  {decision.expected_rate_at_deadline:.2f}, with 80% of paths between
                  {decision.forecast_low:.2f} and {decision.forecast_high:.2f}
                  (±₹{inr(config.send_amount * (decision.forecast_high - decision.forecast_low) / 2)}
                  on ${config.send_amount:,.0f}).
                </div>
                {event_note}
                <div style="margin-top:10px;padding:10px;background:#fffbeb;border-radius:6px;font-size:13px;">
                  <b>Track record of the rule in use:</b> {' '.join(backtest.summary_lines()[:3])}
                  Perfect hindsight timing was worth only
                  ₹{inr(sum(config.send_amount * (m.best_rate - m.naive_rate) for m in backtest.months) / max(backtest.n, 1))}
                  a month, so treat this as a nudge, never a forecast.
                </div>""",
            verdict_colour,
        )
    )

    # --- 3. provider detail ---
    cards.append(_card("③ ALL PROVIDERS", _provider_table(report, config), ACCENT))

    # --- 4. where the level sits ---
    percentile_rows = "".join(
        f"<tr><td style='padding:2px 0;color:{MUTED};'>Better than last {window}d</td>"
        f"<td align='right'><b>{value:.0f}%</b></td></tr>"
        for window, value in sorted(stats.percentile.items())
    )
    ma_rows = "".join(
        f"<tr><td style='padding:2px 0;color:{MUTED};'>vs {window}-day average</td>"
        f"<td align='right'><b>{signed_pct(stats.vs_ma_pct(window))}</b></td></tr>"
        for window in sorted(stats.ma)
    )
    cards.append(
        _card(
            "④ WHERE THIS LEVEL SITS",
            f"""{_range_bar(stats)}
                <table width="100%" style="font:400 13px/1.5 -apple-system,sans-serif;margin-top:10px;">
                  {percentile_rows}{ma_rows}
                  <tr><td style="padding:2px 0;color:{MUTED};">30-day / 90-day change</td>
                      <td align="right"><b>{signed_pct(stats.change.get(30))} / {signed_pct(stats.change.get(90))}</b></td></tr>
                  <tr><td style="padding:2px 0;color:{MUTED};">Realised volatility (60d, annualised)</td>
                      <td align="right"><b>{stats.realized_vol_pct:.1f}%</b></td></tr>
                </table>""",
            ACCENT,
        )
    )

    # --- 5. what is pushing the pair ---
    if context:
        marker_rows = "".join(
            f"<tr><td style='padding:2px 0;color:{MUTED};'>{marker['label']}</td>"
            f"<td align='right'><b>{marker['last']:,.2f}</b></td>"
            f"<td align='right' style='color:{MUTED};'>1m {signed_pct(marker.get('change_1m'), 1)}</td>"
            f"<td align='right' style='color:{MUTED};'>3m {signed_pct(marker.get('change_3m'), 1)}</td></tr>"
            for marker in context.values()
        )
        cards.append(
            _card(
                "⑤ WHAT IS DRIVING THE PAIR",
                f"""<table width="100%" style="font:400 13px/1.5 -apple-system,sans-serif;">{marker_rows}</table>
                    <p style="margin:8px 0 0 0;font:400 11px/1.5 -apple-system,sans-serif;color:{MUTED};">
                      A stronger dollar and costlier crude both tend to push USD/INR up: India imports
                      roughly 85% of its oil and pays in dollars. Equity outflows work the same way.
                      Context for the level, not inputs to the model above.
                    </p>""",
                ACCENT,
            )
        )

    # --- 6. model internals, so the advice is auditable ---
    half_life = (
        f"{model.half_life_days:.0f} days" if model.half_life_days and model.stationary else "not measurable"
    )
    cards.append(
        _card(
            "⑥ MODEL INTERNALS",
            f"""<table width="100%" style="font:400 13px/1.5 -apple-system,sans-serif;">
                  <tr><td style="color:{MUTED};">Trend used (shrunk from fitted)</td>
                      <td align="right"><b>{model.annual_drift_pct:+.1f}%/yr</b>
                      <span style="color:{MUTED};">(fitted {model.annual_drift_pct_unshrunk:+.1f}%)</span></td></tr>
                  <tr><td style="color:{MUTED};">Mean-reversion half-life</td><td align="right"><b>{half_life}</b></td></tr>
                  <tr><td style="color:{MUTED};">AR(1) persistence φ</td><td align="right"><b>{model.phi:.3f}</b></td></tr>
                  <tr><td style="color:{MUTED};">Stretch vs own trend</td><td align="right"><b>{model.stretch_pct:+.2f}%</b></td></tr>
                  <tr><td style="color:{MUTED};">Innovation vol / trend fit R²</td>
                      <td align="right"><b>{model.annual_vol_pct:.1f}%/yr · {model.r_squared:.2f}</b></td></tr>
                  <tr><td style="color:{MUTED};">Observations fitted</td><td align="right"><b>{model.n_obs}</b></td></tr>
                </table>
                <p style="margin:8px 0 0 0;font:400 11px/1.5 -apple-system,sans-serif;color:{MUTED};">
                  Trend plus AR(1) reversion in log space, solved as an optimal-stopping problem by
                  backward induction over the remaining window under CRRA risk aversion
                  (γ={config.risk_aversion:g}). The fitted trend is deliberately halved: extrapolating
                  years of depreciation at full strength is how you talk yourself into waiting forever.
                  {'Reversion was not measurable in this sample, so the level signal is weak.' if not model.stationary else ''}
                </p>""",
            MUTED,
        )
    )

    footer_events = "".join(
        f"<div>• {event['date']} — <b>{event['label']}</b> "
        f"<span style='color:{MUTED};'>{event.get('detail','')}</span></div>"
        for event in events[:5]
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>USD → INR remittance brief</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:18px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
  <tr><td style="padding:0 0 16px 0;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:{ACCENT};border-radius:10px;">
      <tr><td style="padding:18px;text-align:center;color:#ffffff;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
        <div style="font:600 13px/1.4 -apple-system,sans-serif;opacity:.85;letter-spacing:.5px;">USD → INR REMITTANCE BRIEF</div>
        <div style="font:700 40px/1.1 -apple-system,sans-serif;margin:8px 0 2px 0;">₹{stats.spot:.3f}</div>
        <div style="font:400 13px/1.4 -apple-system,sans-serif;opacity:.9;">
          {('day ' + signed_pct(spot.day_change_pct)) if spot.day_change_pct is not None else 'mid-market'}
          &nbsp;·&nbsp; ${config.send_amount:,.0f} ≈ ₹{inr(config.send_amount * stats.spot)} at mid-market
        </div>
        <div style="font:400 11px/1.4 -apple-system,sans-serif;opacity:.75;margin-top:6px;">
          {spot.source} &nbsp;·&nbsp; {spot.as_of.strftime('%Y-%m-%d %H:%M UTC')}
        </div>
      </td></tr>
    </table>
  </td></tr>
  {''.join(cards)}
  <tr><td style="padding:0 0 18px 0;">
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;">
      <tr><td style="padding:14px;background:#ffffff;font:400 12px/1.6 -apple-system,sans-serif;color:{MUTED};">
        <b style="color:{INK};">Policy dates to keep in view</b>
        {footer_events or '<div>No dates loaded.</div>'}
        <div style="margin-top:10px;">
          Data: Yahoo Finance (USDINR=X), open.er-api.com, Wise public comparison API.
          Rates are informational, not a recommendation to trade. Verify the rate in your
          provider's app before sending.
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------

def send_email(config: Config, subject: str, html: str) -> None:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = config.sender_email
    message["To"] = ", ".join(config.receivers)
    message.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=30) as server:
        server.starttls(context=context)
        server.login(config.sender_email, config.sender_password)
        server.sendmail(config.sender_email, config.receivers, message.as_string())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("digest", "alert", "dry-run"), default="digest")
    parser.add_argument("--history-years", type=int, default=5, help="years of history to fit and backtest on")
    args = parser.parse_args(argv)

    config = Config()
    today = date.today()

    problems = config.validate()
    if problems and args.mode != "dry-run":
        print("Configuration errors:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    # --- data ---
    spot = fx_data.fetch_spot()
    series = fx_data.fetch_pair_history(years=args.history_years)
    closes = list(series.closes)
    dates = list(series.dates)
    # The live intraday print is more current than the last daily bar; use it as
    # today's observation rather than appending a duplicate day.
    if dates and dates[-1] == today.isoformat():
        closes[-1] = spot.rate
    else:
        dates.append(today.isoformat())
        closes.append(spot.rate)

    stats = compute_stats(closes)
    model = fit_trend_model(closes, drift_shrink=config.drift_shrink)
    payday, deadline, days_left, total_window = compute_window(today, config.payday_day, config.flex_days)
    decision = decide(
        stats,
        model,
        days_left,
        deadline.isoformat(),
        total_window,
        risk_aversion=config.risk_aversion,
        good_level_percentile=config.alert_percentile,
    )

    try:
        quotes = fx_data.fetch_provider_quotes(config.send_amount)
    except fx_data.DataUnavailable as exc:
        print(f"warning: provider feed unavailable ({exc})", file=sys.stderr)
        quotes = []

    state = load_state()
    preliminary = provider_module.build_report(quotes, stats.spot, config.send_amount)
    baselines = record_markups(state, preliminary, today.isoformat())
    report = provider_module.build_report(quotes, stats.spot, config.send_amount, baselines)

    try:
        context = fx_data.fetch_context_markers()
    except fx_data.DataUnavailable:
        context = {}

    backtest = run_backtest(
        dates,
        closes,
        payday=config.payday_day,
        flex_days=config.flex_days,
        risk_aversion=config.risk_aversion,
        send_amount=config.send_amount,
        good_level_percentile=config.alert_percentile,
    )
    events = load_events()
    alerts = alert_reasons(stats, decision, report, config, state, today)

    # --- act ---
    mine = report.find(config.current_provider)
    summary = (
        f"spot {stats.spot:.3f} | {decision.verdict} (edge {decision.edge_pct:+.2f}%, "
        f"{days_left}d left, deadline {deadline}) | best {report.best.display_name if report.best else 'n/a'}"
        f" ₹{inr(report.best.quote.received_inr) if report.best else 'n/a'}"
    )

    if args.mode == "alert" and not alerts:
        print(f"no alert conditions met — staying quiet. {summary}")
        save_state(state)
        return 0

    html = build_html(spot, stats, model, decision, report, backtest, context, events, config, alerts)

    if args.mode == "dry-run":
        Path("preview.html").write_text(html)
        print(summary)
        print("\n".join(f"  · {line}" for line in decision.rationale))
        print("\nbacktest:")
        print("\n".join(f"  · {line}" for line in backtest.summary_lines()))
        if report.warnings:
            print("\nwarnings:")
            print("\n".join(f"  ! {w}" for w in report.warnings))
        print(f"\nalerts: {alerts or 'none'}")
        print("\nwrote preview.html")
        return 0

    verdict_tag = {"SEND_NOW": "SEND", "HOLD": "HOLD", "FORCED_SEND": "SEND TODAY"}[decision.verdict]
    prefix = "⚡ " if alerts else ""
    best_name = report.best.display_name if report.best else "rate only"
    subject = f"{prefix}USD→INR ₹{stats.spot:.2f} · {verdict_tag} · {best_name}"
    send_email(config, subject, html)

    append_history(
        {
            "date": today.isoformat(),
            "spot": round(stats.spot, 4),
            "verdict": decision.verdict,
            "score": decision.score,
            "reservation_rate": round(decision.reservation_rate, 4),
            "edge_pct": round(decision.edge_pct, 4),
            "best_provider": report.best.display_name if report.best else "",
            "best_received_inr": round(report.best.quote.received_inr, 2) if report.best else "",
            "current_provider_received_inr": round(mine.quote.received_inr, 2) if mine else "",
            "days_left": days_left,
        }
    )
    state["last_digest_date" if not alerts else "last_alert_date"] = today.isoformat()
    if alerts:
        state["last_digest_date"] = today.isoformat()
    save_state(state)
    print(f"sent to {', '.join(config.receivers)} — {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
