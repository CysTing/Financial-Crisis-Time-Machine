import numpy as np
import pandas as pd
import pytest

from core.backtest_engine import Simulation
from core.strategies import make_strategy


@pytest.mark.parametrize("name", ["Moving Average Trend", "Momentum Rotation", "Inverse Volatility"])
def test_future_price_mutation_cannot_change_current_decision(name):
    dates = pd.bdate_range("2020-01-02", periods=10)
    base = pd.DataFrame({"SPY": [10, 11, 10, 12, 13, 12, 14, 15, 16, 17], "AGG": [10, 10.1, 10, 10.2, 10.1, 10.3, 10.2, 10.4, 10.3, 10.5]}, index=dates)
    changed = base.astype(float).copy()
    changed.loc[dates[6]:, "SPY"] = [1000000, 0.00001, 99999999, 0.000001]
    changed.loc[dates[6]:, "AGG"] = [0.00001, 1000000, 0.000001, 99999999]
    strategy = make_strategy(name, lookback=3)
    assert strategy.target(base.loc[:dates[5]]) == strategy.target(changed.loc[:dates[5]])
    first = Simulation(base, dates[4], dates[-1], sessions=dates[4:], strategy=strategy)
    second = Simulation(changed, dates[4], dates[-1], sessions=dates[4:], strategy=strategy)
    first.advance_to(dates[5])
    second.advance_to(dates[5])
    pd.testing.assert_frame_equal(first.history, second.history)
    assert first.pending == second.pending


def test_close_signal_cannot_earn_return_before_next_close_execution():
    dates = pd.bdate_range("2020-01-02", periods=5)
    prices = pd.DataFrame({"SPY": [10, 10, 20, 40, 80]}, index=dates)
    sim = Simulation(prices, dates[1], dates[-1], sessions=dates[1:], capital=100, cost_bps=0, strategy=make_strategy("Moving Average Trend", lookback=2))
    sim.advance_to(dates[2])
    assert sim.pending.weights["SPY"] == 1
    assert sim.portfolio.units["SPY"] == 0
    assert sim.history.equity.iloc[-1] == 100
    sim.advance_to(dates[3])
    # The 20->40 move preceded the fill; the new holdings get none of it.
    assert sim.history.equity.iloc[-1] == 100
    assert sim.portfolio.units["SPY"] == pytest.approx(2.5)
    assert sim.ledger.execution_date.min() == dates[3]
    sim.advance_to(dates[4])
    assert sim.history.equity.iloc[-1] == 200


def test_restricted_snapshots_results_and_mutable_copies():
    dates = pd.bdate_range("2020-01-02", periods=5)
    prices = pd.DataFrame({"A": [10, 11, 12, 99999, 0.01]}, index=dates)
    sim = Simulation(prices, dates[0], dates[-1], sessions=dates, weights={"A": 1})
    sim.advance()
    snapshot = sim.snapshot()
    result = sim.result()
    for frame in [snapshot["prices"], snapshot["history"], snapshot["contributions"], result.prices, result.history]:
        assert frame.index.max() <= sim.current_date
        assert dates[3] not in frame.index
    assert "end" not in snapshot["config"]
    snapshot["prices"].iloc[0, 0] = 123456
    snapshot["history"].iloc[0, 0] = 123456
    assert sim.result().prices.iloc[0, 0] == 10
    assert sim.history.equity.iloc[0] == 100000


def test_manual_target_visibly_replaces_automatic_target_before_execution():
    dates = pd.bdate_range("2020-01-02", periods=5)
    prices = pd.DataFrame({"SPY": [10, 10, 20, 40, 80]}, index=dates)
    sim = Simulation(prices, dates[1], dates[-1], sessions=dates[1:], capital=100, cost_bps=0, strategy=make_strategy("Moving Average Trend", lookback=2))
    sim.advance_to(dates[2])
    assert sim.pending.source == "automatic"
    status = sim.submit_target({"CASH": 1})
    assert "Replaced" in status
    assert sim.pending.source == "manual"
    sim.advance_to(dates[3])
    assert sim.portfolio.units["SPY"] == 0
    assert sim.ledger.empty


def test_prewarmup_returns_do_not_enter_scored_baseline():
    dates = pd.bdate_range("2020-01-02", periods=6)
    prices = pd.DataFrame({"SPY": [1, 100, 1000, 10, 20, 30]}, index=dates)
    sim = Simulation(prices, dates[3], dates[-1], sessions=dates[3:], capital=100, cost_bps=0, weights={"SPY": 1})
    result = sim.run()
    assert result.history.index[0] == dates[3]
    assert result.history.equity.tolist() == [100, 100, 150]
