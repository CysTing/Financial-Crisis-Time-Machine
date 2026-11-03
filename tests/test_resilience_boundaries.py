"""Final input and recovery boundaries that must not escape into UI errors."""

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.backtest_engine import Simulation
from core.data_loader import DataError, exchange_sessions, load_market_data
from core.integrity import build_integrity_snapshot
from core.metrics import performance_metrics
from core.reporting import build_csv_report, build_html_report
from core.strategies import make_strategy


@pytest.mark.parametrize("corrupt", [
    lambda metadata: None,
    lambda metadata: [],
    lambda metadata: 42,
    lambda metadata: "valid JSON, invalid cache metadata",
    lambda metadata: {key: value for key, value in metadata.items() if key != "retrieved_at_utc"},
    lambda metadata: {**metadata, "retrieved_at_utc": None},
    lambda metadata: {**metadata, "retrieved_at_utc": "not a timestamp"},
    lambda metadata: {**metadata, "retrieved_at_utc": "2026-11-03T12:00:00"},
])
def test_malformed_cache_metadata_blocks_safely_and_redownloads_on_recovery(tmp_path, monkeypatch, corrupt):
    sessions = exchange_sessions("2020-01-02", "2020-01-10")
    prices = pd.DataFrame({"SPY": np.arange(100., 100. + len(sessions))}, index=sessions)
    raw = pd.concat({"Adj Close": prices}, axis=1)
    calls = []

    def provider(*args, **kwargs):
        calls.append(kwargs)
        return raw

    fake = SimpleNamespace(download=provider, set_tz_cache_location=lambda path: None, __version__="fixture")
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    options = dict(start="2020-01-02", end="2020-01-10", cache_dir=tmp_path, tickers=["SPY"], warmup_sessions=0)
    original = load_market_data(**options)
    metadata_path = next(tmp_path.glob("*.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    corrupted = json.dumps(corrupt(metadata))
    metadata_path.write_text(corrupted, encoding="utf-8")

    def unavailable(*args, **kwargs):
        raise ConnectionError("offline fixture")

    fake.download = unavailable
    with pytest.raises(DataError, match="no validated genuine cache"):
        load_market_data(**options, force_refresh=True)
    assert metadata_path.read_text(encoding="utf-8") == corrupted
    fake.download = provider
    recovered = load_market_data(**options)
    assert len(calls) == 2  # Initial fill and explicit recovery, never false cache acceptance.
    assert not recovered.synthetic and not recovered.manifest["cache_used"]
    assert recovered.fingerprint == original.fingerprint
    assert recovered.manifest["retrieved_at_utc"]
    pd.testing.assert_frame_equal(recovered.prices, original.prices)


@pytest.fixture(scope="module")
def boundary_data():
    return load_market_data("2020-01-02", "2020-03-31", source="synthetic")


@pytest.mark.parametrize("name,options", [
    ("Buy and Hold", {"weights": {"CASH": 1.0}}),
    ("Traditional 60/40", {}),
    ("Moving Average Trend", {}),
    ("Momentum Rotation", {"lookback": 2}),
    ("Momentum Rotation", {"lookback": 252}),
    ("Inverse Volatility", {"lookback": 2, "exposure_cap": .1}),
    ("Inverse Volatility", {"lookback": 252, "exposure_cap": 1.}),
    ("User-Defined Allocation", {"weights": {"CASH": 1.0}, "frequency": "None"}),
    ("User-Defined Allocation", {"weights": {"SPY": 1.0}, "frequency": "Quarterly"}),
    ("User-Defined Allocation", {"weights": {"AGG": 1.0}, "frequency": "Annually"}),
])
def test_supported_extreme_inputs_reconcile_and_produce_both_reports(boundary_data, name, options):
    strategy = make_strategy(name, **options)
    simulation = Simulation(boundary_data.prices, "2020-01-02", "2020-03-31",
                            capital=1e9, cost_bps=100., strategy=strategy,
                            data_fingerprint=boundary_data.fingerprint)
    simulation.run()
    view = simulation.snapshot()
    assert build_integrity_snapshot(view)["status"] == "pass"
    assert np.isfinite(view["history"].to_numpy()).all()
    assert (view["history"].cash >= 0).all()
    metrics = performance_metrics(view["history"].equity, trades=view["ledger"])
    assert all(value is None or np.isfinite(value) for value in metrics.values())
    metadata = {**view["config"], "data_source": "synthetic", "scenario": "Boundary fixture"}
    before = deepcopy(view)
    csv = build_csv_report(view["history"], view["ledger"], metadata,
                           current_date=view["current_date"], completed=True, decisions=view["decisions"])
    html = build_html_report(view["history"], view["ledger"], metadata, metrics,
                             current_date=view["current_date"], completed=True,
                             contributions=view["contributions"], decisions=view["decisions"])
    assert "SYNTHETIC" in html and "SYNTHETIC" in csv.upper()
    assert "2020-03-31" in html and "2020-03-31" in csv
    for dataset in ("prices", "history", "holdings", "ledger", "contributions", "decisions"):
        pd.testing.assert_frame_equal(view[dataset], before[dataset])
