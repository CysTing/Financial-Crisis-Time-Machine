"""Financial Time Machine: root Streamlit entrypoint for local and Cloud use."""

from __future__ import annotations

from html import escape
from pathlib import Path
import math
import pandas as pd
import streamlit as st

from core.backtest_engine import Simulation
from core.data_loader import DataError, load_market_data
from core.event_timeline import visible_events
from core.experience import market_pulse,report_debrief
from core.integrity import build_integrity_snapshot,build_run_passport,passport_json,run_integrity_challenge
from core.metrics import performance_metrics, survival_score, covariance_risk_contributions
from core.reporting import build_html_report, build_csv_report, build_strategy_comparison_csv
from core.scenarios import SCENARIOS
from core.strategies import STRATEGIES, make_strategy
from ui import charts
from ui.styling import apply_theme
from ui.components import render_market_pulse,render_debrief
from ui.integrity import render_integrity

st.set_page_config(page_title="Financial Time Machine", page_icon="◷", layout="wide")
apply_theme()
PAGES = ["Mission Control", "Financial Time Machine", "Strategy Laboratory", "Crisis Analysis", "Final Performance Report"]
SOURCES = ["Historical adjusted prices", "Synthetic offline demonstration"]
ROOT = Path(__file__).resolve().parent
MIN_CAPITAL = 1000.0
MAX_CAPITAL = 1_000_000_000.0


@st.cache_data(ttl=3600, max_entries=12, show_spinner=False)
def market_data(scenario_key: str, source: str, refresh: int = 0):
    scenario = SCENARIOS[scenario_key]
    return load_market_data(scenario.start, scenario.end, source=source,
                            cache_dir=ROOT / "data" / "cache", force_refresh=bool(refresh))


def go(page: str) -> None:
    st.session_state.page = page


def reset_navigation_scroll() -> None:
    """Run once after a logical workspace change, including guarded pages."""
    if st.session_state.get("visible_workspace")==st.session_state.page:
        return
    st.session_state.visible_workspace=st.session_state.page
    st.session_state.navigation_epoch=st.session_state.get("navigation_epoch",0)+1
    epoch=int(st.session_state.navigation_epoch)
    st.html(f'''<script>
      // Navigation {epoch}; trusted static code, no user-provided JavaScript.
      requestAnimationFrame(() => {{
        const pane = document.querySelector('[data-testid="stMain"]');
        if (pane) pane.scrollTo({{top: 0, left: 0, behavior: 'instant'}});
      }});
    </script>''',unsafe_allow_javascript=True)


def clear_session() -> None:
    """Reset only this browser's replay; cached market observations are immutable."""
    for key in list(st.session_state):
        if key.startswith(("survival", "order_", "ticket_", "replay_passport_", "report_passport_")) or key in ("benchmarks", "notice"):
            del st.session_state[key]
    st.session_state.page = "Mission Control"


def load_current_allocation() -> None:
    view=st.session_state.survival.snapshot()
    for row in view["holdings"].itertuples(index=False):
        st.session_state[f"order_target_{row.asset}"]=float(row.weight*100)


def survival_view(sim) -> dict:
    """Attach the user's dated reasoning to the engine's copied safe view."""
    view=sim.snapshot()
    entries=st.session_state.get("survival_journal",[])
    if not entries:
        return view
    decisions=view["decisions"].copy()
    notes={entry["order_id"]:entry["rationale"] for entry in entries if entry.get("order_id") is not None}
    decisions["rationale"]=decisions["order_id"].map(notes).fillna("")
    observations=[dict(order_id=None,signal_date=entry["signal_date"],execution_date=pd.NaT,
        source="journal",status="observation",rationale=entry["rationale"],weights=None)
        for entry in entries if entry.get("order_id") is None]
    if observations:
        decisions=pd.concat([decisions,pd.DataFrame(observations)],ignore_index=True).sort_values("signal_date",kind="stable")
    view["decisions"]=decisions
    return view


def save_reason(sim,order_id=None) -> None:
    note=st.session_state.get("order_rationale","").strip()
    if note:
        st.session_state.setdefault("survival_journal",[]).append(dict(order_id=order_id,signal_date=sim.current_date,rationale=note))
        st.session_state.order_rationale=""


def queue_manual_target(kind: str) -> None:
    """Callbacks validate before mutation and update the screen in a single rerun."""
    sim=st.session_state.survival
    view=sim.snapshot()
    try:
        if kind=="draft":
            target={a:st.session_state.get(f"order_target_{a}",0.)/100 for a in [*sim.assets,"CASH"]}
        elif kind=="cash":
            target={"CASH":1.0}
        else:
            asset=st.session_state.ticket_asset
            action=st.session_state.ticket_side
            amount=st.session_state.ticket_amount
            current=view["history"].iloc[-1]
            if amount<=0:
                raise ValueError("Enter an amount greater than zero. Your pending order has not changed.")
            available=float(current.cash) if action=="Buy" else float(current.get(f"value_{asset}",0))
            if amount>available+1e-8:
                raise ValueError(f"{action} target is USD {amount:,.2f}; available {'cash' if action=='Buy' else asset+' position'} is USD {available:,.2f}. Reduce the amount or use the allocation desk to fund changes across assets.")
            target=dict(zip(view["holdings"].asset,view["holdings"].weight))
            change=amount/current.equity*(1 if action=="Buy" else -1)
            target[asset]+=change
            target["CASH"]-=change
        status=sim.submit_target(target,source="manual")
        save_reason(sim,sim.pending.order_id if sim.pending else None)
        st.session_state.notice=f"{status} Holdings change at execution; both buys and sells incur costs."
    except ValueError as exc:
        st.session_state.order_error=str(exc)


def save_observation() -> None:
    if not st.session_state.get("order_rationale","").strip():
        st.session_state.order_error="Write a short observation before saving it. Your allocation is unchanged."
        return
    save_reason(st.session_state.survival)
    st.session_state.notice="Observation saved at this historical close. Your pending order and holdings are unchanged."


def cancel_order() -> None:
    st.session_state.survival.cancel_pending()
    st.session_state.notice="Pending target cancelled. No trade was executed."


def advance_replay(step: str) -> None:
    sim=st.session_state.survival
    previous=sim.current_date
    sim.advance(step)
    st.session_state.survival_pulse=market_pulse(sim.history,previous,current_date=sim.current_date)
    st.session_state.survival_pulse_seen=False


def fmt(value, kind="percent") -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:.2%}" if kind == "percent" else f"{value:,.2f}"


def money(value: float) -> str:
    """Keep compact KPI cards legible; exact amounts remain in their help text."""
    if abs(value)>=1e9:
        return f"${value/1e9:,.2f}B"
    if abs(value)>=1e6:
        return f"${value/1e6:,.2f}M"
    return f"${value:,.2f}"


def source_banner(synthetic: bool, provider: str = "Yahoo Finance via yfinance") -> None:
    if synthetic:
        st.warning("SYNTHETIC DEMONSTRATION · Fictional prices, not historical market results. Events are factual.")
    else:
        st.caption(f"HISTORICAL REPLAY · {provider} · Adjusted-close total-return approximation · USD")


def weights_editor(assets, key: str, defaults=None, persistent: bool=False) -> dict[str, float]:
    defaults = defaults or {"SPY": 1.0}
    columns = st.columns(4)
    weights = {}
    for i, asset in enumerate([*assets, "CASH"]):
        with columns[i % 4]:
            widget_key=f"{key}_{asset}"
            options=dict(min_value=0.0,max_value=100.0,step=5.0,key=widget_key)
            if persistent:
                st.session_state.setdefault(widget_key,float(defaults.get(asset,0.0)*100))
                options["persist_state"]="session"
            else:
                options["value"]=float(defaults.get(asset,0.0)*100)
            weights[asset] = st.number_input(f"{asset} %",**options) / 100
    total = sum(weights.values())
    st.caption(f"Target total: {total:.2%} · Must equal 100% · Fractional synthetic total-return units")
    return weights


def strategy_controls(key: str, assets=None):
    assets = assets or ["SPY", "QQQ", "AGG", "IEF", "TLT", "GLD", "SHY"]
    name = st.selectbox("Investment strategy", STRATEGIES, key=f"{key}_strategy")
    frequency = "Monthly"
    lookback = None
    cap = 1.0
    weights = {"SPY": 1.0}
    if name in ("Buy and Hold", "User-Defined Allocation"):
        preset = st.selectbox("Starting allocation", ["100% equities (SPY)", "60% SPY / 40% AGG", "Equal weight", "100% cash", "Custom"], key=f"{key}_preset")
        if preset.startswith("60%"):
            weights = {"SPY": .6, "AGG": .4}
        elif preset == "Equal weight":
            weights = {a: 1 / len(assets) for a in assets}
        elif preset == "100% cash":
            weights = {"CASH": 1.0}
        elif preset == "Custom":
            weights = weights_editor(assets, f"{key}_custom")
        if name == "User-Defined Allocation":
            frequency = st.selectbox("Rebalance frequency", ["None", "Monthly", "Quarterly", "Annually"], index=1, key=f"{key}_frequency")
    if name in ("Momentum Rotation", "Inverse Volatility"):
        lookback = st.number_input("Trailing observations (sessions)", min_value=2, max_value=252,
            value=126 if name == "Momentum Rotation" else 63, key=f"{key}_{'momentum' if name=='Momentum Rotation' else 'volatility'}_lookback")
    if name == "Inverse Volatility":
        cap = st.slider("Maximum weight per asset", .1, 1.0, 1.0, .05, key=f"{key}_cap")
    strategy = make_strategy(name, weights=weights, frequency=frequency, lookback=lookback, exposure_cap=cap)
    st.caption(strategy.description)
    return strategy


def session_required():
    if "survival" not in st.session_state:
        st.info("Start a replay from Mission Control to open this page.")
        st.button("Open Mission Control", on_click=go, args=("Mission Control",))
        reset_navigation_scroll()
        st.stop()
    return st.session_state.survival


def benchmarks_for(data, scenario, capital, cost):
    benchmarks = {}
    for name in ("Buy and Hold", "Traditional 60/40"):
        strategy = make_strategy(name)
        try:
            benchmarks["SPY buy & hold" if name == "Buy and Hold" else "60/40 monthly"] = Simulation(
                data.prices, scenario.start, scenario.end, capital=capital, cost_bps=cost, strategy=strategy,
                data_fingerprint=data.fingerprint)
        except ValueError:
            pass  # Eligibility is separately disclosed before starting.
    return benchmarks


def launch_ready_mission() -> None:
    """A callback changes navigation before widgets render, including in AppTest."""
    key,data,strategy,capital,cost=st.session_state.survival_ready
    for state_key in list(st.session_state):
        if state_key.startswith(("order_","ticket_","replay_passport_","report_passport_")) or state_key=="notice":
            del st.session_state[state_key]
    scenario=SCENARIOS[key]
    st.session_state.survival = Simulation(data.prices,scenario.start,scenario.end,capital,cost,
        strategy=strategy,data_fingerprint=data.fingerprint)
    st.session_state.survival_meta = dict(scenario_key=key,synthetic=data.synthetic,
        provider=data.manifest.get("provider","Unknown"),retrieved_at=data.manifest.get("retrieved_at_utc","Unknown"),strategy=strategy.description)
    st.session_state.benchmarks=benchmarks_for(data,scenario,capital,cost)
    st.session_state.survival_journal=[]
    st.session_state.survival_pulse=None
    st.session_state.survival_pulse_seen=False
    st.session_state.survival_showcase=False
    st.session_state.pop("survival_ready",None)
    st.session_state.pop("survival_ready_config",None)
    st.session_state.page="Financial Time Machine"


def launch_showcase() -> None:
    """An explicit offline introduction using the same engine as every replay.

    Never replace an active session. Pre-roll is disclosed, and only the
    resulting restricted snapshot is sent to presentation code.
    """
    if "survival" in st.session_state:
        go("Financial Time Machine")
        return
    try:
        data=market_data("covid","synthetic")
        strategy=make_strategy("Traditional 60/40")
        st.session_state.survival_ready=("covid",data,strategy,100000.,5.)
        launch_ready_mission()
        sim=st.session_state.survival
        sim.advance_to(pd.Timestamp("2020-02-19"))
        st.session_state.survival_showcase=True
    except (DataError,ValueError,OSError) as exc:
        st.session_state.showcase_error=str(exc)


def replay_integrity(view: dict, meta: dict, benchmark_name: str | None, *, key: str) -> None:
    """Evidence is computed from the exact same revealed view used by this page."""
    context={"scenario":SCENARIOS[meta["scenario_key"]].name,
        "source":"synthetic demonstration" if meta["synthetic"] else "historical adjusted prices",
        "provider":meta["provider"],"retrieved_at_utc":meta["retrieved_at"],
        "benchmark":benchmark_name,"application":"Financial Time Machine",
        "showcase":bool(st.session_state.get("survival_showcase",False))}
    evidence=build_integrity_snapshot(view)
    passport=build_run_passport(view,context)
    fingerprint=passport["payload_fingerprint"]
    challenge=st.session_state.get(f"{key}_challenge") if st.session_state.get(f"{key}_challenge_for")==fingerprint else None
    render_integrity(evidence,passport_json(passport),key=key,challenge=challenge,
        on_challenge=challenge_replay_integrity,challenge_args=(view,key,fingerprint))


def challenge_replay_integrity(view: dict, key: str, fingerprint: str) -> None:
    """Corrupt copies, preserving the engine, journal, pending target, and clock."""
    st.session_state[f"{key}_challenge"]=run_integrity_challenge(view)
    st.session_state[f"{key}_challenge_for"]=fingerprint


def reveal_benchmarks(sim):
    """Advance server-side engines only to the replay clock, then copy restricted data."""
    result = {}
    for name, benchmark in st.session_state.benchmarks.items():
        benchmark.advance_to(sim.current_date)
        result[name] = benchmark.result().history.equity
    return result


def selected_benchmark(benchmarks):
    name=st.session_state.get("survival_benchmark_choice")
    if name not in benchmarks:
        name=next(iter(benchmarks),None)
    return name,benchmarks.get(name) if name is not None else None


def show_chart(figure, *, container=None, width="stretch") -> None:
    """All plot exports carry the same explicit source as their displayed run."""
    if st.session_state.page=="Strategy Laboratory":
        synthetic=st.session_state.lab_result["synthetic"]
    else:
        synthetic=st.session_state.survival_meta["synthetic"]
    target=container if container is not None else st
    target.plotly_chart(charts.with_source_label(figure,synthetic),width=width)


def metrics_panel(history, benchmark=None, trades=None, confidence=.95):
    metrics = performance_metrics(history.equity, benchmark=benchmark, trades=trades,confidence=confidence)
    columns = st.columns(4)
    for col, label, key, kind, explanation in zip(columns,
        ["Total return", "Maximum drawdown", "Sharpe ratio", "Annualized volatility"],
        ["total_return", "max_drawdown", "sharpe", "annualized_volatility"],
        ["percent", "percent", "number", "percent"],
        ["Cumulative portfolio return after execution costs.","Largest loss below a previous portfolio peak, including initial capital.",
         "Mean daily excess return / sample standard deviation × sqrt(252). Risk-free assumption: 0% annual.",
         "Sample standard deviation of daily returns × sqrt(252)."]):
        col.metric(label, fmt(metrics.get(key), kind),help=explanation)
    return metrics


def event_feed(events):
    if not events:
        st.caption("No curated events have been disclosed yet. The feed reveals source-backed announcements as the clock advances.")
    for event in reversed(events[-8:]):
        st.html(f'<div class="event"><time>{escape(str(event["disclosure_date"])[:10])} · DISCLOSED</time>'
            f'<h4>{escape(event["headline"])}</h4><p>{escape(event["explanation"])}</p></div>')
        st.link_button("Read primary source ↗", event["source_url"])


def mission():
    st.html('''<div class="hero hero-grid">
      <div><div class="eyebrow">FINANCIAL TIME MACHINE / INTERACTIVE RESEARCH</div>
      <h1>Replay a crisis.<br><span class="accent">Inspect every<br class="hero-break"> decision.</span></h1>
      <p>A portfolio simulator with an inspectable engine. Make decisions using revealed data, feel the impact of a crisis, and trace every dollar back to the ledger.</p>
      <span class="stamp">3 CRISES</span> &nbsp; <span class="stamp">6 STRATEGIES</span> &nbsp; <span class="stamp">7 ASSETS + CASH</span></div>
      <div class="engine-trace" aria-label="Illustrative execution sequence">
        <div class="eyebrow">THE ENGINE, IN ONE TRADE</div>
        <div class="trace-row"><span class="trace-node">01</span><div><b>Observe. Then decide.</b><p>Close t · Queue a 100% asset target</p></div></div>
        <div class="trace-row"><span class="trace-node">02</span><div><b>Execute at the next close.</b><p>Close t+1 · Size units after costs</p></div></div>
        <div class="trace-row"><span class="trace-node">03</span><div><b>Account for every dollar.</b><p>USD 99,950.02 invested + 49.98 costs<br>= USD 100,000.00 starting cash</p></div></div>
        <div class="trace-footer">ILLUSTRATIVE INITIAL BUY · 5 BPS · NO PRICE MOVE<br>Reconciled accounting. Revealed data. Reproducible evidence.</div>
      </div></div>''')
    with st.container(border=True,key="showcase_entry"):
        intro,action=st.columns([2.4,1],vertical_alignment="center")
        with intro:
            st.markdown("**A crisis. A portfolio. Every decision on record.**")
            st.caption("Interactive offline showcase · Explicit synthetic prices · No download or API key")
        with action:
            if "survival" in st.session_state:
                st.button("Resume your replay",key="resume_replay",type="primary",width="stretch",on_click=go,args=("Financial Time Machine",))
            else:
                st.button("Start the crisis showcase",key="start_showcase",type="primary",width="stretch",on_click=launch_showcase)
        st.caption("COVID scenario · USD 100,000 · 60/40 monthly · 5 bps per side · Pre-rolled from January 2019 to 19 February 2020. Fictional prices; factual event sources.")
        if st.session_state.get("showcase_error"):
            st.error(st.session_state.pop("showcase_error"))
    st.markdown('<a class="mission-anchor" href="#configure-your-mission">Or configure your own mission ↓</a>',unsafe_allow_html=True)
    st.subheader("Choose your point of entry")
    for col, scenario in zip(st.columns(3), SCENARIOS.values()):
        with col, st.container(border=True,height="stretch"):
            st.caption(f"{scenario.start[:4]} — {scenario.end[:4]}")
            st.markdown(f"### {scenario.name}")
            st.write(scenario.description)
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.subheader("Configure your mission")
        key = st.selectbox("Historical scenario", list(SCENARIOS), format_func=lambda k: SCENARIOS[k].name, key="mission_scenario")
        source = st.radio("Market data", SOURCES, key="mission_source")
        if source == SOURCES[1]:
            source_banner(True)
        a,b = st.columns(2)
        capital = a.number_input("Initial capital (USD)", min_value=MIN_CAPITAL, max_value=MAX_CAPITAL, value=100000., step=10000.)
        cost = b.number_input("Cost per side (basis points)", min_value=0., max_value=100., value=5., step=1.)
        strategy = strategy_controls("mission")
        configuration=(key,source,float(capital),float(cost),repr(strategy))
        if "survival_ready" in st.session_state and st.session_state.get("survival_ready_config")!=configuration:
            st.session_state.pop("survival_ready",None)
            st.session_state.pop("survival_ready_config",None)
            st.info("Mission settings changed. Initialize again to validate the updated configuration.")
        st.caption("Changing mission settings takes effect only when you start a fresh session.")
        if st.button("Initialize mission →", type="primary", width="stretch"):
            st.session_state.pop("survival_ready",None)
            st.session_state.pop("survival_ready_config",None)
            try:
                with st.spinner("Validating calendar, prices, and eligible assets…"):
                    data = market_data(key, "synthetic" if source == SOURCES[1] else "historical", st.session_state.get("data_refresh",0))
                    strategy.validate_universe(list(data.eligible))
                    st.session_state.survival_ready = (key,data,strategy,capital,cost)
                    st.session_state.survival_ready_config=configuration
            except (DataError,ValueError,OSError) as exc:
                st.error(str(exc))
                st.info("Retry genuine data, or explicitly select the Synthetic offline demonstration above. No synthetic prices are substituted automatically.")
        if st.button("Retry provider / refresh validated cache"):
            st.session_state.data_refresh = st.session_state.get("data_refresh",0)+1
            market_data.clear()
            st.session_state.pop("survival_ready",None)
            st.info("Cache refresh requested. Initialize the mission again.")
        if "survival_ready" in st.session_state:
            key,data,strategy,capital,cost = st.session_state.survival_ready
            st.success("Preflight validated. Review the eligible universe before launching.")
            st.markdown(f"**{SCENARIOS[key].name}** · USD {capital:,.0f} · {cost:g} bps per side")
            st.caption(f"{strategy.name} · {source}. {strategy.description}")
            st.write("Eligible: " + ", ".join(data.eligible) + " + CASH")
            if data.exclusions:
                st.warning("Excluded: " + "; ".join(f"{a}: {why}" for a,why in data.exclusions.items()))
            st.caption(f"Provider: {data.manifest.get('provider','synthetic')} · Retrieved: {data.manifest.get('retrieved_at_utc','see manifest')} · Fingerprint: {data.fingerprint[:12]}")
            warnings=data.manifest.get("validation_warnings",[])
            if warnings:
                st.warning(f"{len(warnings)} validation notice(s) recorded. Dated diagnostics remain private until completion; exclusions above are fixed before launch.")
            if data.manifest.get("cache_used"):
                st.info(f"Using a validated genuine local cache, originally retrieved {data.manifest.get('retrieved_at_utc','at the recorded retrieval date')}. No invented prices were substituted.")
            if strategy.name in ("Moving Average Trend","Momentum Rotation","Inverse Volatility"):
                required=strategy.lookback+(0 if strategy.name=="Moving Average Trend" else 1)
                st.caption(f"Indicator warm-up requires {required} trailing closes. The first decision uses only observations available at the first scenario close; insufficient history stays in cash.")
            st.button("Launch validated mission →",type="primary",width="stretch",on_click=launch_ready_mission)
    with right:
        st.subheader("The rules of time travel")
        for number,title,text in [("01","Observe the close","Charts and events reveal only what your replay clock has reached."),
                ("02","Make your decision","Target allocations become pending orders, sized at the next trading close."),
                ("03","Live with the outcome","Advance one day, five sessions, or to the end of the next month. Every intervening session counts.")]:
            with st.container(border=True):
                st.caption(number)
                st.markdown(f"**{title}**")
                st.write(text)
        st.caption("Educational replay, not a blind prediction experiment: scenario names disclose historical context. Cash earns 0%; SHY is a traded ETF. No macroeconomic observations are shown because release-vintage integrity is not available.")


def replay():
    sim = session_required()
    view = survival_view(sim)
    meta = st.session_state.survival_meta
    source_banner(meta["synthetic"],meta["provider"])
    st.caption("SURVIVAL / REVEALED DATA ONLY")
    st.title(sim.current_date.strftime("%d %B %Y"))
    scenario=SCENARIOS[meta["scenario_key"]]
    st.caption(f"{scenario.name} · Observed closes: {len(view['history']):,} · USD {sim.capital:,.0f} starting capital")
    bench = reveal_benchmarks(sim)
    benchmark_name,primary = selected_benchmark(bench)
    if benchmark_name:
        st.caption(f"Benchmark: {benchmark_name} · Same dates, capital, and costs")
    history = view["history"]
    current = history.iloc[-1]
    a,b,c,d = st.columns(4)
    a.metric("Portfolio value",money(current.equity),f"{current.equity-sim.capital:+,.2f} USD",help=f"Exact portfolio value: USD {current.equity:,.2f}")
    b.metric("Available cash",money(current.cash),help=f"Exact cash balance: USD {current.cash:,.2f}")
    c.metric("Return to date",fmt(current.equity/sim.capital-1))
    m=performance_metrics(history.equity,benchmark=primary,trades=view["ledger"])
    d.metric("Maximum drawdown",fmt(m.get("max_drawdown")))
    st.caption("Adjusted close units · Costs included · Cash interest 0% · Risk-free assumption 0% · No external cash flows")
    if st.session_state.get("survival_showcase"):
        st.caption("SHOWCASE · Pre-rolled from 2 Jan 2019. Try Next month-end, then open Replay integrity below the chart. The Final Performance Report offers a matching interim report.")
    if st.session_state.get("notice"):
        st.success(st.session_state.pop("notice"))
    if st.session_state.get("order_error"):
        st.error(st.session_state.pop("order_error"))
    if sim.complete:
        st.success("Replay complete. Crisis Analysis and the final survival report are unlocked.")
        st.button("Open final performance report →",on_click=go,args=("Final Performance Report",))
    else:
        columns=st.columns(3)
        for col,step,label in zip(columns,["day","week","month"],["Next trading day","Next 5 sessions","Next month-end"]):
            destination=sim.destination(step)
            col.button(f"{label} → {destination:%d %b %Y}",width="stretch",key=f"advance_{step}",on_click=advance_replay,args=(step,))
    pulse=st.session_state.get("survival_pulse")
    if pulse:
        render_market_pulse(pulse,animate=st.session_state.get("motion_enabled",True) and not st.session_state.get("survival_pulse_seen",False))
        st.session_state.survival_pulse_seen=True
    events=visible_events(sim.current_date,start=history.index.min())
    show_chart(charts.equity_chart({"Your portfolio":history.equity,**bench},events),width="stretch")
    replay_integrity(view,meta,benchmark_name,key="replay_passport")
    left,right=st.columns([1.7,1],gap="large")
    with left:
        st.subheader("Allocation desk")
        st.caption(meta["strategy"])
        pending=view.get("pending")
        if pending:
            st.info(f"Pending {pending['source']} target · Signal {pd.Timestamp(pending['signal_date']):%Y-%m-%d} · Execution {pd.Timestamp(pending['execution_date']):%Y-%m-%d}. A manual order replaces this target.")
            st.caption("Pending allocation: "+" · ".join(f"{a} {w:.1%}" for a,w in pending["weights"].items() if w>0))
            st.button("Cancel pending target",disabled=sim.complete,on_click=cancel_order)
        if not sim.complete:
            st.caption("Allocation inputs are a persistent draft. Review the weights before queuing a replacement.")
            st.button("Load current allocation into draft",on_click=load_current_allocation)
            defaults = pending["weights"] if pending else dict(zip(view["holdings"].asset,view["holdings"].weight))
            if not pending:
                defaults["CASH"]=float(current.weight_CASH)
            with st.container(border=True):
                weights=weights_editor(sim.assets,"order_target",defaults,persistent=True)
                st.text_area("Decision journal · why this allocation?",placeholder="Optional: capture your reasoning before the next close reveals the outcome.",max_chars=500,key="order_rationale",persist_state="session",height=90)
                st.button("Queue target for next close",type="primary",on_click=queue_manual_target,args=("draft",))
                st.button("Save observation without trading",on_click=save_observation)
            with st.expander("Buy / sell a single asset"):
                st.caption("The ticket adjusts a target weight using current equity. Units are sized at the next close; the final traded notional may differ.")
                asset=st.selectbox("Asset",sim.assets,key="ticket_asset")
                action=st.radio("Side",["Buy","Sell"],horizontal=True,key="ticket_side")
                amount=st.number_input("Target change (USD at current equity)",min_value=0.,value=1000.,step=100.,key="ticket_amount")
                st.button("Queue single-asset target",on_click=queue_manual_target,args=("ticket",))
            st.button("Queue 100% cash",on_click=queue_manual_target,args=("cash",))
        holdings=view["holdings"].copy()
        holdings["weight"]*=100
        st.dataframe(holdings,hide_index=True,width="stretch",column_config={
            "asset":"Asset","units":st.column_config.NumberColumn("Units",format="%.4f"),
            "price":st.column_config.NumberColumn("Adjusted unit price",format="$%.2f"),
            "value":st.column_config.NumberColumn("Position value",format="$%.2f"),
            "weight":st.column_config.NumberColumn("Weight",format="%.2f%%")})
        with st.expander("Allocation comparison and decision log"):
            weight_cols=[c for c in history if c.startswith("weight_")]
            st.dataframe(history[weight_cols].tail(15),width="stretch")
            st.dataframe(view["decisions"],width="stretch",hide_index=True)
    with right:
        show_chart(charts.allocation_chart({a:float(current.get(f"weight_{a}",0)) for a in [*sim.assets,"CASH"]}),width="stretch")
        st.subheader("Dispatches from history")
        event_feed(events)
    with st.expander("Revealed asset prices · normalized to 100 at first available observation"):
        prices=view["prices"]
        normalized=prices.apply(lambda column: column / column.dropna().iloc[0] * 100 if column.notna().any() else column)
        show_chart(charts.equity_chart({a:normalized[a].dropna() for a in prices},normalized=True,current_date=sim.current_date),width="stretch")
    with st.expander("Execution ledger"):
        st.dataframe(view["ledger"],hide_index=True,width="stretch")


def laboratory():
    st.caption("RETROSPECTIVE RESEARCH / SEPARATE FROM SURVIVAL")
    st.title("Strategy Laboratory")
    st.write("Compare systematic rules over the same evaluation window, capital, execution delay, and costs.")
    key=st.selectbox("Research scenario",list(SCENARIOS),format_func=lambda k:SCENARIOS[k].name,key="lab_scenario")
    source=st.radio("Research data",SOURCES,key="lab_source")
    st.caption(f"Requested data: {source}. Saved results below retain the source used when they were run.")
    # Recover a session created before the research capital bound was added.
    # Both modes share the same numerically supported USD capital range.
    previous_capital=st.session_state.get("lab_capital",100000.)
    if not isinstance(previous_capital,(int,float)) or not math.isfinite(previous_capital) or not MIN_CAPITAL<=previous_capital<=MAX_CAPITAL:
        st.session_state.lab_capital=100000.
        st.warning("Research capital must be between USD 1,000 and USD 1 billion. The input has been reset to USD 100,000.")
    saved_capital=st.session_state.get("lab_result",{}).get("capital",100000.)
    if not math.isfinite(saved_capital) or not MIN_CAPITAL<=saved_capital<=MAX_CAPITAL:
        st.session_state.pop("lab_result",None)
        st.warning("The saved research run exceeded the supported capital range. Run research again with valid settings.")
    a,b=st.columns(2)
    capital=a.number_input("Research capital (USD)",min_value=MIN_CAPITAL,max_value=MAX_CAPITAL,value=100000.,key="lab_capital")
    cost=b.number_input("Research cost per side (bps)",min_value=0.,max_value=100.,value=5.,key="lab_cost")
    chosen=st.multiselect("Strategies to compare",STRATEGIES,default=list(STRATEGIES),key="lab_chosen")
    with st.expander("Custom allocation and rebalancing",expanded=False):
        st.caption("These weights apply to both Buy and Hold and User-Defined Allocation. The other four rules determine their own targets.")
        weights=weights_editor(["SPY","QQQ","AGG","IEF","TLT","GLD","SHY"],"lab_weights",{"SPY":.6,"AGG":.4})
        frequency=st.selectbox("Custom rebalance frequency",["None","Monthly","Quarterly","Annually"],index=1,key="lab_frequency")
    a,b,c=st.columns(3)
    momentum=a.number_input("Momentum lookback",min_value=2,max_value=252,value=126,key="lab_momentum")
    vol=b.number_input("Volatility lookback",min_value=2,max_value=252,value=63,key="lab_vol")
    cap=c.slider("Inverse-vol per-asset cap",.1,1.,1.,.05,key="lab_cap")
    if st.button("Run comparative research →",type="primary"):
        try:
            if not chosen:
                raise ValueError("Choose at least one strategy.")
            with st.spinner("Processing every exchange session…"):
                data=market_data(key,"synthetic" if source==SOURCES[1] else "historical")
                scenario=SCENARIOS[key]
                results={}
                exclusions={}
                for name in chosen:
                    strategy=make_strategy(name,weights=weights,frequency=frequency,
                        lookback=momentum if name=="Momentum Rotation" else vol if name=="Inverse Volatility" else None,exposure_cap=cap)
                    try:
                        results[name]=Simulation(data.prices,scenario.start,scenario.end,capital,cost,strategy=strategy,data_fingerprint=data.fingerprint).run()
                    except ValueError as exc:
                        exclusions[name]=str(exc)
                benchmarks=benchmarks_for(data,scenario,capital,cost)
                benchmark_results={name:sim.run() for name,sim in benchmarks.items()}
                st.session_state.lab_result=dict(results=results,benchmarks=benchmark_results,synthetic=data.synthetic,
                    exclusions=exclusions,scenario=key,manifest=data.manifest,weights=weights,capital=capital,cost=cost,frequency=frequency)
        except (ValueError,DataError,OSError) as exc:
            st.error(str(exc))
    if "lab_result" in st.session_state:
        saved=st.session_state.lab_result
        source_banner(saved["synthetic"])
        st.caption(f"Displayed run: {SCENARIOS[saved['scenario']].name}. Input changes take effect after Run comparative research.")
        if "weights" in saved:
            st.caption("Buy-and-hold / custom starting target: "+" · ".join(f"{a} {w:.1%}" for a,w in saved["weights"].items() if w>0)+f" · Cost {saved.get('cost',5):g} bps per side")
        else:
            st.caption("Rerun research to record the allocation and cost summary for this earlier saved result.")
        for name,why in saved["exclusions"].items():
            st.warning(f"{name} excluded: {why}")
        if not saved["results"]:
            st.info("No compatible strategies completed. Review the exclusions.")
            return
        all_equities={name:result.history.equity for name,result in saved["results"].items()}
        all_equities.update({f"Benchmark · {name}":result.history.equity for name,result in saved["benchmarks"].items()})
        unique=[]
        coincident=[]
        for name,equity in all_equities.items():
            match=next((previous for previous in unique if equity.equals(all_equities[previous])),None)
            if match is None:
                unique.append(name)
            else:
                coincident.append(f"{name} = {match}")
        run_key=f"lab_chart_{saved['scenario']}_{len(saved['results'])}_{saved.get('cost',5)}"
        visible_names=st.multiselect("Display on chart",list(all_equities),default=unique,key=run_key)
        if not visible_names:
            st.info("Choose one or more series to display. All strategy results remain in the comparison table.")
        else:
            comparison_figure=charts.equity_chart({name:all_equities[name] for name in visible_names})
            labels={"Buy and Hold":"Buy & hold","Traditional 60/40":"60/40 monthly","Moving Average Trend":"Trend · 200D","Momentum Rotation":"Momentum","Inverse Volatility":"Inverse volatility","User-Defined Allocation":"Custom allocation","Benchmark · SPY buy & hold":"SPY benchmark","Benchmark · 60/40 monthly":"60/40 benchmark"}
            for trace in comparison_figure.data:
                full_name=trace.name
                trace.name=labels.get(full_name,full_name)
                trace.hovertemplate=f"%{{y:$,.2f}}<extra>{full_name}</extra>"
            show_chart(comparison_figure,width="stretch")
        if coincident:
            st.caption("Identical curves are hidden initially to keep the chart readable; select them above to display. "+"; ".join(coincident))
        benchmark=next(iter(saved["benchmarks"].values()),None)
        rows={name:performance_metrics(result.history.equity,benchmark=benchmark.history.equity if benchmark else None,trades=result.ledger) for name,result in saved["results"].items()}
        table=pd.DataFrame(rows).T
        summary=table[["total_return","cagr","max_drawdown","sharpe","annualized_volatility","excess_return","total_costs","gross_turnover"]].copy()
        summary["excess_return"]=summary["excess_return"]*100
        st.subheader("Strategy comparison")
        st.dataframe(summary,width="stretch",height=(len(summary)+1)*35+3,column_config={
            "_index":st.column_config.TextColumn("Strategy",width="medium"),
            "total_return":st.column_config.NumberColumn("Total return",format="percent",help="Cumulative return after trading costs."),
            "cagr":st.column_config.NumberColumn("CAGR",format="percent",help="Annualized using actual elapsed calendar time."),
            "max_drawdown":st.column_config.NumberColumn("Max drawdown",format="percent",help="Positive loss below the running equity peak."),
            "sharpe":st.column_config.NumberColumn("Sharpe",format="%.2f",help="252-session annualization; 0% risk-free assumption."),
            "annualized_volatility":st.column_config.NumberColumn("Volatility",format="percent",help="Annualized sample standard deviation of daily returns."),
            "excess_return":st.column_config.NumberColumn("Excess return (pp)",format="%.2f pp",help="Portfolio minus benchmark cumulative return, in percentage points."),
            "total_costs":st.column_config.NumberColumn("Costs (USD)",format="dollar"),
            "gross_turnover":st.column_config.NumberColumn("Turnover",format="%.2fx",help="Gross traded notional / average daily equity, including initial purchases."),
        })
        research_benchmark=next(iter(saved["benchmarks"]),"No eligible benchmark")
        st.caption(f"Net results over matching evaluation windows. Excess return is a percentage-point difference against {research_benchmark}. Turnover includes initial purchases and both trade sides.")
        with st.expander("Full metrics and assumptions"):
            from core.reporting import formatted_metric_table
            detail=st.selectbox("Detailed strategy",list(rows),key="lab_metric_detail")
            st.dataframe(formatted_metric_table(rows[detail]),hide_index=True,width="stretch",height=400,
                column_config={"Metric":st.column_config.TextColumn(width="large"),"Value":st.column_config.TextColumn(width="medium")})
            st.caption("N/A means insufficient observations or an undefined ratio. CAGR and daily risk statistics can be unstable over short periods; 0% risk-free and minimum acceptable return assumptions are explicit.")
        evaluation=next(iter(saved["results"].values())).history.index
        comparison_csv=build_strategy_comparison_csv(table,"synthetic" if saved["synthetic"] else "historical adjusted prices",
            SCENARIOS[saved["scenario"]].name,research_benchmark,evaluation[0],evaluation[-1])
        st.download_button("Download strategy comparison CSV",comparison_csv,"strategy-comparison.csv","text/csv")
        selection=st.selectbox("Inspect strategy",list(saved["results"]),key="lab_inspect")
        selected=saved["results"][selection]
        a,b=st.columns(2)
        show_chart(charts.drawdown_chart(selected.history.equity),container=a,width="stretch")
        show_chart(charts.volatility_chart(selected.history.equity),container=b,width="stretch")
        show_chart(charts.trading_timeline(selected.ledger),width="stretch")
        show_chart(charts.rolling_correlation_chart(selected.prices),width="stretch")
        with st.expander("Provenance and validation manifest"):
            st.json(saved["manifest"])


def crisis_analysis():
    sim=session_required()
    source_banner(st.session_state.survival_meta["synthetic"])
    st.title("Crisis Analysis")
    if not sim.complete:
        st.info("Full-period crisis analysis unlocks when your Survival replay is complete. The replay page shows risk measures using revealed observations.")
        return
    view=sim.snapshot()
    history=view["history"]
    metrics_panel(history,trades=view["ledger"])
    a,b=st.columns(2)
    show_chart(charts.drawdown_chart(history.equity),container=a,width="stretch")
    show_chart(charts.volatility_chart(history.equity),container=b,width="stretch")
    a,b=st.columns(2)
    show_chart(charts.correlation_chart(view["prices"]),container=a,width="stretch")
    show_chart(charts.distribution_chart(history.equity),container=b,width="stretch")
    st.subheader("Crisis window comparison")
    scenario=SCENARIOS[st.session_state.survival_meta["scenario_key"]]
    all_equities={"Your portfolio":history.equity,**reveal_benchmarks(sim)}
    rows=[]
    for name,equity in all_equities.items():
        window=equity.loc[scenario.crisis_start:scenario.crisis_end]
        if len(window)>1:
            rows.append({"Portfolio":name,"Window start":window.index[0].date(),"Window end":window.index[-1].date(),
                         "Window return":window.iloc[-1]/window.iloc[0]-1,"Maximum drawdown":performance_metrics(window)["max_drawdown"]})
    stress_table=pd.DataFrame(rows)
    for column in ["Window return","Maximum drawdown"]:
        if column in stress_table:
            stress_table[column]*=100
    st.dataframe(stress_table,hide_index=True,width="stretch",column_config={
        "Window return":st.column_config.NumberColumn("Window return",format="%.2f%%"),
        "Maximum drawdown":st.column_config.NumberColumn("Maximum drawdown",format="%.2f%%")})
    st.caption("Descriptive fixed historical stress windows; returns use the actual portfolio positions present during that period.")
    st.subheader("P&L attribution · reconciliation in USD")
    contributions=view["contributions"].sum()
    st.dataframe(contributions.rename("Cumulative P&L (USD)").to_frame(),width="stretch",column_config={
        "Cumulative P&L (USD)":st.column_config.NumberColumn("Cumulative P&L (USD)",format="$%.2f")})
    st.caption(f"Contributions sum USD {contributions.sum():,.2f}; equity change USD {history.equity.iloc[-1]-sim.capital:,.2f}. Cash interest is zero. COSTS is a separate negative contribution.")
    st.subheader("Covariance-based risk attribution")
    prices=view["prices"]
    returns=prices.pct_change(fill_method=None).tail(63)
    weights={a:float(history.iloc[-1].get(f"weight_{a}",0)) for a in sim.assets}
    try:
        risk=covariance_risk_contributions(returns,{**weights,"CASH":float(history.iloc[-1].weight_CASH)})
        risk=risk.rename(columns={"weight":"Weight (%)","volatility_contribution":"Annual volatility contribution (pp)","fraction_of_volatility":"Share of volatility (%)"})*100
        st.dataframe(risk,width="stretch",column_config={
            "Weight (%)":st.column_config.NumberColumn("Weight",format="%.2f%%"),
            "Annual volatility contribution (pp)":st.column_config.NumberColumn("Volatility contribution",format="%.3f pp"),
            "Share of volatility (%)":st.column_config.NumberColumn("Share of volatility",format="%.2f%%")})
    except (ValueError,TypeError) as exc:
        st.info(str(exc))
    st.caption("Trailing 63 returns, current weights held constant for attribution; sample covariance. This is a risk estimate, distinct from realized P&L attribution. Zero volatility is undefined.")
    confidence=st.slider("Historical tail-risk confidence",min_value=.90,max_value=.99,value=.95,step=.01,key="crisis_tail_confidence")
    tail=performance_metrics(history.equity,trades=view["ledger"],confidence=confidence)
    a,b=st.columns(2)
    a.metric(f"One-session historical VaR · {confidence:.0%}",fmt(tail["var"]))
    b.metric(f"Expected shortfall · {confidence:.0%}",fmt(tail["expected_shortfall"]))
    st.caption("Empirical loss distribution; linear quantile and inclusive tail mean, at least 20 returns. A negative loss estimate describes sample gains. Estimates are not forecasts.")
    if len(prices.columns)>1:
        st.subheader("Rolling asset correlation")
        show_chart(charts.rolling_correlation_chart(prices,window=63),width="stretch")


def report():
    sim=session_required()
    view=survival_view(sim)
    meta=st.session_state.survival_meta
    source_banner(meta["synthetic"],meta["provider"])
    st.title("Final Performance Report" if sim.complete else "Interim Performance Report")
    if not sim.complete:
        st.info(f"PARTIAL SESSION · Through {sim.current_date:%Y-%m-%d}. Final score and full-period analysis unlock at completion.")
    bench=reveal_benchmarks(sim)
    benchmark_name,primary=selected_benchmark(bench)
    final=view["history"].equity.iloc[-1]
    st.subheader("Your crisis outcome" if sim.complete else "Your replay so far")
    outcome,comparison=st.columns(2)
    outcome.metric("Final portfolio value" if sim.complete else "Portfolio value to date",f"USD {final:,.2f}",
        f"{final-sim.capital:+,.2f} USD net profit / loss")
    if primary is not None:
        comparison.metric(f"Benchmark · {benchmark_name}",f"USD {primary.iloc[-1]:,.2f}")
        verb="finished" if sim.complete else "stands"
        st.write(f"Your portfolio {verb} at **USD {final:,.2f}**, compared with **USD {primary.iloc[-1]:,.2f}** for **{benchmark_name}**" +
            ("." if sim.complete else " over the matching partial window."))
    st.caption(f"Retained {final/sim.capital:.2%} of initial capital. The educational score gives 35% weight to capital preservation and 35% to drawdown resilience; a high survival score does not by itself imply strong growth.")
    with st.expander("Advanced report settings"):
        confidence=st.selectbox("Report tail-risk confidence",[.90,.95,.99],index=1,format_func=lambda value:f"{value:.0%}",key="report_confidence")
        st.caption("This changes the historical VaR and Expected Shortfall estimates. It does not change portfolio results or the survival score.")
    metrics=performance_metrics(view["history"].equity,benchmark=primary,trades=view["ledger"],confidence=confidence)
    events=visible_events(sim.current_date,start=view["history"].index.min())
    metadata={**view["config"],**meta,"data_source":"synthetic" if meta["synthetic"] else "historical adjusted prices",
        "scenario":SCENARIOS[meta["scenario_key"]].name,"fingerprint":view["config"].get("data_fingerprint",""),
        "cash_interest_annual":0,"risk_free_annual":0,"benchmark_name":benchmark_name}
    html=build_html_report(view["history"],view["ledger"],metadata,metrics,events=events,
        contributions=view["contributions"],benchmark=primary,current_date=sim.current_date,completed=sim.complete,
        decisions=view["decisions"],confidence=confidence)
    csv=build_csv_report(view["history"],view["ledger"],metadata,current_date=sim.current_date,completed=sim.complete,
        benchmark=primary,decisions=view["decisions"])
    a,b=st.columns(2)
    a.download_button("Download self-contained HTML report",html,"financial-time-machine-report.html","text/html",width="stretch")
    b.download_button("Download replay CSV",csv,"financial-time-machine-replay.csv","text/csv",width="stretch")
    a,b=st.columns(2)
    a.download_button("Download trade ledger CSV",build_csv_report(view["history"],view["ledger"],metadata,
        current_date=sim.current_date,completed=sim.complete,kind="trades"),"financial-time-machine-trades.csv","text/csv",width="stretch")
    b.download_button("Download decision history CSV",build_csv_report(view["history"],metadata=metadata,
        current_date=sim.current_date,completed=sim.complete,kind="decisions",decisions=view["decisions"]),
        "financial-time-machine-decisions.csv","text/csv",width="stretch")
    replay_integrity(view,meta,benchmark_name,key="report_passport")
    metrics_panel(view["history"],primary,view["ledger"],confidence=confidence)
    if sim.complete:
        st.subheader("DID YOUR PORTFOLIO SURVIVE THE CRISIS?")
        score=survival_score(metrics.get("total_return"),metrics.get("max_drawdown"),metrics.get("sharpe"),metrics.get("excess_return"))
        st.metric(score["category"],f"{score['score']:.1f} / 100")
        component_table=pd.DataFrame({"Component score / 100":score["components"],"Weight":score["weights"]})
        component_table.index=[name.replace("_"," ").title() for name in component_table.index]
        component_table["Weight"]*=100
        st.dataframe(component_table,width="stretch",column_config={
            "Component score / 100":st.column_config.NumberColumn("Component score / 100",format="%.1f"),
            "Weight":st.column_config.NumberColumn("Weight",format="%.0f%%")})
        st.caption("Educational custom score; not an institutional investment rating. Capital preservation is a single capped reward, which deliberately favors preservation over unlimited growth.")
        with st.expander("Transparent survival score formula"):
            st.write("Weighted score: 35% capital preservation + 35% drawdown resilience + 15% risk-adjusted performance + 15% benchmark-relative performance. Each component is bounded from 0 to 100.")
            st.code("preservation = 100 × clip(1 + total_return, 0, 1)\ndrawdown = 100 × clip(1 − max_drawdown / 0.60, 0, 1)\nrisk_adjusted = 100 × clip((Sharpe + 1) / 3, 0, 1)\nrelative = 100 × clip(0.5 + excess_return / 0.40, 0, 1)",language=None)
            st.write("Missing components receive neutral 50%; weights are not redistributed. Categories: excellent ≥85, strong ≥70, moderate ≥50, severe damage ≥25, collapse below 25. Returns are decimal fractions; drawdown is a positive loss.")
    debrief=report_debrief(view["history"],benchmark=primary,current_date=sim.current_date)
    debrief["benchmark_name"]=benchmark_name
    render_debrief(debrief)
    show_chart(charts.equity_chart({"Your portfolio":view["history"].equity,**bench}),width="stretch")
    from core.reporting import formatted_metric_table
    st.subheader("Performance detail")
    st.dataframe(formatted_metric_table(metrics),hide_index=True,width="stretch",height=440,
        column_config={"Metric":st.column_config.TextColumn(width="large"),"Value":st.column_config.TextColumn(width="medium")})
    st.caption("N/A indicates insufficient observations or an undefined ratio. Short-period annualizations are unstable. VaR and Expected Shortfall are empirical one-session loss estimates, not forecasts.")
    with st.expander("Investment decisions and execution ledger"):
        st.dataframe(view["decisions"],width="stretch",hide_index=True)
        st.dataframe(view["ledger"],width="stretch",hide_index=True)
    show_chart(charts.trading_timeline(view["ledger"],current_date=sim.current_date),width="stretch")
    event_feed(events)


with st.sidebar:
    st.html('<div class="eyebrow">◷ &nbsp; FTM / RESEARCH</div><div class="brand">FINANCIAL<br>TIME MACHINE<span class="accent">.</span></div><div class="smallcaps">WOULD YOUR STRATEGY SURVIVE 2008?</div>')
    st.radio("Workspace",PAGES,key="page",label_visibility="collapsed")
    st.divider()
    if "survival" in st.session_state:
        sim=st.session_state.survival
        st.caption("REPLAY CLOCK")
        st.markdown(f"**{sim.current_date:%d %b %Y}**")
        st.caption("COMPLETE" if sim.complete else "IN PROGRESS · REVEALED DATA ONLY")
        meta=st.session_state.survival_meta
        scenario=SCENARIOS[meta["scenario_key"]]
        first=sim.history.index[0]
        span=(pd.Timestamp(scenario.end)-first).days
        progress=min(1.,max(0.,(sim.current_date-first).days/max(1,span)))
        st.progress(1. if sim.complete else progress,text=f"Session {len(sim.history):,}")
        if st.session_state.benchmarks:
            st.selectbox("Comparison benchmark",list(st.session_state.benchmarks),key="survival_benchmark_choice")
        st.button("Reset mission",on_click=clear_session,width="stretch")
    else:
        st.caption("ENGINE STATUS")
        st.markdown("● Ready for a new mission")
    with st.expander("Methodology & assumptions"):
        st.write("Observe close t → decide → execute close t+1 → new holdings earn subsequent returns. Both buys and sells incur the selected bps cost. Targets use post-cost equity; cash funds purchases.")
        st.write("Adjusted prices approximate reinvested returns in synthetic units. They are retrospectively revised, not executable quotes or a fully point-in-time database. No separate dividends, splits, leverage, taxes, or cash interest.")
        st.write("Metrics: 252 sessions/year; actual elapsed days for CAGR; sample standard deviation; effective annual RF and MAR converted daily; default both 0%. VaR: linear empirical quantile of losses, 95%, at least 20 observations. Turnover includes initial purchases.")
        st.write("Today's surviving ETF universe introduces survivorship bias. Named scenarios reveal broad context. Historical event timestamps are conservatively disclosed at the next exchange session when uncertain.")
    with st.expander("Display preferences"):
        st.checkbox("Animate large portfolio moves",value=True,key="motion_enabled")
        st.caption("Brief emphasis for daily moves of at least 3%. System reduced-motion preferences are respected; sound is never played.")
    st.caption("Educational research. Not investment advice.")

{"Mission Control":mission,"Financial Time Machine":replay,"Strategy Laboratory":laboratory,
 "Crisis Analysis":crisis_analysis,"Final Performance Report":report}[st.session_state.page]()
st.html('<div class="footnote">FINANCIAL TIME MACHINE / CAUSAL REPLAY ENGINE · USD · FRACTIONAL UNITS · LONG ONLY</div>')

# Logical workspace changes should open at the new page's heading. Ordinary
# reruns (orders, downloads, benchmark changes) preserve the reading position.
reset_navigation_scroll()
