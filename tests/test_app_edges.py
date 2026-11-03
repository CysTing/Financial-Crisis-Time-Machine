"""Regression cases discovered during the independent mission-state UX audit.

These tests use only the explicit synthetic source and do not download data.
"""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"


def _find(elements, label):
    return next(item for item in elements if item.label == label)


def _clean(app):
    assert not app.exception, "\n".join(str(item.value) for item in app.exception)
    return app


def _preflight():
    app = _clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.radio(key="mission_source").set_value("Synthetic offline demonstration").run()
    _find(app.button, "Initialize mission →").click().run(timeout=180)
    _clean(app)
    assert "survival_ready" in app.session_state
    return app


def _launch():
    app = _preflight()
    _find(app.button, "Launch validated mission →").click().run(timeout=180)
    _clean(app)
    assert app.session_state["page"] == "Financial Time Machine"
    return app


def test_changing_scenario_invalidates_previous_preflight_before_launch():
    app = _preflight()
    app.selectbox(key="mission_scenario").set_value("covid").run()
    _clean(app)
    assert "survival_ready" not in app.session_state
    assert not any(button.label == "Launch validated mission →" for button in app.button)


def test_changing_capital_invalidates_previous_preflight_before_launch():
    app = _preflight()
    _find(app.number_input, "Initial capital (USD)").set_value(200000.0).run()
    _clean(app)
    assert "survival_ready" not in app.session_state
    assert not any(button.label == "Launch validated mission →" for button in app.button)


def test_changing_source_cannot_launch_preflight_from_another_source():
    app = _preflight()
    app.radio(key="mission_source").set_value("Historical adjusted prices").run()
    _clean(app)
    assert "survival_ready" not in app.session_state
    assert not any(button.label == "Launch validated mission →" for button in app.button)


def test_inverse_volatility_default_does_not_inherit_momentum_lookback():
    app = _clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.selectbox(key="mission_strategy").set_value("Momentum Rotation").run()
    lookback = _find(app.number_input, "Trailing observations (sessions)")
    assert lookback.value == 126
    lookback.set_value(42).run()
    app.selectbox(key="mission_strategy").set_value("Inverse Volatility").run()
    _clean(app)
    assert _find(app.number_input, "Trailing observations (sessions)").value == 63


def test_unsent_allocation_draft_survives_page_navigation_without_queuing():
    app = _launch()
    sim = app.session_state["survival"]
    pending_before = sim.pending
    clock_before = sim.current_date
    app.number_input(key="order_target_SPY").set_value(60.0).run()
    app.number_input(key="order_target_AGG").set_value(40.0).run()
    app.radio(key="page").set_value("Final Performance Report").run()
    app.radio(key="page").set_value("Financial Time Machine").run()
    _clean(app)
    assert app.number_input(key="order_target_SPY").value == 60.0
    assert app.number_input(key="order_target_AGG").value == 40.0
    assert app.number_input(key="order_target_CASH").value == 0.0
    assert sim.pending == pending_before
    assert sim.current_date == clock_before
    assert sim.ledger.empty


def test_new_mission_clears_previous_unsent_draft_and_uses_new_start_allocation():
    app = _launch()
    old_sim = app.session_state["survival"]
    app.number_input(key="order_target_SPY").set_value(60.0).run()
    app.number_input(key="order_target_AGG").set_value(40.0).run()
    app.radio(key="page").set_value("Mission Control").run()
    app.radio(key="mission_source").set_value("Synthetic offline demonstration").run()
    app.selectbox(key="mission_preset").set_value("100% cash").run()
    _find(app.button, "Initialize mission →").click().run(timeout=180)
    _find(app.button, "Launch validated mission →").click().run(timeout=180)
    _clean(app)
    sim = app.session_state["survival"]
    assert sim is not old_sim
    assert sim.pending.weights["CASH"] == 1.0
    assert app.number_input(key="order_target_SPY").value == 0.0
    assert app.number_input(key="order_target_AGG").value == 0.0
    assert app.number_input(key="order_target_CASH").value == 100.0
    assert sim.ledger.empty
    assert len(sim.history) == 1


def test_zero_dollar_ticket_preserves_pending_target_and_explains_error():
    app = _launch()
    sim = app.session_state["survival"]
    pending_before = sim.pending
    decisions_before = sim.decisions.copy()
    app.number_input(key="ticket_amount").set_value(0.0).run()
    _find(app.button, "Queue single-asset target").click().run()
    _clean(app)
    assert sim.pending == pending_before
    pd.testing.assert_frame_equal(sim.decisions, decisions_before)
    assert any("greater than zero" in item.value for item in app.error)


@pytest.mark.parametrize("side,amount,available_text", [
    ("Buy", 200000.0, "available cash is USD 100,000.00"),
    ("Sell", 1000.0, "available SPY position is USD 0.00"),
])
def test_unfunded_tickets_show_requested_and_available_amounts_without_replacement(side, amount, available_text):
    app = _launch()
    sim = app.session_state["survival"]
    pending_before = sim.pending
    decisions_before = sim.decisions.copy()
    app.selectbox(key="ticket_asset").set_value("SPY").run()
    app.radio(key="ticket_side").set_value(side).run()
    app.number_input(key="ticket_amount").set_value(amount).run()
    _find(app.button, "Queue single-asset target").click().run()
    _clean(app)
    assert sim.pending == pending_before
    pd.testing.assert_frame_equal(sim.decisions, decisions_before)
    errors = " ".join(item.value for item in app.error)
    assert f"USD {amount:,.2f}" in errors
    assert available_text in errors
    assert sim.ledger.empty


def test_sidebar_benchmark_selection_updates_comparison_without_mutating_replay():
    app = _launch()
    sim = app.session_state["survival"]
    app.button(key="advance_week").click().run()
    clock_before, ledger_before = sim.current_date, sim.ledger.copy()
    pending_before = sim.pending
    selector = app.selectbox(key="survival_benchmark_choice")
    assert set(selector.options) == {"SPY buy & hold", "60/40 monthly"}
    selector.set_value("60/40 monthly").run()
    _clean(app)
    assert app.session_state["survival_benchmark_choice"] == "60/40 monthly"
    assert any("Benchmark: 60/40 monthly" in item.value for item in app.caption)
    app.radio(key="page").set_value("Final Performance Report").run()
    _clean(app)
    assert app.selectbox(key="survival_benchmark_choice").value == "60/40 monthly"
    assert sim.current_date == clock_before
    assert sim.pending == pending_before
    pd.testing.assert_frame_equal(sim.ledger, ledger_before)


def _journal_table(app):
    """Inspect the copied view actually presented in the decision-log table."""
    return next(element.value for element in app.dataframe if "rationale" in element.value.columns)


def test_saving_journal_observation_preserves_pending_clock_and_portfolio():
    app = _launch()
    sim = app.session_state["survival"]
    pending, clock = sim.pending, sim.current_date
    history, decisions, ledger = sim.history.copy(), sim.decisions.copy(), sim.ledger.copy()
    app.text_area(key="order_rationale").set_value("  Keep cash available while I review the revealed prices.  ").run()
    _find(app.button, "Save observation without trading").click().run()
    _clean(app)
    journal = app.session_state["survival_journal"]
    assert journal == [{"order_id": None, "signal_date": clock,
                        "rationale": "Keep cash available while I review the revealed prices."}]
    assert app.text_area(key="order_rationale").value == ""
    assert sim.pending == pending and sim.current_date == clock
    pd.testing.assert_frame_equal(sim.history, history)
    pd.testing.assert_frame_equal(sim.decisions, decisions)
    pd.testing.assert_frame_equal(sim.ledger, ledger)
    presented = _journal_table(app)
    observation = presented.loc[presented.source == "journal"].iloc[0]
    assert observation.status == "observation"
    assert observation.signal_date == clock
    assert observation.rationale == journal[0]["rationale"]
    assert pd.isna(observation.order_id)
    app.radio(key="page").set_value("Final Performance Report").run()
    _clean(app)
    assert _journal_table(app).rationale.str.contains("Keep cash available", regex=False).any()
    app.radio(key="page").set_value("Financial Time Machine").run()
    app.run()
    assert len(app.session_state["survival_journal"]) == 1
    assert sim.pending == pending and sim.current_date == clock


def test_queued_rationale_stays_attached_to_order_and_signal_date_after_execution():
    app = _launch()
    sim = app.session_state["survival"]
    signal_date = sim.current_date
    app.number_input(key="order_target_SPY").set_value(60.0).run()
    app.number_input(key="order_target_AGG").set_value(40.0).run()
    app.text_area(key="order_rationale").set_value("Choose a balanced allocation before seeing the next close.").run()
    _find(app.button, "Queue target for next close").click().run()
    _clean(app)
    order_id = sim.pending.order_id
    assert sim.pending.source == "manual"
    assert app.session_state["survival_journal"] == [{
        "order_id": order_id, "signal_date": signal_date,
        "rationale": "Choose a balanced allocation before seeing the next close.",
    }]
    assert app.text_area(key="order_rationale").value == ""
    queued = _journal_table(app).set_index("order_id").loc[order_id]
    assert queued.rationale == app.session_state["survival_journal"][0]["rationale"]
    app.button(key="advance_day").click().run()
    _clean(app)
    assert (sim.ledger.order_id == order_id).all()
    assert (sim.ledger.signal_date == signal_date).all()
    assert (sim.ledger.execution_date > signal_date).all()
    executed = _journal_table(app).set_index("order_id").loc[order_id]
    assert executed.status == "executed"
    assert executed.signal_date == signal_date
    assert executed.rationale == queued.rationale
    # Notes are presentation metadata; merging them must not mutate engine rows.
    assert "rationale" not in sim.decisions
    app.radio(key="page").set_value("Final Performance Report").run()
    _clean(app)
    exported_view = _journal_table(app).set_index("order_id").loc[order_id]
    assert exported_view.rationale == queued.rationale
    assert exported_view.signal_date == signal_date


def test_empty_observation_shows_error_without_creating_note_or_replacing_target():
    app = _launch()
    sim = app.session_state["survival"]
    pending, clock = sim.pending, sim.current_date
    app.text_area(key="order_rationale").set_value("   ").run()
    _find(app.button, "Save observation without trading").click().run()
    _clean(app)
    assert app.session_state["survival_journal"] == []
    assert any("Write a short observation" in error.value for error in app.error)
    assert sim.pending == pending and sim.current_date == clock
    assert sim.ledger.empty
    assert len(sim.decisions) == 1


def test_invalid_allocation_keeps_unsaved_reason_and_does_not_create_journal_entry():
    app = _launch()
    sim = app.session_state["survival"]
    pending = sim.pending
    app.number_input(key="order_target_SPY").set_value(60.0).run()
    app.text_area(key="order_rationale").set_value("My draft still needs a complete allocation.").run()
    _find(app.button, "Queue target for next close").click().run()
    _clean(app)
    assert any("sum to 100%" in error.value for error in app.error)
    assert sim.pending == pending
    assert app.session_state["survival_journal"] == []
    assert app.text_area(key="order_rationale").value == "My draft still needs a complete allocation."


def test_reset_clears_saved_journal_and_unsaved_reason():
    app = _launch()
    app.text_area(key="order_rationale").set_value("Saved observation at this close.").run()
    _find(app.button, "Save observation without trading").click().run()
    app.text_area(key="order_rationale").set_value("Unsubmitted note for the next decision.").run()
    assert app.session_state["survival_journal"]
    _find(app.button, "Reset mission").click().run()
    _clean(app)
    assert "survival" not in app.session_state
    assert "survival_journal" not in app.session_state
    assert "order_rationale" not in app.session_state


@pytest.mark.parametrize("button_label,expected_spy,expected_cash", [
    ("Queue 100% cash", 0.0, 1.0),
    ("Queue single-asset target", 0.01, 0.99),
])
def test_cash_and_single_asset_tickets_record_reason_for_the_replacement_order(
    button_label, expected_spy, expected_cash
):
    app = _launch()
    sim = app.session_state["survival"]
    original_order_id = sim.pending.order_id
    clock = sim.current_date
    rationale = "Keep this decision tied to the information revealed at this close."
    app.text_area(key="order_rationale").set_value(rationale).run()
    _find(app.button, button_label).click().run()
    _clean(app)
    replacement = sim.pending
    assert replacement.order_id != original_order_id
    assert replacement.source == "manual"
    assert replacement.weights["SPY"] == pytest.approx(expected_spy)
    assert replacement.weights["CASH"] == pytest.approx(expected_cash)
    assert app.session_state["survival_journal"] == [{
        "order_id": replacement.order_id, "signal_date": clock, "rationale": rationale,
    }]
    assert app.text_area(key="order_rationale").value == ""
    recorded = _journal_table(app).set_index("order_id")
    assert recorded.loc[replacement.order_id, "rationale"] == rationale
    assert recorded.loc[original_order_id, "status"] == "replaced"
    assert recorded.loc[original_order_id, "rationale"] == ""
    app.run()
    assert len(app.session_state["survival_journal"]) == 1
    assert sim.pending == replacement and sim.current_date == clock
    assert sim.ledger.empty


def test_new_mission_discards_prior_journal_and_unsaved_reason_without_reset():
    app = _launch()
    old_sim = app.session_state["survival"]
    app.text_area(key="order_rationale").set_value("Recorded in the first mission only.").run()
    _find(app.button, "Save observation without trading").click().run()
    app.text_area(key="order_rationale").set_value("Unsubmitted first-mission draft.").run()
    assert app.session_state["survival_journal"]
    app.radio(key="page").set_value("Mission Control").run()
    app.radio(key="mission_source").set_value("Synthetic offline demonstration").run()
    app.selectbox(key="mission_scenario").set_value("covid").run()
    _find(app.button, "Initialize mission →").click().run(timeout=180)
    _find(app.button, "Launch validated mission →").click().run(timeout=180)
    _clean(app)
    new_sim = app.session_state["survival"]
    assert new_sim is not old_sim
    assert new_sim.current_date.year == 2019
    assert app.session_state["survival_journal"] == []
    assert app.text_area(key="order_rationale").value == ""
    assert new_sim.ledger.empty and len(new_sim.history) == 1
    assert "rationale" not in new_sim.decisions
    assert old_sim.ledger.empty and len(old_sim.history) == 1
