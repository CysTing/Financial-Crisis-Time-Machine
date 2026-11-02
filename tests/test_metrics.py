"""Independent, hand-calculated numerical checks; no network is used."""

import math

import numpy as np
import pandas as pd
import pytest

from core.metrics import (
    annual_to_daily,
    covariance_risk_contributions,
    daily_returns,
    drawdown_series,
    historical_tail_risk,
    performance_metrics,
    rolling_correlations,
    rolling_volatility,
    survival_score,
)


def equity(values, dates=None):
    dates = dates if dates is not None else pd.bdate_range("2020-01-02", periods=len(values))
    return pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)


def test_returns_drawdown_and_peak_include_initial_capital():
    observed = equity([100, 90, 108, 81])
    assert daily_returns(observed).to_numpy() == pytest.approx([-0.1, 0.2, -0.25])
    assert drawdown_series(observed).to_numpy() == pytest.approx([0, 0.1, 0, 0.25])
    metrics = performance_metrics(observed)
    assert metrics["total_return"] == pytest.approx(-0.19)
    assert metrics["max_drawdown"] == pytest.approx(0.25)
    assert metrics["profit_loss"] == -19


def test_annualization_sharpe_and_all_observation_sortino():
    # Session returns are +10%, -10%, +20%; sample variance is 7/300.
    observed = equity([100, 110, 99, 118.8])
    metrics = performance_metrics(observed)
    mean = (0.1 - 0.1 + 0.2) / 3
    sample_sd = math.sqrt(((0.1 - mean) ** 2 + (-0.1 - mean) ** 2 + (0.2 - mean) ** 2) / 2)
    assert metrics["annualized_volatility"] == pytest.approx(sample_sd * math.sqrt(252))
    assert metrics["sharpe"] == pytest.approx(mean / sample_sd * math.sqrt(252))
    # Two above-threshold days contribute zero, but stay in the denominator.
    downside = math.sqrt(0.1 ** 2 / 3)
    assert metrics["sortino"] == pytest.approx(mean / downside * math.sqrt(252))


def test_actual_elapsed_time_cagr_and_calmar():
    observed = equity([100, 80, 110], ["2020-01-01", "2020-07-01", "2021-01-01"])
    metrics = performance_metrics(observed)
    expected_cagr = 1.1 ** (365.2425 / 366) - 1
    assert metrics["cagr"] == pytest.approx(expected_cagr)
    assert metrics["calmar"] == pytest.approx(expected_cagr / 0.2)


def test_risk_free_effective_annual_conversion():
    effective_annual = 1.001 ** 252 - 1
    assert annual_to_daily(effective_annual) == pytest.approx(0.001)
    observed = equity([100, 110, 99, 118.8])
    result = performance_metrics(observed, risk_free_annual=effective_annual)
    excess = np.array([0.1, -0.1, 0.2]) - 0.001
    assert result["sharpe"] == pytest.approx(excess.mean() / excess.std(ddof=1) * math.sqrt(252))


def test_undefined_ratios_are_none_and_cash_volatility_is_zero():
    observed = equity([100, 100, 100])
    result = performance_metrics(observed)
    assert result["annualized_volatility"] == 0
    for key in ("sharpe", "sortino", "calmar", "var", "expected_shortfall"):
        assert result[key] is None
    assert performance_metrics(equity([100]))["cagr"] is None
    assert performance_metrics(pd.Series(dtype=float))["total_return"] is None


def test_turnover_and_costs_include_initial_purchase_and_both_sides():
    observed = equity([100, 99, 98])
    ledger = pd.DataFrame({"gross_notional": [90.0, 45.0, 30.0], "costs": [0.9, 0.45, 0.3]})
    metrics = performance_metrics(observed, trades=ledger)
    assert metrics["gross_turnover"] == pytest.approx(165 / 99)
    assert metrics["total_costs"] == pytest.approx(1.65)


def test_benchmark_comparison_requires_matching_window_and_capital():
    observed = equity([100, 90, 110])
    reference = equity([100, 95, 105])
    assert performance_metrics(observed, reference)["excess_return"] == pytest.approx(0.05)
    with pytest.raises(ValueError, match="identical evaluation"):
        performance_metrics(observed, reference.iloc[1:])
    with pytest.raises(ValueError, match="initial capital"):
        performance_metrics(observed, reference * 2)


def test_linear_var_expected_shortfall_and_minimum_sample():
    # Losses 0.01,...,0.20: linear 95th percentile = 0.1905; tail mean = 0.20.
    returns = pd.Series(-np.arange(1, 21) / 100)
    risk = historical_tail_risk(returns)
    assert risk["var"] == pytest.approx(0.1905)
    assert risk["expected_shortfall"] == pytest.approx(0.20)
    assert historical_tail_risk(returns.iloc[:19])["var"] is None
    gains = historical_tail_risk(pd.Series(np.arange(1, 21) / 100))
    assert gains["var"] < 0
    assert gains["expected_shortfall"] < 0


def test_rolling_windows_do_not_use_future_observations():
    observed = equity([100, 110, 99, 118.8, 70])
    changed = observed.copy()
    changed.iloc[-1] = 9000
    pd.testing.assert_series_equal(rolling_volatility(observed, 2).iloc[:-1], rolling_volatility(changed, 2).iloc[:-1])
    assert rolling_volatility(observed, 3).iloc[:2].isna().all()
    prices = pd.DataFrame({"A": observed, "B": observed * np.array([1, 1.1, 1.2, 1.1, 1.3])})
    correlations = rolling_correlations(prices, window=3)
    assert correlations.loc[prices.index[2]].isna().all().all()
    assert correlations.loc[prices.index[3]].notna().all().all()


def test_risk_contributions_reconcile_and_zero_volatility_is_undefined():
    returns = pd.DataFrame({"A": [0.1, -0.1, 0.2], "B": [0.1, -0.1, 0.2]})
    weights = {"A": 0.6, "B": 0.3, "CASH": 0.1}
    contributions = covariance_risk_contributions(returns, weights, window=3)
    portfolio_sd = (returns @ np.array([0.6, 0.3])).std(ddof=1) * np.sqrt(252)
    assert contributions["volatility_contribution"].sum() == pytest.approx(portfolio_sd)
    assert contributions["fraction_of_volatility"].sum() == pytest.approx(1)
    zeros = covariance_risk_contributions(returns * 0, weights, window=3)
    assert zeros["volatility_contribution"].sum() == 0
    assert zeros["fraction_of_volatility"].isna().all()


def test_score_explicit_hand_example_and_no_duplicate_growth_reward():
    score = survival_score(0, 0, 0.5, 0)
    # 35 + 35 + 7.5 + 7.5 = 85.
    assert score["score"] == pytest.approx(85)
    assert score["category"] == "Excellent crisis resilience"
    assert survival_score(2.0, 0, 0.5, 0)["score"] == score["score"]
    neutral = survival_score(None, None, None, None)
    assert neutral["score"] == pytest.approx(50)
    assert len(neutral["missing_components"]) == 4


def test_score_drawdown_monotonicity_bounds_and_thresholds():
    values = [survival_score(-0.1, float(dd), 0.5, -0.02)["score"] for dd in np.linspace(0, 1, 101)]
    assert all(later <= earlier for earlier, later in zip(values, values[1:]))
    assert 0 <= min(values) <= max(values) <= 100
    assert survival_score(-1, 1, -3, -1)["score"] == 0
    with pytest.raises(ValueError):
        survival_score(0, -0.2, None, None)


def test_missing_and_invalid_equity_never_forward_filled():
    with pytest.raises(ValueError, match="missing"):
        performance_metrics(equity([100, np.nan, 110]))
    with pytest.raises(ValueError, match="strictly positive"):
        performance_metrics(equity([0, 0]))
    with pytest.raises(ValueError, match="external cash flow"):
        performance_metrics(equity([100, 0, 10]))
    assert daily_returns(equity([100, 0, 0])).to_numpy() == pytest.approx([-1, 0])
