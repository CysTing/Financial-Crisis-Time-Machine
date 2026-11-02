"""Restricted Plotly presentation with explicit, never-future date-axis bounds.

Callers supply revealed data. An optional current_date also enforces a cutoff
inside chart builders, so neither chart arrays nor annotations can cross it.
"""

from __future__ import annotations

from html import escape
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from core.metrics import drawdown_series, rolling_volatility

COLORS = ["#5de4d3", "#6da6ff", "#f5bc63", "#bba2ff", "#f58191", "#79bdb4", "#ced6e2", "#64748b"]


def style(fig: go.Figure, title: str, ytitle: str = "", height: int = 370) -> go.Figure:
    main,separator,subtitle=title.partition(" • ")
    title_text=main+(f"<br><span style='font-size:11px;color:#8296af'>{subtitle}</span>" if separator else "")
    has_legend=len(fig.data)>1 or any(trace.type=="pie" for trace in fig.data)
    legend_items=sum(len(trace.labels) if trace.type=="pie" else 1 for trace in fig.data)
    many_items=legend_items>3
    fig.update_layout(title=dict(text=title_text, x=0, xanchor="left", y=.98, yanchor="top",font=dict(size=15)), template="plotly_dark",
                      paper_bgcolor="#090f1b", plot_bgcolor="#090f1b", font=dict(color="#aabbd0", family="Arial"),
                      colorway=COLORS, height=height+(140 if many_items else 100 if has_legend else 0), margin=dict(l=12,r=20,t=76,b=180 if many_items else 150 if has_legend else 46),
                      showlegend=has_legend,
                      legend=dict(orientation="h",y=-.16,x=0,yanchor="top",xanchor="left",font=dict(size=11),
                                  entrywidth=220,entrywidthmode="pixels",maxheight=120 if many_items else 96), hovermode="x unified",
                      yaxis_title=ytitle)
    fig.update_xaxes(showgrid=False, zeroline=False,automargin=True,tickfont=dict(size=11),title_standoff=14)
    fig.update_yaxes(gridcolor="#202d40", zeroline=False,automargin=True,tickfont=dict(size=11),title_standoff=16)
    return fig


def _empty_message(fig: go.Figure, text: str) -> go.Figure:
    """Explain a genuinely unavailable chart instead of presenting a blank panel."""
    fig.add_annotation(x=.5, y=.5, xref="paper", yref="paper", text=text,
                       showarrow=False, align="center", font=dict(size=12, color="#aabbd0"),
                       bgcolor="#090f1b", borderpad=8)
    return fig


def _date(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)
    return stamp.normalize()


def _restricted_series(series: pd.Series, current_date=None) -> pd.Series:
    result = series.copy()
    result.index = pd.DatetimeIndex(result.index).tz_localize(None).normalize()
    result = result.loc[result.index.notna()]
    if current_date is not None:
        result = result.loc[result.index <= _date(current_date)]
    return result


def _restricted_prices(prices: pd.DataFrame, current_date=None) -> pd.DataFrame:
    result = prices.copy()
    result.index = pd.DatetimeIndex(result.index).tz_localize(None).normalize()
    result = result.loc[result.index.notna()]
    if current_date is not None:
        result = result.loc[result.index <= _date(current_date)]
    return result


def _bound_dates(fig: go.Figure, indices: list[pd.DatetimeIndex], current_date=None) -> go.Figure:
    """Disable date autoranging, including one-point, empty, and all-null traces."""
    dates = pd.DatetimeIndex([date for index in indices for date in index if pd.notna(date)])
    cutoff = _date(current_date) if current_date is not None else None
    if cutoff is not None:
        dates = dates[dates <= cutoff]
    has_dates = len(dates) > 0
    # Fixed historical anchor avoids Plotly's future default range for empty data.
    end = cutoff if cutoff is not None else (dates.max() if has_dates else pd.Timestamp("1970-01-01"))
    start = dates.min() if has_dates else end - pd.Timedelta(days=1)
    if start >= end:
        start = end - pd.Timedelta(days=1)
    fig.update_xaxes(type="date", autorange=False, range=[start.isoformat(), end.isoformat()],
                     rangeslider_visible=False, visible=has_dates or cutoff is not None,
                     tickformat="%d %b<br>%Y" if (end - start).days <= 180 else "%b %Y", nticks=5)
    if (end - start).days <= 8:
        # Daily-close observations must not suggest intraday sampling. Four or
        # fewer calendar tick positions remain legible in narrow columns.
        fig.update_xaxes(dtick=max(1, math.ceil((end - start).days / 3)) * 86400000,
                         tick0=start.isoformat())
    return fig


def equity_chart(
    equities: dict[str, pd.Series],
    events: list[dict] | None = None,
    *,
    current_date=None,
    normalized: bool = False,
    title: str | None = None,
) -> go.Figure:
    """Plot USD equity, or an already-normalized index without currency labels."""
    fig = go.Figure()
    indices = []
    visible_values = []
    for name, raw in equities.items():
        series = _restricted_series(raw, current_date)
        indices.append(series.index)
        visible_values.extend(series.to_numpy(dtype=float))
        hover = "%{y:,.2f} index points<extra>%{fullData.name}</extra>" if normalized else "%{y:$,.2f}<extra>%{fullData.name}</extra>"
        fig.add_trace(go.Scatter(x=series.index.to_pydatetime().tolist(), y=series.tolist(), name=name,
                                 mode="lines+markers" if len(series) == 1 else "lines", line=dict(width=2),
                                 marker=dict(size=6), hovertemplate=hover))
    visible_dates = [date for index in indices for date in index]
    last_visible = _date(current_date) if current_date is not None else (max(visible_dates) if visible_dates else None)
    eligible_events = [event for event in events or [] if last_visible is not None and _date(event["disclosure_date"]) <= last_visible]
    grouped_events = {}
    for event in eligible_events[-5:]:
        grouped_events.setdefault(_date(event["disclosure_date"]), []).append(event)
    for number, (date, grouped) in enumerate(grouped_events.items(), start=1):
        fig.add_shape(type="line", x0=date, x1=date, y0=0, y1=1, yref="paper", line=dict(color="#42546b", dash="dot"))
        headline = "<br>".join(escape(event["headline"]) for event in grouped)
        fig.add_annotation(x=date, y=.98 - ((number - 1) % 3) * .075, yref="paper", text=str(number),
                           hovertext=f"{date:%d %b %Y}<br>{headline}", showarrow=False, textangle=0,
                           font=dict(size=10, color="#f5bc63"), bgcolor="#142337", bordercolor="#42546b", borderpad=3)
    default_title = "Revealed asset prices • normalized to 100" if normalized else "Portfolio / benchmark • net of trading costs"
    fig = style(fig, title or default_title, "Index · first observed price = 100" if normalized else "USD", 420)
    fig.update_yaxes(tickprefix="" if normalized else "$", tickformat=",.1f" if normalized else ",.0f")
    finite_values = np.asarray(visible_values, dtype=float)
    finite_values = finite_values[np.isfinite(finite_values)]
    if len(finite_values):
        center = float((finite_values.min() + finite_values.max()) / 2)
        if finite_values.max() - finite_values.min() < max(abs(center) * .001, 1e-8):
            padding = max(abs(center) * .005, .5 if normalized else 2.0)
            fig.update_yaxes(range=[center - padding, center + padding], nticks=5)
    else:
        _empty_message(fig, "No revealed observations yet")
    return _bound_dates(fig, indices, current_date)


def drawdown_chart(equity: pd.Series, *, current_date=None) -> go.Figure:
    equity = _restricted_series(equity, current_date)
    dd = drawdown_series(equity)
    fig = go.Figure(go.Scatter(x=dd.index.to_pydatetime().tolist(), y=(-dd.abs()).tolist(), fill="tozeroy",
                               line=dict(color="#f58191"), name="Drawdown"))
    fig = style(fig, "Underwater curve", "From running peak")
    fig.update_yaxes(tickformat=".2%" if dd.max()<.02 else ".1%")
    if equity.empty:
        _empty_message(fig, "No revealed equity observations yet")
    return _bound_dates(fig, [equity.index], current_date)


def volatility_chart(equity: pd.Series, window: int = 21, *, current_date=None) -> go.Figure:
    equity = _restricted_series(equity, current_date)
    vol = rolling_volatility(equity, window=window)
    fig = style(go.Figure(go.Scatter(x=vol.index.to_pydatetime().tolist(), y=vol.tolist(), line=dict(color="#f5bc63"))),
                f"Rolling volatility • {window} sessions, {window} returns required", "Annualized")
    fig.update_yaxes(tickformat=".1%")
    if not vol.notna().any():
        _empty_message(fig, f"Volatility not yet available<br>{max(0, len(equity) - 1)} of {window} daily returns observed")
    return _bound_dates(fig, [equity.index], current_date)


def allocation_chart(weights: dict[str, float]) -> go.Figure:
    positive = {k: v for k, v in weights.items() if v > 1e-8}
    fig = go.Figure(go.Pie(labels=list(positive), values=list(positive.values()), hole=.74,
                           textinfo="percent",textposition="inside",marker=dict(colors=COLORS),
                           hovertemplate="%{label}: %{percent}<extra></extra>"))
    fig = style(fig, "Current allocation", height=330)
    return fig if positive else _empty_message(fig, "No nonzero allocations")


def correlation_chart(prices: pd.DataFrame, window: int = 63, *, current_date=None) -> go.Figure:
    prices = _restricted_prices(prices, current_date)
    returns = prices.pct_change(fill_method=None).tail(window)
    corr = returns.corr(min_periods=window)
    fig = go.Figure(go.Heatmap(z=corr.to_numpy(), x=corr.columns, y=corr.index, zmin=-1, zmax=1,
                               colorscale=[[0,"#bd6176"],[.5,"#142337"],[1,"#5de4d3"]],
                               hovertemplate="%{x} / %{y}: %{z:.2f}<extra></extra>"))
    fig = style(fig, f"Asset correlation • trailing {window} returns, pairwise complete", height=380)
    if not corr.notna().any().any():
        _empty_message(fig, f"No valid correlations yet<br>{window} matched returns with variation required")
    return fig


def distribution_chart(equity: pd.Series, *, current_date=None) -> go.Figure:
    equity = _restricted_series(equity, current_date)
    returns = equity.pct_change(fill_method=None).dropna()
    fig = style(go.Figure(go.Histogram(x=returns, nbinsx=45, marker_color="#6da6ff")),
                "One-session return distribution", "Observations")
    fig.update_xaxes(tickformat=".2%")
    if returns.empty:
        _empty_message(fig, "No daily returns yet<br>Advance at least one trading session")
    return fig


def rolling_correlation_chart(prices: pd.DataFrame, window: int = 63, *, current_date=None) -> go.Figure:
    prices = _restricted_prices(prices, current_date)
    fig = go.Figure()
    if "SPY" in prices:
        returns = prices.pct_change(fill_method=None)
        for asset in ("AGG", "TLT", "GLD"):
            if asset in returns:
                correlation = returns.SPY.rolling(window, min_periods=window).corr(returns[asset])
                fig.add_trace(go.Scatter(x=correlation.index.to_pydatetime().tolist(), y=correlation.tolist(),
                                         name=f"SPY / {asset}", mode="lines"))
    fig = style(fig, f"Rolling asset correlations • {window} returns required", "Correlation")
    fig.update_yaxes(range=[-1, 1])
    if not any(pd.Series(trace.y, dtype=float).notna().any() for trace in fig.data):
        _empty_message(fig, f"No valid rolling correlations yet<br>{window} matched returns with variation required")
    return _bound_dates(fig, [prices.index], current_date)


def trading_timeline(trades: pd.DataFrame, *, current_date=None) -> go.Figure:
    trades = trades.copy()
    if not trades.empty:
        trades["execution_date"] = pd.to_datetime(trades["execution_date"])
        if current_date is not None:
            trades = trades.loc[trades.execution_date <= _date(current_date)]
    fig = go.Figure()
    dates = []
    if not trades.empty:
        for asset, group in trades.groupby("asset"):
            index = pd.DatetimeIndex(group.execution_date)
            dates.append(index)
            fig.add_trace(go.Scatter(x=index.to_pydatetime().tolist(), y=[asset] * len(group), mode="markers", name=asset,
                                     marker=dict(size=9, symbol=["triangle-up" if x > 0 else "triangle-down" for x in group.units]),
                                     text=[f"Units {u:,.4f} · cost ${c:,.2f}" for u, c in zip(group.units, group.costs)],
                                     hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra>%{fullData.name}</extra>"))
    fig = style(fig, "Execution timeline • ▲ buy / ▼ sell", height=290)
    if trades.empty:
        _empty_message(fig, "No executed trades yet<br>Targets execute at the next eligible close")
    return _bound_dates(fig, dates, current_date)
