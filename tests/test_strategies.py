import numpy as np
import pandas as pd
import pytest

from core.backtest_engine import Simulation
from core.strategies import STRATEGIES, make_strategy


def test_all_six_strategies_run_offline():
    dates = pd.bdate_range("2018-01-02", periods=270)
    x = np.arange(len(dates))
    prices = pd.DataFrame({"SPY": 100 * np.exp(0.001 * x + 0.01 * np.sin(x)), "AGG": 80 * np.exp(0.0002 * x + 0.002 * np.cos(x))}, index=dates)
    for name in STRATEGIES:
        strategy = make_strategy(name, weights={"SPY": 0.5, "AGG": 0.3, "CASH": 0.2})
        result = Simulation(prices, dates[210], dates[-1], sessions=dates[210:], strategy=strategy).run()
        assert len(result.history) == 60
        assert np.isfinite(result.history.equity).all()
        assert (result.history.cash >= 0).all()


def test_traditional_6040_requires_both_assets():
    prices = pd.DataFrame({"SPY": [1, 2, 3]}, index=pd.bdate_range("2020-01-02", periods=3))
    with pytest.raises(ValueError, match="SPY and AGG"):
        make_strategy("Traditional 60/40").target(prices)


def test_trend_has_200_close_default_and_cash_warmup():
    strategy = make_strategy("Moving Average Trend")
    short = pd.DataFrame({"SPY": np.arange(1, 200)}, index=pd.bdate_range("2020-01-02", periods=199))
    assert strategy.lookback == 200
    assert strategy.target(short) == {"CASH": 1.0}
    full = pd.DataFrame({"SPY": np.arange(1, 201)}, index=pd.bdate_range("2020-01-02", periods=200))
    assert strategy.target(full) == {"SPY": 1.0}


def test_momentum_positive_only_ticker_tie_breaking_and_lookback():
    dates = pd.bdate_range("2020-01-02", periods=4)
    prices = pd.DataFrame({"Z": [10, 11, 12, 20], "A": [10, 11, 12, 20], "B": [10, 9, 8, 5]}, index=dates)
    strategy = make_strategy("Momentum Rotation", lookback=3)
    assert strategy.target(prices) == {"A": 1.0}
    assert strategy.target(prices.iloc[:3]) == {"CASH": 1.0}
    assert strategy.target(prices[["B"]]) == {"CASH": 1.0}
    assert make_strategy("Momentum Rotation").lookback == 126


def test_momentum_top_n_equal_weight_and_missing_warmup_excluded():
    dates = pd.bdate_range("2020-01-02", periods=4)
    prices = pd.DataFrame({"A": [10, 11, 12, 20], "B": [10, 11, 12, 15], "C": [np.nan, 11, 12, 30]}, index=dates)
    strategy = make_strategy("Momentum Rotation", lookback=3, momentum_top_n=2)
    assert strategy.target(prices) == {"A": 0.5, "B": 0.5}


def test_inverse_vol_sample_estimate_caps_and_residual_cash():
    dates = pd.bdate_range("2020-01-02", periods=4)
    # A returns [10%, -10%, 10%]; B [20%, -20%, 20%]. B volatility is twice A.
    prices = pd.DataFrame({"A": [100, 110, 99, 108.9], "B": [100, 120, 96, 115.2], "ZERO": [10, 10, 10, 10]}, index=dates)
    strategy = make_strategy("Inverse Volatility", lookback=3, exposure_cap=0.5)
    target = strategy.target(prices)
    assert target["A"] == pytest.approx(0.5)
    assert target["B"] == pytest.approx(1 / 3)
    assert target["CASH"] == pytest.approx(1 / 6)
    assert "ZERO" not in target
    assert make_strategy("Inverse Volatility").lookback == 63


def test_inverse_vol_cash_for_zero_or_insufficient_volatility():
    prices = pd.DataFrame({"A": [10, 10, 10, 10]}, index=pd.bdate_range("2020-01-02", periods=4))
    strategy = make_strategy("Inverse Volatility", lookback=3)
    assert strategy.target(prices) == {"CASH": 1.0}
    assert strategy.target(prices.iloc[:3]) == {"CASH": 1.0}


@pytest.mark.parametrize("frequency,signal,next_day", [
    ("Monthly", "2020-01-31", "2020-02-03"),
    ("Quarterly", "2020-03-31", "2020-04-01"),
    ("Annually", "2020-12-31", "2021-01-04"),
])
def test_custom_period_end_signals(frequency, signal, next_day):
    strategy = make_strategy("User-Defined Allocation", frequency=frequency)
    assert strategy.should_signal(pd.Timestamp(signal), pd.Timestamp(next_day))
    assert not strategy.should_signal(pd.Timestamp("2020-01-15"), pd.Timestamp("2020-01-16"))


def test_month_end_signal_executes_next_session_not_same_close():
    dates = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"])
    prices = pd.DataFrame({"SPY": [10, 10, 20, 21], "AGG": [10, 10, 10, 10]}, index=dates)
    sim = Simulation(prices, dates[0], dates[-1], sessions=dates, capital=100, cost_bps=0, strategy=make_strategy("Traditional 60/40"))
    sim.advance()
    assert sim.pending.signal_date == dates[1]
    assert sim.pending.execution_date == dates[2]
    assert sim.ledger.execution_date.tolist() == [dates[1], dates[1]]
    sim.advance()
    automatic = sim.ledger.loc[sim.ledger.source == "automatic"]
    assert (automatic.execution_date == dates[2]).all()
    assert sim.history.equity.iloc[-1] == pytest.approx(160)
