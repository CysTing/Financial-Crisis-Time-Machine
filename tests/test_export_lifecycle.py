"""Cross-scenario export lifecycle checks using public simulation snapshots."""

from copy import deepcopy
from functools import lru_cache
import hashlib
import io
import json

import pandas as pd
import pytest

from core.backtest_engine import Simulation
from core.data_loader import load_market_data
from core.integrity import build_run_passport, passport_json
from core.reporting import build_csv_report, build_html_report
from core.scenarios import SCENARIOS
from core.strategies import STRATEGIES, make_strategy


NOTE = '保留現金，先觀察 — "risk, costs" <script>alert("x")</script> & review\nsecond line'


@lru_cache(maxsize=3)
def _data(key):
    scenario = SCENARIOS[key]
    return load_market_data(scenario.start, scenario.end, source="synthetic")


def _simulation(key, strategy, **kwargs):
    scenario, data = SCENARIOS[key], _data(key)
    return Simulation(data.prices, scenario.start, scenario.end,
                      strategy=make_strategy(strategy, **kwargs),
                      data_fingerprint=data.fingerprint)


def _assert_exports(view, benchmark, *, confidence):
    before = deepcopy(view)
    cutoff = view["current_date"]
    metadata = {**view["config"], "data_source": "synthetic", "scenario": "Test <review>",
                "benchmark_name": "Selected & benchmark", "future_secret": "MUST_NOT_EXPORT"}
    decisions = view["decisions"].copy()
    decisions["rationale"] = NOTE
    html = build_html_report(view["history"], view["ledger"], metadata,
                             contributions=view["contributions"], benchmark=benchmark,
                             current_date=cutoff, completed=view["complete"],
                             decisions=decisions, confidence=confidence)
    assert "SYNTHETIC DEMONSTRATION" in html
    assert "MUST_NOT_EXPORT" not in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "保留現金" in html and "Selected &amp; benchmark" in html
    assert ("Educational survival score:" in html) == view["complete"]
    assert f"linear {confidence:.0%} quantile" in html
    assert f"As of {cutoff:%Y-%m-%d}" in html
    payloads = {}
    for kind in ("history", "trades", "decisions"):
        csv = build_csv_report(view["history"], view["ledger"], metadata,
                               current_date=cutoff, completed=view["complete"], kind=kind,
                               benchmark=benchmark, decisions=decisions)
        frame = pd.read_csv(io.StringIO(csv))
        payloads[kind] = frame
        assert not frame.empty  # Empty ledgers retain a labeled provenance row.
        assert frame["as_of"].eq(cutoff.strftime("%Y-%m-%d")).all()
        assert frame["data_source"].str.contains("SYNTHETIC").all()
        assert frame["report_status"].str.contains("Completed" if view["complete"] else "Partial").all()
        for column in ("date", "signal_date", "execution_date"):
            if column in frame:
                dates = pd.to_datetime(frame[column].dropna())
                assert (dates <= cutoff).all()
    assert payloads["history"].equity.iloc[-1] == pytest.approx(view["history"].equity.iloc[-1])
    assert payloads["history"].benchmark_equity.iloc[-1] == pytest.approx(benchmark.iloc[-1])
    assert payloads["decisions"].rationale.tolist() == [NOTE] * len(decisions)
    assert len(payloads["history"]) == len(view["history"])
    assert len(payloads["trades"]) == max(1, len(view["ledger"]))
    passport = build_run_passport({**view, "decisions": decisions}, {"source": "synthetic"})
    loaded = json.loads(passport_json(passport))
    digest = loaded.pop("payload_fingerprint")
    canonical = json.dumps(loaded, sort_keys=True, ensure_ascii=False,
                           allow_nan=False, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == digest
    assert loaded["integrity"]["status"] == "pass"
    assert loaded["datasets"]["history"]["rows"] == len(view["history"])
    assert loaded["effective_end"] == cutoff.isoformat()
    for name in ("prices", "history", "holdings", "ledger", "contributions", "decisions"):
        pd.testing.assert_frame_equal(view[name], before[name])


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_all_strategy_scenario_reports_at_baseline_first_fill_and_completion(scenario, strategy):
    sim = _simulation(scenario, strategy)
    benchmark = _simulation(scenario, "Traditional 60/40")
    for state, confidence in (("baseline", .90), ("first_fill", .95), ("complete", .99)):
        if state == "first_fill":
            sim.advance("day")
        elif state == "complete":
            sim.run()
        benchmark.advance_to(sim.current_date)
        _assert_exports(sim.snapshot(), benchmark.history.equity, confidence=confidence)


@pytest.mark.parametrize("confidence", [.90, .95, .99])
@pytest.mark.parametrize("benchmark_strategy", ["Buy and Hold", "Traditional 60/40"])
def test_completed_cash_only_reports_remain_downloadable_with_undefined_ratios(confidence, benchmark_strategy):
    sim = _simulation("covid", "User-Defined Allocation", weights={"CASH": 1.})
    sim.run()
    assert sim.ledger.empty
    assert sim.history.equity.eq(100000).all()
    benchmark = _simulation("covid", benchmark_strategy)
    benchmark.run()
    _assert_exports(sim.snapshot(), benchmark.history.equity, confidence=confidence)


@pytest.mark.parametrize("kind", ["history", "decisions"])
@pytest.mark.parametrize("note,expected", [
    ('=HYPERLINK("https://example.invalid","open")', '\'=HYPERLINK("https://example.invalid","open")'),
    ("+1+2", "'+1+2"),
    ("-1+2", "'-1+2"),
    ("@SUM(A1:A3)", "'@SUM(A1:A3)"),
    ("   =1+2", "'   =1+2"),
    ("\n  +1+2", "'\n  +1+2"),
    ("\t=1+2", "'\t=1+2"),
    ("\r-1+2", "'\r-1+2"),
    ("  \tordinary text", "'  \tordinary text"),
    ("\r普通文字", "'\r普通文字"),
    ("保留現金，等待下一日。", "保留現金，等待下一日。"),
    ("  Risk - costs, then review", "  Risk - costs, then review"),
    (NOTE, NOTE),
    ("'=1+2", "'=1+2"),
])
def test_csv_journal_reasoning_stays_literal_without_changing_financial_numbers(kind, note, expected):
    index = pd.DatetimeIndex(["2020-01-02", "2020-01-03"], name="date")
    history = pd.DataFrame({"equity": [100., 99.], "daily_return": [0., -.01],
                            "rationale": [note, note]}, index=index)
    decisions = pd.DataFrame({"signal_date": [index[0]], "execution_date": [index[1]],
                              "rationale": [note], "status": ["executed"]})
    trades = pd.DataFrame({"signal_date": [index[0]], "execution_date": [index[1]],
                           "asset": ["SPY"], "units": [-1.25], "signed_notional": [-25.5],
                           "gross_notional": [25.5], "costs": [.01275]})
    original_history, original_decisions = history.copy(), decisions.copy()
    csv = build_csv_report(history, trades, {"data_source": "synthetic"}, kind=kind,
                            current_date=index[-1], decisions=decisions)
    exported = pd.read_csv(io.StringIO(csv))
    assert exported.rationale.tolist() == [expected] * len(exported)
    if kind == "history":
        assert exported.daily_return.tolist() == [0., -.01]
        assert exported.equity.tolist() == [100., 99.]
    trade_csv = build_csv_report(history, trades, {"data_source": "synthetic"},
                                 kind="trades", current_date=index[-1])
    trade_exported = pd.read_csv(io.StringIO(trade_csv))
    assert trade_exported.units.tolist() == [-1.25]
    assert trade_exported.signed_notional.tolist() == [-25.5]
    pd.testing.assert_frame_equal(history, original_history)
    pd.testing.assert_frame_equal(decisions, original_decisions)


def test_formula_like_reasoning_remains_original_escaped_text_in_html():
    sim = _simulation("covid", "Buy and Hold")
    sim.advance("day")
    view = sim.snapshot()
    note = '=HYPERLINK("<script>alert(1)</script>","保留現金 & review")'
    decisions = view["decisions"].copy()
    decisions["rationale"] = note
    html = build_html_report(view["history"], view["ledger"], {"data_source": "synthetic"},
                             current_date=sim.current_date, decisions=decisions)
    # pandas escapes HTML table content without changing the journal's text.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "保留現金 &amp; review" in html
    assert "'=HYPERLINK" not in html
    assert "<script>" not in html
