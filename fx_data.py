"""Data acquisition for the USD/INR remittance notifier.

Every source here is free and key-less. Fetches degrade gracefully: an optional
source that fails returns None and the pipeline continues with a warning, but a
failed spot fetch raises, because an email without a rate is worse than silence.

Sources
    Yahoo Finance chart API  - intraday spot + multi-year daily history (primary)
    open.er-api.com          - key-less daily reference rate (cross-check + fallback)
    Wise Comparison API      - public multi-provider effective rates and fees
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
ER_API = "https://open.er-api.com/v6/latest/USD"
FRANKFURTER_SERIES = "https://api.frankfurter.dev/v1/{start}.."
WISE_COMPARISON = "https://api.wise.com/v4/comparisons/"

PAIR_SYMBOL = "USDINR=X"

# Daily history changes once a day, so it is cached on disk. Yahoo throttles by
# IP and GitHub Actions runners share addresses, so a 429 during a scheduled run
# is routine rather than exceptional. Serving a slightly stale local copy is far
# better than failing the run, and it keeps repeated local testing off the API.
CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_TTL_HOURS = 8

# Context markers. USD/INR is driven less by its own history than by these:
# a stronger dollar and costlier crude both push the pair up, because India
# imports roughly 85% of the oil it burns and pays for it in dollars.
CONTEXT_SYMBOLS = {
    "DXY": ("DX-Y.NYB", "US Dollar Index"),
    "BRENT": ("BZ=F", "Brent crude"),
    "NIFTY": ("^NSEI", "Nifty 50"),
}


class DataUnavailable(RuntimeError):
    """Raised when no source could supply a spot rate."""


@dataclass
class Series:
    """A daily close series, oldest first, with gaps already dropped."""

    symbol: str
    dates: list[str]
    closes: list[float]

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def last(self) -> float:
        return self.closes[-1]

    def change_pct(self, lookback: int) -> float | None:
        """Percent change over `lookback` observations, or None if too short."""
        if len(self.closes) <= lookback:
            return None
        return 100.0 * (self.closes[-1] / self.closes[-1 - lookback] - 1.0)


@dataclass
class SpotQuote:
    rate: float
    source: str
    as_of: datetime
    previous_close: float | None = None

    @property
    def day_change_pct(self) -> float | None:
        if not self.previous_close:
            return None
        return 100.0 * (self.rate / self.previous_close - 1.0)


@dataclass
class ProviderQuote:
    """One provider's offer, normalised to what actually lands in India."""

    name: str
    kind: str          # "bank" or "moneyTransferProvider"
    rate: float        # exchange rate applied to the transfer
    fee: float         # USD fee charged on top
    received_inr: float
    markup_pct: float  # provider's own stated FX markup vs mid-market
    collected_at: str | None = None

    def total_cost_pct(self, mid_market: float, send_amount: float) -> float:
        """All-in cost vs a hypothetical mid-market transfer with zero fee.

        This is the only number worth comparing across providers: a zero-fee
        provider with a wide spread and a zero-spread provider with a fat fee
        can easily land on opposite sides of where their headlines suggest.
        """
        ideal = send_amount * mid_market
        if ideal <= 0:
            return 0.0
        return 100.0 * (1.0 - self.received_inr / ideal)


def _get_json(url: str, params: dict | None = None, timeout: int = 20, retries: int = 3) -> dict:
    """GET JSON with linear backoff.

    Uses requests rather than urllib so certificate verification goes through
    certifi's bundle. A stock macOS Python has no CA bundle wired up and fails
    every HTTPS call with CERTIFICATE_VERIFY_FAILED, which makes local runs
    impossible to debug even though CI would have worked.
    """
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries - 1:
                throttled = isinstance(exc, requests.HTTPError) and getattr(
                    exc.response, "status_code", None
                ) == 429
                time.sleep((6.0 if throttled else 1.5) * (attempt + 1))
    raise DataUnavailable(f"{url} failed after {retries} attempts: {last_error}")


def _cache_path(symbol: str, period: str, interval: str) -> Path:
    safe_name = urllib.parse.quote(symbol, safe="") + f"_{period}_{interval}.json"
    return CACHE_DIR / safe_name


def _read_cache(path: Path, ttl_hours: float | None) -> dict | None:
    """Return cached payload, or None if absent or older than ttl_hours."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        stamped = payload.get("_cached_at")
        if ttl_hours is not None and stamped:
            age = datetime.now(tz=timezone.utc) - datetime.fromisoformat(stamped)
            if age > timedelta(hours=ttl_hours):
                return None
        return payload
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["_cached_at"] = datetime.now(tz=timezone.utc).isoformat()
        path.write_text(json.dumps(payload))
    except OSError as exc:
        print(f"warning: could not write cache {path.name} ({exc})", file=sys.stderr)


def fetch_series(
    symbol: str,
    period: str = "2y",
    interval: str = "1d",
    cache_ttl_hours: float | None = CACHE_TTL_HOURS,
    retries: int = 3,
) -> Series:
    """Daily closes for a Yahoo symbol, oldest first, nulls dropped.

    Served from the on-disk cache when fresh. If the network call fails, a stale
    cache is used with a warning rather than aborting the run.
    """
    path = _cache_path(symbol, period, interval)
    payload = _read_cache(path, cache_ttl_hours)

    if payload is None:
        # `safe="=^"` keeps symbols like USDINR=X and ^NSEI literal in the path,
        # which is the form verified against the API.
        encoded = urllib.parse.quote(symbol, safe="=^")
        try:
            payload = _get_json(
                YAHOO_CHART.format(symbol=encoded),
                {"range": period, "interval": interval},
                retries=retries,
            )
            _write_cache(path, payload)
        except DataUnavailable as exc:
            stale = _read_cache(path, ttl_hours=None)
            if stale is None:
                raise
            print(
                f"warning: {symbol} live fetch failed ({exc}); using cached copy from "
                f"{stale.get('_cached_at', 'unknown time')}",
                file=sys.stderr,
            )
            payload = stale

    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    dates: list[str] = []
    values: list[float] = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue  # market holidays and mid-session gaps arrive as nulls
        dates.append(datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d"))
        values.append(float(close))
    if not values:
        raise DataUnavailable(f"{symbol} returned no usable closes")
    return Series(symbol=symbol, dates=dates, closes=values)


def _fetch_frankfurter_history(start: str) -> Series:
    """USD/INR daily history from the ECB reference series via Frankfurter.

    Preferred over Yahoo for history: it is official reference data, needs no
    key, and does not rate-limit. The tradeoff is resolution -- the ECB fixes
    once per weekday, so this series has no intraday detail and skips European
    holidays. That is immaterial for a model fitted on daily closes.
    """
    payload = _get_json(FRANKFURTER_SERIES.format(start=start), {"base": "USD", "symbols": "INR"})
    rates = payload.get("rates") or {}
    dates: list[str] = []
    values: list[float] = []
    for day in sorted(rates):
        value = (rates[day] or {}).get("INR")
        if value:
            dates.append(day)
            values.append(float(value))
    if not values:
        raise DataUnavailable("Frankfurter returned no USD/INR observations")
    return Series(symbol="USDINR (ECB)", dates=dates, closes=values)


def fetch_pair_history(years: int = 5, cache_ttl_hours: float | None = CACHE_TTL_HOURS) -> Series:
    """USD/INR daily history, from whichever source is reachable.

    Order is Frankfurter, then Yahoo, then any cached copy however stale. The
    model cannot run without history, so this is the one fetch with three
    independent ways to succeed.
    """
    start = (datetime.now(tz=timezone.utc).date() - timedelta(days=int(years * 365.25))).isoformat()
    path = CACHE_DIR / f"pair_history_{years}y.json"

    cached = _read_cache(path, cache_ttl_hours)
    if cached:
        return Series(symbol=cached["symbol"], dates=cached["dates"], closes=cached["closes"])

    errors: list[str] = []
    for label, loader in (
        ("Frankfurter/ECB", lambda: _fetch_frankfurter_history(start)),
        ("Yahoo", lambda: fetch_series(PAIR_SYMBOL, period=f"{years}y", cache_ttl_hours=None)),
    ):
        try:
            series = loader()
            _write_cache(path, {"symbol": series.symbol, "dates": series.dates, "closes": series.closes})
            return series
        except (DataUnavailable, KeyError, IndexError, TypeError) as exc:
            errors.append(f"{label}: {exc}")

    stale = _read_cache(path, ttl_hours=None)
    if stale:
        print(
            f"warning: all live history sources failed ({'; '.join(errors)}); "
            f"using cached copy from {stale.get('_cached_at', 'unknown time')}",
            file=sys.stderr,
        )
        return Series(symbol=stale["symbol"], dates=stale["dates"], closes=stale["closes"])
    raise DataUnavailable("no USD/INR history available -> " + "; ".join(errors))


def fetch_spot() -> SpotQuote:
    """Live USD/INR, preferring Yahoo's intraday print over a daily reference."""
    try:
        encoded = urllib.parse.quote(PAIR_SYMBOL, safe="=^")
        payload = _get_json(YAHOO_CHART.format(symbol=encoded), {"range": "5d", "interval": "1d"})
        meta = payload["chart"]["result"][0]["meta"]
        rate = meta.get("regularMarketPrice")
        if rate:
            stamp = meta.get("regularMarketTime")
            as_of = (
                datetime.fromtimestamp(stamp, tz=timezone.utc)
                if stamp
                else datetime.now(tz=timezone.utc)
            )
            return SpotQuote(
                rate=float(rate),
                source="Yahoo Finance (USDINR=X)",
                as_of=as_of,
                previous_close=meta.get("chartPreviousClose"),
            )
    except (DataUnavailable, KeyError, IndexError, TypeError) as exc:
        # Fall through to the daily reference source, but say why. A silent
        # fallback hides the case where the primary source has broken for good.
        print(f"warning: Yahoo spot unavailable, using daily reference ({exc})", file=sys.stderr)

    payload = _get_json(ER_API)
    rate = (payload.get("rates") or {}).get("INR")
    if not rate:
        raise DataUnavailable("neither Yahoo nor open.er-api returned a USD/INR rate")
    return SpotQuote(rate=float(rate), source="open.er-api.com (daily)", as_of=datetime.now(tz=timezone.utc))


def fetch_provider_quotes(send_amount: float) -> list[ProviderQuote]:
    """Live provider offers from Wise's public comparison endpoint.

    Wise publishes its competitors' rates alongside its own, which is the only
    free way to see what Remitly, SBI or a US bank would actually pay out. The
    quotes are collected by Wise on their own cadence, so `collected_at` is
    surfaced rather than assumed to be live.
    """
    payload = _get_json(
        WISE_COMPARISON,
        {"sourceCurrency": "USD", "targetCurrency": "INR", "sendAmount": f"{send_amount:.0f}"},
    )
    quotes: list[ProviderQuote] = []
    for provider in payload.get("providers") or []:
        name = provider.get("name") or provider.get("alias") or "unknown"
        kind = provider.get("type") or "unknown"
        for quote in provider.get("quotes") or []:
            received = quote.get("receivedAmount")
            rate = quote.get("rate")
            if not received or not rate:
                continue
            quotes.append(
                ProviderQuote(
                    name=name,
                    kind=kind,
                    rate=float(rate),
                    fee=float(quote.get("fee") or 0.0),
                    received_inr=float(received),
                    # The API reports markup already expressed in percent.
                    markup_pct=float(quote.get("markup") or 0.0),
                    collected_at=quote.get("dateCollected"),
                )
            )
    quotes.sort(key=lambda q: -q.received_inr)
    return quotes


def fetch_context_markers() -> dict[str, dict]:
    """DXY, Brent and Nifty moves. Missing markers are omitted, never fatal.

    Yahoo-only, so these are the first thing to disappear when it throttles.
    That is acceptable: they explain the level rather than feeding the model.
    """
    markers: dict[str, dict] = {}
    for key, (symbol, label) in CONTEXT_SYMBOLS.items():
        try:
            # One attempt only. These markers are context, not inputs, and Yahoo
            # backs off 6s per retry on a 429 -- three symbols retrying three
            # times each would spend two minutes to render a cosmetic card.
            series = fetch_series(symbol, period="6mo", retries=1)
        except (DataUnavailable, KeyError, IndexError):
            continue
        markers[key] = {
            "label": label,
            "symbol": symbol,
            "last": series.last,
            "change_1m": series.change_pct(22),
            "change_3m": series.change_pct(66),
        }
    return markers
