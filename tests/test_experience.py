"""Hand-calculated, causal replay highlights without a UI or internet dependency."""

import numpy as np
import pandas as pd
import pytest

from core.experience import market_pulse, report_debrief


def history(values, **columns):
    dates = pd.bdate_range("2020-01-02", periods=len(values))
    frame = pd.DataFrame({"equity": values, **columns}, index=dates, dtype=float)
    frame["daily_return"] = frame.equity.pct_change(fill_method=None).fillna(0)
    return frame


def test_five_session_advance_highlights_intermediate_loss_not_just_last_day():
    observed = history([100, 99, 89.1, 89.991, 90.89091, 91.7998191],
        costs=[0, .1, .2, .3, .4, .5], gross_notional=[0, 10, 20, 30, 40, 50])
    pulse = market_pulse(observed, observed.index[0])
    assert pulse["count"] == 5
    assert pulse["start_date"] == "2020-01-03"
    assert pulse["latest_date"] == "2020-01-09"
    assert pulse["latest_return"] == pytest.approx(.01)
    assert pulse["largest_abs_move"] == {"date": "2020-01-06", "return": pytest.approx(-.1)}
    assert pulse["tone"] == "drop"
    assert pulse["costs"] == pytest.approx(1.5)
    assert pulse["gross_notional"] == 150
    assert "2020-01-06" in pulse["headline"] and "10.00%" in pulse["headline"]


def test_pulse_threshold_uses_actual_largest_daily_move_and_earliest_tie():
    observed = history([100, 104, 99.84])  # +4%, -4%; first tied magnitude wins.
    pulse = market_pulse(observed, observed.index[0], threshold=.03)
    assert pulse["tone"] == "rise"
    assert pulse["largest_abs_move"]["date"] == "2020-01-03"
    assert market_pulse(observed, observed.index[0], threshold=.05)["tone"] == "steady"


def test_pulse_never_counts_initial_cash_baseline_or_repeats_old_rows():
    observed = history([100, 100, 100])
    assert market_pulse(observed.iloc[:1], "2019-12-01") is None
    assert market_pulse(observed, observed.index[-1]) is None
    pulse = market_pulse(observed, "2019-12-01", threshold=0)
    assert pulse["count"] == 2
    assert pulse["tone"] == "steady"
    assert pulse["costs"] is None and pulse["gross_notional"] is None


def test_reconciled_stored_returns_used_otherwise_recomputed_from_equity():
    observed = history([100, 110, 99])
    assert market_pulse(observed, observed.index[0])["return_source"] == "stored_reconciled"
    observed["daily_return"] = [0, .99, np.nan]
    pulse = market_pulse(observed, observed.index[0])
    assert pulse["return_source"] == "equity_recomputed"
    assert pulse["latest_return"] == pytest.approx(-.1)
    result = report_debrief(observed)
    assert any("did not reconcile" in note for note in result["notes"])


def test_every_highlight_ignores_dramatically_changed_or_invalid_future_rows():
    observed = history([100, 110, 88, 99, 112, 100],
        costs=[0, 1, 2, 3, 4, 5], gross_notional=[0, 10, 20, 30, 40, 50],
        weight_CASH=[1, .4, .5, .6, .7, .8])
    cutoff = observed.index[3]
    altered = observed.copy()
    altered.loc[altered.index > cutoff, "equity"] = [np.nan, 1e15]
    altered.loc[altered.index > cutoff, ["costs", "gross_notional", "weight_CASH", "daily_return"]] = -999
    benchmark = observed.equity.copy()
    changed_benchmark = benchmark.copy()
    changed_benchmark.loc[changed_benchmark.index > cutoff] = np.nan
    assert market_pulse(observed, observed.index[0], cutoff) == market_pulse(altered, observed.index[0], cutoff)
    assert report_debrief(observed, benchmark, cutoff) == report_debrief(altered, changed_benchmark, cutoff)
    result = report_debrief(observed, benchmark, cutoff)
    assert result["as_of"] == "2020-01-07"
    assert result["recovery"]["status"] == "unrecovered"
    assert result["recovery"]["recovery_date"] is None
    assert "2020-01-08" not in str(result)


def test_cost_cash_and_net_return_reconcile_to_independent_hand_case():
    # Initial $100. Buy $90, pay $0.90: equity99.10/cash9.10.
    # Asset gains $9, then sell $99 and pay $0.99: final cash/equity107.11.
    observed = history([100, 99.1, 107.11], costs=[0, .9, .99],
        gross_notional=[0, 90, 99], cash=[100, 9.1, 107.11])
    result = report_debrief(observed)
    assert result["capital_retained"] == pytest.approx(1.0711)
    assert result["total_return"] == pytest.approx(.0711)
    assert result["cost_total"] == pytest.approx(1.89)
    assert result["cost_drag"] == pytest.approx(.0189)
    assert result["total_return"] * 100 + result["cost_total"] == pytest.approx(9.0)
    assert result["cash_average_weight"] == pytest.approx((1 + 9.1 / 99.1 + 1) / 3)
    pulse = market_pulse(observed, observed.index[0])
    assert pulse["costs"] == pytest.approx(1.89)
    assert pulse["gross_notional"] == 189


def test_deepest_drawdown_recovery_and_current_underwater_are_distinct():
    observed = history([100, 110, 88, 99, 112, 100])
    result = report_debrief(observed)
    assert result["weakest_day"] == {"date": "2020-01-06", "return": pytest.approx(-.2)}
    assert result["strongest_day"] == {"date": "2020-01-08", "return": pytest.approx(112 / 99 - 1)}
    assert result["deepest_drawdown"] == {"date": "2020-01-06", "magnitude": pytest.approx(.2), "peak_date": "2020-01-03"}
    recovery = result["recovery"]
    assert recovery["status"] == "recovered"
    assert recovery["recovery_date"] == "2020-01-08"
    assert recovery["sessions_to_recover"] == 3
    assert recovery["calendar_days_to_recover"] == 5
    assert recovery["currently_underwater"] is True
    assert recovery["current_drawdown"] == pytest.approx(1 - 100 / 112)


def test_recovery_uses_latest_equal_peak_before_trough():
    observed = history([100, 110, 110, 88, 110])
    recovery = report_debrief(observed)["recovery"]
    assert recovery["peak_date"] == "2020-01-06"
    assert recovery["recovery_date"] == "2020-01-08"
    assert recovery["sessions_to_recover"] == 2


def test_cash_only_and_empty_or_initial_only_debriefs_are_honest():
    empty = report_debrief(pd.DataFrame())
    assert empty["available"] is False
    assert empty["total_return"] is None
    assert empty["weakest_day"] is None and empty["cost_total"] is None
    initial = report_debrief(history([100]))
    assert initial["available"] is False and initial["observations"] == 0
    assert initial["capital_retained"] == 1
    assert initial["recovery"] is None
    cash = report_debrief(history([100, 100, 100], weight_CASH=[1, 1, 1]))
    assert cash["available"] is True
    assert cash["recovery"]["status"] == "no_drawdown"
    assert cash["recovery"]["recovery_date"] is None
    assert cash["deepest_drawdown"]["magnitude"] == 0
    assert cash["cash_average_weight"] == 1
    assert cash["relative_return"] is None


def test_cutoff_before_any_revealed_observation_is_empty_not_future_summary():
    observed = history([100, 90, 200])
    assert market_pulse(observed, "2019-12-01", current_date="2019-12-31") is None
    result = report_debrief(observed, current_date="2019-12-31")
    assert result["available"] is False
    assert result["as_of"] is None
    assert result["capital_retained"] is None


def test_benchmark_comparison_matches_dates_capital_and_partial_window():
    observed = history([100, 110, 99, 150])
    benchmark = pd.Series([100, 105, 100, 900], index=observed.index)
    cutoff = observed.index[2]
    result = report_debrief(observed, benchmark, cutoff)
    assert result["benchmark_return"] == 0
    assert result["relative_return"] == pytest.approx(-.01)
    wrong_capital = report_debrief(observed, benchmark * 2, cutoff)
    assert wrong_capital["relative_return"] is None
    wrong_dates = report_debrief(observed, benchmark.iloc[[0, 2]], cutoff)
    assert wrong_dates["relative_return"] is None
    assert any("do not match" in note for note in wrong_dates["notes"])


@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf])
def test_invalid_revealed_equity_is_rejected(value):
    observed = history([100, 110, 99])
    observed.iloc[1, observed.columns.get_loc("equity")] = value
    with pytest.raises(ValueError, match="finite and strictly positive"):
        market_pulse(observed, observed.index[0])
    with pytest.raises(ValueError, match="finite and strictly positive"):
        report_debrief(observed)


@pytest.mark.parametrize("threshold", [-.1, np.nan, np.inf])
def test_invalid_animation_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        market_pulse(history([100, 101]), "2020-01-02", threshold=threshold)


def test_invalid_optional_revealed_accounting_values_are_not_fabricated():
    observed = history([100, 101], costs=[0, -1])
    with pytest.raises(ValueError, match="costs"):
        report_debrief(observed)
    observed = history([100, 101], weight_CASH=[1, 1.1])
    with pytest.raises(ValueError, match="cash weights"):
        report_debrief(observed)
