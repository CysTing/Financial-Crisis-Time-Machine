"""Factual replay highlights from revealed portfolio observations only.

These pure functions describe recorded portfolio outcomes. They do not infer
news, explain market causes, forecast a recovery, or choose investments. Dates
are exchange-session labels and returns are decimal fractions. The first equity
observation is the initial cash baseline, never an extra daily return.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _date(value: Any) -> pd.Timestamp:
    if value is None:
        raise ValueError("An explicit observation date is required.")
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("Observation dates must be valid.")
    if result.tzinfo is not None:
        result = result.tz_localize(None)
    return result.normalize()


def _revealed_history(history: pd.DataFrame, current_date: Any = None) -> pd.DataFrame:
    """Restrict BEFORE numeric validation so future observations cannot affect a view."""
    if not isinstance(history, pd.DataFrame):
        raise TypeError("History must be a pandas DataFrame containing equity.")
    if history.empty:
        return history.copy()
    if not isinstance(history.index, pd.DatetimeIndex):
        raise ValueError("History requires a DatetimeIndex of exchange-session labels.")
    result = history.copy()
    dates = result.index.tz_localize(None) if result.index.tz is not None else result.index
    result.index = dates.normalize()
    if result.index.hasnans:
        raise ValueError("History contains invalid observation dates.")
    if current_date is not None:
        result = result.loc[result.index <= _date(current_date)].copy()
    if result.empty:
        return result
    if not result.index.is_unique or not result.index.is_monotonic_increasing:
        raise ValueError("Revealed observations must have unique, increasing session dates.")
    if "equity" not in result:
        raise ValueError("Revealed history requires an equity column.")
    try:
        equity = pd.to_numeric(result["equity"], errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Revealed equity must be numeric, finite, and strictly positive.") from exc
    if not np.isfinite(equity.to_numpy()).all() or (equity <= 0).any():
        raise ValueError("Revealed equity must be finite and strictly positive.")
    result["equity"] = equity
    return result


def _returns(history: pd.DataFrame) -> tuple[pd.Series, str]:
    computed = history["equity"].pct_change(fill_method=None).iloc[1:]
    computed.name = "daily_return"
    if "daily_return" in history:
        try:
            stored = pd.to_numeric(history["daily_return"], errors="raise").iloc[1:].astype(float)
            if np.isfinite(stored.to_numpy()).all() and np.allclose(stored, computed, rtol=1e-8, atol=1e-10):
                return stored, "stored_reconciled"
        except (TypeError, ValueError):
            pass
    return computed, "equity_recomputed"


def _sum_optional(history: pd.DataFrame, column: str) -> float | None:
    if column not in history:
        return None
    try:
        values = pd.to_numeric(history[column], errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Revealed {column} must be numeric, finite, and nonnegative.") from exc
    if not np.isfinite(values.to_numpy()).all() or (values < 0).any():
        raise ValueError(f"Revealed {column} must be finite and nonnegative.")
    return float(values.sum())


def _move(returns: pd.Series, which: str) -> dict[str, str | float] | None:
    if returns.empty:
        return None
    date = returns.idxmin() if which == "worst" else returns.idxmax() if which == "best" else returns.abs().idxmax()
    return {"date": date.date().isoformat(), "return": float(returns.loc[date])}


def market_pulse(
    history: pd.DataFrame,
    after_date: Any,
    current_date: Any = None,
    threshold: float = 0.03,
) -> dict[str, Any] | None:
    """Summarize every newly processed session, highlighting its largest daily move.

    ``after_date`` is the last already-presented close. A five-session or monthly
    advance can highlight an intermediate loss even if the last day was quiet.
    ``tone`` uses the sign of the largest absolute one-session return only when
    its magnitude meets ``threshold``. It is presentation metadata, not a rating.
    Ties select the earliest revealed session. Missing costs/notional stay N/A.
    """
    if not np.isfinite(threshold) or threshold < 0:
        raise ValueError("The pulse threshold must be finite and nonnegative.")
    after = _date(after_date)
    revealed = _revealed_history(history, current_date)
    if len(revealed) < 2:
        return None
    returns, source = _returns(revealed)
    new = returns.loc[returns.index > after]
    if new.empty:
        return None
    new_history = revealed.loc[new.index]
    largest = _move(new, "absolute")
    magnitude = abs(largest["return"])
    tone = "steady"
    if magnitude >= threshold and largest["return"] != 0:
        tone = "rise" if largest["return"] > 0 else "drop"
    count = len(new)
    headline = (
        f"Portfolio {'rose' if largest['return'] > 0 else 'fell'} {magnitude:.2%} on {largest['date']}."
        if tone != "steady" else
        f"{count} session{'s' if count != 1 else ''} processed; largest daily move {largest['return']:+.2%} on {largest['date']}."
    )
    return {
        "count": count,
        "start_date": new.index[0].date().isoformat(),
        "latest_date": new.index[-1].date().isoformat(),
        "latest_return": float(new.iloc[-1]),
        "largest_abs_move": largest,
        "worst_day": _move(new, "worst"),
        "best_day": _move(new, "best"),
        "costs": _sum_optional(new_history, "costs"),
        "gross_notional": _sum_optional(new_history, "gross_notional"),
        "tone": tone,
        "headline": headline,
        "return_source": source,
    }


def _cash_average(history: pd.DataFrame) -> float | None:
    if "weight_CASH" in history:
        values = pd.to_numeric(history["weight_CASH"], errors="raise").astype(float)
    elif "cash" in history:
        values = pd.to_numeric(history["cash"], errors="raise").astype(float) / history["equity"]
    else:
        return None
    if not np.isfinite(values.to_numpy()).all() or (values < -1e-10).any() or (values > 1 + 1e-10).any():
        raise ValueError("Revealed cash weights must be finite and within an unleveraged 0–100% range.")
    return float(values.clip(0, 1).mean())


def report_debrief(
    history: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None = None,
    current_date: Any = None,
) -> dict[str, Any]:
    """Describe the revealed path without inventing causes or unseen recoveries.

    Recovery describes the *deepest observed drawdown episode*. Its duration is
    from the most recent preceding peak to the first subsequent close at/above
    that peak, measured both in observed sessions and elapsed calendar days.
    Current underwater status is separate: a recovered old episode can coexist
    with a later drawdown. No future recovery date is sent to presentation code.

    ``capital_retained`` is final/initial, without a cap. ``cost_drag`` is recorded
    costs/initial capital, not a counterfactual return estimate. Mean cash weight
    includes the initial cash baseline. Benchmark excess is a cumulative decimal
    return difference, available only for identical revealed sessions and capital.
    """
    revealed = _revealed_history(history, current_date)
    result: dict[str, Any] = {
        "available": False, "as_of": None, "observations": 0,
        "weakest_day": None, "strongest_day": None,
        "deepest_drawdown": None, "capital_retained": None,
        "total_return": None, "cost_total": None, "cost_drag": None,
        "cash_average_weight": None, "relative_return": None,
        "benchmark_return": None, "recovery": None,
        "return_source": None, "notes": [],
    }
    if revealed.empty:
        result["notes"].append("No revealed equity observations are available.")
        return result
    equity = revealed["equity"]
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    returns, source = _returns(revealed)
    costs = _sum_optional(revealed, "costs")
    result.update(
        as_of=equity.index[-1].date().isoformat(), observations=len(returns),
        capital_retained=final / initial, total_return=final / initial - 1,
        cost_total=costs, cost_drag=costs / initial if costs is not None else None,
        cash_average_weight=_cash_average(revealed), return_source=source,
    )
    if len(returns) == 0:
        result["notes"].append("Only the initial cash baseline is revealed; no investment session return exists yet.")
        return result
    result["available"] = True
    result["weakest_day"], result["strongest_day"] = _move(returns, "worst"), _move(returns, "best")
    running_peak = equity.cummax()
    drawdowns = 1 - equity / running_peak
    magnitude = float(drawdowns.max())
    current_drawdown = float(drawdowns.iloc[-1])
    if magnitude <= 1e-12:
        result["deepest_drawdown"] = {"date": None, "magnitude": 0.0, "peak_date": None}
        result["recovery"] = {
            "status": "no_drawdown", "peak_date": None, "trough_date": None,
            "recovery_date": None, "sessions_to_recover": None,
            "calendar_days_to_recover": None, "current_drawdown": 0.0,
            "currently_underwater": False,
        }
    else:
        trough = drawdowns.idxmax()  # Earliest tie, fixed by observed index order.
        peak_value = float(running_peak.loc[trough])
        before = equity.loc[:trough]
        peak = before.index[np.isclose(before.to_numpy(), peak_value, rtol=1e-12, atol=1e-10)][-1]
        later = equity.loc[equity.index > trough]
        recovered = later.loc[later >= peak_value - max(1e-10, abs(peak_value) * 1e-12)]
        recovery_date = recovered.index[0] if not recovered.empty else None
        result["deepest_drawdown"] = {
            "date": trough.date().isoformat(), "magnitude": magnitude,
            "peak_date": peak.date().isoformat(),
        }
        result["recovery"] = {
            "status": "recovered" if recovery_date is not None else "unrecovered",
            "peak_date": peak.date().isoformat(), "trough_date": trough.date().isoformat(),
            "recovery_date": recovery_date.date().isoformat() if recovery_date is not None else None,
            "sessions_to_recover": int(equity.index.get_loc(recovery_date) - equity.index.get_loc(peak)) if recovery_date is not None else None,
            "calendar_days_to_recover": int((recovery_date - peak).days) if recovery_date is not None else None,
            "current_drawdown": current_drawdown,
            "currently_underwater": current_drawdown > 1e-12,
        }
    if benchmark is not None:
        if isinstance(benchmark, pd.Series):
            benchmark_frame = benchmark.rename("equity").to_frame()
        elif isinstance(benchmark, pd.DataFrame):
            benchmark_frame = benchmark
        else:
            raise TypeError("Benchmark must be an equity Series or DataFrame.")
        comparison = _revealed_history(benchmark_frame, equity.index[-1])
        if (not comparison.empty and comparison.index.equals(equity.index)
                and np.isclose(float(comparison.equity.iloc[0]), initial, rtol=1e-10, atol=1e-8)):
            benchmark_return = float(comparison.equity.iloc[-1] / comparison.equity.iloc[0] - 1)
            result["benchmark_return"] = benchmark_return
            result["relative_return"] = result["total_return"] - benchmark_return
        else:
            result["notes"].append("Benchmark-relative results are unavailable because revealed sessions or initial capital do not match.")
    if "daily_return" in revealed and source == "equity_recomputed":
        result["notes"].append("Stored daily returns did not reconcile; highlights use returns recomputed from revealed equity.")
    result["notes"].append("Recovery refers to the deepest observed drawdown; durations run from its prior peak to the first observed recovery close.")
    result["notes"].append("Average cash includes the initial cash baseline. Recorded costs / initial capital is a cost burden, not a counterfactual return loss.")
    return result
