"""Route costing for INR -> USD: what it takes to land a fixed dollar amount in Chase.

The student pays a monthly USD amount out of a Credila education loan and has
two routes, both executed by Credila rather than chosen from a market:

  * Credila wire   - SWIFT from Credila's Indian bank at that bank's TT-selling
                     card rate, plus remittance charges, GST on the currency
                     conversion, correspondent deductions en route and Chase's
                     incoming-wire fee on arrival.
  * Global Pay     - Credila's payment partner. Converts at its own quoted rate
                     and pays out domestically in the US, so the SWIFT chain
                     and Chase's incoming-wire fee usually disappear. The whole
                     cost sits inside the rate markup.

Neither route publishes a live quote without logging in, so each is modelled as
"mid-market plus markup plus fees" with every number overridable from GitHub
variables. The markups are the uncertain part: once she has a real transfer
receipt, `implied_markup_pct` turns it into a calibrated value.

Wise's public quote is fetched as a yardstick. It is not available to a sender
living in the US, but it is an honest live measure of what a near-mid-market
transfer costs, which is what makes a bank markup visible as a number.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace

import requests

WISE_QUOTE = "https://api.wise.com/v3/quotes/"
USER_AGENT = "Mozilla/5.0 (inr-usd-brief)"


def gst_on_conversion(inr_amount: float) -> float:
    """GST on currency conversion (Rule 32(2)(b), CGST Rules), 18% of a slab value.

    Taxable value: 1% of the gross amount up to ₹1 lakh (minimum ₹250);
    ₹1,000 + 0.5% of the part between ₹1 lakh and ₹10 lakh; above that
    ₹5,500 + 0.1% of the excess, capped at ₹60,000.
    """
    if inr_amount <= 100_000:
        value = max(250.0, 0.01 * inr_amount)
    elif inr_amount <= 1_000_000:
        value = 1_000.0 + 0.005 * (inr_amount - 100_000)
    else:
        value = min(60_000.0, 5_500.0 + 0.001 * (inr_amount - 1_000_000))
    return 0.18 * value


@dataclass
class Route:
    name: str
    markup_pct: float            # rate markup over mid-market, in percent
    fee_inr: float = 0.0         # flat charges in INR before GST (processing, SWIFT)
    fee_gst: bool = True         # whether 18% GST applies to fee_inr
    conversion_gst: bool = True  # whether GST on currency conversion applies
    usd_deductions: float = 0.0  # correspondent + beneficiary-bank fees, taken from the dollars
    notes: list[str] = field(default_factory=list)
    available: bool = True       # False for yardsticks she cannot actually use


@dataclass
class RouteCost:
    route: Route
    rate: float                  # INR per USD applied
    inr_converted: float
    fees_inr: float
    gst_inr: float
    total_inr: float
    extra_vs_mid: float          # rupees above a zero-fee mid-market purchase
    usd_landed: float

    @property
    def cost_pct(self) -> float:
        ideal = self.total_inr - self.extra_vs_mid
        return 100.0 * self.extra_vs_mid / ideal if ideal else 0.0


def cost_to_land(route: Route, usd_target: float, mid: float) -> RouteCost:
    """Total rupees needed so that exactly `usd_target` lands in the account.

    Deductions taken from the dollars en route are grossed up: to land $1,000
    through a $15 correspondent and a $15 Chase fee, $1,030 must be bought.
    """
    rate = mid * (1.0 + route.markup_pct / 100.0)
    gross_usd = usd_target + route.usd_deductions
    converted = gross_usd * rate
    fees = route.fee_inr
    gst = (0.18 * fees if route.fee_gst else 0.0) + (gst_on_conversion(converted) if route.conversion_gst else 0.0)
    total = converted + fees + gst
    return RouteCost(
        route=route,
        rate=rate,
        inr_converted=converted,
        fees_inr=fees,
        gst_inr=gst,
        total_inr=total,
        extra_vs_mid=total - usd_target * mid,
        usd_landed=usd_target,
    )


def implied_markup_pct(inr_debited: float, usd_received: float, mid_on_day: float, route: Route) -> float:
    """Back out a route's true markup from a real receipt, fees held at their modelled values.

    This is how the defaults get replaced by facts: plug in what Credila
    debited, what reached Chase, and that day's mid-market close.
    """
    gross_usd = usd_received + route.usd_deductions
    fees = route.fee_inr * (1.18 if route.fee_gst else 1.0)
    # GST depends on the converted amount, which depends on the rate; one
    # fixed-point pass is plenty at these magnitudes.
    converted = inr_debited - fees
    for _ in range(3):
        gst = gst_on_conversion(converted) if route.conversion_gst else 0.0
        converted = inr_debited - fees - gst
    return 100.0 * (converted / gross_usd / mid_on_day - 1.0)


def fetch_wise_benchmark(usd_target: float, timeout: int = 20) -> dict | None:
    """Wise's live INR cost to land `usd_target`, or None if the endpoint misbehaves.

    Unauthenticated quotes are public. The India pay-in option comes back
    marked disabled for non-residents, but its price is still quoted, which is
    all a yardstick needs.
    """
    try:
        response = requests.post(
            WISE_QUOTE,
            json={"sourceCurrency": "INR", "targetCurrency": "USD", "targetAmount": usd_target},
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        options = response.json().get("paymentOptions") or []
    except (requests.RequestException, ValueError) as exc:
        print(f"warning: Wise benchmark unavailable ({exc})", file=sys.stderr)
        return None
    bank = [o for o in options if o.get("payIn") == "BANK_TRANSFER" and o.get("payOut") == "BANK_TRANSFER"]
    pick = min(bank or options, key=lambda o: o.get("sourceAmount") or float("inf"), default=None)
    if not pick or not pick.get("sourceAmount"):
        return None
    return {"total_inr": float(pick["sourceAmount"]), "fee_pct": 100.0 * float(pick.get("feePercentage") or 0.0)}


# --------------------------------------------------------------------------
# bank card rates (public HTML, no key)
# --------------------------------------------------------------------------

# Credila disburses into an HSBC India account opened at loan signing, and the
# onward wire converts at HSBC's TT-selling card rate. IOB is the student's own
# bank and prices far tighter, which is worth knowing when talking to Credila.
CARD_RATE_PAGES = {
    # name: (url, cell that starts the USD row, offset of TT SELL after it)
    "HSBC India": ("https://www.hsbc.bank.in/nri/foreign-exchange-rates/", "United States Dollar (USD)", 4),
    "IOB": ("https://www.iob.bank.in/en/forex-rates", "USD", 2),
}


@dataclass
class CardRate:
    bank: str
    tt_sell: float
    updated: str


def _cells(html: str) -> list[str]:
    """Flatten a page to its visible text cells, in document order."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    cells = (re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip() for c in re.split(r"<[^>]+>", html))
    return [c for c in cells if c]


def parse_card_rate(bank: str, html: str) -> CardRate | None:
    """USD TT-selling rate from a bank's card-rate page, or None if the layout moved."""
    _, row_cell, offset = CARD_RATE_PAGES[bank]
    cells = _cells(html)
    try:
        value = float(cells[cells.index(row_cell) + offset])
    except (ValueError, IndexError):
        return None
    # A card rate always sits within a few percent of mid; anything else means
    # a column shifted and the wrong cell is being read.
    if not 50 < value < 200:
        return None
    text = " ".join(cells)
    stamp = re.search(r"Updated on:\s*(.+?IST)", text) or re.search(
        r"CARD RATES\s*-\s*([\d.]+\s*updated at\s*[\d.:]+\s*[AP]M)", text
    )
    return CardRate(bank=bank, tt_sell=value, updated=stamp.group(1).strip() if stamp else "unknown")


def fetch_card_rate(bank: str, timeout: int = 25) -> CardRate | None:
    try:
        response = requests.get(CARD_RATE_PAGES[bank][0], headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"warning: {bank} card rate unavailable ({exc})", file=sys.stderr)
        return None
    rate = parse_card_rate(bank, response.text)
    if rate is None:
        print(f"warning: {bank} card-rate page layout not recognised", file=sys.stderr)
    return rate


def break_even_markup(route: Route, target_total: float, usd_target: float, mid: float) -> float:
    """The markup at which `route` costs exactly `target_total`. Cost rises with markup, so bisect."""
    low, high = -5.0, 10.0
    for _ in range(60):
        trial = replace(route, markup_pct=(low + high) / 2)
        if cost_to_land(trial, usd_target, mid).total_inr > target_total:
            high = trial.markup_pct
        else:
            low = trial.markup_pct
    return (low + high) / 2
