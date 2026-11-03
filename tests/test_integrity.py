"""Independent hand-built evidence and deliberate corruption cases."""

from copy import deepcopy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from core.integrity import build_integrity_snapshot, build_run_passport, passport_json, run_integrity_challenge


def hand_view():
    dates = pd.DatetimeIndex(["2020-01-02", "2020-01-03", "2020-01-06"], name="date")
    # Starting cash 100; signal Jan 2, buy five units at Jan 3 close 20;
    # old holdings then earn 5*(22-20)=10 into Jan 6. No costs here.
    return dict(
        current_date=dates[-1], complete=True,
        prices=pd.DataFrame({"A": [10., 20., 22.]}, index=dates),
        history=pd.DataFrame(dict(equity=[100., 100., 110.], cash=[100., 0., 0.], costs=[0., 0., 0.],
                                  value_A=[0., 100., 110.], units_A=[0., 5., 5.]), index=dates),
        holdings=pd.DataFrame([dict(asset="A", units=5., price=22., value=110., weight=1.),
                               dict(asset="CASH", units=0., price=1., value=0., weight=0.)]),
        ledger=pd.DataFrame([dict(order_id=1, asset="A", signal_date=dates[0], execution_date=dates[1],
                                 units=5., price=20., signed_notional=100., gross_notional=100., costs=0., cash_after=0.)]),
        contributions=pd.DataFrame({"A": [0., 0., 10.], "CASH": [0., 0., 0.], "COSTS": [0., 0., 0.]}, index=dates),
        decisions=pd.DataFrame([dict(order_id=1, signal_date=dates[0], execution_date=dates[1],
                                    status="executed", weights={"A": 1., "CASH": 0.}, rationale="Hand example")]),
        pending=None, config=dict(capital=100., cost_bps=0., data_fingerprint="known-input", strategy="Hand example"),
    )


def by_id(report, identifier):
    return next(item for item in report["checks"] if item["id"] == identifier)


def set_column(view, dataset, column, values):
    frame = view[dataset]
    frame[column] = values


def test_hand_calculated_snapshot_proves_last_close_and_does_not_mutate():
    view = hand_view()
    before = deepcopy(view)
    report = build_integrity_snapshot(view)
    assert report["status"] == "pass"
    assert report["passed"] == report["total"] == 8
    assert report["proof"]["previous_equity"] == 100
    assert report["proof"]["asset_pnl"] == 10
    assert report["proof"]["equity_change"] == report["proof"]["expected_change"] == 10
    assert report["proof"]["residual"] == 0
    assert report["proof"]["positions_value"] == 110
    for name in ("prices", "history", "holdings", "ledger", "contributions", "decisions"):
        pd.testing.assert_frame_equal(view[name], before[name])
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("identifier,mutate", [
    ("balance", lambda v: v["history"].__setitem__("cash", [100., 0., 100.])),
    ("valuation", lambda v: v["holdings"].loc.__setitem__((0, "price"), 21.)),
    ("valuation", lambda v: v["holdings"].loc.__setitem__((0, "units"), np.inf)),
    ("valuation", lambda v: v["holdings"].loc.__setitem__((0, "weight"), .5)),
    ("valuation", lambda v: v["history"].loc.__setitem__((v["current_date"], "value_A"), np.nan)),
    ("pnl", lambda v: v["contributions"].loc.__setitem__((v["current_date"], "A"), 11.)),
    ("pnl", lambda v: v["contributions"].loc.__setitem__((v["history"].index[1], "A"), 1.)),
    ("pnl", lambda v: v["contributions"].loc.__setitem__((v["current_date"], "COSTS"), -1.)),
    ("liquidity", lambda v: v["ledger"].__setitem__("cash_after", [-1.])),
    ("liquidity", lambda v: v["history"].__setitem__("cash", [100., -1., 0.])),
    ("causality", lambda v: set_column(v, "ledger", "signal_date", [v["ledger"].execution_date.iloc[0]])),
    ("causality", lambda v: set_column(v, "ledger", "execution_date", [v["current_date"]])),
    ("uniqueness", lambda v: v.__setitem__("ledger", pd.concat([v["ledger"], v["ledger"]], ignore_index=True))),
    ("boundary", lambda v: v["decisions"].__setitem__("signal_date", [v["current_date"] + pd.Timedelta(days=1)])),
    ("boundary", lambda v: v["decisions"].__setitem__("execution_date", [v["current_date"] + pd.Timedelta(days=1)])),
    ("coverage", lambda v: v.__setitem__("history", v["history"].iloc[[0, 2]])),
    ("coverage", lambda v: v.__setitem__("prices", v["prices"].iloc[:-1])),
    ("coverage", lambda v: v.__setitem__("contributions", v["contributions"].iloc[[0, 2]])),
])
def test_corruption_cannot_appear_green(identifier, mutate):
    view = hand_view()
    mutate(view)
    report = build_integrity_snapshot(view)
    assert report["status"] == "fail"
    assert by_id(report, identifier)["status"] == "fail"


def test_future_prices_fail_both_boundary_and_coverage_without_hidden_data_access():
    view = hand_view()
    view["prices"].loc[pd.Timestamp("2020-01-07")] = 999999.
    report = build_integrity_snapshot(view)
    assert by_id(report, "boundary")["status"] == "fail"
    assert by_id(report, "coverage")["status"] == "fail"


def test_missing_or_nonfinite_account_evidence_is_unavailable_not_passed():
    for view in ({}, {**hand_view(), "history": pd.DataFrame()}, hand_view()):
        if "history" in view and not view["history"].empty:
            view["history"].iloc[-1, 0] = np.nan
        report = build_integrity_snapshot(view)
        assert report["status"] == "unavailable"
        assert report["passed"] == 0


def test_missing_holdings_schema_fails_instead_of_crashing_or_passing():
    view = hand_view()
    view["holdings"] = pd.DataFrame({"wrong": [1.]})
    report = build_integrity_snapshot(view)
    assert report["status"] == "fail"
    assert by_id(report, "balance")["status"] == "fail"
    assert by_id(report, "valuation")["status"] == "fail"


def test_initial_cash_baseline_has_seven_applicable_checks_and_no_pnl_claim():
    from core.backtest_engine import Simulation
    prices = hand_view()["prices"]
    sim = Simulation(prices, prices.index[0], prices.index[-1], sessions=prices.index, capital=100, cost_bps=0, weights={"A": 1.})
    view = sim.snapshot()
    assert view["pending"]["execution_date"] > view["current_date"]
    report = build_integrity_snapshot(view)
    assert report["status"] == "pass"
    assert report["passed"] == report["total"] == 7
    assert by_id(report, "pnl")["status"] == "not_applicable"
    assert "equity_change" not in report["proof"]


def test_warmup_observations_and_nonexecuted_future_destinations_are_allowed():
    view = hand_view()
    warmup = pd.DataFrame({"A": [9.]}, index=pd.DatetimeIndex(["2019-12-31"], name="date"))
    view["prices"] = pd.concat([warmup, view["prices"]])
    view["decisions"] = pd.concat([view["decisions"], pd.DataFrame([dict(order_id=2, signal_date=view["current_date"],
        execution_date=view["current_date"] + pd.Timedelta(days=1), status="pending", weights={"CASH": 1.})])], ignore_index=True)
    assert build_integrity_snapshot(view)["status"] == "pass"


def test_cost_example_uses_negative_cost_contribution_and_positive_cost_ratio():
    from core.backtest_engine import Simulation
    prices = hand_view()["prices"]
    sim = Simulation(prices, prices.index[0], prices.index[-1], sessions=prices.index, capital=100, cost_bps=100, weights={"A": 1.})
    sim.advance()
    proof = build_integrity_snapshot(sim.snapshot())["proof"]
    assert proof["asset_pnl"] == 0
    assert proof["costs"] == pytest.approx(100 / 101)
    assert proof["expected_change"] == pytest.approx(-100 / 101)
    assert abs(proof["residual"]) < proof["tolerance"]


def test_passport_is_stable_json_fingerprint_with_no_observation_arrays():
    view = hand_view()
    passport = build_run_passport(view, {"source": "Synthetic demonstration", "benchmark": "SPY"})
    assert passport == build_run_passport(deepcopy(view), {"benchmark": "SPY", "source": "Synthetic demonstration"})
    content = json.loads(passport_json(passport))
    assert content == passport
    fingerprint = content.pop("payload_fingerprint")
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == fingerprint
    assert len(fingerprint) == 64
    assert passport["datasets"]["prices"]["rows"] == 3
    assert passport["datasets"]["prices"]["last_observation"] == view["current_date"].isoformat()
    assert set(passport["datasets"]["prices"]) == {"rows", "sha256", "first_observation", "last_observation"}
    assert "not a digital signature" in passport["fingerprint_notice"]


@pytest.mark.parametrize("mutate", [
    lambda v: v["prices"].iloc.__setitem__((0, 0), 10.01),
    lambda v: set_column(v, "ledger", "source", ["manual"]),
    lambda v: set_column(v, "decisions", "rationale", ["Updated rationale"]),
    lambda v: v["config"].__setitem__("cost_bps", 5.),
])
def test_any_recorded_data_or_configuration_change_changes_passport(mutate):
    view = hand_view()
    before = build_run_passport(view)["payload_fingerprint"]
    mutate(view)
    assert build_run_passport(view)["payload_fingerprint"] != before


def test_engine_hidden_future_mutation_cannot_change_current_evidence_or_passport():
    from core.backtest_engine import Simulation
    original = hand_view()["prices"]
    changed = original.copy()
    changed.iloc[-1] = 999999.
    snapshots = []
    for prices in (original, changed):
        sim = Simulation(prices, prices.index[0], prices.index[-1], sessions=prices.index,
                         capital=100, cost_bps=0, weights={"A": 1.}, data_fingerprint="fixed-source-id")
        sim.advance()
        snapshots.append(sim.snapshot())
    # Full-source identifier is intentionally fixed: only the provided revealed
    # arrays are hashed by this evidence layer, not the engine's private future.
    assert build_run_passport(snapshots[0]) == build_run_passport(snapshots[1])


def test_diagnostic_challenge_detects_three_faults_and_does_not_mutate_original():
    view = hand_view()
    before = build_run_passport(view)
    challenge = run_integrity_challenge(view)
    assert challenge["passed"] and challenge["original_unchanged"]
    assert len(challenge["cases"]) == 3
    assert all(item["detected"] is True for item in challenge["cases"])
    assert build_run_passport(view) == before


def test_baseline_challenge_skips_close_to_close_pnl():
    from core.backtest_engine import Simulation
    prices = hand_view()["prices"]
    sim = Simulation(prices, prices.index[0], prices.index[-1], sessions=prices.index, weights={"A": 1.})
    result = run_integrity_challenge(sim.snapshot())
    assert result["passed"] and result["original_unchanged"]
    assert result["cases"][-1]["detected"] is None
