"""Small factual presentation components for the revealed replay experience.

The core experience helpers calculate every number. These functions only format
their restricted dictionaries; they never read market data or advance a clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import math
from typing import Any

import pandas as pd
import streamlit as st


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _percent(value: Any, *, signed: bool = False) -> str:
    result = _number(value)
    if result is None:
        return "N/A"
    return f"{result:+.2%}" if signed else f"{result:.2%}"


def _date(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return "N/A"
    return "N/A" if pd.isna(result) else result.strftime("%d %b %Y")


def _count(value: Any) -> str:
    result = _number(value)
    return "N/A" if result is None or result < 0 else f"{int(result):,}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def render_market_pulse(pulse: dict | None, animate: bool = True) -> None:
    """Show newly processed account changes with an optional bounded emphasis.

    A strongest newly revealed daily move of at least +3% uses muted cyan;
    at most -3% uses muted coral. Other changes use amber and never animate.
    Motion lasts 1.6 seconds per cycle, at most two cycles, and respects the
    browser's reduced-motion preference. ``animate=False`` also disables it.
    The owner decides when to call this after an advance, rather than on a timer.
    """
    if not isinstance(pulse, Mapping) or not pulse:
        return
    largest = _mapping(pulse.get("largest_abs_move"))
    move = _number(largest.get("return"))
    if move is None:
        move = _number(pulse.get("latest_return"))
    tone = "rise" if move is not None and move >= .03 else "drop" if move is not None and move <= -.03 else "steady"
    colors = {"rise": ("#78c6bc", "120,198,188"), "drop": ("#d18a93", "209,138,147"), "steady": ("#c8ac74", "200,172,116")}
    accent, channels = colors[tone]
    motion = bool(animate and tone != "steady")
    # The key contains no external content. A changed newly revealed close
    # restarts emphasis, while the caller can suppress it on a normal rerun.
    try:
        stamp = pd.Timestamp(pulse.get("latest_date")).strftime("%Y%m%d")
    except (TypeError, ValueError, OverflowError):
        stamp = "unknown"
    key = f"ftm_pulse_{tone}_{'motion' if motion else 'still'}_{stamp}"
    animation = "ftm_portfolio_pulse 1.6s ease-in-out 2" if motion else "none"
    st.html(f"""<style>
    .st-key-{key} {{ border-left:3px solid {accent}; background:#111b2b;
        animation:{animation}; }}
    @keyframes ftm_portfolio_pulse {{
        0%,100% {{ box-shadow:0 0 0 0 rgba({channels},0); }}
        45% {{ box-shadow:0 0 0 4px rgba({channels},.13); }}
    }}
    @media (prefers-reduced-motion:reduce) {{
        .st-key-{key} {{ animation:none !important; }}
    }}
    </style>""")
    with st.container(border=True, key=key):
        st.caption("PORTFOLIO PULSE · NEWLY REVEALED RESULTS")
        if tone=="steady":
            count=_number(pulse.get("count"))
            unit="session" if count==1 else "sessions"
            st.markdown(f"**{_count(count)} new {unit}** · Latest {_percent(pulse.get('latest_return'),signed=True)} · Largest daily move {_percent(largest.get('return'),signed=True)} on {_date(largest.get('date'))}")
            costs=_number(pulse.get("costs"))
            cost_text="N/A" if costs is None else f"USD {costs:,.2f}"
            st.caption(f"{_date(pulse.get('start_date'))} → {_date(pulse.get('latest_date'))} · Execution costs {cost_text}. Recorded portfolio results after costs; not market news.")
            return
        headline = str(pulse.get("headline") or "New trading sessions processed")
        st.subheader(escape(headline), anchor=False)
        count = _number(pulse.get("count"))
        unit = "session" if count == 1 else "sessions"
        st.caption(f"{_count(count)} trading {unit} processed · {_date(pulse.get('start_date'))} → {_date(pulse.get('latest_date'))}")
        st.markdown(f"**Latest session:** {_percent(pulse.get('latest_return'),signed=True)}  ·  "
            f"**Largest new daily move:** {_percent(largest.get('return'),signed=True)} on {_date(largest.get('date'))}")
        costs = _number(pulse.get("costs"))
        if costs is not None:
            st.caption(f"Execution costs across these sessions: USD {costs:,.2f}.")
        st.caption("Calculated from recorded portfolio changes, including costs. This is a replay summary, not historical news.")


def render_debrief(debrief: dict) -> None:
    """Render three concise, deterministic observations from a revealed history.

    No recommendation, invented explanation, future observation, or generated
    historical news is introduced. Missing or undefined quantities remain N/A.
    """
    data = _mapping(debrief)
    if not data.get("available", False):
        st.info("Process at least one trading session to see observations based on revealed portfolio results.")
        return
    st.subheader("Your replay, in three observations")
    st.caption(f"As of {_date(data.get('as_of'))} · {_count(data.get('observations'))} recorded daily returns · Calculated from your account history")
    first, second, third = st.columns(3)
    weakest = _mapping(data.get("weakest_day"))
    deepest = _mapping(data.get("deepest_drawdown"))
    recovery = _mapping(data.get("recovery"))
    with first, st.container(border=True, height="stretch"):
        st.caption("TOUGHEST SESSION")
        st.subheader(_percent(weakest.get("return"),signed=True), anchor=False)
        st.write(_date(weakest.get("date")))
        st.caption("Lowest recorded one-session return, after execution costs. It may be positive if every observed session gained.")
    with second, st.container(border=True, height="stretch"):
        st.caption("DEEPEST DRAWDOWN")
        st.subheader(_percent(deepest.get("magnitude")), anchor=False)
        if deepest.get("peak_date") is not None and deepest.get("date") is not None:
            st.caption(f"Peak {_date(deepest.get('peak_date'))} → trough {_date(deepest.get('date'))}")
        status = recovery.get("status")
        if status == "recovered":
            st.write(f"Recovered on {_date(recovery.get('recovery_date'))}: {_count(recovery.get('sessions_to_recover'))} sessions from the peak.")
        elif status == "unrecovered":
            st.write(f"This peak had not been recovered by {_date(data.get('as_of'))}.")
        elif status == "no_drawdown":
            st.write("No loss below a recorded running peak was observed.")
        else:
            st.write("Recovery status: N/A.")
        current = _number(recovery.get("current_drawdown"))
        if current is not None and status != "no_drawdown":
            st.caption(f"Current drawdown: {current:.2%}. Recovery describes the deepest episode, not every later decline.")
    with third, st.container(border=True, height="stretch"):
        st.caption("CASH, COSTS & COMPARISON")
        st.write(f"**Average recorded cash weight:** {_percent(data.get('cash_average_weight'))}")
        costs, drag = _number(data.get("cost_total")), _number(data.get("cost_drag"))
        st.write(f"**Recorded costs:** {'N/A' if costs is None else f'USD {costs:,.2f}'}")
        st.caption(f"Costs / initial capital: {_percent(drag)}. This is a direct cost ratio, not a compounded no-cost counterfactual.")
        relative = _number(data.get("relative_return"))
        benchmark = str(data.get("benchmark_name") or "selected benchmark")
        if relative is None:
            st.write("**Benchmark-relative return:** N/A")
            st.caption("Cash earns 0% interest.")
        else:
            st.write(f"**Cumulative return difference:** {relative*100:+.2f} pp vs {escape(benchmark)}")
            st.caption("Comparison uses the same elapsed evaluation window. Cash earns 0% interest.")
    with st.expander("How these observations are calculated"):
        st.caption("These are deterministic observations from the revealed portfolio ledger and equity series, not generated market news or investment advice. Drawdown uses running peaks including initial capital. Recovery is measured from the deepest episode's peak to its first subsequent recovery close.")
        for note in data.get("notes", []) or []:
            st.caption(escape(str(note)))
