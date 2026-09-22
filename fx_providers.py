"""Provider intelligence: what each service actually pays out, and when that changes.

On a $3,000 transfer the gap between the best and worst provider is routinely
larger than a month of exchange-rate movement, and unlike the rate it is a
certain gain rather than a bet. So this module treats provider selection as the
primary lever and the exchange rate as the secondary one.

Two things are tracked that a single snapshot cannot show:

  1. All-in cost, not headline rate. A zero-fee provider with a wide spread and
     a zero-spread provider with a fat fee are routinely ranked the wrong way
     round by their own marketing. Only INR-in-hand settles it.
  2. Markup drift. Promotional rates for new customers decay, quietly, weeks
     later. Comparing today's markup against the trailing median for the same
     provider catches that before it costs a transfer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fx_data import ProviderQuote

# Providers the user actually holds or would consider, mapped to the names the
# Wise comparison feed reports. Anything else still appears in the table, but
# these are called out as actionable.
TRACKED_ALIASES = {
    "wise": "Wise",
    "remitly": "Remitly",
    "state bank of india": "SBI (bank)",
    "icici": "ICICI Money2India",
    "xoom": "Xoom (PayPal)",
    "western union": "Western Union",
    "worldremit": "WorldRemit",
    "instarem": "Instarem",
}

# A markup this far above a provider's own trailing median is treated as a
# promotional rate having expired, which is worth an explicit warning.
MARKUP_DRIFT_ALERT_PCT = 0.15


@dataclass
class ProviderView:
    """A provider quote enriched with cost and drift context."""

    quote: ProviderQuote
    total_cost_pct: float
    inr_vs_best: float
    is_tracked: bool
    baseline_markup_pct: float | None = None
    markup_drift_pct: float | None = None

    @property
    def display_name(self) -> str:
        return TRACKED_ALIASES.get(self.quote.name.strip().lower(), self.quote.name)

    @property
    def promo_expired(self) -> bool:
        """True when this provider's spread has widened well beyond its norm."""
        return self.markup_drift_pct is not None and self.markup_drift_pct >= MARKUP_DRIFT_ALERT_PCT


@dataclass
class ProviderReport:
    views: list[ProviderView]
    mid_market: float
    send_amount: float
    best: ProviderView | None
    worst: ProviderView | None
    warnings: list[str] = field(default_factory=list)

    @property
    def spread_inr(self) -> float:
        """INR left on the table by choosing the worst listed provider."""
        if not self.best or not self.worst:
            return 0.0
        return self.best.quote.received_inr - self.worst.quote.received_inr

    @property
    def best_tracked(self) -> ProviderView | None:
        return next((view for view in self.views if view.is_tracked), None)

    def find(self, needle: str) -> ProviderView | None:
        needle = needle.strip().lower()
        return next((v for v in self.views if needle in v.quote.name.strip().lower()), None)


def build_report(
    quotes: list[ProviderQuote],
    mid_market: float,
    send_amount: float,
    markup_baselines: dict[str, float] | None = None,
) -> ProviderReport:
    """Rank providers by INR delivered and flag any that have drifted.

    `markup_baselines` maps a lowercased provider name to its trailing median
    markup from the stored history, which is what makes drift detectable.
    """
    baselines = markup_baselines or {}
    ranked = sorted(quotes, key=lambda q: -q.received_inr)
    if not ranked:
        return ProviderReport(
            views=[],
            mid_market=mid_market,
            send_amount=send_amount,
            best=None,
            worst=None,
            warnings=["Provider comparison feed returned no quotes; rate advice only."],
        )

    best_received = ranked[0].received_inr
    views: list[ProviderView] = []
    for quote in ranked:
        key = quote.name.strip().lower()
        baseline = baselines.get(key)
        drift = (quote.markup_pct - baseline) if baseline is not None else None
        views.append(
            ProviderView(
                quote=quote,
                total_cost_pct=quote.total_cost_pct(mid_market, send_amount),
                inr_vs_best=quote.received_inr - best_received,
                is_tracked=any(alias in key for alias in TRACKED_ALIASES),
                baseline_markup_pct=baseline,
                markup_drift_pct=drift,
            )
        )

    warnings: list[str] = []
    for view in views:
        if view.promo_expired:
            warnings.append(
                f"{view.display_name} markup widened to {view.quote.markup_pct:.2f}% "
                f"from a typical {view.baseline_markup_pct:.2f}% — a promotional rate may have ended."
            )

    stale = {v.quote.collected_at[:10] for v in views if v.quote.collected_at}
    if len(stale) > 1:
        warnings.append(
            "Provider quotes were collected on different dates; treat small gaps between "
            "close competitors as noise and confirm in-app before sending."
        )

    return ProviderReport(
        views=views,
        mid_market=mid_market,
        send_amount=send_amount,
        best=views[0],
        worst=views[-1],
        warnings=warnings,
    )
