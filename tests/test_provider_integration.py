"""Opt-in network integration: FTM_INTEGRATION=1 python -m pytest -q -m integration."""

import os

import pytest

from core.data_loader import exchange_sessions, load_market_data


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FTM_INTEGRATION") != "1", reason="Live provider access is opt-in; offline tests never require the internet.")
@pytest.mark.parametrize("start,end", [("2007-01-01", "2010-12-31"), ("2019-01-01", "2021-12-31"), ("2021-01-01", "2023-12-31")])
def test_live_adjusted_close_provider(start, end, tmp_path):
    dataset = load_market_data(start, end, source="historical", cache_dir=tmp_path, force_refresh=True)
    sessions = exchange_sessions(start, end)
    assert not dataset.synthetic
    assert dataset.manifest["provider"] == "Yahoo Finance via yfinance"
    assert {"SPY", "AGG"}.issubset(dataset.eligible)
    assert dataset.prices.loc[sessions].notna().all().all()
    assert dataset.prices.loc[sessions].gt(0).all().all()
