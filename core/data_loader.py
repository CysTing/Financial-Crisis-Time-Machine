"""Validated adjusted-close data with an explicit exchange calendar and honest fallbacks.

No genuine observations ship with this project. Yahoo data downloaded by a user
are cached locally for that user's research; the software license does not grant
redistribution rights to those observations. Synthetic data require an explicit
request and are never substituted into a genuine-data run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Iterable
import uuid

import exchange_calendars as xcals
import numpy as np
import pandas as pd


ASSETS: dict[str, dict[str, str]] = {
    "SPY": {"name": "S&P 500", "inception": "1993-01-22", "issuer": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy"},
    "QQQ": {"name": "Nasdaq-100", "inception": "1999-03-10", "issuer": "https://www.invesco.com/qqq-etf/en/about.html"},
    "AGG": {"name": "US aggregate bonds", "inception": "2003-09-22", "issuer": "https://www.ishares.com/us/products/239458/ishares-core-total-us-bond-market-etf"},
    "IEF": {"name": "7–10 year Treasuries", "inception": "2002-07-22", "issuer": "https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf"},
    "TLT": {"name": "20+ year Treasuries", "inception": "2002-07-22", "issuer": "https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf"},
    "GLD": {"name": "Gold", "inception": "2004-11-18", "issuer": "https://www.ssga.com/us/en/individual/etfs/spdr-gold-shares-gld"},
    "SHY": {"name": "1–3 year Treasuries", "inception": "2002-07-22", "issuer": "https://www.ishares.com/us/products/239452/ishares-13-year-treasury-bond-etf"},
}

ADJUSTMENT_SETTINGS = {
    "auto_adjust": False,
    "field": "Adj Close",
    "actions": False,
    "repair": False,
    "keepna": True,
    "interval": "1d",
    "calendar": "XNYS",
    "dividends": "Already incorporated in adjusted units; never credited separately",
}


class DataError(ValueError):
    """A provider response or requested evaluation window cannot be used safely."""


@dataclass(frozen=True)
class MarketData:
    """Internal dataset; presentation code must restrict it before rendering."""

    prices: pd.DataFrame
    manifest: dict
    eligible: tuple[str, ...]
    exclusions: dict[str, str]
    fingerprint: str
    synthetic: bool

    def restricted_manifest(self, as_of: str | pd.Timestamp) -> dict:
        """Return provenance without future observations or dated future warnings."""
        cutoff = _date(as_of)
        result = dict(self.manifest)
        # Internal request keys encode the full download window. The public
        # scenario end may be known, but provenance exports need only the
        # revealed range and must not expose internal full-dataset metadata.
        for key in ("requested_end", "request_key", "raw_fingerprint"):
            result.pop(key, None)
        result["observed_end"] = min(cutoff, _date(result["observed_end"])).date().isoformat()
        result["visible_through"] = cutoff.date().isoformat()
        result["exclusions"] = {
            ticker: "Excluded before the run during fixed-window eligibility validation."
            for ticker in self.exclusions
        }
        # The actual retrieval timestamp is retained as present-day provenance,
        # not presented as information available at the simulated historical date.
        # Detailed data-quality messages can mention future dates. Their existence
        # is disclosed before starting, while dated details stay internal.
        result["validation_warnings"] = [
            "Fixed eligible universe validated before the run; no price filling is used.",
            "Detailed validation diagnostics are available after scenario completion.",
        ]
        return result


def _date(value: str | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("America/New_York").tz_localize(None)
    return stamp.normalize()


@lru_cache(maxsize=16)
def _calendar(start: str, end: str):
    return xcals.get_calendar("XNYS", start=start, end=end)


def exchange_sessions(start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DatetimeIndex:
    """Actual XNYS sessions, including exceptional closures; never business-day guesses."""
    first, last = _date(start), _date(end)
    if last < first:
        raise DataError("The end date must be on or after the start date.")
    calendar = _calendar((first - pd.Timedelta(days=8)).date().isoformat(), (last + pd.Timedelta(days=8)).date().isoformat())
    sessions = calendar.sessions_in_range(first, last)
    return pd.DatetimeIndex(sessions).tz_localize(None).normalize()


def data_fingerprint(prices: pd.DataFrame) -> str:
    """Stable content digest, including the date index, ticker order, and missingness."""
    digest = hashlib.sha256("|".join(map(str, prices.columns)).encode())
    digest.update(pd.util.hash_pandas_object(prices, index=True).values.tobytes())
    return digest.hexdigest()


def normalize_yfinance(raw: pd.DataFrame, tickers: Iterable[str]) -> pd.DataFrame:
    """Extract only explicitly requested adjusted closes from supported yf schemas."""
    symbols = tuple(tickers)
    if raw is None or raw.empty:
        raise DataError("Yahoo Finance returned no observations. Retry or select the explicit synthetic demonstration.")
    if isinstance(raw.columns, pd.MultiIndex):
        field_level = next((level for level in range(raw.columns.nlevels) if "Adj Close" in raw.columns.get_level_values(level)), None)
        if field_level is None:
            raise DataError("Provider response has no Adj Close field; price-only Close is not substituted.")
        prices = raw.xs("Adj Close", axis=1, level=field_level).copy()
    elif "Adj Close" in raw.columns and len(symbols) == 1:
        prices = raw[["Adj Close"]].rename(columns={"Adj Close": symbols[0]}).copy()
    else:
        raise DataError("Unexpected provider schema: explicit adjusted closes are required.")
    prices.columns = prices.columns.astype(str)
    index = pd.DatetimeIndex(pd.to_datetime(prices.index))
    if index.tz is not None:
        index = index.tz_convert("America/New_York").tz_localize(None)
    prices.index = index.normalize()
    prices.index.name = "date"
    if prices.index.has_duplicates:
        raise DataError("Provider response contains duplicate trading dates.")
    return prices.reindex(columns=symbols).sort_index().apply(pd.to_numeric, errors="coerce")


def validate_prices(
    prices: pd.DataFrame,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    tickers: Iterable[str] | None = None,
    *,
    expected_sessions: pd.DatetimeIndex | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...], dict[str, str], list[str]]:
    """Fix the run universe, excluding invalid assets before any holdings exist.

    Every scenario session is required. Missing warm-up observations remain NaN;
    strategies must wait for sufficient contiguous valid trailing observations.
    Callers must disclose exclusions and never change this universe mid-run.
    """
    if not isinstance(prices.index, pd.DatetimeIndex) or prices.index.has_duplicates:
        raise DataError("Prices require a unique DatetimeIndex.")
    if prices.index.tz is not None or not prices.index.equals(prices.index.normalize()):
        raise DataError("Price dates must be naive, normalized exchange-session labels.")
    if not prices.index.is_monotonic_increasing or prices.columns.has_duplicates:
        raise DataError("Price dates must be increasing and ticker names must be unique.")
    first, last = _date(start), _date(end)
    sessions = exchange_sessions(first, last) if expected_sessions is None else expected_sessions
    if len(sessions) < 2:
        raise DataError("A run needs at least two exchange sessions for delayed execution.")
    symbols = tuple(tickers if tickers is not None else prices.columns)
    unknown = set(symbols) - set(ASSETS)
    if unknown:
        raise DataError(f"Unsupported asset(s): {', '.join(sorted(unknown))}.")
    all_sessions = exchange_sessions(min(first, prices.index.min()) if len(prices) else first, last)
    extraneous = prices.index.difference(all_sessions)
    if len(extraneous):
        raise DataError(f"Unexpected non-XNYS price dates, beginning {extraneous[0].date()}; no rows silently discarded.")
    aligned = prices.reindex(index=all_sessions, columns=symbols)
    exclusions: dict[str, str] = {}
    warnings: list[str] = []
    eligible: list[str] = []
    for ticker in symbols:
        if pd.Timestamp(ASSETS[ticker]["inception"]) > sessions[0]:
            exclusions[ticker] = f"Fund inception {ASSETS[ticker]['inception']} is after the first scenario session."
            continue
        series = aligned.loc[sessions, ticker]
        invalid = ~np.isfinite(series.to_numpy(dtype=float)) | (series.to_numpy(dtype=float) <= 0)
        if invalid.any():
            dates = series.index[invalid]
            exclusions[ticker] = f"Missing or nonpositive adjusted close on {len(dates)} required XNYS session(s), beginning {dates[0].date()}. Retry the provider or explicitly choose another valid window; no filling is used."
            continue
        eligible.append(ticker)
        changes = aligned[ticker].pct_change(fill_method=None)
        unusual = changes[changes.abs() > 0.20]
        for date, change in unusual.items():
            warnings.append(f"{ticker}: unusual {change:.1%} adjusted-unit move on {date.date()}; retained for review.")
        warmup = aligned.loc[aligned.index < sessions[0], ticker]
        missing_warmup = int(warmup.isna().sum())
        if missing_warmup:
            warnings.append(f"{ticker}: {missing_warmup} missing warm-up observations; trailing strategies wait until sufficient valid history.")
        pre_inception = aligned.index < pd.Timestamp(ASSETS[ticker]["inception"])
        if aligned.loc[pre_inception, ticker].notna().any():
            raise DataError(f"{ticker} has observations before its documented inception; reject the dataset.")
    if not eligible:
        details = "; ".join(f"{ticker}: {reason}" for ticker, reason in exclusions.items())
        raise DataError(f"No eligible assets for this fixed window. {details}")
    return aligned.loc[:, eligible], tuple(eligible), exclusions, warnings


def _synthetic_prices(end: pd.Timestamp) -> pd.DataFrame:
    """Fictional correlated paths; seed and prefix are stable across requested windows."""
    sessions = exchange_sessions("1990-01-01", end)
    rng = np.random.default_rng(200820202022)
    shocks = rng.standard_normal((len(sessions), 8))
    market = shocks[:, 0]
    prices: dict[str, np.ndarray] = {}
    # These hand-chosen fictional regimes demonstrate allocation mechanics. They
    # do not calibrate, reproduce, or claim to describe actual ETF performance.
    stress08 = (sessions >= "2008-09-01") & (sessions <= "2009-03-09")
    stress20 = (sessions >= "2020-02-20") & (sessions <= "2020-03-23")
    stress22 = (sessions >= "2022-01-01") & (sessions <= "2022-10-31")
    recovery = ((sessions >= "2009-03-10") & (sessions <= "2010-12-31")) | ((sessions >= "2020-03-24") & (sessions <= "2021-12-31"))
    for j, ticker in enumerate(ASSETS):
        equity = ticker in {"SPY", "QQQ"}
        vol = {"SPY": .009, "QQQ": .012, "AGG": .002, "IEF": .0035, "TLT": .006, "GLD": .008, "SHY": .0007}[ticker]
        beta = {"SPY": .90, "QQQ": .85, "AGG": -.10, "IEF": -.25, "TLT": -.30, "GLD": .12, "SHY": -.05}[ticker]
        innovations = vol * (beta * market + np.sqrt(1 - beta**2) * shocks[:, j + 1])
        drift = np.full(len(sessions), .00020 if equity else .00008)
        if equity:
            drift[stress08] = -.0035
            drift[stress20] = -.011
            drift[stress22] = -.0012
            drift[recovery] = .0011
            innovations[stress08 | stress20] *= 2.3
        elif ticker in {"AGG", "IEF", "TLT"}:
            drift[stress08 | stress20] = .0008
            drift[stress22] = -.0008 if ticker != "TLT" else -.0014
        valid = sessions >= pd.Timestamp(ASSETS[ticker]["inception"])
        series = np.full(len(sessions), np.nan)
        series[valid] = 100 * np.exp(np.cumsum((drift + innovations)[valid]))
        prices[ticker] = series
    return pd.DataFrame(prices, index=sessions).rename_axis("date")


def load_market_data(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    source: str = "historical",
    cache_dir: str | Path | None = None,
    tickers: Iterable[str] = tuple(ASSETS),
    warmup_sessions: int = 280,
    force_refresh: bool = False,
) -> MarketData:
    """Load a genuine adjusted-close run or an explicitly selected synthetic demo.

    A validated genuine local cache is preferred and reused until a forced
    refresh. A failed refresh falls back only to that exact genuine cache. No
    market data are bundled or licensed for redistribution by this project.
    """
    first, last = _date(start), _date(end)
    if source not in {"historical", "synthetic"}:
        raise DataError("Source must be 'historical' or 'synthetic'.")
    if not isinstance(warmup_sessions, int) or not 0 <= warmup_sessions <= 1500:
        raise DataError("Warm-up sessions must be an integer between 0 and 1500.")
    symbols = tuple(dict.fromkeys(tickers))
    if not symbols or set(symbols) - set(ASSETS):
        raise DataError("Choose at least one supported ticker.")
    scenario_sessions = exchange_sessions(first, last)
    if len(scenario_sessions) < 2:
        raise DataError("The scenario must contain at least two XNYS sessions.")
    warmup_candidates = exchange_sessions(first - pd.Timedelta(days=warmup_sessions * 2 + 30), last)
    before = warmup_candidates[warmup_candidates < scenario_sessions[0]]
    warm_start = before[-warmup_sessions] if warmup_sessions and len(before) >= warmup_sessions else scenario_sessions[0]
    requested_index = exchange_sessions(warm_start, last)
    now = datetime.now(timezone.utc).isoformat()
    meta: dict = {}
    cache_warning: str | None = None
    if source == "synthetic":
        raw_prices = _synthetic_prices(last).reindex(index=requested_index, columns=symbols)
        meta = {"provider": "Deterministic synthetic demonstration (fictional prices)", "retrieved_at_utc": now, "generator_version": 1, "seed": 200820202022, "cache_used": False}
    else:
        folder = Path(cache_dir) if cache_dir is not None else Path(__file__).resolve().parents[1] / ".cache" / "market_data"
        key_data = json.dumps({"start": str(warm_start.date()), "end": str(last.date()), "tickers": symbols, "settings": ADJUSTMENT_SETTINGS}, sort_keys=True)
        key = hashlib.sha256(key_data.encode()).hexdigest()[:24]
        csv_path, meta_path = folder / f"{key}.csv", folder / f"{key}.json"

        def read_cache() -> tuple[pd.DataFrame, dict] | None:
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if cached_meta.get("provider") != "Yahoo Finance via yfinance" or cached_meta.get("request_key") != key_data:
                    return None
                cached = pd.read_csv(csv_path, index_col=0, parse_dates=True, float_precision="round_trip")
                cached.index = pd.DatetimeIndex(cached.index)
                if data_fingerprint(cached) != cached_meta.get("raw_fingerprint"):
                    return None
                validate_prices(cached, first, last, symbols)
                return cached, {**cached_meta, "cache_used": True}
            except (OSError, ValueError, KeyError, DataError):
                return None

        cached = read_cache()
        if cached is not None and not force_refresh:
            raw_prices, meta = cached
        else:
            try:
                import yfinance as yf

                # Override yf's own timezone cache path to keep runtime files local.
                folder.mkdir(parents=True, exist_ok=True)
                yf.set_tz_cache_location(str(folder / "yfinance"))
                downloaded = yf.download(
                    list(symbols), start=warm_start.date().isoformat(),
                    end=(last + pd.Timedelta(days=1)).date().isoformat(),
                    auto_adjust=False, actions=False, repair=False, keepna=True,
                    interval="1d", group_by="column", threads=4,
                    progress=False, timeout=10,
                )
                raw_prices = normalize_yfinance(downloaded, symbols)
                # Validate BEFORE a cache can replace a previously usable file.
                validate_prices(raw_prices, first, last, symbols)
                meta = {"provider": "Yahoo Finance via yfinance", "retrieved_at_utc": now, "request_key": key_data, "raw_fingerprint": data_fingerprint(raw_prices), "cache_used": False, "yfinance_version": yf.__version__}
                temp_id = uuid.uuid4().hex
                csv_temp, meta_temp = folder / f"{key}.{temp_id}.csv.tmp", folder / f"{key}.{temp_id}.json.tmp"
                # Round-trip precision is required for content validation.
                raw_prices.to_csv(csv_temp, float_format="%.17g")
                # pandas' default CSV parser can round at its last bit; calculate
                # the cache digest from the exact representation we will reload.
                roundtrip = pd.read_csv(csv_temp, index_col=0, parse_dates=True, float_precision="round_trip")
                meta["raw_fingerprint"] = data_fingerprint(roundtrip)
                meta_temp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                csv_temp.replace(csv_path)
                meta_temp.replace(meta_path)
                raw_prices = roundtrip
            except Exception as exc:
                if cached is None:
                    raise DataError(f"Historical download failed and no validated genuine cache is available: {type(exc).__name__}: {exc}. Retry historical download, choose another valid window, or explicitly select the synthetic demonstration.") from exc
                raw_prices, meta = cached
                cache_warning = f"Download refresh failed ({type(exc).__name__}); using validated genuine cache retrieved {meta['retrieved_at_utc']}."

    # Retain every required session, including wholly missing provider rows.
    # Provider validation above rejects unexpected dates before this alignment.
    # Add absent leading warm-up sessions explicitly so shortages are disclosed.
    prices, eligible, exclusions, warnings = validate_prices(raw_prices.reindex(requested_index), first, last, symbols)
    if cache_warning:
        warnings.append(cache_warning)
    fingerprint = data_fingerprint(prices)
    observed = prices.dropna(how="all").index
    manifest = {
        **meta,
        "schema_version": 1,
        "synthetic": source == "synthetic",
        "requested_start": first.date().isoformat(),
        "requested_end": last.date().isoformat(),
        "warmup_start": warm_start.date().isoformat(),
        "warmup_sessions_requested": warmup_sessions,
        "observed_start": observed.min().date().isoformat(),
        "observed_end": observed.max().date().isoformat(),
        "tickers_requested": list(symbols),
        "eligible": list(eligible),
        "exclusions": exclusions,
        "settings": ADJUSTMENT_SETTINGS.copy(),
        "validation_warnings": warnings,
        "data_fingerprint": fingerprint,
        "licensing": "No genuine observations bundled. Yahoo Finance data access/redistribution rights are separate from this project's software license; personal research use only unless you obtain appropriate rights.",
        "limitations": "Adjusted units approximate reinvested total returns; retrospectively adjusted and revised data are not a fully point-in-time database. No separate dividends or split credits. Today's surviving ETF universe creates survivorship bias.",
    }
    return MarketData(prices, manifest, eligible, exclusions, fingerprint, source == "synthetic")
