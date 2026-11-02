"""Deterministic provider/schema/calendar validation, with no network requests."""

from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest

from core.data_loader import (
    DataError, data_fingerprint, exchange_sessions, load_market_data,
    normalize_yfinance, validate_prices,
)


def fixture_prices(start="2020-01-02", end="2020-01-10"):
    sessions = exchange_sessions(start, end)
    return pd.DataFrame({"SPY": 100.0 + np.arange(len(sessions)), "AGG": 50.0 + np.arange(len(sessions)) / 10}, index=sessions)


def test_calendar_distinguishes_holiday_and_exceptional_closure():
    assert pd.Timestamp("2020-01-01") not in exchange_sessions("2019-12-30", "2020-01-03")
    # NYSE was closed for President Ford's funeral, beyond regular holidays.
    assert pd.Timestamp("2007-01-02") not in exchange_sessions("2007-01-01", "2007-01-05")
    assert pd.Timestamp("2007-01-03") in exchange_sessions("2007-01-01", "2007-01-05")


def test_missing_required_session_is_never_silently_dropped_or_filled():
    prices = fixture_prices().drop(pd.Timestamp("2020-01-07"))
    with pytest.raises(DataError, match="No eligible assets"):
        validate_prices(prices, "2020-01-02", "2020-01-10")


def test_invalid_asset_excluded_from_fixed_universe_before_start():
    prices = fixture_prices()
    prices.loc["2020-01-07", "AGG"] = np.nan
    clean, eligible, exclusions, _ = validate_prices(prices, "2020-01-02", "2020-01-10")
    assert eligible == ("SPY",)
    assert "2020-01-07" in exclusions["AGG"]
    assert list(clean.columns) == ["SPY"]


def test_asset_unavailable_before_inception():
    prices = fixture_prices("2000-01-03", "2000-01-10")
    _, eligible, exclusions, _ = validate_prices(prices, "2000-01-03", "2000-01-10")
    assert eligible == ("SPY",)
    assert "inception" in exclusions["AGG"]


def test_duplicate_dates_rejected():
    prices = fixture_prices()
    with pytest.raises(DataError, match="unique"):
        validate_prices(pd.concat([prices, prices.iloc[[0]]]), "2020-01-02", "2020-01-10")


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_nonfinite_or_nonpositive_prices_excluded(value):
    prices = fixture_prices()
    prices.iloc[2, prices.columns.get_loc("AGG")] = value
    _, eligible, exclusions, _ = validate_prices(prices, "2020-01-02", "2020-01-10")
    assert eligible == ("SPY",)
    assert "AGG" in exclusions


def test_suspicious_moves_retained_and_flagged():
    prices = fixture_prices()
    prices.loc["2020-01-07":, "SPY"] *= 2
    clean, eligible, _, warnings = validate_prices(prices, "2020-01-02", "2020-01-10")
    assert "SPY" in eligible
    assert clean.loc["2020-01-07", "SPY"] == prices.loc["2020-01-07", "SPY"]
    assert any("unusual" in warning and "SPY" in warning for warning in warnings)


def test_warmup_missingness_stays_missing_without_backfill():
    prices = fixture_prices()
    prices.loc["2020-01-03", "SPY"] = np.nan
    clean, _, _, warnings = validate_prices(prices, "2020-01-06", "2020-01-10")
    assert pd.isna(clean.loc["2020-01-03", "SPY"])
    assert any("warm-up" in warning for warning in warnings)


def test_normalization_uses_adjusted_field_and_explicit_tickers():
    prices = fixture_prices()
    raw = pd.concat({"Close": prices * 2, "Adj Close": prices}, axis=1)
    result = normalize_yfinance(raw, ["SPY", "AGG"])
    pd.testing.assert_frame_equal(result, prices.rename_axis("date"))
    # Also support ticker-first provider MultiIndex ordering.
    pd.testing.assert_frame_equal(normalize_yfinance(raw.swaplevel(axis=1), ["SPY", "AGG"]), result)


def test_price_only_response_is_not_substituted():
    with pytest.raises(DataError, match="Adj Close|adjusted closes"):
        normalize_yfinance(pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex(["2020-01-02"])), ["SPY"])


def test_synthetic_demonstration_is_deterministic_explicit_and_prefix_stable():
    first = load_market_data("2007-01-01", "2007-02-28", source="synthetic", warmup_sessions=10)
    same = load_market_data("2007-01-01", "2007-02-28", source="synthetic", warmup_sessions=10)
    longer = load_market_data("2007-01-01", "2007-03-30", source="synthetic", warmup_sessions=10)
    assert first.synthetic and "synthetic" in first.manifest["provider"].lower()
    assert first.fingerprint == same.fingerprint == data_fingerprint(first.prices)
    pd.testing.assert_frame_equal(first.prices, longer.prices.loc[first.prices.index])
    assert len(first.eligible) == 7
    assert first.prices.index[0] < pd.Timestamp("2007-01-03")


def test_restricted_provenance_has_no_future_observed_date():
    data = load_market_data("2020-01-01", "2020-03-31", source="synthetic", warmup_sessions=0)
    restricted = data.restricted_manifest("2020-01-10")
    assert restricted["observed_end"] == "2020-01-10"
    assert restricted["visible_through"] == "2020-01-10"
    assert "requested_end" not in restricted
    assert "request_key" not in restricted
    assert "2020-03-31" not in str(restricted)
    # Retrieval time is deliberately retained as present-day data provenance.
    assert restricted["retrieved_at_utc"] == data.manifest["retrieved_at_utc"]


def test_restricted_manifest_strips_future_diagnostics_and_request_key():
    from dataclasses import replace

    data = load_market_data("2020-01-01", "2020-03-31", source="synthetic", warmup_sessions=0)
    data = replace(data, manifest={**data.manifest, "request_key": '{"end":"2020-03-31"}', "raw_fingerprint": "internal-full-dataset", "validation_warnings": ["Unusual move on 2020-03-20"]}, exclusions={"FAKE": "Missing price on 2020-03-25"})
    restricted = data.restricted_manifest("2020-01-10")
    assert "request_key" not in restricted and "raw_fingerprint" not in restricted
    assert "2020-03-20" not in str(restricted)
    assert "2020-03-25" not in str(restricted)
    assert "2020-03-31" not in str(restricted)


def _fake_yfinance(monkeypatch, download):
    fake = SimpleNamespace(download=download, set_tz_cache_location=lambda path: None, __version__="test-provider")
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_historical_cache_fallback_is_genuine_and_preserves_retrieval_date(tmp_path, monkeypatch):
    prices = fixture_prices()
    raw = pd.concat({"Adj Close": prices}, axis=1)
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        return raw

    _fake_yfinance(monkeypatch, download)
    first = load_market_data("2020-01-02", "2020-01-10", cache_dir=tmp_path, tickers=["SPY", "AGG"], warmup_sessions=0)
    assert not first.synthetic
    assert calls[0]["auto_adjust"] is False and calls[0]["actions"] is False
    assert calls[0]["repair"] is False and calls[0]["keepna"] is True
    assert calls[0]["threads"] == 4 and calls[0]["timeout"] == 10

    def unavailable(*args, **kwargs):
        raise ConnectionError("offline test")

    _fake_yfinance(monkeypatch, unavailable)
    cached = load_market_data("2020-01-02", "2020-01-10", cache_dir=tmp_path, tickers=["SPY", "AGG"], warmup_sessions=0, force_refresh=True)
    assert cached.manifest["cache_used"] is True
    assert cached.manifest["retrieved_at_utc"] == first.manifest["retrieved_at_utc"]
    assert first.fingerprint == cached.fingerprint
    assert any("using validated genuine cache" in warning for warning in cached.manifest["validation_warnings"])
    pd.testing.assert_frame_equal(first.prices, cached.prices)


def test_download_failure_never_silently_substitutes_synthetic(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("offline test")

    _fake_yfinance(monkeypatch, unavailable)
    with pytest.raises(DataError, match="no validated genuine cache"):
        load_market_data("2020-01-02", "2020-01-10", cache_dir=tmp_path, tickers=["SPY"], warmup_sessions=0)


def test_corrupt_cache_is_rejected(tmp_path, monkeypatch):
    raw = pd.concat({"Adj Close": fixture_prices()}, axis=1)
    _fake_yfinance(monkeypatch, lambda *args, **kwargs: raw)
    load_market_data("2020-01-02", "2020-01-10", cache_dir=tmp_path, tickers=["SPY", "AGG"], warmup_sessions=0)
    csv = next(tmp_path.glob("*.csv"))
    csv.write_text(csv.read_text().replace("100", "999", 1))

    def unavailable(*args, **kwargs):
        raise ConnectionError("offline test")

    _fake_yfinance(monkeypatch, unavailable)
    with pytest.raises(DataError, match="no validated genuine cache"):
        load_market_data("2020-01-02", "2020-01-10", cache_dir=tmp_path, tickers=["SPY", "AGG"], warmup_sessions=0)
