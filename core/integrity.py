"""Read-only accounting evidence and reproducibility fingerprints.

These helpers accept the copied, revealed-only snapshot used by presentation
code. A passing snapshot is evidence for these checks, not a certification.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd


SCHEMA_VERSION = "financial-time-machine/run-passport/1"
DATASETS = ("prices", "history", "holdings", "ledger", "contributions", "decisions")


def _native(value: Any) -> Any:
    """Canonical JSON values, retaining row/column order and missing values."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray, pd.Index)):
        return [_native(item) for item in value]
    if isinstance(value, np.generic):
        return _native(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else {"nonfinite": str(value)}
    if isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported passport value: {type(value).__name__}")


def _canonical(value: Any) -> str:
    return json.dumps(_native(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _frame(value: Any) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame):
        raise ValueError("A required revealed dataset is missing.")
    if value.columns.duplicated().any():
        raise ValueError("Duplicate dataset columns are not permitted.")
    return value


def _dates(values: Any) -> pd.DatetimeIndex:
    result = pd.DatetimeIndex(pd.to_datetime(values, errors="raise"))
    if result.tz is not None:
        result = result.tz_localize(None)
    if result.isna().any():
        raise ValueError("Missing observation date.")
    return result.normalize()


def _finite(values: Any) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if not np.isfinite(array).all():
        raise ValueError("Nonfinite numeric observation.")
    return array


def build_integrity_snapshot(view: Mapping[str, Any]) -> dict:
    """Compute eight checks from a public revealed snapshot.

    Monetary comparisons use max(1e-7 USD, abs(current equity) * 1e-9).
    Pending/replaced decision execution destinations may be future-dated: they
    are schedules, not revealed executions. No future signal is permitted.
    Coverage compares recorded sessions with supplied revealed prices; it does
    not certify the provider's upstream exchange-calendar completeness.
    """
    checks: list[dict] = []
    proof: dict[str, Any] = {}
    try:
        clock = _dates([view["current_date"]])[0]
        history = _frame(view["history"])
        if history.empty:
            raise ValueError("No revealed account observations.")
        equity = float(_finite(history["equity"])[-1])
        cash = float(_finite(history["cash"])[-1])
        tolerance = max(1e-7, abs(equity) * 1e-9)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {"available": False, "status": "unavailable", "as_of": None, "checks": [], "proof": {},
                "passed": 0, "total": 0, "detail": str(exc)}

    def check(identifier: str, label: str, operation) -> None:
        try:
            outcome = operation()
            if outcome is None:
                checks.append(dict(id=identifier, label=label, status="not_applicable", detail="Initial cash baseline; no elapsed close-to-close change yet."))
            else:
                passed, detail = outcome
                checks.append(dict(id=identifier, label=label, status="pass" if passed else "fail", detail=detail))
        except (KeyError, TypeError, ValueError, IndexError, OverflowError, AttributeError) as exc:
            checks.append(dict(id=identifier, label=label, status="fail", detail=f"Unavailable or invalid evidence: {exc}"))

    def balance():
        holdings = _frame(view["holdings"])
        positions = float(_finite(holdings.loc[holdings.asset != "CASH", "value"]).sum())
        residual = equity - cash - positions
        proof.update(equity=equity, cash=cash, positions_value=positions, balance_residual=residual, tolerance=tolerance)
        return abs(residual) <= tolerance, f"Equity − cash − positions = USD {residual:+.8f}; tolerance USD {tolerance:.8f}."

    def valuation():
        holdings, prices = _frame(view["holdings"]), _frame(view["prices"])
        if holdings.empty or holdings.asset.duplicated().any() or sum(holdings.asset == "CASH") != 1:
            raise ValueError("Holdings need unique assets and exactly one CASH row.")
        if set(holdings.asset) != {*prices.columns, "CASH"}:
            raise ValueError("Holdings and revealed price universes differ.")
        latest = prices.loc[clock]
        residuals = []
        for row in holdings.itertuples(index=False):
            _finite([row.units, row.price, row.value, row.weight])
            price = 1.0 if row.asset == "CASH" else float(latest[row.asset])
            if not math.isfinite(price) or price <= 0 or row.units < -tolerance:
                raise ValueError("Invalid closing valuation or long-only units.")
            residuals += [abs(row.price - price), abs(row.value - row.units * price), abs(row.weight * equity - row.value)]
            if row.asset == "CASH":
                residuals += [abs(row.value - cash)]
            else:
                residuals += [abs(float(history.iloc[-1][f"value_{row.asset}"]) - row.value),
                              abs(float(history.iloc[-1][f"units_{row.asset}"]) * price - row.value)]
        residuals += [abs(float(holdings.weight.sum()) - 1.0) * abs(equity)]
        worst = float(_finite(residuals).max())
        return worst <= tolerance, f"Largest closing valuation/weight residual: USD {worst:.8f}."

    def pnl():
        contributions = _frame(view["contributions"])
        if not contributions.index.equals(history.index):
            raise ValueError("Contribution dates must exactly match account dates.")
        costs, values = _finite(history["costs"]), _finite(contributions)
        changes = np.diff(_finite(history["equity"]), prepend=float(history.equity.iloc[0]))
        residuals = values.sum(axis=1) - changes
        cost_residuals = _finite(contributions["COSTS"]) + costs
        if not (np.abs(residuals) <= tolerance).all() or not (np.abs(cost_residuals) <= tolerance).all():
            return False, "Daily asset P&L minus costs does not reconcile to recorded equity changes."
        if (costs < -tolerance).any() or not (np.abs(_finite(contributions["CASH"])) <= tolerance).all():
            return False, "Costs must be nonnegative and cash P&L zero."
        if len(history) < 2:
            return None
        last = contributions.iloc[-1]
        asset_pnl = float(last.drop(labels=["CASH", "COSTS"]).sum())
        change, expected = float(changes[-1]), float(last.sum())
        proof.update(previous_equity=float(history.equity.iloc[-2]), asset_pnl=asset_pnl, costs=float(costs[-1]),
                     equity_change=change, expected_change=expected, residual=change - expected)
        return True, f"Every revealed daily P&L reconciles; latest residual USD {change - expected:+.8f}."

    def liquidity():
        equities, balances = _finite(history["equity"]), _finite(history["cash"])
        ledger = _frame(view["ledger"])
        if not ledger.empty:
            balances = np.append(balances, _finite(ledger["cash_after"]))
        valid=bool((equities > 0).all() and (balances >= -tolerance).all())
        return valid, "Recorded equity is positive and cash nonnegative within tolerance." if valid else "Nonpositive equity or negative cash appears in the recorded observations."

    def causality():
        ledger = _frame(view["ledger"])
        if ledger.empty:
            return True, "No executions yet; no same-close execution has occurred."
        signal, execution = _dates(ledger.signal_date), _dates(ledger.execution_date)
        revealed_dates = _dates(_frame(view["prices"]).index)
        positions = revealed_dates.get_indexer(signal)
        following = positions + 1
        next_close = bool((positions >= 0).all() and (following < len(revealed_dates)).all())
        if next_close:
            next_close = bool((revealed_dates[following] == execution).all())
        valid = bool((signal < execution).all() and (execution <= clock).all() and next_close)
        return valid, "Every executed trade follows its signal at the next supplied trading close, no later than the replay clock." if valid else "An execution is not at the next supplied close after its signal, or crosses the replay clock."

    def uniqueness():
        ledger = _frame(view["ledger"])
        if ledger.empty:
            return True, "No executed trade rows yet."
        valid = not ledger[["order_id", "asset"]].isna().any().any() and not ledger.duplicated(["order_id", "asset"]).any()
        return bool(valid), "Each executed (order ID, asset) pair occurs once." if valid else "An executed (order ID, asset) pair is duplicated or missing."

    def boundary():
        for name in ("prices", "history", "contributions"):
            frame = _frame(view[name])
            dates = _dates(frame.index)
            if not len(dates) or not (dates <= clock).all():
                return False, f"{name} contains missing or future-dated observations."
        ledger = _frame(view["ledger"])
        if not ledger.empty and not (_dates(ledger.execution_date) <= clock).all():
            return False, "Ledger contains a future execution."
        decisions = _frame(view["decisions"])
        if not decisions.empty:
            if not (_dates(decisions.signal_date) <= clock).all():
                return False, "Decisions contain a future signal."
            executed = decisions.loc[decisions.status == "executed"]
            if not executed.empty and not (_dates(executed.execution_date) <= clock).all():
                return False, "Decisions contain a future executed result."
        return True, "Observed datasets and decision signals stop at this close; pending destinations are schedules."

    def coverage():
        dates = _dates(history.index)
        prices, contributions = _frame(view["prices"]), _frame(view["contributions"])
        for name, frame in (("history", history), ("prices", prices), ("contributions", contributions)):
            index = _dates(frame.index)
            if not len(index) or index.has_duplicates or not index.is_monotonic_increasing or index[-1] != clock:
                return False, f"{name} dates must be unique, increasing, and current at this close."
        observed_prices = _dates(prices.index)
        observed_prices = observed_prices[observed_prices >= dates[0]]
        valid = dates.equals(observed_prices) and dates.equals(_dates(contributions.index))
        return valid, "Every supplied evaluation-session price has an account/contribution row; warm-up prices precede the baseline." if valid else "Evaluation-session dates differ across prices, account history, and contributions."

    check("balance", "Equity reconciliation", balance)
    check("valuation", "Closing valuation", valuation)
    check("pnl", "Daily P&L reconciliation", pnl)
    check("liquidity", "Cash & capital", liquidity)
    check("causality", "Next-close execution", causality)
    check("uniqueness", "Execution uniqueness", uniqueness)
    check("boundary", "Revealed-data boundary", boundary)
    check("coverage", "Recorded-session coverage", coverage)
    applicable = [item for item in checks if item["status"] != "not_applicable"]
    return _native(dict(available=True, status="fail" if any(item["status"] == "fail" for item in checks) else "pass",
                        as_of=clock, checks=checks, proof=proof, passed=sum(item["status"] == "pass" for item in checks),
                        total=len(applicable), detail="Checks of the supplied revealed snapshot; not a certification of upstream data."))


def build_run_passport(view: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict:
    """Metadata plus SHA-256 digests of supplied public datasets, no arrays.

    Dataset hashes include ordered columns, index names/values and cell values,
    including decision rationales. The root digest covers metadata and dataset
    digests. This is a change detector, not authentication or a digital signature.
    Retrospectively revised upstream prices remain an explicit model limitation.
    """
    datasets = {}
    for name in DATASETS:
        frame = _frame(view[name])
        serialized = dict(columns=list(frame.columns), index_name=frame.index.name,
                          index=list(frame.index), rows=frame.to_numpy(dtype=object).tolist())
        dates = []
        if name in ("prices", "history", "contributions"):
            dates = list(_dates(frame.index))
        elif name in ("ledger", "decisions") and not frame.empty:
            date_column = "execution_date" if name == "ledger" else "signal_date"
            dates = list(_dates(frame[date_column]))
        datasets[name] = dict(rows=len(frame), sha256=_hash(serialized),
                              first_observation=min(dates) if dates else None,
                              last_observation=max(dates) if dates else None)
    history = _frame(view["history"])
    payload = _native(dict(schema_version=SCHEMA_VERSION, as_of=view["current_date"],
                         complete=bool(view.get("complete", False)), context=dict(context or {}),
                         configuration=dict(view.get("config", {})),
                         effective_start=history.index[0] if not history.empty else None,
                         effective_end=history.index[-1] if not history.empty else None,
                         pending_order=view.get("pending"), datasets=datasets,
                         integrity=build_integrity_snapshot(view),
                         fingerprint_notice="SHA-256 change detector over this passport and revealed dataset digests; not a digital signature or independent data certification."))
    payload["payload_fingerprint"] = _hash(payload)
    return payload


def run_integrity_challenge(view: Mapping[str, Any]) -> dict:
    """Corrupt isolated copies and ask the same auditor to detect each fault.

    These deliberately simple diagnostic faults demonstrate the scope of the
    snapshot checks. They are not trading-engine mutation, penetration testing,
    or evidence of production security. The caller's inputs remain untouched.
    """
    before = build_run_passport(view)["payload_fingerprint"]
    cases = []

    def attempt(name, identifier, mutate):
        isolated = {key: value.copy(deep=True) if isinstance(value, pd.DataFrame) else value
                    for key, value in view.items()}
        mutate(isolated)
        report = build_integrity_snapshot(isolated)
        item = next((check for check in report["checks"] if check["id"] == identifier), None)
        detected = bool(item and item["status"] == "fail") or report["status"] == "unavailable"
        cases.append(dict(name=name, detected=detected,
                          detail=item["detail"] if item else report.get("detail", "No applicable evidence.")))

    def alter_cash(copy):
        copy["history"].loc[copy["history"].index[-1], "cash"] += 100.0

    def append_future(copy):
        history = copy["history"]
        future = _dates([copy["current_date"]])[0] + pd.Timedelta(days=1)
        copy["history"] = pd.concat([history, history.iloc[[-1]].set_axis([future])])

    def alter_contribution(copy):
        frame = copy["contributions"]
        asset = next(column for column in frame if column not in ("CASH", "COSTS"))
        frame.loc[frame.index[-1], asset] += 100.0

    attempt("Unexplained USD 100 cash", "balance", alter_cash)
    attempt("Future-dated account row", "boundary", append_future)
    if len(view["history"]) > 1:
        attempt("Unexplained USD 100 P&L", "pnl", alter_contribution)
    else:
        cases.append(dict(name="Unexplained USD 100 P&L", detected=None,
                          detail="Advance one trading session before testing close-to-close P&L."))
    unchanged = before == build_run_passport(view)["payload_fingerprint"]
    return dict(cases=cases, original_unchanged=unchanged,
                passed=unchanged and all(case["detected"] is not False for case in cases))


def passport_json(passport: Mapping[str, Any]) -> str:
    """Format an already built passport without altering its fingerprint."""
    return json.dumps(_native(passport), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
