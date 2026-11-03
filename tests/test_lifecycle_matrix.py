"""Offline lifecycle matrix: every scenario/rule can report and start again.

The UI drives configuration, launch, elapsed-session controls, downloads,
navigation, and relaunch. The already-tested engine fast-forwards the remaining
sessions so completed reports are exercised without hundreds of UI clicks.
"""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.integrity import build_integrity_snapshot
from core.scenarios import SCENARIOS
from core.strategies import STRATEGIES


APP = Path(__file__).resolve().parents[1] / "app.py"
SYNTHETIC = "Synthetic offline demonstration"
DOWNLOADS = {
    "Download self-contained HTML report", "Download replay CSV",
    "Download trade ledger CSV", "Download decision history CSV",
    "Download run passport JSON",
}


def clean(app):
    assert not app.exception, "\n".join(str(item.value) for item in app.exception)
    return app


def button(app, label):
    return next(item for item in app.button if item.label == label)


def open_page(app, page):
    app.radio(key="page").set_value(page).run(timeout=180)
    return clean(app)


def initialize(app, scenario, strategy):
    app.radio(key="mission_source").set_value(SYNTHETIC)
    app.selectbox(key="mission_scenario").set_value(scenario)
    app.selectbox(key="mission_strategy").set_value(strategy)
    app.run(timeout=180)
    clean(app)
    button(app, "Initialize mission →").click().run(timeout=180)
    clean(app)
    assert "survival_ready" in app.session_state
    button(app, "Launch validated mission →").click().run(timeout=180)
    return clean(app)


def download_without_state_change(app):
    simulation = app.session_state["survival"]
    clock, pending = simulation.current_date, simulation.pending
    history, ledger = simulation.history.copy(), simulation.ledger.copy()
    assert {item.label for item in app.download_button} == DOWNLOADS
    for label in sorted(DOWNLOADS):
        next(item for item in app.download_button if item.label == label).click().run(timeout=180)
        clean(app)
        assert simulation.current_date == clock
        assert simulation.pending == pending
        pd.testing.assert_frame_equal(simulation.history, history)
        pd.testing.assert_frame_equal(simulation.ledger, ledger)


@pytest.mark.parametrize("scenario_key", list(SCENARIOS))
@pytest.mark.parametrize("strategy_name", STRATEGIES)
def test_each_mission_reports_completes_relaunches_and_resets(scenario_key, strategy_name, monkeypatch):
    import yfinance

    def no_provider(*args, **kwargs):
        raise AssertionError("The offline lifecycle matrix must never call a provider.")

    monkeypatch.setattr(yfinance, "download", no_provider)
    app = clean(AppTest.from_file(str(APP), default_timeout=180).run())
    initialize(app, scenario_key, strategy_name)
    simulation = app.session_state["survival"]
    assert simulation.strategy.name == strategy_name
    assert len(simulation.history) == 1 and simulation.ledger.empty
    assert simulation.pending is not None
    open_page(app, "Final Performance Report")
    assert any("PARTIAL SESSION" in item.value for item in app.info)
    # Empty initial ledgers and undefined initial ratios must still export.
    download_without_state_change(app)
    open_page(app, "Financial Time Machine")
    for step in ("day", "week", "month"):
        target = simulation.destination(step)
        app.button(key=f"advance_{step}").click().run(timeout=180)
        clean(app)
        assert simulation.current_date == target
        assert build_integrity_snapshot(simulation.snapshot())["status"] == "pass"
    simulation.run()
    open_page(app, "Crisis Analysis")
    assert simulation.complete and simulation.pending is None
    open_page(app, "Final Performance Report")
    assert not any("PARTIAL SESSION" in item.value for item in app.info)
    assert any("DID YOUR PORTFOLIO SURVIVE" in item.value for item in app.subheader)
    download_without_state_change(app)
    finished_history, finished_ledger = simulation.history.copy(), simulation.ledger.copy()
    # Start another attempt directly from the starting page, without resetting
    # the prior mission first. Use another scenario and explicit cash allocation.
    open_page(app, "Mission Control")
    next_scenario = list(SCENARIOS)[(list(SCENARIOS).index(scenario_key) + 1) % len(SCENARIOS)]
    app.radio(key="mission_source").set_value(SYNTHETIC)
    app.selectbox(key="mission_scenario").set_value(next_scenario)
    app.selectbox(key="mission_strategy").set_value("Buy and Hold")
    app.run()
    clean(app)
    app.selectbox(key="mission_preset").set_value("100% cash")
    next(item for item in app.number_input if item.label == "Initial capital (USD)").set_value(200000.)
    app.run()
    clean(app)
    button(app, "Initialize mission →").click().run(timeout=180)
    clean(app)
    button(app, "Launch validated mission →").click().run(timeout=180)
    clean(app)
    second = app.session_state["survival"]
    assert second is not simulation
    assert app.session_state["survival_meta"]["scenario_key"] == next_scenario
    assert second.capital == 200000.
    assert len(second.history) == 1 and second.ledger.empty
    assert second.pending.weights["CASH"] == 1.0
    assert app.session_state["survival_journal"] == []
    app.button(key="advance_day").click().run(timeout=180)
    clean(app)
    assert second.history.equity.iloc[-1] == 200000.
    assert second.ledger.empty
    open_page(app, "Final Performance Report")
    download_without_state_change(app)
    pd.testing.assert_frame_equal(simulation.history, finished_history)
    pd.testing.assert_frame_equal(simulation.ledger, finished_ledger)
    button(app, "Reset mission").click().run()
    clean(app)
    assert app.session_state["page"] == "Mission Control"
    assert "survival" not in app.session_state and "benchmarks" not in app.session_state
    assert not app.button(key="start_showcase").disabled


def test_laboratory_result_survives_new_mission_without_changing_its_own_run():
    app = clean(AppTest.from_file(str(APP), default_timeout=180).run())
    open_page(app, "Strategy Laboratory")
    app.radio(key="lab_source").set_value(SYNTHETIC).run()
    button(app, "Run comparative research →").click().run(timeout=180)
    clean(app)
    saved = app.session_state["lab_result"]
    histories = {name: result.history.copy() for name, result in saved["results"].items()}
    open_page(app, "Mission Control")
    initialize(app, "covid", "Inverse Volatility")
    simulation = app.session_state["survival"]
    app.button(key="advance_month").click().run()
    clean(app)
    clock, ledger = simulation.current_date, simulation.ledger.copy()
    open_page(app, "Strategy Laboratory")
    assert app.session_state["lab_result"] is saved
    for name, history in histories.items():
        pd.testing.assert_frame_equal(saved["results"][name].history, history)
    next(item for item in app.download_button if item.label == "Download strategy comparison CSV").click().run()
    clean(app)
    assert simulation.current_date == clock
    pd.testing.assert_frame_equal(simulation.ledger, ledger)
    button(app, "Reset mission").click().run()
    clean(app)
    open_page(app, "Strategy Laboratory")
    assert app.session_state["lab_result"] is saved
    for name, history in histories.items():
        pd.testing.assert_frame_equal(saved["results"][name].history, history)


@pytest.mark.parametrize("frequency", ["None", "Monthly", "Quarterly", "Annually"])
def test_full_cash_custom_mission_completes_and_exports_with_undefined_ratios(frequency):
    app = clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.radio(key="mission_source").set_value(SYNTHETIC)
    app.selectbox(key="mission_scenario").set_value("covid")
    app.selectbox(key="mission_strategy").set_value("User-Defined Allocation")
    app.run()
    clean(app)
    app.selectbox(key="mission_preset").set_value("100% cash")
    app.selectbox(key="mission_frequency").set_value(frequency)
    app.run()
    clean(app)
    button(app, "Initialize mission →").click().run(timeout=180)
    clean(app)
    button(app, "Launch validated mission →").click().run(timeout=180)
    clean(app)
    simulation = app.session_state["survival"]
    assert simulation.strategy.frequency == frequency
    simulation.run()
    assert simulation.complete and simulation.pending is None
    assert simulation.ledger.empty
    assert simulation.history.equity.eq(simulation.capital).all()
    assert simulation.history.costs.eq(0).all()
    assert simulation.contributions.sum(axis=1).eq(0).all()
    assert build_integrity_snapshot(simulation.snapshot())["status"] == "pass"
    open_page(app, "Crisis Analysis")
    assert not app.error
    open_page(app, "Final Performance Report")
    assert not app.error
    assert next(item for item in app.metric if item.label == "Sharpe ratio").value == "N/A"
    assert next(item for item in app.metric if item.label == "Annualized volatility").value == "0.00%"
    assert next(item for item in app.metric if item.label == "Maximum drawdown").value == "0.00%"
    download_without_state_change(app)
