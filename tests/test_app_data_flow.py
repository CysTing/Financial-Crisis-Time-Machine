"""Offline UI data-flow regressions, including independent browser-session state.

Provider failures and excluded assets are explicit test fixtures. Successful
flows use only the labeled synthetic generator and do not contact Yahoo Finance.
"""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import core.data_loader as data_loader


APP = Path(__file__).resolve().parents[1] / "app.py"
SYNTHETIC = "Synthetic offline demonstration"
HISTORICAL = "Historical adjusted prices"


@pytest.fixture(autouse=True)
def isolated_streamlit_cache():
    """The app's cross-session immutable data cache must not mask mocked failures."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def button(app, label):
    return next(element for element in app.button if element.label == label)


def clean(app):
    assert not app.exception, "\n".join(str(element.value) for element in app.exception)
    return app


def new_app():
    return clean(AppTest.from_file(str(APP), default_timeout=180).run())


def synthetic_mission():
    app = new_app()
    app.radio(key="mission_source").set_value(SYNTHETIC).run()
    button(app, "Initialize mission →").click().run()
    clean(app)
    assert "survival_ready" in app.session_state
    button(app, "Launch validated mission →").click().run()
    clean(app)
    return app


def test_first_render_and_control_reruns_never_download_without_initialize(monkeypatch):
    calls = []

    def forbidden_provider(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Provider must not be called before explicit initialization")

    monkeypatch.setattr(data_loader, "load_market_data", forbidden_provider)
    app = new_app()
    assert calls == []
    app.radio(key="mission_source").set_value(SYNTHETIC).run()
    clean(app)
    app.selectbox(key="mission_scenario").set_value("covid").run()
    clean(app)
    app.run()
    clean(app)
    assert calls == []
    assert "survival" not in app.session_state


def test_provider_failure_requires_explicit_synthetic_choice(monkeypatch):
    original_loader = data_loader.load_market_data
    calls = []

    def offline_backend(*args, **kwargs):
        calls.append(kwargs["source"])
        if kwargs["source"] == "historical":
            raise data_loader.DataError("OFFLINE_TEST_PROVIDER_FAILURE: no genuine cache")
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(data_loader, "load_market_data", offline_backend)
    app = new_app()
    button(app, "Initialize mission →").click().run()
    clean(app)
    assert calls == ["historical"]
    assert any("OFFLINE_TEST_PROVIDER_FAILURE" in element.value for element in app.error)
    assert app.radio(key="mission_source").value == HISTORICAL
    assert "survival_ready" not in app.session_state
    assert "survival" not in app.session_state
    button(app, "Retry provider / refresh validated cache").click().run()
    clean(app)
    assert calls == ["historical"]  # Retry schedules a refresh, not a hidden request.
    app.radio(key="mission_source").set_value(SYNTHETIC).run()
    clean(app)
    assert calls == ["historical"]
    button(app, "Initialize mission →").click().run()
    clean(app)
    assert calls == ["historical", "synthetic"]
    button(app, "Launch validated mission →").click().run()
    clean(app)
    assert app.session_state["survival_meta"]["synthetic"] is True
    assert any("SYNTHETIC DEMONSTRATION" in element.value for element in app.warning)


def test_browser_sessions_isolate_clock_holdings_pending_orders_and_reset():
    first, second = synthetic_mission(), synthetic_mission()
    left, right = first.session_state["survival"], second.session_state["survival"]
    assert left is not right
    assert first.session_state["benchmarks"] is not second.session_state["benchmarks"]
    start, right_history = right.current_date, right.history.copy()
    initial_pending = right.pending
    button(first, "Queue 100% cash").click().run()
    first.button(key="advance_week").click().run()
    clean(first)
    clean(second.run())
    assert left.current_date > start
    assert right.current_date == start
    assert right.pending == initial_pending
    assert right.ledger.empty
    pd.testing.assert_frame_equal(right.history, right_history)
    button(first, "Reset mission").click().run()
    clean(first)
    clean(second.run())
    assert "survival" not in first.session_state
    assert second.session_state["survival"] is right
    pd.testing.assert_frame_equal(right.history, right_history)


def test_custom_zero_weight_for_excluded_asset_remains_launchable(monkeypatch):
    original = data_loader.load_market_data("2007-01-01", "2010-12-31", source="synthetic")
    prices = original.prices.drop(columns="AGG")
    eligible = tuple(ticker for ticker in original.eligible if ticker != "AGG")
    exclusions = {"AGG": "OFFLINE TEST FIXTURE: required scenario observation unavailable"}
    fingerprint = data_loader.data_fingerprint(prices)
    limited = replace(original, prices=prices, eligible=eligible, exclusions=exclusions,
        fingerprint=fingerprint, manifest={**original.manifest, "eligible": list(eligible),
            "exclusions": exclusions, "data_fingerprint": fingerprint})
    monkeypatch.setattr(data_loader, "load_market_data", lambda *args, **kwargs: limited)
    app = new_app()
    app.radio(key="mission_source").set_value(SYNTHETIC).run()
    app.selectbox(key="mission_preset").set_value("Custom").run()
    # Default custom weights are 100% SPY; AGG must not block a zero exposure.
    button(app, "Initialize mission →").click().run()
    clean(app)
    assert not app.error
    assert "survival_ready" in app.session_state
    assert any("AGG" in element.value and "Excluded" in element.value for element in app.warning)
    button(app, "Launch validated mission →").click().run()
    clean(app)
    sim = app.session_state["survival"]
    assert "AGG" not in sim.assets
    assert sim.pending.weights["SPY"] == 1.0
    assert "AGG" not in sim.pending.weights
