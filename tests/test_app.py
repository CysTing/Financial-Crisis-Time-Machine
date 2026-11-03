"""Offline Streamlit AppTest flows; a real browser complements these tests.

Only the explicit synthetic source is used. These tests never call a provider.
"""

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest
from core.metrics import performance_metrics

APP = Path(__file__).resolve().parents[1] / "app.py"


def _button(app, label):
    return next(element for element in app.button if element.label == label)


def _assert_clean(app):
    assert not app.exception, "\n".join(str(item.value) for item in app.exception)
    return app


def _mission():
    app = _assert_clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.radio(key="mission_source").set_value("Synthetic offline demonstration").run()
    _button(app, "Initialize mission →").click().run(timeout=180)
    _assert_clean(app)
    assert "survival_ready" in app.session_state
    _button(app, "Launch validated mission →").click().run(timeout=180)
    _assert_clean(app)
    assert "survival" in app.session_state
    return app


def test_survival_trade_advance_rerun_pages_reports_and_reset():
    app = _mission()
    simulation = app.session_state["survival"]
    start = simulation.current_date
    assert len(simulation.history) == 1
    assert simulation.ledger.empty
    assert simulation.pending is not None
    # A manual allocation visibly replaces the initial automatic target.
    _button(app, "Queue 100% cash").click().run()
    assert simulation.pending.source == "manual"
    assert simulation.pending.weights["CASH"] == 1
    app.button(key="advance_day").click().run()
    _assert_clean(app)
    assert simulation.current_date > start
    assert len(simulation.history) == 2
    # Buy via the actual target form, then let it execute at the next close.
    app.number_input(key="order_target_CASH").set_value(0.0)
    app.number_input(key="order_target_SPY").set_value(100.0)
    _button(app, "Queue target for next close").click().run()
    assert simulation.pending.source == "manual"
    old_count = len(simulation.history)
    app.button(key="advance_week").click().run()
    _assert_clean(app)
    assert len(simulation.history) == old_count + 5
    assert not simulation.ledger.empty
    clock, ledger = simulation.current_date, simulation.ledger.copy()
    app.run()
    _assert_clean(app)
    assert simulation.current_date == clock
    pd.testing.assert_frame_equal(simulation.ledger, ledger)
    destination = simulation.destination("month")
    app.button(key="advance_month").click().run()
    _assert_clean(app)
    assert simulation.current_date == destination
    for page in ("Mission Control", "Financial Time Machine", "Crisis Analysis", "Final Performance Report"):
        app.radio(key="page").set_value(page).run()
        _assert_clean(app)
    assert any("PARTIAL SESSION" in element.value for element in app.info)
    # AppTest drives download-button reruns; browser smoke additionally checks
    # actual downloaded file bytes.
    downloads = app.get("download_button")
    assert len(downloads) == 4
    assert {element.proto.label for element in downloads} == {
        "Download self-contained HTML report", "Download replay CSV",
        "Download trade ledger CSV", "Download decision history CSV",
    }
    download_labels=[element.label for element in app.download_button]
    for label in download_labels:
        next(element for element in app.download_button if element.label==label).click().run()
        _assert_clean(app)
        assert simulation.current_date==destination
    before = simulation.current_date
    app.radio(key="page").set_value("Strategy Laboratory").run()
    _assert_clean(app)
    assert simulation.current_date == before
    _button(app, "Reset mission").click().run()
    _assert_clean(app)
    assert "survival" not in app.session_state
    assert "benchmarks" not in app.session_state
    assert app.session_state["page"] == "Mission Control"


def test_lab_runs_all_six_strategies_without_survival_mutation():
    app = _mission()
    simulation = app.session_state["survival"]
    before, ledger = simulation.current_date, simulation.ledger.copy()
    app.radio(key="page").set_value("Strategy Laboratory").run()
    app.radio(key="lab_source").set_value("Synthetic offline demonstration").run()
    _button(app, "Run comparative research →").click().run(timeout=180)
    _assert_clean(app)
    saved = app.session_state["lab_result"]
    assert saved["synthetic"]
    assert len(saved["results"]) == 6
    assert not saved["exclusions"]
    indices = [result.history.index for result in saved["results"].values()]
    assert all(index.equals(indices[0]) for index in indices)
    assert simulation.current_date == before
    pd.testing.assert_frame_equal(simulation.ledger, ledger)


def test_completed_crisis_analysis_and_final_report_render():
    app = _mission()
    # Engine stepping has its own detailed tests. Advance server state here to
    # exercise the completion gates without hundreds of button clicks.
    app.session_state["survival"].run()
    app.radio(key="page").set_value("Crisis Analysis").run(timeout=180)
    _assert_clean(app)
    assert any(element.label.startswith("One-session historical VaR") for element in app.metric)
    app.radio(key="page").set_value("Final Performance Report").run(timeout=180)
    _assert_clean(app)
    assert any("DID YOUR PORTFOLIO SURVIVE" in element.value for element in app.subheader)
    assert len(app.get("download_button")) == 4


def test_report_benchmark_selection_changes_comparison_without_advancing():
    app=_mission()
    simulation=app.session_state["survival"]
    simulation.advance("week")
    clock=simulation.current_date
    for name in ("SPY buy & hold","60/40 monthly"):
        app.session_state["survival_benchmark_choice"]=name
        app.radio(key="page").set_value("Final Performance Report").run()
        _assert_clean(app)
        assert any(element.label==f"Benchmark · {name}" for element in app.metric)
        benchmark=app.session_state["benchmarks"][name]
        assert benchmark.current_date==clock
        assert benchmark.history.index.equals(simulation.history.index)
        expected=performance_metrics(simulation.history.equity,benchmark.history.equity,simulation.ledger)["excess_return"]
        details=next(element.value for element in app.dataframe if {"Metric","Value"}.issubset(element.value.columns))
        actual=details.set_index("Metric").loc["Excess cumulative return","Value"]
        assert actual==f"{expected*100:+.2f} pp"
        assert simulation.current_date==clock
