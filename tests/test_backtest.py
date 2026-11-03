"""Small independently calculated cash-accounting and replay examples."""

import numpy as np
import pandas as pd
import pytest

from core.backtest_engine import Simulation
from core.portfolio import Portfolio, validate_weights
from core.strategies import make_strategy


def test_old_valid_window_uses_requested_calendar_bounds_not_today():
    # These SPY dates are after inception but before the library's rolling
    # default calendar window. Historical replay must remain usable as time passes.
    dates=pd.DatetimeIndex(["2000-01-03","2000-01-04","2000-01-05"])
    prices=pd.DataFrame({"SPY":[100.,110.,121.]},index=dates)
    sim=Simulation(prices,dates[0],dates[-1],capital=100,cost_bps=0,weights={"SPY":1.0})
    result=sim.run()
    assert result.history.index.equals(dates)
    assert result.history.equity.tolist()==pytest.approx([100,100,110])
    assert result.ledger.execution_date.tolist()==[dates[1]]


def replay(values, **kwargs):
    prices = pd.DataFrame(values, index=pd.bdate_range("2020-01-02", periods=len(next(iter(values.values())))))
    return Simulation(prices, prices.index[0], prices.index[-1], sessions=prices.index, **kwargs)


def test_initial_cash_then_next_close_execution_and_hand_calculated_return():
    sim = replay({"A": [10, 20, 22]}, capital=100, cost_bps=100, weights={"A": 1.0})
    assert sim.history.equity.tolist() == [100]
    assert sim.portfolio.units == {"A": 0.0}
    sim.advance()
    # E + 1% E = 100, buy only after the move from 10 to 20.
    assert sim.history.equity.iloc[-1] == pytest.approx(99.009900990099)
    assert sim.portfolio.units["A"] == pytest.approx(4.950495049505)
    assert sim.portfolio.cash == pytest.approx(0, abs=1e-10)
    sim.advance()
    assert sim.history.equity.iloc[-1] == pytest.approx(108.910891089109)
    assert sim.history.daily_return.iloc[-1] == pytest.approx(0.1)


def test_full_allocation_costs_are_funded_without_negative_cash():
    sim = replay({"A": [10, 10], "B": [20, 20]}, capital=100, cost_bps=100, weights={"A": 0.6, "B": 0.4})
    sim.advance()
    row = sim.history.iloc[-1]
    assert row.equity == pytest.approx(100 / 1.01)
    assert row.value_A == pytest.approx(60 / 1.01)
    assert row.value_B == pytest.approx(40 / 1.01)
    assert row.weight_A == pytest.approx(0.6)
    assert row.weight_B == pytest.approx(0.4)
    assert row.cash >= 0
    assert row.costs == pytest.approx(100 - 100 / 1.01)


def test_cost_aware_rotation_charges_both_sides_and_sells_first():
    portfolio = Portfolio(0.0, {"A": 10.0, "B": 0.0})
    prices = pd.Series({"A": 10.0, "B": 10.0})
    trades = portfolio.rebalance({"B": 1.0}, prices, 1000)
    # Sell 100 with 10 fee, buy E with 0.1 E fee: E = 90/1.1.
    assert trades[0]["asset"] == "A"
    assert trades[0]["cash_after"] == pytest.approx(90)
    assert trades[1]["asset"] == "B"
    assert portfolio.equity(prices) == pytest.approx(81.8181818181818)
    assert sum(t["costs"] for t in trades) == pytest.approx(18.1818181818182)
    assert portfolio.cash == pytest.approx(0, abs=1e-10)


def test_cash_reconciliation_for_every_trade_and_daily_equity():
    sim = replay({"A": [10, 10, 12, 11], "B": [20, 21, 19, 22]}, capital=1000, cost_bps=17, weights={"A": 0.5, "B": 0.3, "CASH": 0.2})
    sim.advance()
    sim.submit_target({"B": 0.8, "CASH": 0.2})
    sim.run()
    cash = 1000.0
    for trade in sim.ledger.itertuples():
        cash -= trade.signed_notional + trade.costs
        assert trade.cash_after == pytest.approx(cash)
        assert cash >= -1e-8
    for row in sim.history.itertuples():
        assert row.equity == pytest.approx(row.cash + row.value_A + row.value_B)
        assert row.weight_A + row.weight_B + row.weight_CASH == pytest.approx(1)


def test_contributions_reconcile_asset_cash_and_separate_costs():
    sim = replay({"A": [10, 10, 11, 12], "B": [20, 20, 18, 22]}, capital=100, cost_bps=25, weights={"A": 0.5, "B": 0.5})
    result = sim.run()
    expected = result.history.equity - 100
    np.testing.assert_allclose(result.contributions.sum(axis=1).cumsum(), expected, atol=1e-9)
    assert result.contributions.CASH.sum() == 0
    assert result.contributions.COSTS.sum() == pytest.approx(-result.ledger.costs.sum())


@pytest.mark.parametrize("weights", [{"A": -0.1, "CASH": 1.1}, {"A": float("nan")}, {"A": float("inf")}, {"A": 0.5}, {}, {"B": 1}])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError):
        validate_weights(weights, ["A"])


def test_small_weight_rounding_tolerance_only():
    assert sum(validate_weights({"A": 1.000000001}, ["A"]).values()) == 1
    with pytest.raises(ValueError):
        validate_weights({"A": 1.00001}, ["A"])


def test_exactly_zero_excluded_asset_is_ignored_without_creating_exposure():
    weights = validate_weights({"SPY": .6, "AGG": .4, "EXCLUDED": 0.0}, ["SPY", "AGG"])
    assert weights == {"SPY": .6, "AGG": .4, "CASH": 0.0}
    assert "EXCLUDED" not in weights


@pytest.mark.parametrize("weight", [1e-15, -.1, np.nan, np.inf])
def test_unavailable_nonzero_or_nonfinite_weights_are_never_ignored(weight):
    with pytest.raises(ValueError):
        validate_weights({"SPY": 1.0, "EXCLUDED": weight}, ["SPY"])


def test_week_advance_processes_all_five_sessions_and_pending_execution():
    sim = replay({"A": [10, 20, 22, 24, 26, 28, 30]}, capital=100, cost_bps=0, weights={"A": 1})
    sim.advance("week")
    assert len(sim.history) == 6
    assert sim.current_date == sim.history.index[5]
    assert sim.ledger.execution_date.iloc[0] == sim.history.index[1]
    assert sim.history.equity.iloc[-1] == pytest.approx(140)


def test_month_destination_is_final_session_of_next_calendar_month():
    dates = pd.bdate_range("2020-01-02", "2020-04-02")
    prices = pd.DataFrame({"A": 10.0}, index=dates)
    sim = Simulation(prices, dates[0], dates[-1], sessions=dates)
    assert sim.destination("month") == pd.Timestamp("2020-02-28")
    sim.advance("month")
    assert len(sim.history) == len(dates[dates <= "2020-02-28"])
    assert sim.destination("month") == pd.Timestamp("2020-03-31")


def test_month_advance_caps_at_end_without_inventing_sessions():
    sim = replay({"A": [10, 10, 10]}, cost_bps=0)
    assert sim.destination("month") == sim._sessions[-1]
    sim.advance("month")
    assert sim.complete
    assert len(sim.history) == 3


def test_terminal_order_cancelled_without_liquidation_or_future_execution():
    sim = replay({"A": [10, 10]}, capital=100, cost_bps=0, weights={"A": 1})
    sim.run()
    assert sim.portfolio.units["A"] == 10
    assert len(sim.ledger) == 1
    sim.submit_target({"CASH": 1})
    assert sim.pending is None
    assert sim.decisions.iloc[-1].status == "cancelled"
    assert pd.isna(sim.decisions.iloc[-1].execution_date)
    assert sim.portfolio.units["A"] == 10


def test_final_automatic_signal_is_cancelled_without_terminal_fill():
    prices = pd.DataFrame({"SPY": [10, 11, 12]}, index=pd.bdate_range("2020-01-29", periods=3))
    sim = Simulation(
        prices, prices.index[0], prices.index[-1], sessions=prices.index,
        capital=100, cost_bps=0, strategy=make_strategy("User-Defined Allocation", weights={"SPY": 1}),
    )
    result = sim.run()
    last = result.decisions.iloc[-1]
    assert last.source == "automatic"
    assert last.status == "cancelled"
    assert pd.isna(last.execution_date)
    assert "No execution session remains" in last.reason
    assert result.ledger.execution_date.max() == prices.index[1]
    assert len(result.history) == 3
    assert sim.portfolio.units["SPY"] == pytest.approx(100 / 11)


def test_manual_replaces_pending_target_and_cancellation_leaves_cash():
    sim = replay({"A": [10, 20, 30]}, weights={"A": 1})
    message = sim.submit_target({"CASH": 1})
    assert "Replaced" in message
    assert sim.decisions.iloc[0].status == "replaced"
    assert sim.pending.source == "manual"
    assert sim.cancel_pending()
    assert not sim.cancel_pending()
    sim.run()
    assert sim.ledger.empty
    assert sim.history.equity.iloc[-1] == 100000


def test_rerun_read_and_repeated_current_advance_do_not_duplicate_execution():
    sim = replay({"A": [10, 11, 12]}, weights={"A": 1})
    sim.advance()
    before = sim.ledger.copy()
    for _ in range(3):
        sim.snapshot()
        sim.result()
        sim.advance_to(sim.current_date)
    pd.testing.assert_frame_equal(sim.ledger, before)
    assert len(sim.history) == 2


def test_reset_by_new_session_clears_clock_ledger_and_holdings():
    sim = replay({"A": [10, 11, 12]}, weights={"A": 1})
    sim.run()
    reset = replay({"A": [10, 11, 12]}, weights={"A": 1})
    assert len(reset.history) == 1
    assert reset.ledger.empty
    assert reset.portfolio.units["A"] == 0
    assert reset.portfolio.cash == 100000
    assert reset.pending.order_id == 1


def test_missing_held_or_eligible_price_blocks_without_forward_fill():
    prices = pd.DataFrame({"A": [10, np.nan, 12]}, index=pd.bdate_range("2020-01-02", periods=3))
    with pytest.raises(ValueError, match="Missing/invalid A"):
        Simulation(prices, prices.index[0], prices.index[-1], sessions=prices.index)


def test_missing_exchange_session_is_not_silently_dropped():
    dates = pd.bdate_range("2020-01-02", periods=3)
    prices = pd.DataFrame({"A": [10, 12]}, index=dates[[0, 2]])
    with pytest.raises(ValueError, match="Missing exchange-session"):
        Simulation(prices, dates[0], dates[-1], sessions=dates)


def test_before_asset_inception_rejected_even_if_prices_are_supplied():
    dates = pd.bdate_range("2000-01-03", periods=3)
    prices = pd.DataFrame({"AGG": [10, 11, 12]}, index=dates)
    with pytest.raises(ValueError, match="did not exist"):
        Simulation(prices, dates[0], dates[-1], sessions=dates)


def test_matched_benchmarks_use_identical_baselines_windows_and_delays():
    prices = pd.DataFrame({"SPY": [10, 20, 22], "AGG": [10, 11, 12]}, index=pd.bdate_range("2020-01-02", periods=3))
    common = dict(start=prices.index[0], end=prices.index[-1], sessions=prices.index, capital=100, cost_bps=10)
    spy = Simulation(prices, strategy=make_strategy("Buy and Hold"), **common).run()
    balanced = Simulation(prices, strategy=make_strategy("Traditional 60/40"), **common).run()
    pd.testing.assert_index_equal(spy.history.index, balanced.history.index)
    assert spy.history.equity.iloc[0] == balanced.history.equity.iloc[0] == 100
    assert spy.ledger.execution_date.min() == balanced.ledger.execution_date.min() == prices.index[1]
    assert spy.metadata["cost_bps"] == balanced.metadata["cost_bps"] == 10


def test_no_retroactive_clock_movement():
    sim = replay({"A": [10, 11, 12]})
    start = sim.current_date
    sim.advance()
    with pytest.raises(ValueError, match="cannot move backward"):
        sim.advance_to(start)


def test_independent_simulations_do_not_share_mutable_state():
    first = replay({"A": [10, 11, 12]}, weights={"A": 1})
    second = replay({"A": [10, 11, 12]}, weights={"A": 1})
    first.advance()
    assert len(second.history) == 1
    assert second.portfolio.units["A"] == 0


def test_zero_cost_cash_allocation_has_no_trades_or_growth():
    sim = replay({"A": [10, 100, 1]}, capital=100, cost_bps=0, weights={"CASH": 1})
    result = sim.run()
    assert result.history.equity.tolist() == [100, 100, 100]
    assert result.ledger.empty
