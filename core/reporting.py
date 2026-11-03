"""Escaped Survival reports and explicitly retrospective Laboratory exports.

Every export carries its data source. Survival exports additionally enforce an
as-of boundary; filtering is defense in depth over already restricted views.
Laboratory comparisons are separately labeled retrospective research.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

import pandas as pd

from core.metrics import metric_notes, performance_metrics, survival_score

PUBLIC_METADATA = (
    "scenario", "label", "data_source", "source", "provider", "retrieved_at",
    "fingerprint", "cash_interest_annual", "risk_free_annual", "cost_bps", "capital", "benchmark_name",
    "strategy", "strategy_parameters", "adjustment", "execution", "turnover",
)

METHODOLOGY = (
    "USD; fractional synthetic total-return units; long only; no leverage, taxes, or external flows. "
    "Starting capital is cash at the first close. Targets submitted after a close execute at the next session close, "
    "sized at that close against post-cost equity. Costs apply once per side to gross executed notional. "
    "Adjusted prices approximate reinvested returns; dividends and splits are not credited again. "
    "They are not actual executable share prices or a complete point-in-time database; provider adjustments and revisions remain possible. "
    "Cash interest is 0%. Today's ETF universe introduces survivorship bias. No terminal liquidation is assumed."
)


def formatted_metric_table(metrics: Mapping[str, Any]) -> pd.DataFrame:
    """Present metric values with readable labels and explicit financial units.

    This is a presentation copy. Numeric calculations and downloadable CSV
    observations retain their original decimal-return and USD conventions.
    """
    confidence = float(metrics.get("tail_confidence", 0.95))
    presentation = {
        "initial_value": ("Initial capital", "usd"),
        "final_value": ("Portfolio value", "usd"),
        "profit_loss": ("Net profit / loss", "usd"),
        "total_return": ("Total return", "percent"),
        "cagr": ("CAGR · annualized", "percent"),
        "annualized_volatility": ("Annualized volatility", "percent"),
        "sharpe": ("Sharpe ratio", "ratio"),
        "sortino": ("Sortino ratio", "ratio"),
        "max_drawdown": ("Maximum drawdown · loss", "percent"),
        "calmar": ("Calmar ratio", "ratio"),
        "best_daily_return": ("Best daily return", "percent"),
        "worst_daily_return": ("Worst daily return", "percent"),
        "excess_return": ("Excess cumulative return", "pp"),
        "gross_turnover": ("Gross turnover", "multiple"),
        "total_costs": ("Trading costs", "usd"),
        "var": (f"One-session VaR · {confidence:.0%} confidence", "percent"),
        "expected_shortfall": (f"Expected shortfall · {confidence:.0%} confidence", "percent"),
        "observations": ("Observed daily returns", "count"),
        "elapsed_years": ("Evaluation period", "years"),
        "risk_free_annual": ("Risk-free assumption · annual", "percent"),
        "mar_annual": ("Minimum acceptable return · annual", "percent"),
        "tail_confidence": ("Tail-risk confidence", "percent"),
        "tail_min_samples": ("Minimum tail-risk observations", "count"),
    }
    records = []
    for key, value in metrics.items():
        label, units = presentation.get(key, (key.replace("_", " ").title(), "ratio"))
        if value is None or pd.isna(value):
            formatted = "N/A"
        elif units == "usd":
            formatted = f"${float(value):,.2f}"
        elif units == "percent":
            formatted = f"{float(value):.2%}"
        elif units == "pp":
            formatted = f"{float(value) * 100:+.2f} pp"
        elif units == "multiple":
            formatted = f"{float(value):.2f}×"
        elif units == "count":
            formatted = f"{int(value):,}"
        elif units == "years":
            formatted = f"{float(value):.2f} years"
        else:
            formatted = f"{float(value):,.2f}"
        records.append({"Metric": label, "Value": formatted})
    return pd.DataFrame(records, columns=["Metric", "Value"])


def _timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("A valid current simulation date is required for exports.")
    if result.tzinfo is not None:
        result = result.tz_localize(None)
    return result.normalize()


def _restrict(frame: pd.DataFrame | pd.Series | None, cutoff: pd.Timestamp, *, pending_decisions: bool = False) -> pd.DataFrame | pd.Series:
    if frame is None:
        return pd.DataFrame()
    result = frame.copy()
    if result.empty:
        return result
    if isinstance(result.index, pd.DatetimeIndex):
        index = result.index.tz_localize(None) if result.index.tz is not None else result.index
        result = result.loc[index.normalize() <= cutoff].copy()
    elif isinstance(result, pd.DataFrame):
        date_column = next((key for key in ("disclosure_session", "disclosure_date", "date", "signal_date", "execution_date") if key in result), None)
        if date_column is None:
            raise ValueError("Nonempty exported tables must have a date index or an explicit date column.")
        dates = pd.to_datetime(result[date_column], errors="raise").map(lambda value: _timestamp(value) if pd.notna(value) else pd.NaT)
        result = result.loc[dates <= cutoff].copy()
    else:
        raise ValueError("Exported time series require a date index.")
    # Reject a future-valued secondary date rather than leaking it in a cell.
    if isinstance(result, pd.DataFrame):
        for key in ("execution_date", "signal_date", "date", "disclosure_session", "disclosure_date", "publication_date"):
            if key in result and not result.empty:
                dates = pd.to_datetime(result[key], errors="raise")
                mask = dates.isna() | dates.map(lambda value: _timestamp(value) <= cutoff if pd.notna(value) else True)
                if pending_decisions and key == "execution_date":
                    # Decisions already made are revealed information. Preserve
                    # them without exporting a later scheduled execution date.
                    result.loc[~mask, key] = pd.NaT
                    result.loc[~mask, "execution_status_as_of"] = "Pending after report date"
                    continue
                result = result.loc[mask].copy()
    return result


def _context(
    history: pd.DataFrame,
    metadata: Mapping[str, Any] | None,
    current_date: Any | None,
    completed: bool,
) -> tuple[pd.DataFrame, dict[str, Any], pd.Timestamp, str, str]:
    if history.empty or not isinstance(history.index, pd.DatetimeIndex):
        raise ValueError("A report requires recorded, date-indexed equity history.")
    cutoff = _timestamp(current_date if current_date is not None else history.index.max())
    revealed = _restrict(history, cutoff)
    if revealed.empty or "equity" not in revealed:
        raise ValueError("No equity observations exist through the selected simulation date.")
    public = {key: value for key, value in (metadata or {}).items() if key in PUBLIC_METADATA}
    source = str(public.get("data_source", public.get("source", public.get("provider", "Source unspecified"))))
    synthetic = "synthetic" in source.lower() or "demo" in source.lower()
    source_label = "SYNTHETIC DEMONSTRATION — NOT HISTORICAL MARKET PERFORMANCE" if synthetic else source
    status = "Completed session" if completed else "Partial session — revealed history only"
    return revealed, public, cutoff, source_label, status


def build_strategy_comparison_csv(
    table: pd.DataFrame,
    data_source: str,
    scenario: str,
    benchmark_name: str,
    evaluation_start: Any | None = None,
    evaluation_end: Any | None = None,
) -> str:
    """Export a rectangular, provenance-labeled retrospective comparison.

    Strategy names come from the unique, nonempty string index. Metric columns
    are copied without formatting or rescaling, so returns remain decimals and
    money remains USD. Provenance lives in ordinary columns on every row rather
    than a one-field preamble that would break standard CSV readers. This is a
    full-window Laboratory export, not a Survival as-of report.
    """
    if not isinstance(table, pd.DataFrame):
        raise TypeError("Strategy comparison requires a pandas DataFrame.")
    if table.empty:
        raise ValueError("Strategy comparison requires at least one strategy and metric.")
    if isinstance(table.columns, pd.MultiIndex) or not table.columns.is_unique:
        raise ValueError("Metric columns must be unique, single-level names.")
    if not table.index.is_unique or any(not isinstance(name, str) or not name.strip() for name in table.index):
        raise ValueError("Strategy labels must be unique, nonempty strings.")
    reserved = {
        "data_source", "research_mode", "scenario", "benchmark_name",
        "evaluation_start", "evaluation_end", "strategy",
    }
    collisions = reserved.intersection(table.columns)
    if collisions:
        raise ValueError("Metric columns conflict with reserved export fields: " + ", ".join(sorted(collisions)) + ".")
    labels = {}
    for key, value in (("data_source", data_source), ("scenario", scenario), ("benchmark_name", benchmark_name)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be an explicit, nonempty label.")
        labels[key] = value.strip()
    source = labels["data_source"]
    if "synthetic" in source.lower() or "demo" in source.lower():
        source = "SYNTHETIC DEMONSTRATION — NOT HISTORICAL MARKET PERFORMANCE"
    start = _timestamp(evaluation_start) if evaluation_start is not None else None
    end = _timestamp(evaluation_end) if evaluation_end is not None else None
    if start is not None and end is not None and start > end:
        raise ValueError("Evaluation start must be on or before evaluation end.")
    result = table.copy()
    metadata = {
        "data_source": source,
        "research_mode": "Retrospective research — full evaluation window",
        "scenario": labels["scenario"],
        "benchmark_name": labels["benchmark_name"],
        "evaluation_start": start.strftime("%Y-%m-%d") if start is not None else "",
        "evaluation_end": end.strftime("%Y-%m-%d") if end is not None else "",
    }
    result.insert(0, "strategy", table.index.to_list())
    for key, value in reversed(tuple(metadata.items())):
        result.insert(0, key, value)
    return result.to_csv(index=False, lineterminator="\n")


def build_csv_report(
    history: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    metadata: Mapping[str, Any] | None = None,
    current_date: Any | None = None,
    completed: bool = False,
    kind: str = "history",
    contributions: pd.DataFrame | None = None,
    events: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    decisions: pd.DataFrame | None = None,
) -> str:
    """UTF-8 CSV with source/status columns on every row; no hidden future cells."""
    revealed, _, cutoff, source_label, status = _context(history, metadata, current_date, completed)
    tables = {
        "history": revealed,
        "trades": trades,
        "contributions": contributions,
        "events": pd.DataFrame(events) if events is not None else None,
        "decisions": decisions,
    }
    if kind not in tables:
        raise ValueError("CSV kind must be history, trades, contributions, events, or decisions.")
    selected = _restrict(tables[kind], cutoff, pending_decisions=kind == "decisions")
    if isinstance(selected.index, pd.DatetimeIndex):
        selected.index.name = selected.index.name or "date"
        selected = selected.reset_index()
    else:
        selected = selected.reset_index(drop=True)
    if kind == "history" and benchmark is not None:
        reference = _restrict(benchmark, cutoff)
        if not reference.index.equals(revealed.index):
            raise ValueError("Export benchmark and portfolio windows must match exactly.")
        selected["benchmark_equity"] = reference.to_numpy()
    if selected.empty:
        # An empty trade ledger still carries a visible provenance record rather
        # than producing an unlabeled file containing column headers alone.
        selected = selected.reindex([0])
        selected["export_note"] = "No revealed observations in this table."
    selected.insert(0, "report_status", status)
    selected.insert(0, "as_of", cutoff.strftime("%Y-%m-%d"))
    selected.insert(0, "data_source", source_label)
    return selected.to_csv(index=False, lineterminator="\n")


def _table(frame: pd.DataFrame | pd.Series | None) -> str:
    if frame is None or frame.empty:
        return "<p class='muted'>No observations or executions available.</p>"
    if isinstance(frame, pd.Series):
        frame = frame.to_frame()
    return frame.to_html(escape=True, border=0, classes="data", float_format=lambda value: f"{value:,.6f}")


def build_html_report(
    history: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    metadata: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    events: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None,
    contributions: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    current_date: Any | None = None,
    completed: bool = False,
    decisions: pd.DataFrame | None = None,
    confidence: float = 0.95,
) -> str:
    """Standalone HTML; recomputes performance after restricting all inputs.

    ``metrics`` is accepted for caller compatibility but intentionally not used:
    a metric computed on unrevealed observations must not leak into an interim
    export. No external scripts, fonts, chart libraries, or network are required.
    """
    revealed, public, cutoff, source_label, status = _context(history, metadata, current_date, completed)
    executions = _restrict(trades, cutoff)
    reference = _restrict(benchmark, cutoff) if benchmark is not None else None
    actual = performance_metrics(revealed["equity"], benchmark=reference, trades=executions, risk_free_annual=float(public.get("risk_free_annual", 0.0)),confidence=confidence)
    numeric = formatted_metric_table(actual).set_index("Metric")
    points = survival_score(actual["total_return"], actual["max_drawdown"], actual["sharpe"], actual["excess_return"])
    notes = "".join(f"<li>{escape(item)}</li>" for item in metric_notes(actual))
    metadata_table = pd.DataFrame({"value": {key: str(value) for key, value in public.items()}})
    event_frame = pd.DataFrame(events) if events is not None else None
    comparison = ""
    if reference is not None:
        benchmark_name=escape(str(public.get("benchmark_name","selected benchmark")))
        verb="finished" if completed else "stands"
        comparison = f"<p>Your portfolio {verb} at ${actual['final_value']:,.2f}, compared with ${reference.iloc[-1]:,.2f} for <strong>{benchmark_name}</strong>. This comparison uses matching elapsed windows.</p>"
    headline = "DID YOUR PORTFOLIO SURVIVE THE CRISIS?" if completed else "INTERIM PERFORMANCE — SESSION IN PROGRESS"
    score_html = ""
    if completed:
        component_frame = pd.DataFrame({"component_score_0_to_100": points["components"], "weight": points["weights"]})
        component_frame["component_score_0_to_100"]=component_frame["component_score_0_to_100"].map(lambda value:f"{value:.1f}")
        component_frame["weight"]=component_frame["weight"].map(lambda value:f"{value:.0%}")
        score_html = f"<h2>Educational survival score: {points['score']:.1f}/100</h2><p>{escape(points['category'])}. {escape(points['label'])}</p>{_table(component_frame)}<p>Preservation 35%: clip(1 + total return, 0, 1). Drawdown 35%: clip(1 − maximum drawdown / 0.60, 0, 1). Risk-adjusted 15%: clip((Sharpe + 1) / 3, 0, 1). Relative 15%: clip(0.5 + excess cumulative return / 0.40, 0, 1). Multiply components by 100 before weighting. Missing components receive neutral 50%; weights are not redistributed. Growth above capital preservation is deliberately capped. Thresholds: excellent ≥85, strong ≥70, moderate ≥50, severe damage ≥25, collapse &lt;25.</p>"
    history_table = revealed.copy()
    if reference is not None:
        history_table["benchmark_equity"] = reference
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Financial Time Machine — {escape(status)}</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;background:#0b1220;color:#e4edf8;margin:0 auto;max-width:1200px;padding:42px 24px}}h1,h2{{color:#f5fbff}}h2{{margin-top:36px}}.eyebrow{{color:#57d8ee;letter-spacing:.12em}}.notice{{padding:16px;border:1px solid #e4aa53;background:#302719;border-radius:8px;color:#ffd38b}}.muted{{color:#a2afc0}}table{{border-collapse:collapse;width:100%;font-size:12px;display:block;overflow-x:auto}}th,td{{padding:9px 12px;text-align:right;border-bottom:1px solid #27354a;white-space:nowrap}}th{{background:#142235}}a{{color:#67dced}}@media print{{body{{background:white;color:#152033}}h1,h2{{color:#152033}}th{{background:#edf2f7}}}}</style></head>
<body><p class="eyebrow">FINANCIAL TIME MACHINE / RESEARCH REPORT</p><h1>{headline}</h1>
<p class="notice">{escape(source_label)}</p><p>{escape(status)} · As of {cutoff:%Y-%m-%d}</p>
{comparison}{score_html}<h2>Performance metrics</h2><p>Returns and risk estimates are percentages; monetary values are USD; turnover is a multiple of average daily equity. Excess cumulative return is expressed in percentage points (pp), not annualized alpha. Daily history and CSV return columns retain decimal fractions.</p>{_table(numeric)}
<h2>Metric conventions</h2><ul>{notes}</ul><h2>Daily portfolio history</h2>{_table(history_table)}
<h2>Executed trade ledger</h2>{_table(executions)}<h2>Investment decision history</h2>{_table(_restrict(decisions, cutoff, pending_decisions=True))}
<h2>Daily P&amp;L contributions (USD)</h2><p>Asset and cash contributions plus negative execution costs reconcile to the change in equity; this is realized return attribution, not covariance risk attribution.</p>{_table(_restrict(contributions, cutoff))}
<h2>Revealed historical events</h2>{_table(_restrict(event_frame, cutoff))}<h2>Data provenance</h2>{_table(metadata_table)}
<h2>Methodology and limitations</h2><p>{escape(METHODOLOGY)}</p><p>Named scenarios disclose broad historical context. This is educational replay, not a blind prediction experiment. Synthetic demonstrations do not reproduce real crisis returns. For financial education and software research only; no investment advice or performance guarantee.</p></body></html>"""
