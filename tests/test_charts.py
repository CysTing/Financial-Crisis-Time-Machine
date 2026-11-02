"""Serialized chart payloads and axis bounds must respect the replay clock."""

import json

import pandas as pd
import pytest

from ui import charts


def _payload(figure):
    return json.loads(figure.to_json())


def _assert_date_bounds(payload, current_date):
    axis = payload["layout"]["xaxis"]
    first, last = map(pd.Timestamp, axis["range"])
    assert first < last
    assert last <= pd.Timestamp(current_date)
    assert axis["autorange"] is False
    assert axis["type"] == "date"
    for trace in payload["data"]:
        for date in trace.get("x", []):
            assert pd.Timestamp(date) <= pd.Timestamp(current_date)


def test_equity_json_ends_at_clock_and_filters_future_annotations():
    dates = pd.date_range("2007-01-03", periods=5)
    equity = pd.Series([100, 101, 102, 999999, 1], index=dates)
    events = [
        {"disclosure_date": "2007-01-04", "headline": "Revealed event"},
        {"disclosure_date": "2007-01-07", "headline": "Future secret event"},
    ]
    payload = _payload(charts.equity_chart({"Replay": equity}, events, current_date=dates[2]))
    _assert_date_bounds(payload, dates[2])
    assert payload["data"][0]["y"] == [100, 101, 102]
    assert pd.Timestamp(payload["data"][0]["x"][-1]) == dates[2]
    assert "Future secret" not in json.dumps(payload)
    assert len(payload["layout"]["annotations"]) == 1


def test_equity_single_observation_has_finite_nonfuture_range():
    equity = pd.Series([100000.0], index=pd.to_datetime(["2007-01-03"]))
    payload = _payload(charts.equity_chart({"Replay": equity}))
    _assert_date_bounds(payload, "2007-01-03")
    assert pd.Timestamp(payload["layout"]["xaxis"]["range"][-1]) == equity.index[0]


def test_normalized_chart_has_index_labels_and_no_currency_units():
    equity = pd.Series([100, 105], index=pd.to_datetime(["2007-01-03", "2007-01-04"]))
    payload = _payload(charts.equity_chart({"SPY": equity}, normalized=True))
    assert "USD" not in payload["layout"]["yaxis"]["title"]["text"]
    assert payload["layout"]["yaxis"]["tickprefix"] == ""
    assert "$" not in payload["data"][0]["hovertemplate"]
    assert "index points" in payload["data"][0]["hovertemplate"]
    assert "normalized to 100" in payload["layout"]["title"]["text"]


@pytest.mark.parametrize("builder", [charts.drawdown_chart, charts.volatility_chart])
def test_risk_chart_payloads_and_ranges_stop_at_asof(builder):
    dates = pd.bdate_range("2020-01-02", periods=5)
    equity = pd.Series([100, 101, 98, 999999, 1], index=dates)
    payload = _payload(builder(equity, current_date=dates[2]))
    _assert_date_bounds(payload, dates[2])


@pytest.mark.parametrize("length", [1, 3])
def test_insufficient_rolling_volatility_remains_bounded_with_null_values(length):
    dates = pd.bdate_range("2020-01-02", periods=length)
    equity = pd.Series([100.0] * length, index=dates)
    payload = _payload(charts.volatility_chart(equity, window=21))
    _assert_date_bounds(payload, dates[-1])
    assert all(value is None for value in payload["data"][0]["y"])


def test_rolling_correlations_all_null_have_no_future_axis_or_arrays():
    dates = pd.bdate_range("2020-01-02", periods=4)
    prices = pd.DataFrame({"SPY": [100, 101, 99, 103], "AGG": [100, 100, 101, 100]}, index=dates)
    payload = _payload(charts.rolling_correlation_chart(prices, window=63, current_date=dates[2]))
    _assert_date_bounds(payload, dates[2])
    assert all(value is None for value in payload["data"][0]["y"])


def test_empty_timeline_uses_explicit_clock_bound():
    payload = _payload(charts.trading_timeline(pd.DataFrame(), current_date="2007-01-03"))
    _assert_date_bounds(payload, "2007-01-03")
    assert payload["data"] == []


def test_timeline_json_filters_future_executions():
    trades = pd.DataFrame({
        "execution_date": pd.to_datetime(["2007-01-04", "2007-01-05"]),
        "asset": ["SPY", "SPY"], "units": [1, -1], "costs": [.1, .1],
    })
    payload = _payload(charts.trading_timeline(trades, current_date="2007-01-04"))
    _assert_date_bounds(payload, "2007-01-04")
    assert len(payload["data"][0]["x"]) == 1


def test_empty_equity_without_clock_has_fixed_historical_hidden_axis():
    payload = _payload(charts.equity_chart({}))
    _assert_date_bounds(payload, "1970-01-01")
    assert payload["layout"]["xaxis"]["visible"] is False


def test_empty_correlation_universe_has_fixed_historical_hidden_axis():
    payload = _payload(charts.rolling_correlation_chart(pd.DataFrame(index=pd.DatetimeIndex([]))))
    _assert_date_bounds(payload, "1970-01-01")
    assert payload["layout"]["xaxis"]["visible"] is False


@pytest.mark.parametrize("trace_count", [3, 8])
def test_chart_title_and_scrollable_legend_have_separate_reserved_regions(trace_count):
    dates = pd.bdate_range("2020-01-02", periods=3)
    values = pd.Series([100000, 101000, 99000], index=dates)
    payload = _payload(charts.equity_chart({f"Strategy {i}": values for i in range(trace_count)}))
    layout = payload["layout"]
    assert layout["title"]["y"] >= .95
    assert layout["title"]["yanchor"] == "top"
    assert layout["margin"]["t"] >= 70
    assert "<br>" in layout["title"]["text"]
    legend = layout["legend"]
    assert legend["y"] < 0
    assert legend["yanchor"] == "top"
    assert legend["maxheight"] >= 90
    # Native scrolling caps the legend even when a narrow screen wraps it into
    # one column. Reserve the actual pixel extent below the plot, not just a row.
    plot_height = layout["height"] - layout["margin"]["t"] - layout["margin"]["b"]
    required_bottom = abs(legend["y"]) * plot_height + legend["maxheight"]
    assert layout["margin"]["b"] >= required_bottom + 4
    assert legend["entrywidth"] >= 210  # room for the longest shipped strategy names


def test_constant_initial_equity_uses_readable_financial_axis_range():
    equity = pd.Series([100000.0], index=pd.to_datetime(["2007-01-03"]))
    payload = _payload(charts.equity_chart({"Replay": equity}))
    low, high = payload["layout"]["yaxis"]["range"]
    assert low < 100000 < high
    assert high - low >= 1000
    assert payload["layout"]["yaxis"]["tickprefix"] == "$"


def test_short_daily_chart_uses_date_ticks_without_implied_intraday_sampling():
    equity = pd.Series([100, 101], index=pd.to_datetime(["2007-01-03", "2007-01-04"]))
    payload = _payload(charts.equity_chart({"Replay": equity}))
    axis = payload["layout"]["xaxis"]
    assert axis["dtick"] >= 86400000
    assert axis["dtick"] % 86400000 == 0
    assert "%H" not in axis["tickformat"]
    assert "%d" in axis["tickformat"]


def test_clustered_events_use_short_hover_markers_instead_of_rotated_headlines():
    dates = pd.bdate_range("2008-09-01", periods=15)
    equity = pd.Series(100000.0, index=dates)
    events = [{"disclosure_date": str(dates[-1].date()), "headline": f"Source-backed announcement {i}"} for i in range(4)]
    payload = _payload(charts.equity_chart({"Replay": equity}, events))
    annotations = payload["layout"]["annotations"]
    assert len(annotations) == 1  # same-session events share one marker
    marker = annotations[0]
    assert marker["text"].isdigit()
    assert marker["textangle"] == 0
    assert all(f"announcement {i}" in marker["hovertext"] for i in range(4))


def test_empty_charts_explain_why_observations_are_unavailable():
    dates = pd.bdate_range("2020-01-02", periods=3)
    equity = pd.Series([100.0, 101.0, 102.0], index=dates)
    prices = pd.DataFrame({"SPY": [100, 101, 102], "AGG": [100, 101, 100]}, index=dates)
    figures = [
        charts.volatility_chart(equity),
        charts.correlation_chart(prices),
        charts.rolling_correlation_chart(prices),
        charts.trading_timeline(pd.DataFrame(), current_date=dates[-1]),
        charts.distribution_chart(equity.iloc[:1]),
    ]
    for figure in figures:
        annotations = _payload(figure)["layout"]["annotations"]
        assert any("required" in item["text"] or "yet" in item["text"] for item in annotations)


def test_allocation_labels_remain_inside_and_do_not_repeat_tickers():
    payload = _payload(charts.allocation_chart({"SPY": .6, "AGG": .4}))
    pie = payload["data"][0]
    assert pie["textinfo"] == "percent"
    assert pie["textposition"] == "inside"
    assert payload["layout"]["legend"]["y"] < 0
