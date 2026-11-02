"""Transparent performance, tail-risk, attribution, and educational scoring tools.

Returns are decimal fractions, money is USD, and maximum drawdown is a positive
loss magnitude. Daily statistics use 252 sessions; CAGR uses actual elapsed time.
No function imports the UI or downloads data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

SESSIONS_PER_YEAR = 252
DAYS_PER_YEAR = 365.2425
EPSILON = 1e-12


def _equity_series(equity: pd.Series) -> pd.Series:
    result = pd.Series(equity, dtype=float).copy()
    if result.empty:
        return result
    if not result.index.is_unique or not result.index.is_monotonic_increasing:
        raise ValueError("Equity observations must have a unique, increasing index.")
    if not np.isfinite(result.to_numpy()).all() or (result < 0).any():
        raise ValueError("Equity must be finite and nonnegative; missing values cannot be filled.")
    if result.iloc[0] <= 0:
        raise ValueError("The initial cash observation must be strictly positive.")
    if ((result.shift(1) == 0) & (result > 0)).any():
        raise ValueError("Equity cannot recover from zero without an external cash flow.")
    return result


def daily_returns(equity: pd.Series) -> pd.Series:
    """Returns after the initial cash baseline; never pad missing observations."""
    values = _equity_series(equity)
    if len(values) < 2:
        return pd.Series(index=values.index[:0], dtype=float, name="daily_return")
    result = values.pct_change(fill_method=None).iloc[1:]
    result = result.where(values.shift(1).iloc[1:] != 0, 0.0)
    result.name = "daily_return"
    return result


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Loss below the running peak, including initial capital, as a positive fraction."""
    values = _equity_series(equity)
    result = 1.0 - values / values.cummax()
    result.name = "drawdown"
    return result


def annual_to_daily(rate: float) -> float:
    """Convert a constant effective annual rate to its effective session rate."""
    if not np.isfinite(rate) or rate <= -1:
        raise ValueError("An effective annual rate must be finite and greater than -100%.")
    return float((1.0 + rate) ** (1.0 / SESSIONS_PER_YEAR) - 1.0)


def historical_tail_risk(
    returns: pd.Series,
    confidence: float = 0.95,
    min_samples: int = 20,
) -> dict[str, float | int | None]:
    """One-session empirical loss VaR and ES; linear quantile, inclusive tail.

    ES is the average observed loss at or above VaR. Both estimates can be
    negative when every observed return is a gain; that means a gain threshold,
    not a guarantee against future loss. At least 20 observations by default.
    """
    if not 0 < confidence < 1:
        raise ValueError("Confidence must lie strictly between 0 and 1.")
    if min_samples < 2:
        raise ValueError("At least two samples must be required for tail estimates.")
    values = pd.Series(returns, dtype=float)
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("Tail risk requires finite observed returns.")
    if len(values) < min_samples:
        return {"var": None, "expected_shortfall": None, "samples": len(values)}
    losses = -values.to_numpy()
    threshold = float(np.quantile(losses, confidence, method="linear"))
    tail = losses[losses >= threshold]
    return {
        "var": threshold,
        "expected_shortfall": float(np.mean(tail)),
        "samples": len(values),
    }


def performance_metrics(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    trades: pd.DataFrame | None = None,
    risk_free_annual: float = 0.0,
    mar_annual: float = 0.0,
    confidence: float = 0.95,
) -> dict[str, float | int | None]:
    """Compute cost-inclusive performance from an equity series with cash baseline.

    Undefined ratios return ``None``. A benchmark must have exactly the same
    observation index and initial capital. Gross turnover includes initial buys
    and divides gross absolute traded notional by average daily recorded equity.
    """
    values = _equity_series(equity)
    rf_daily, mar_daily = annual_to_daily(risk_free_annual), annual_to_daily(mar_annual)
    returns = daily_returns(values)
    tail = historical_tail_risk(returns, confidence=confidence)
    results: dict[str, float | int | None] = {
        "initial_value": None,
        "final_value": None,
        "profit_loss": None,
        "total_return": None,
        "cagr": None,
        "annualized_volatility": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "calmar": None,
        "best_daily_return": None,
        "worst_daily_return": None,
        "excess_return": None,
        "gross_turnover": None,
        "total_costs": 0.0,
        "var": tail["var"],
        "expected_shortfall": tail["expected_shortfall"],
        "observations": len(returns),
        "elapsed_years": None,
        "risk_free_annual": float(risk_free_annual),
        "mar_annual": float(mar_annual),
        "tail_confidence": float(confidence),
        "tail_min_samples": 20,
    }
    if values.empty:
        return results
    initial, final = float(values.iloc[0]), float(values.iloc[-1])
    results.update(initial_value=initial, final_value=final, profit_loss=final - initial)
    results["total_return"] = final / initial - 1.0
    results["max_drawdown"] = float(drawdown_series(values).max())
    if isinstance(values.index, pd.DatetimeIndex) and len(values) > 1:
        years = (values.index[-1] - values.index[0]).total_seconds() / (86400 * DAYS_PER_YEAR)
        results["elapsed_years"] = years
        if years > 0:
            exponent = np.log(final / initial) / years if final > 0 else -np.inf
            # Very short synthetic windows can overflow annualization. N/A is
            # preferable to displaying an invented finite value or infinity.
            if exponent < np.log(np.finfo(float).max):
                results["cagr"] = float(np.expm1(exponent))
    if len(returns) >= 1:
        results["best_daily_return"] = float(returns.max())
        results["worst_daily_return"] = float(returns.min())
    if len(returns) >= 2:
        sigma = float(returns.std(ddof=1))
        results["annualized_volatility"] = float(sigma * np.sqrt(SESSIONS_PER_YEAR))
        excess = returns - rf_daily
        excess_sigma = float(excess.std(ddof=1))
        if excess_sigma > EPSILON:
            results["sharpe"] = float(excess.mean() / excess_sigma * np.sqrt(SESSIONS_PER_YEAR))
        shortfall = returns - mar_daily
        downside = float(np.sqrt(np.mean(np.minimum(shortfall, 0.0) ** 2)))
        if downside > EPSILON:
            results["sortino"] = float(shortfall.mean() / downside * np.sqrt(SESSIONS_PER_YEAR))
    if results["cagr"] is not None and results["max_drawdown"] > EPSILON:
        results["calmar"] = float(results["cagr"] / results["max_drawdown"])
    if benchmark is not None:
        reference = _equity_series(benchmark)
        if not reference.index.equals(values.index):
            raise ValueError("Portfolio and benchmark must use identical evaluation observations.")
        if not np.isclose(reference.iloc[0], initial, rtol=1e-10, atol=1e-8):
            raise ValueError("Portfolio and benchmark must start with matching initial capital.")
        results["excess_return"] = float(results["total_return"] - (reference.iloc[-1] / initial - 1.0))
    notional, costs = 0.0, 0.0
    if trades is not None and not trades.empty:
        notional_column = "gross_notional" if "gross_notional" in trades else "notional"
        cost_column = "costs" if "costs" in trades else "cost"
        if notional_column not in trades or cost_column not in trades:
            raise ValueError("Trade ledger requires gross_notional and costs columns.")
        notionals = trades[notional_column].astype(float).abs()
        cost_values = trades[cost_column].astype(float)
        if not np.isfinite(notionals).all() or not np.isfinite(cost_values).all() or (cost_values < 0).any():
            raise ValueError("Executed notional and costs must be finite, with nonnegative costs.")
        notional, costs = float(notionals.sum()), float(cost_values.sum())
    results["gross_turnover"] = notional / float(values.mean()) if values.mean() > 0 else None
    results["total_costs"] = costs
    return results


def metric_notes(metrics: Mapping[str, Any]) -> list[str]:
    """Plain-language explanations suitable for charts, tables, and reports."""
    notes = [
        "Returns include execution costs. Cash interest is 0%; it is separate from the metric risk-free assumption.",
        "Daily statistics annualize at 252 sessions. CAGR uses actual elapsed calendar time.",
        "Maximum drawdown is a positive loss below the running peak, including initial capital.",
        "Sharpe uses sample standard deviation of daily excess returns; Sortino uses downside deviation over all days.",
        "Gross turnover includes initial purchases and both trade sides, divided by average daily equity.",
        f"VaR/ES use one-session empirical losses, the linear {metrics.get('tail_confidence', 0.95):.0%} quantile, and at least 20 observations. Negative loss estimates describe gains in the sample.",
        "N/A means insufficient observations or an undefined denominator, never an assumed zero.",
    ]
    if metrics.get("elapsed_years") is not None and metrics["elapsed_years"] < 1:
        notes.append("Short-period annualization: annualized estimates can be unstable and are not forecasts.")
    if metrics.get("risk_free_annual", 0.0) == 0.0:
        notes.append("Risk-free rate assumption: 0% effective annual; historical point-in-time rates are not supplied.")
    return notes


def rolling_volatility(equity: pd.Series, window: int = 21) -> pd.Series:
    """Annualized sample volatility, requiring the entire stated return window."""
    if window < 2:
        raise ValueError("Rolling volatility requires a window of at least two returns.")
    return daily_returns(equity).rolling(window, min_periods=window).std(ddof=1) * np.sqrt(SESSIONS_PER_YEAR)


def rolling_correlations(prices: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    """Pairwise rolling return correlations; no filling and full windows required."""
    if window < 2:
        raise ValueError("Rolling correlation requires at least two returns.")
    if not np.isfinite(prices.to_numpy(dtype=float)).all() or (prices <= 0).any().any():
        raise ValueError("Rolling correlations require positive, finite observed prices.")
    returns = prices.pct_change(fill_method=None).iloc[1:]
    return returns.rolling(window, min_periods=window).corr()


def covariance_risk_contributions(
    asset_returns: pd.DataFrame,
    weights: Mapping[str, float],
    window: int = 63,
) -> pd.DataFrame:
    """Euler contributions to annualized volatility, not realized return attribution.

    Uses a trailing sample covariance matrix and the supplied current weights.
    Cash contributes zero. Negative contributions can reflect diversification.
    The entire window is required; zero-volatility ratios are N/A.
    """
    if window < 2:
        raise ValueError("Risk attribution requires at least two returns.")
    if any(not np.isfinite(w) or w < 0 for w in weights.values()) or sum(weights.values()) > 1.0 + 1e-8:
        raise ValueError("Risk weights must be finite, nonnegative, and unleveraged.")
    assets = [str(asset) for asset in asset_returns.columns if asset != "CASH"]
    missing = [asset for asset, weight in weights.items() if weight > 0 and asset != "CASH" and asset not in assets]
    if missing:
        raise ValueError(f"Missing returns for weighted assets: {', '.join(missing)}.")
    result = pd.DataFrame(index=assets + ["CASH"], columns=["weight", "volatility_contribution", "fraction_of_volatility"], dtype=float)
    result["weight"] = [float(weights.get(asset, 0.0)) for asset in assets] + [float(weights.get("CASH", max(0.0, 1 - sum(weights.values()))))]
    result.loc["CASH", "volatility_contribution"] = 0.0
    if len(asset_returns) < window or not assets:
        return result
    observations = asset_returns[assets].iloc[-window:].astype(float)
    if not np.isfinite(observations.to_numpy()).all():
        raise ValueError("Risk attribution does not fill missing return observations.")
    vector = np.array([weights.get(asset, 0.0) for asset in assets], dtype=float)
    covariance = observations.cov(ddof=1).to_numpy() * SESSIONS_PER_YEAR
    variance = float(vector @ covariance @ vector)
    if variance <= EPSILON ** 2:
        result["volatility_contribution"] = 0.0
        return result
    volatility = np.sqrt(variance)
    contributions = vector * (covariance @ vector) / volatility
    result.loc[assets, "volatility_contribution"] = contributions
    result.loc[assets, "fraction_of_volatility"] = contributions / volatility
    result.loc["CASH", "fraction_of_volatility"] = 0.0
    return result


def survival_score(
    total_return: float | None,
    max_drawdown: float | None,
    sharpe: float | None,
    excess_return: float | None,
) -> dict[str, Any]:
    """Bounded educational resilience score, not an institutional risk rating.

    Preservation 35%: clip(1 + total return, 0, 1); rewards retaining starting
    capital once, without separately rewarding final value. Drawdown 35%:
    clip(1 - maximum loss / 0.60, 0, 1). Sharpe 15%: clip((Sharpe + 1) / 3,
    0, 1). Relative 15%: clip(0.5 + excess cumulative return / 0.40, 0, 1).
    Missing components receive neutral 50%, weights are never redistributed.
    All ratios use unleveraged, causal simulation outputs. Preservation caps
    growth rewards, a deliberate preservation-versus-growth tradeoff.
    """
    weights = {"capital_preservation": 0.35, "drawdown_resilience": 0.35, "risk_adjusted": 0.15, "relative_performance": 0.15}
    supplied = {"capital_preservation": total_return, "drawdown_resilience": max_drawdown, "risk_adjusted": sharpe, "relative_performance": excess_return}
    formulas = {
        "capital_preservation": lambda x: 1.0 + x,
        "drawdown_resilience": lambda x: 1.0 - x / 0.60,
        "risk_adjusted": lambda x: (x + 1.0) / 3.0,
        "relative_performance": lambda x: 0.5 + x / 0.40,
    }
    if max_drawdown is not None and np.isfinite(max_drawdown) and not 0 <= max_drawdown <= 1:
        raise ValueError("Maximum drawdown must be a positive loss magnitude between 0 and 1.")
    missing: list[str] = []
    components: dict[str, float] = {}
    for key, value in supplied.items():
        if value is None or not np.isfinite(value):
            components[key] = 50.0
            missing.append(key)
        else:
            components[key] = float(np.clip(formulas[key](float(value)), 0.0, 1.0) * 100)
    score = float(np.clip(sum(components[key] * weight for key, weight in weights.items()), 0, 100))
    if score >= 85:
        category = "Excellent crisis resilience"
    elif score >= 70:
        category = "Strong survival"
    elif score >= 50:
        category = "Moderate survival"
    elif score >= 25:
        category = "Severe portfolio damage"
    else:
        category = "Portfolio collapse"
    return {"score": score, "category": category, "components": components, "weights": weights, "missing_components": missing, "label": "Educational custom score; not an institutional investment risk rating."}
