"""The decision engine: when is today a good day to send USD to India?

Modelling choice, and why it matters
------------------------------------
A pure random walk with positive drift gives a degenerate answer. If USD/INR
only ever grinds upward, expected value says "always wait", and if you add
constant relative risk aversion to a geometric random walk the problem becomes
scale-invariant, so the *level* of the rate stops mattering at all. Neither
result is usable advice for somebody sending money on a monthly cadence.

So the model here is a trend plus mean reversion around it:

    log S_t = a + b*t + x_t          with   x_t = phi * x_(t-1) + e_t

The `b*t` term carries the structural story (INR has depreciated against USD for
years). The AR(1) residual `x_t` carries the part that actually reverts, which is
what makes today's level informative: a rate stretched above its own trend tends
to fall back toward it, and that reversion can offset the drift you give up by
waiting. Breaking scale-invariance this way is what produces a real threshold.

The decision is then an optimal-stopping problem solved by backward induction
over the days you are still willing to wait, scoring outcomes with a constant
relative risk aversion certainty equivalent. The output is a reservation rate:
the risk-adjusted rate that waiting is worth. Send when today beats it.

The trend slope is deliberately shrunk (see DEFAULT_DRIFT_SHRINK). Extrapolating
several years of depreciation forward at full strength is the single easiest way
to talk yourself into waiting forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

TRADING_DAYS = 252

DEFAULT_LOOKBACK = 500        # observations used to fit trend + reversion
DEFAULT_DRIFT_SHRINK = 0.5    # keep half the fitted trend; see module docstring
DEFAULT_RISK_AVERSION = 3.0   # CRRA gamma. 0 = indifferent to risk, higher = more eager to lock in
GRID_POINTS = 121
QUAD_NODES = 15


@dataclass
class TrendModel:
    """Fitted trend-plus-reversion model, in log space."""

    intercept: float
    slope_daily: float          # fitted, before shrinkage
    slope_used: float           # after shrinkage, what forecasts actually use
    phi: float                  # AR(1) persistence of the residual
    sigma_eps: float            # residual innovation std, daily
    residual_now: float         # today's deviation from trend, in logs
    log_spot: float             # log of the latest close; forecasts anchor here
    n_obs: int
    r_squared: float
    stationary: bool

    @property
    def half_life_days(self) -> float | None:
        """Days for a trend deviation to decay by half. None if it doesn't."""
        if not (0.0 < self.phi < 1.0):
            return None
        return -math.log(2.0) / math.log(self.phi)

    @property
    def annual_drift_pct(self) -> float:
        return 100.0 * (math.exp(self.slope_used * TRADING_DAYS) - 1.0)

    @property
    def annual_drift_pct_unshrunk(self) -> float:
        return 100.0 * (math.exp(self.slope_daily * TRADING_DAYS) - 1.0)

    @property
    def annual_vol_pct(self) -> float:
        """Annualised vol of the innovations."""
        return 100.0 * self.sigma_eps * math.sqrt(TRADING_DAYS)

    @property
    def stretch_pct(self) -> float:
        """How far above (+) or below (-) its own trend the rate sits, in percent."""
        return 100.0 * (math.exp(self.residual_now) - 1.0)


@dataclass
class Stats:
    """Plain descriptive statistics, no model assumptions."""

    spot: float
    ma: dict[int, float]
    percentile: dict[int, float]
    high_52w: float
    low_52w: float
    change: dict[int, float]
    realized_vol_pct: float

    def vs_ma_pct(self, window: int) -> float | None:
        if window not in self.ma:
            return None
        return 100.0 * (self.spot / self.ma[window] - 1.0)


@dataclass
class Decision:
    """The engine's call, with everything needed to explain it."""

    verdict: str                # SEND_NOW | HOLD | FORCED_SEND
    reservation_rate: float     # risk-adjusted rate that waiting is worth
    edge_pct: float             # spot vs reservation; positive means send
    score: int                  # 0-100 presentation score
    days_left: int
    deadline: str
    expected_rate_at_deadline: float
    forecast_low: float         # 80% band
    forecast_high: float
    rationale: list[str] = field(default_factory=list)


def compute_stats(closes: list[float]) -> Stats:
    """Descriptive stats on a daily close series, oldest first."""
    values = np.asarray(closes, dtype=float)
    spot = float(values[-1])

    ma: dict[int, float] = {}
    for window in (5, 20, 50, 100, 200):
        if len(values) >= window:
            ma[window] = float(values[-window:].mean())

    percentile: dict[int, float] = {}
    for window in (30, 90, 252, len(values)):
        if len(values) >= window:
            recent = values[-window:]
            # Share of the window that sits below today: 100 means the best
            # USD -> INR rate seen in that window.
            percentile[window] = float(100.0 * (recent < spot).sum() / len(recent))

    change: dict[int, float] = {}
    for window in (1, 7, 30, 90, 252):
        if len(values) > window:
            change[window] = float(100.0 * (spot / values[-1 - window] - 1.0))

    returns = np.diff(np.log(values))
    tail = returns[-60:] if len(returns) >= 60 else returns
    realized_vol = float(tail.std(ddof=1) * math.sqrt(TRADING_DAYS) * 100.0) if len(tail) > 2 else 0.0

    window_52w = values[-TRADING_DAYS:] if len(values) >= TRADING_DAYS else values
    return Stats(
        spot=spot,
        ma=ma,
        percentile=percentile,
        high_52w=float(window_52w.max()),
        low_52w=float(window_52w.min()),
        change=change,
        realized_vol_pct=realized_vol,
    )


def fit_trend_model(
    closes: list[float],
    lookback: int = DEFAULT_LOOKBACK,
    drift_shrink: float = DEFAULT_DRIFT_SHRINK,
) -> TrendModel:
    """Fit log-linear trend, then an AR(1) on what the trend leaves behind."""
    values = np.asarray(closes[-lookback:], dtype=float)
    if len(values) < 60:
        raise ValueError(f"need at least 60 observations to fit, got {len(values)}")

    logs = np.log(values)
    times = np.arange(len(logs), dtype=float)
    slope, intercept = np.polyfit(times, logs, 1)
    fitted = intercept + slope * times
    residuals = logs - fitted

    total_var = float(((logs - logs.mean()) ** 2).sum())
    r_squared = 1.0 - float((residuals**2).sum()) / total_var if total_var > 0 else 0.0

    # AR(1) through the origin: residuals are mean-zero by construction.
    lagged, current = residuals[:-1], residuals[1:]
    denominator = float((lagged**2).sum())
    phi = float((lagged * current).sum() / denominator) if denominator > 0 else 0.0
    stationary = 0.0 < phi < 1.0
    # A phi at or above 1 means no measurable reversion over this sample. Clamp
    # just below 1 so the induction stays finite, and flag it so the email can
    # say the level signal is weak rather than quietly pretending otherwise.
    phi_used = min(max(phi, 0.0), 0.999)

    innovations = current - phi_used * lagged
    sigma_eps = float(innovations.std(ddof=2)) if len(innovations) > 2 else 0.0

    return TrendModel(
        intercept=float(intercept),
        slope_daily=float(slope),
        slope_used=float(slope * drift_shrink),
        phi=phi_used,
        sigma_eps=sigma_eps,
        residual_now=float(residuals[-1]),
        log_spot=float(logs[-1]),
        n_obs=len(values),
        r_squared=r_squared,
        stationary=stationary,
    )


def forecast_distribution(model: TrendModel, horizon_days: int) -> tuple[float, float]:
    """Mean and std of the log rate `horizon_days` ahead. Horizon 0 is today.

    Anchored on today's actual close and expressed as increments from it:

        log S_(T+h) = log S_T + slope*h + (phi^h - 1) * x_T

    The `slope*h` term is the trend you give up by not waiting; the
    `(phi^h - 1) * x_T` term is mean reversion pulling a stretched rate back
    toward trend, and it carries the opposite sign to the stretch. Anchoring
    this way keeps horizon 0 exactly equal to today's rate, which a from-scratch
    rebuild of the trend line does not: the residual was fitted against the
    unshrunk slope, so reusing it with a shrunk slope silently misplaces the level.
    """
    mean = (
        model.log_spot
        + model.slope_used * horizon_days
        + (model.phi**horizon_days - 1.0) * model.residual_now
    )
    if model.phi >= 0.999 or model.phi <= 0.0:
        variance = (model.sigma_eps**2) * horizon_days
    else:
        variance = (model.sigma_eps**2) * (1.0 - model.phi ** (2 * horizon_days)) / (1.0 - model.phi**2)
    return float(mean), float(math.sqrt(max(variance, 0.0)))


def _crra_certainty_equivalent(values: np.ndarray, weights: np.ndarray, gamma: float) -> np.ndarray:
    """Certainty equivalent of a payoff distribution under CRRA utility.

    gamma = 0 collapses to the plain expectation; larger gamma prices in more of
    the downside, which is what makes the engine willing to lock a good rate
    rather than chase a slightly better expected one.
    """
    safe = np.maximum(np.atleast_2d(values), 1e-9)
    # Weighted sums are written as an elementwise product and a reduction
    # rather than a matmul on purpose. The values here are finite and
    # well-scaled, but numpy's matmul surfaces spurious divide-by-zero and
    # overflow flags raised inside some BLAS backends (Accelerate on macOS),
    # which would otherwise bury a genuine warning in noise.
    if gamma <= 0.0:
        return np.squeeze((safe * weights).sum(axis=1))
    # A certainty equivalent is scale-equivariant: CE(c*V) = c*CE(V). Dividing
    # by a reference level before taking a large negative power keeps the
    # arithmetic near 1.0, where it is well conditioned. Without this, a gamma
    # of 12 raises rates near 90 to the -11th power and underflows to garbage.
    scale = (safe * weights).sum(axis=1)        # one reference level per row
    scale = np.where(scale <= 0.0, 1e-9, scale)
    normalised = safe / scale[:, None]
    if abs(gamma - 1.0) < 1e-9:
        return np.squeeze(scale * np.exp((np.log(normalised) * weights).sum(axis=1)))
    utility = np.maximum((normalised ** (1.0 - gamma) * weights).sum(axis=1), 1e-300)
    return np.squeeze(scale * utility ** (1.0 / (1.0 - gamma)))


def solve_reservation_rate(
    model: TrendModel,
    days_left: int,
    risk_aversion: float = DEFAULT_RISK_AVERSION,
) -> float:
    """Risk-adjusted rate that waiting is worth, by backward induction.

    Returns the certainty equivalent of *not* sending today and instead playing
    the remaining window optimally. If today's spot is above this number,
    waiting is not worth the risk.

    `days_left = 0` is a boundary convention, not a continuation value: with no
    flexibility left the money goes out at spot, so spot is returned. It is
    therefore not comparable to the `days_left = 1` value, which can sit below
    spot when the rate is stretched above its trend. Monotonicity in
    `days_left` only holds from 1 upward.
    """
    if days_left <= 0:
        return float(math.exp(forecast_distribution(model, 0)[0]))

    sigma = max(model.sigma_eps, 1e-8)
    unconditional_sd = sigma / math.sqrt(max(1.0 - model.phi**2, 1e-6))
    span = 4.0 * max(unconditional_sd, abs(model.residual_now), sigma)
    grid = np.linspace(-span, span, GRID_POINTS)

    # Gauss-Hermite quadrature for the innovation, rescaled to N(0, sigma^2).
    nodes, raw_weights = np.polynomial.hermite_e.hermegauss(QUAD_NODES)
    weights = raw_weights / raw_weights.sum()
    shocks = nodes * sigma

    # Today's trend level, defined so that horizon 0 at today's residual
    # reproduces today's spot exactly. See forecast_distribution.
    base = model.log_spot - model.residual_now

    def rate_at(horizon: int, residual: np.ndarray) -> np.ndarray:
        return np.exp(base + model.slope_used * horizon + residual)

    # Terminal condition: at the deadline the money goes out at whatever the
    # rate is. No optionality left.
    value = rate_at(days_left, grid)

    # Every grid point's next-day residuals at once: rows are today's residual,
    # columns are the quadrature shocks. The backtest solves this hundreds of
    # times, so the inner loop is vectorised rather than looped per grid point.
    next_residuals = model.phi * grid[:, None] + shocks[None, :]

    for horizon in range(days_left - 1, -1, -1):
        next_value = np.interp(next_residuals, grid, value)
        continuation = np.atleast_1d(_crra_certainty_equivalent(next_value, weights, risk_aversion))
        if horizon == 0:
            # Today's continuation value, evaluated at today's actual residual.
            return float(np.interp(model.residual_now, grid, continuation))
        # Otherwise the holder of the option would take the better of the two.
        value = np.maximum(rate_at(horizon, grid), continuation)

    return float(np.interp(model.residual_now, grid, value))


def _score_from_components(stats: Stats, edge_pct: float, days_left: int, total_window: int) -> int:
    """Blend the level signal, the model edge and deadline pressure into 0-100.

    Presentation only. The verdict comes from the reservation rule; this exists
    so a glance at the email conveys roughly how strong the case is.
    """
    level = stats.percentile.get(TRADING_DAYS, stats.percentile.get(90, 50.0))
    ma_component = 50.0
    vs_ma20 = stats.vs_ma_pct(20)
    if vs_ma20 is not None:
        ma_component = float(np.clip(50.0 + vs_ma20 * 25.0, 0.0, 100.0))
    edge_component = float(np.clip(50.0 + edge_pct * 60.0, 0.0, 100.0))
    elapsed = 0.0 if total_window <= 0 else 1.0 - (days_left / total_window)
    pressure = float(np.clip(elapsed, 0.0, 1.0) * 100.0)

    blended = 0.30 * level + 0.15 * ma_component + 0.40 * edge_component + 0.15 * pressure
    return int(round(float(np.clip(blended, 0.0, 100.0))))


def decide(
    stats: Stats,
    model: TrendModel,
    days_left: int,
    deadline: str,
    total_window: int,
    risk_aversion: float = DEFAULT_RISK_AVERSION,
    good_level_percentile: float = 85.0,
) -> Decision:
    """Turn stats plus a fitted model into an actionable call.

    The verdict is driven by where the level sits in its own 12-month
    distribution, not by the stopping model, because that is what the backtest
    supports. Over 45 walk-forward windows the percentile rule added about 142
    INR a month on a $3,000 transfer while the model-reservation rule lost 49.
    Neither is statistically significant, but only one of them avoids the
    failure mode that matters here: in a steadily depreciating currency the
    drift term always argues for waiting, so a purely model-driven rule can
    recommend holding a rate that already beats 90% of the year.

    The reservation rate is still computed and reported. It is genuinely useful
    as a statement of what waiting is worth in risk-adjusted terms; it just is
    not trusted to make the call on its own.
    """
    reservation = solve_reservation_rate(model, days_left, risk_aversion)
    edge_pct = 100.0 * (stats.spot / reservation - 1.0) if reservation > 0 else 0.0

    mean_log, sd_log = forecast_distribution(model, max(days_left, 0))
    expected = math.exp(mean_log)
    low = math.exp(mean_log - 1.2816 * sd_log)   # 80% band
    high = math.exp(mean_log + 1.2816 * sd_log)

    percentile_1y = stats.percentile.get(TRADING_DAYS, stats.percentile.get(90))
    strong_level = percentile_1y is not None and percentile_1y >= good_level_percentile

    rationale: list[str] = []
    if days_left <= 0:
        verdict = "FORCED_SEND"
    elif strong_level:
        verdict = "SEND_NOW"
        rationale.append(
            f"Level beats {percentile_1y:.0f}% of the last 12 months, at or above the "
            f"{good_level_percentile:.0f}% bar that the backtest favours. Take it: the measured "
            f"edge from holding out for better is indistinguishable from zero."
        )
    elif edge_pct >= 0.0:
        verdict = "SEND_NOW"
        rationale.append(
            "Waiting is not worth its risk at this level, even before the percentile test."
        )
    else:
        verdict = "HOLD"
        rationale.append(
            f"Level beats only {percentile_1y:.0f}% of the last 12 months, below the "
            f"{good_level_percentile:.0f}% bar. Holding is reasonable, but the historical edge "
            f"from doing so is near zero, and the {deadline} deadline sends regardless."
        )

    rationale.append(
        f"Spot {stats.spot:.3f} vs risk-adjusted value of waiting {reservation:.3f} "
        f"({edge_pct:+.2f}%)."
    )
    rationale.append(
        f"Rate sits {model.stretch_pct:+.2f}% versus its own trend; "
        + (
            f"deviations have been decaying with a {model.half_life_days:.0f}-day half-life."
            if model.half_life_days and model.stationary
            else "no reliable mean reversion measured, so the level signal is weak."
        )
    )
    rationale.append(
        f"Trend carries {model.annual_drift_pct:+.1f}%/yr as used "
        f"(fitted {model.annual_drift_pct_unshrunk:+.1f}%/yr, halved deliberately); "
        f"innovation vol {model.annual_vol_pct:.1f}%/yr."
    )
    if days_left > 0:
        rationale.append(
            f"{days_left} day(s) of flexibility left before the {deadline} deadline; "
            f"80% of paths land between {low:.2f} and {high:.2f}."
        )

    return Decision(
        verdict=verdict,
        reservation_rate=reservation,
        edge_pct=edge_pct,
        score=_score_from_components(stats, edge_pct, days_left, total_window),
        days_left=days_left,
        deadline=deadline,
        expected_rate_at_deadline=expected,
        forecast_low=low,
        forecast_high=high,
        rationale=rationale,
    )
