"""Exports enforce the Survival boundary independently of presentation code."""

import io

import pandas as pd
import pytest

from core.reporting import build_csv_report, build_html_report, build_strategy_comparison_csv, formatted_metric_table


@pytest.fixture
def inputs():
    index = pd.to_datetime(["2007-01-03", "2007-01-04", "2007-01-05"])
    history = pd.DataFrame({"equity": [100.0, 99.0, 999999.0], "cash": [100.0, 0.0, 0.0]}, index=index)
    history.index.name = "date"
    trades = pd.DataFrame({
        "signal_date": [index[0], index[1]],
        "execution_date": [index[1], index[2]],
        "asset": ["SPY", "FUTURE_ASSET"],
        "gross_notional": [99.0, 999999.0],
        "costs": [1.0, 0.0],
    })
    metadata = {"data_source": "synthetic", "scenario": "<script>alert('x')</script>", "future_secret": "DO_NOT_EXPORT", "requested_end": "2099-12-31"}
    events = [
        {"disclosure_session": index[1], "headline": "Known event", "source_url": "https://example.org/known"},
        {"disclosure_session": index[2], "headline": "UNKNOWN_FUTURE_EVENT", "source_url": "https://example.org/future"},
    ]
    return history, trades, metadata, events


def test_interim_csv_never_contains_future_data_and_labels_each_row(inputs):
    history, trades, metadata, _ = inputs
    csv = build_csv_report(history, trades, metadata, current_date="2007-01-04")
    frame = pd.read_csv(io.StringIO(csv))
    assert len(frame) == 2
    assert frame["data_source"].str.contains("SYNTHETIC").all()
    assert frame["report_status"].str.contains("Partial").all()
    assert frame["as_of"].eq("2007-01-04").all()
    assert "999999" not in csv
    assert "2007-01-05" not in csv
    assert "DO_NOT_EXPORT" not in csv
    ledger = build_csv_report(history, trades, metadata, "2007-01-04", kind="trades")
    assert "FUTURE_ASSET" not in ledger
    assert "SPY" in ledger


def test_html_is_self_contained_escaped_and_recomputes_restricted_metrics(inputs):
    history, trades, metadata, events = inputs
    html = build_html_report(history, trades, metadata, metrics={"final_value": 999999}, events=events, current_date="2007-01-04")
    assert "SYNTHETIC DEMONSTRATION" in html
    assert "Partial session" in html
    assert "Known event" in html
    for secret in ("UNKNOWN_FUTURE_EVENT", "FUTURE_ASSET", "999999", "2099-12-31", "DO_NOT_EXPORT", "2007-01-05"):
        assert secret not in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<script src=" not in html and "<link" not in html
    assert "Educational survival score:" not in html


def test_completed_report_score_and_same_window_comparison(inputs):
    history, trades, metadata, events = inputs
    metadata={**metadata,"benchmark_name":"SPY buy & hold"}
    benchmark = pd.Series([100.0, 95.0], index=history.index[:2])
    html = build_html_report(history, trades, metadata, events=events, benchmark=benchmark, current_date="2007-01-04", completed=True)
    assert "DID YOUR PORTFOLIO SURVIVE THE CRISIS?" in html
    assert "Educational survival score:" in html
    assert "$99.00" in html and "$95.00" in html
    assert "SPY buy &amp; hold" in html
    with pytest.raises(ValueError, match="identical evaluation"):
        build_html_report(history, trades, metadata, benchmark=benchmark.iloc[:1], current_date="2007-01-04")


def test_all_export_tables_obey_cutoff_and_missing_dates_rejected(inputs):
    history, trades, metadata, events = inputs
    contributions = pd.DataFrame({"SPY": [0, 0, 1234567], "COSTS": [0, -1, 0]}, index=history.index)
    exported = build_csv_report(history, trades, metadata, "2007-01-04", kind="contributions", contributions=contributions)
    assert "1234567" not in exported
    exported_events = build_csv_report(history, trades, metadata, "2007-01-04", kind="events", events=events)
    assert "UNKNOWN_FUTURE_EVENT" not in exported_events
    with pytest.raises(ValueError, match="date"):
        build_csv_report(history, pd.DataFrame({"gross_notional": [1]}), metadata, kind="trades")


def test_empty_ledger_retains_provenance_headers(inputs):
    history, _, metadata, _ = inputs
    csv = build_csv_report(history.iloc[:1], pd.DataFrame(), metadata, kind="trades")
    assert csv.startswith("data_source,as_of,report_status")
    assert "SYNTHETIC DEMONSTRATION" in csv
    assert "No revealed observations" in csv


def test_terminal_cancelled_decision_and_configurable_confidence(inputs):
    history, trades, metadata, _ = inputs
    decisions = pd.DataFrame({"signal_date":[history.index[1]],"execution_date":[None],"status":["cancelled"]})
    html = build_html_report(history,trades,metadata,current_date="2007-01-04",decisions=decisions,confidence=.99)
    assert "cancelled" in html
    assert "linear 99% quantile" in html


def test_pending_decision_preserved_without_future_execution_date(inputs):
    history, trades, metadata, _ = inputs
    decisions=pd.DataFrame({"signal_date":[history.index[1]],"execution_date":[history.index[2]],"status":["pending"],"decision":["MY_CURRENT_TARGET"]})
    csv=build_csv_report(history,metadata=metadata,current_date="2007-01-04",kind="decisions",decisions=decisions)
    assert "MY_CURRENT_TARGET" in csv
    assert "Pending after report date" in csv
    assert "2007-01-05" not in csv
    html=build_html_report(history,trades,metadata,current_date="2007-01-04",decisions=decisions)
    assert "MY_CURRENT_TARGET" in html
    assert "2007-01-05" not in html


def test_metric_presentation_labels_percentage_points_and_dollars():
    values=formatted_metric_table({"total_return":.125,"excess_return":.025,"total_costs":1234.5,"gross_turnover":1.75,"sharpe":None}).set_index("Metric")["Value"]
    assert values["Total return"]=="12.50%"
    assert values["Excess cumulative return"]=="+2.50 pp"
    assert values["Trading costs"]=="$1,234.50"
    assert values["Gross turnover"]=="1.75×"
    assert values["Sharpe ratio"]=="N/A"


@pytest.mark.parametrize("name,ending",[("SPY buy & hold",95.0),("60/40 monthly",102.0)])
def test_selected_benchmark_exports_keep_matching_revealed_window(inputs,name,ending):
    history,trades,metadata,_=inputs
    reference=pd.Series([100.0,ending,9999999.0],index=history.index)
    public={**metadata,"benchmark_name":name}
    csv=build_csv_report(history,trades,public,current_date="2007-01-04",benchmark=reference)
    exported=pd.read_csv(io.StringIO(csv))
    assert list(exported["date"])==["2007-01-03","2007-01-04"]
    assert exported["benchmark_equity"].tolist()==[100.0,ending]
    html=build_html_report(history,trades,public,benchmark=reference,current_date="2007-01-04")
    assert name.replace("&","&amp;") in html
    assert "9999999" not in html and "2007-01-05" not in html


def test_strategy_comparison_is_rectangular_with_source_on_every_row():
    table = pd.DataFrame({
        "total_return": [.125, -.25], "total_costs": [49.97501249375312, 103.25],
        "sharpe": [1.75, None], "observations": [1007, 1007],
    }, index=["Buy and Hold", "Momentum, monthly"])
    original = table.copy(deep=True)
    csv = build_strategy_comparison_csv(table, "synthetic", "2008 crisis, research", "SPY buy & hold",
                                        "2007-01-03", "2010-12-31")
    # A standard reader needs no custom skiprows/header handling.
    exported = pd.read_csv(io.StringIO(csv))
    assert len(exported) == 2
    assert csv.startswith("data_source,research_mode,scenario,benchmark_name,evaluation_start,evaluation_end,strategy,")
    assert exported.data_source.str.contains("SYNTHETIC DEMONSTRATION").all()
    assert exported.research_mode.str.contains("Retrospective research").all()
    assert exported.scenario.eq("2008 crisis, research").all()
    assert exported.benchmark_name.eq("SPY buy & hold").all()
    assert exported.evaluation_start.eq("2007-01-03").all()
    assert exported.evaluation_end.eq("2010-12-31").all()
    pd.testing.assert_frame_equal(exported.set_index("strategy")[table.columns], table.rename_axis("strategy"))
    pd.testing.assert_frame_equal(table, original)


def test_strategy_comparison_retains_genuine_source_and_optional_dates():
    table = pd.DataFrame({"total_return": [.2]}, index=["Trend"])
    exported = pd.read_csv(io.StringIO(build_strategy_comparison_csv(table, "Historical adjusted prices", "COVID", "60/40 monthly")))
    assert exported.data_source.tolist() == ["Historical adjusted prices"]
    assert exported.evaluation_start.isna().all() and exported.evaluation_end.isna().all()
    assert exported.total_return.tolist() == [.2]


@pytest.mark.parametrize("field", ["data_source", "research_mode", "scenario", "benchmark_name", "evaluation_start", "evaluation_end", "strategy"])
def test_strategy_comparison_rejects_reserved_metric_names(field):
    table = pd.DataFrame({field: [.1]}, index=["Trend"])
    with pytest.raises(ValueError, match="reserved"):
        build_strategy_comparison_csv(table, "synthetic", "Crisis", "SPY")


@pytest.mark.parametrize("source", ["", "  ", None])
def test_strategy_comparison_rejects_missing_source(source):
    with pytest.raises(ValueError, match="data_source"):
        build_strategy_comparison_csv(pd.DataFrame({"total_return": [.1]}, index=["Trend"]), source, "Crisis", "SPY")


def test_strategy_comparison_rejects_empty_or_ambiguous_tables_and_dates():
    with pytest.raises(ValueError, match="at least one"):
        build_strategy_comparison_csv(pd.DataFrame(), "synthetic", "Crisis", "SPY")
    duplicates = pd.DataFrame({"total_return": [.1, .2]}, index=["Trend", "Trend"])
    with pytest.raises(ValueError, match="Strategy labels"):
        build_strategy_comparison_csv(duplicates, "synthetic", "Crisis", "SPY")
    duplicate_metrics = pd.DataFrame([[.1, .2]], index=["Trend"], columns=["return", "return"])
    with pytest.raises(ValueError, match="Metric columns"):
        build_strategy_comparison_csv(duplicate_metrics, "synthetic", "Crisis", "SPY")
    with pytest.raises(ValueError, match="Evaluation start"):
        build_strategy_comparison_csv(pd.DataFrame({"total_return": [.1]}, index=["Trend"]), "synthetic", "Crisis", "SPY", "2010-01-02", "2010-01-01")
