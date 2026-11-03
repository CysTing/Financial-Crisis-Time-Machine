"""The first-minute technical demonstration uses real engine boundaries offline."""

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from core.backtest_engine import Simulation
from core.data_loader import load_market_data
from core.scenarios import SCENARIOS
from core.strategies import make_strategy


APP = Path(__file__).resolve().parents[1] / "app.py"


def test_guarded_workspace_navigation_tracks_page_without_starting_a_replay():
    app = _clean(AppTest.from_file(str(APP), default_timeout=180).run())
    assert "survival" not in app.session_state
    epoch = app.session_state["navigation_epoch"]
    for page in ("Final Performance Report", "Crisis Analysis", "Financial Time Machine"):
        app.radio(key="page").set_value(page).run()
        _clean(app)
        assert "survival" not in app.session_state
        assert any("Start a replay" in item.value for item in app.info)
        assert app.session_state["visible_workspace"] == page
        assert app.session_state["navigation_epoch"] == epoch + 1
        epoch += 1
        app.run()
        _clean(app)
        assert app.session_state["navigation_epoch"] == epoch


def _clean(app):
    assert not app.exception, "\n".join(str(item.value) for item in app.exception)
    return app


def _showcase():
    app = _clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.button(key="start_showcase").click().run(timeout=180)
    return _clean(app)


def test_showcase_is_explicit_synthetic_and_matches_normal_engine_without_provider(monkeypatch):
    import yfinance

    def refuse_provider(*args, **kwargs):
        raise AssertionError("The explicitly offline showcase must not download observations.")

    monkeypatch.setattr(yfinance, "download", refuse_provider)
    app = _showcase()
    sim = app.session_state["survival"]
    scenario = SCENARIOS["covid"]
    assert app.session_state["page"] == "Financial Time Machine"
    assert app.session_state["survival_showcase"] is True
    assert app.session_state["survival_meta"]["synthetic"] is True
    assert app.session_state["survival_meta"]["scenario_key"] == "covid"
    assert any("SYNTHETIC" in item.value for item in app.warning)
    assert sim.current_date == pd.Timestamp("2020-02-19")
    assert sim.capital == 100000 and sim.cost_bps == 5
    assert sim.strategy.name == "Traditional 60/40"
    assert sim.strategy.frequency == "Monthly"
    assert not sim.complete
    data = load_market_data(scenario.start, scenario.end, source="synthetic")
    expected = Simulation(data.prices, scenario.start, scenario.end, 100000, 5,
                          strategy=make_strategy("Traditional 60/40"),
                          data_fingerprint=data.fingerprint)
    expected.advance_to("2020-02-19")
    pd.testing.assert_frame_equal(sim.history, expected.history)
    pd.testing.assert_frame_equal(sim.ledger, expected.ledger)
    pd.testing.assert_frame_equal(sim.contributions, expected.contributions)
    assert sim.snapshot()["prices"].index.max() == sim.current_date
    assert (sim.ledger.execution_date <= sim.current_date).all()
    assert (sim.ledger.signal_date < sim.ledger.execution_date).all()
    assert app.download_button(key="replay_passport").label == "Download run passport JSON"


def test_showcase_month_jump_processes_crisis_and_reruns_do_not_duplicate_execution():
    from core.integrity import build_integrity_snapshot

    app = _showcase()
    sim = app.session_state["survival"]
    original_rows = len(sim.history)
    destination = sim.destination("month")
    assert destination == pd.Timestamp("2020-03-31")
    app.button(key="advance_month").click().run(timeout=180)
    _clean(app)
    assert sim.current_date == destination
    assert len(sim.history) > original_rows + 5
    snapshot = sim.snapshot()
    assert snapshot["prices"].index.max() == destination
    assert build_integrity_snapshot(snapshot)["status"] == "pass"
    pulse = app.session_state["survival_pulse"]
    assert pulse["count"] == len(sim.history) - original_rows
    assert pd.Timestamp(pulse["latest_date"]) == destination
    before = sim.ledger.copy()
    history = sim.history.copy()
    pending = sim.pending
    app.download_button(key="replay_passport").click().run()
    _clean(app)
    app.run()
    _clean(app)
    assert sim.current_date == destination and sim.pending == pending
    pd.testing.assert_frame_equal(sim.ledger, before)
    pd.testing.assert_frame_equal(sim.history, history)


def test_existing_replay_is_resumed_without_showcase_replacement():
    app = _showcase()
    sim = app.session_state["survival"]
    clock, history, ledger = sim.current_date, sim.history.copy(), sim.ledger.copy()
    app.radio(key="page").set_value("Mission Control").run()
    _clean(app)
    assert not any(button.key == "start_showcase" for button in app.button)
    app.button(key="resume_replay").click().run()
    _clean(app)
    assert app.session_state["page"] == "Financial Time Machine"
    assert app.session_state["survival"] is sim
    assert sim.current_date == clock
    pd.testing.assert_frame_equal(sim.history, history)
    pd.testing.assert_frame_equal(sim.ledger, ledger)


def test_showcase_report_passport_and_reset_keep_session_boundary():
    app = _showcase()
    sim = app.session_state["survival"]
    clock = sim.current_date
    app.radio(key="page").set_value("Final Performance Report").run()
    _clean(app)
    assert any("PARTIAL SESSION" in item.value for item in app.info)
    assert len(app.download_button) == 5
    app.download_button(key="report_passport").click().run()
    _clean(app)
    assert sim.current_date == clock
    next(button for button in app.button if button.label == "Reset mission").click().run()
    _clean(app)
    assert "survival" not in app.session_state
    assert "survival_showcase" not in app.session_state
    assert app.button(key="start_showcase").label == "Start the crisis showcase"


def test_corruption_drill_detects_all_faults_preserves_replay_and_expires_after_advance():
    app = _showcase()
    sim = app.session_state["survival"]
    clock, pending = sim.current_date, sim.pending
    original = sim.snapshot()
    app.button(key="replay_passport_drill").click().run(timeout=180)
    _clean(app)
    challenge = app.session_state["replay_passport_challenge"]
    assert challenge["passed"] and challenge["original_unchanged"]
    assert len(challenge["cases"]) == 3
    assert all(case["detected"] is True for case in challenge["cases"])
    assert any("All applicable corruptions detected" in item.value for item in app.success)
    faults = next(item.value for item in app.dataframe if "Injected fault" in item.value.columns)
    assert faults["Result"].tolist() == ["DETECTED"] * 3
    assert sim.current_date == clock and sim.pending == pending
    after = sim.snapshot()
    for key in ("history", "holdings", "ledger", "contributions", "decisions", "prices"):
        pd.testing.assert_frame_equal(original[key], after[key])
    app.button(key="advance_day").click().run()
    _clean(app)
    assert sim.current_date > clock
    assert not any("All applicable corruptions detected" in item.value for item in app.success)
    assert not any("Injected fault" in item.value.columns for item in app.dataframe)
