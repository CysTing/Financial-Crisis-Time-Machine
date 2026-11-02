# Financial methodology

This document describes the implemented educational simulation, including the
approximations that determine what its results mean. The Python engine is shared
by the interactive Survival replay and retrospective Strategy Laboratory.

## Accounting and execution

The account uses USD, fractional units, long-only positions, no leverage, no
short sales, no taxes, and no external cash flows. Cash interest is explicitly
0%. SHY is an ETF with a market return and execution costs; it is not cash.

Prices are adjusted closes. A position consists of synthetic total-return units:
dividend and split adjustments are already represented in those prices, and the
engine never credits dividends or applies splits again. These units approximate
reinvestment; they are not a reconstruction of actual shares or executable
brokerage prices. Adjustments and revisions made after a historical date remain
in the downloaded series, so this is not a fully point-in-time market database.

At the first scenario close, the account records its starting capital entirely
as cash and queues the starting target. At each following exchange session:

1. Existing units earn the price move into that session's close.
2. A target submitted at an earlier close executes at the current close.
3. Execution costs are deducted and post-execution holdings, cash, equity, and
   P&L are recorded.
4. Revealed information can create a target for a later execution.

A signal from close *t* therefore cannot earn the return into close *t+1*; newly
purchased units start earning returns after their execution at *t+1*. Targets
specify weights rather than fixed units; the engine sizes units at the execution
close. This is a simplified delayed-close convention, not a claim that adjusted
closing prices could have been traded in real time.

Weights must be finite and nonnegative and sum to 100% including cash, within
an absolute tolerance of `1e-8` in fractional weights. Targets refer to equity after
execution costs. The engine solves the cost-aware allocation deterministically,
sells before buying, and leaves rounding residuals in cash. It does not borrow
to fund costs or renormalize allocations above an exposure cap.

For pre-trade equity `E`, current asset values `V_i`, and target asset weights
`w_i`, post-cost equity `E'` solves
`E' + k × sum(abs(w_i × E' − V_i)) = E` by monotone bisection. Cash is excluded
from traded notional. This sizes a fully invested target feasibly even when
costs must be paid from the same account.

For a configured one-side cost rate `k = cost_bps / 10_000`:

```text
cost = k × sum(abs(trade_units × execution_price))
cash_after = cash_before − signed_trade_notional − cost
equity = cash + sum(units × valuation_price)
```

Both sales and purchases incur that one rate. There is no fixed fee and no
second slippage deduction. The ledger records order identifier, signal date,
execution date, asset, signed units and notional, price, gross notional, costs,
cash after execution, and decision source. Final positions are valued without
an assumed sale. A target with no remaining execution session is canceled and
recorded, rather than assigned an invented terminal close.

## Calendar and missing observations

The explicit US exchange calendar is XNYS, supplied by `exchange_calendars`.
Missing exchange-session observations are distinct from weekends, holidays, and
exceptional exchange closures. No prices are backfilled from the future or
forward-filled across an unavailable observation. Assets must exist and have
valid observations throughout the evaluation window. Exclusions are disclosed
before a run. A held asset without a required close blocks a valid run rather
than silently using a stale valuation. Unusual moves are flagged for review.

The data pipeline requests warm-up history for trailing indicators. Warm-up
prices may support signals, but never enter the scored equity baseline. A rule
with insufficient trailing observations remains in cash until eligible.

"Next day" means one exchange session; "next week" means five sessions; "next
month" means the final exchange session of the following calendar month, capped
at the scenario end. Every intervening session is processed. Period-end strategy
signals use that final session's close and execute at the next available close.

Only one target can be pending for the next execution. A manual target visibly
replaces the pending target, including an automatic one. The user can cancel a
pending target before execution. Reading state or rerunning the page never
executes a trade or advances the clock.

## Strategy defaults

| Strategy | Signal and allocation rule | Rebalancing |
|---|---|---|
| Buy and Hold | User-selected starting weights, default 100% SPY | Initial target only |
| Traditional 60/40 | 60% SPY, 40% AGG; both must be eligible | Month-end signals |
| Moving Average Trend | 100% SPY when its latest close is above its trailing 200-close simple mean, otherwise cash | Daily signals |
| Momentum Rotation | Rank positive trailing 126-session returns; select the strongest eligible asset; ticker-order tie break; cash if none qualify | Month-end signals |
| Inverse Volatility | Inverse trailing 63-return sample standard deviation, with configurable per-asset cap; exclude insufficient or zero-volatility assets; residual goes to cash | Month-end signals |
| User-Defined Allocation | Validated custom asset and cash weights | None, monthly, quarterly, or annually |

All strategy decisions receive only data through the signal close. Benchmarks
use SPY buy-and-hold and monthly 60/40 where eligible, with identical capital,
evaluation sessions, initial execution delay, adjustment convention, and costs.
Laboratory inputs and outputs are separate from Survival state. Laboratory
results are explicitly retrospective research and can reveal full-period data.

## Performance metrics

Let `E_0` be the initial cash observation and `E_t` post-execution equity. Daily
returns are `r_t = E_t / E_(t−1) − 1`, excluding the baseline as a return. All
execution costs are included. No missing return is silently padded.

| Metric | Implemented convention |
|---|---|
| Total return | `E_T / E_0 − 1` |
| CAGR | `(E_T/E_0)^(1/Y) − 1`, where `Y` is elapsed calendar time / 365.2425 days |
| Annualized volatility | Sample standard deviation of daily returns × `sqrt(252)` |
| Sharpe | Mean daily excess return / its sample standard deviation × `sqrt(252)` |
| Sortino | Mean return above daily MAR / `sqrt(mean(min(r_t − MAR_daily, 0)^2))` × `sqrt(252)`; all observations stay in the denominator |
| Maximum drawdown | Maximum of `1 − E_t / running_max(E)`; positive loss, including initial capital in running peaks |
| Calmar | CAGR / positive maximum drawdown |
| Best/worst day | Largest/smallest observed daily account return |
| Excess cumulative return | Portfolio total return minus benchmark total return; multiply by 100 to express percentage points, not alpha |
| Gross turnover | Sum of gross traded notional / mean daily recorded equity; initial purchases and both trade sides included |

A constant effective annual risk-free rate or minimum acceptable return is
converted as `(1 + annual_rate)^(1/252) − 1`. Both default to a labeled 0%
assumption; neither silently uses today's interest rate. The risk-free metric
assumption is separate from the account's 0% cash interest.

Undefined ratios, zero denominators, and insufficient observations produce N/A,
not an invented zero or infinity. Zero observed volatility itself is a valid zero
volatility estimate. CAGR and risk ratios annualized from a short period are
labeled as unstable short-period estimates, not forecasts.

## Risk analysis and attribution

Rolling portfolio volatility uses 21 complete daily returns by default, sample
standard deviation, and 252-session annualization. Rolling asset correlations
use trailing 63 returns with full-window requirements. No warm-up observations
are scored as portfolio performance.

Historical Value at Risk uses one-session losses `L_t = −r_t` and NumPy's linear
empirical quantile at the selected confidence level (95% by default). Expected
Shortfall is the average observed loss at or above that threshold, including
ties. At least 20 returns are required. If all observed outcomes are gains, a
loss quantile or tail mean can be negative; this describes that sample and does
not guarantee a gain or protection against future losses.

Realized P&L attribution records each asset's entering units multiplied by the
change in its adjusted close. Cash contributes zero interest, and `COSTS` is a
separate negative contribution. The cumulative sum of asset, cash, and cost
contributions reconciles to ending equity minus initial capital.

Covariance-based risk attribution is a different calculation: for current
weights `w` held constant and trailing sample covariance `Σ` annualized by 252,
portfolio volatility is `σ = sqrt(w'Σw)`. Asset *i* contributes
`w_i(Σw)_i / σ` to volatility; division by `σ` gives its fraction of volatility.
Components sum to portfolio volatility (and fractions to one) when defined.
Diversifying assets can have negative contributions. Cash contributes zero;
zero-volatility fractions and incomplete windows are N/A. This is an estimate
at current weights, not a claim about realized historical P&L.

Fixed crisis-window tables compare the portfolios actually held during each
specified stress interval. They do not reset portfolio weights or investment
capital at the start of the stress window.

## Educational survival score

The custom score uses unleveraged, causally generated account results and is
bounded from 0 to 100. It is not an institutional credit or investment rating.
Define `clip(x) = min(max(x, 0), 1)`:

| Component | Weight | Component value before multiplying by 100 |
|---|---|---|
| Capital preservation | 35% | `clip(1 + total_return)` |
| Drawdown resilience | 35% | `clip(1 − maximum_drawdown / 0.60)` |
| Risk-adjusted outcome | 15% | `clip((Sharpe + 1) / 3)` |
| Relative performance | 15% | `clip(0.5 + excess_cumulative_return / 0.40)` |

The score is the weighted sum of the component scores. A missing component
receives neutral 50%, and weights are not redistributed. Increasing drawdown
with all other inputs fixed cannot improve the score. Initial capital retained,
total return, and final value are not rewarded as three independent variables:
capital preservation is a single capped component. This consciously favors
preservation over rewarding unlimited growth. Cash may preserve capital but
forgo growth; the components and benchmark comparison make that tradeoff visible.

Result categories are excellent crisis resilience at 85 or above; strong survival
at 70; moderate survival at 50; severe portfolio damage at 25; and portfolio
collapse below 25. Only completed sessions display a final score. Interim results
are explicitly partial and compare matching elapsed evaluation windows.

## Information boundaries and provenance

Survival UI functions consume copied views restricted to the replay clock.
Prices, chart arrays, benchmarks, event feeds, decision history, and exports
cannot contain later market observations. Full-period Crisis Analysis and the
final score remain gated until completion. Export generation independently
filters date-bearing tables, whitelists public metadata, and recomputes metrics
from restricted equity rather than trusting precomputed values. HTML reports
escape content and embed no remote scripts or assets. Every CSV, including an
empty-ledger export, carries source and partial/completed status.

Curated events have factual descriptions and primary-source URLs. Unknown
publication times are disclosed conservatively on the next exchange session
after the verified publication date. After-close, weekend, and holiday events
follow the exchange-session boundary. Optional macroeconomic data are omitted:
release dates and vintage integrity are not reconstructed, so revised statistics
cannot accidentally become historical inputs.

The provider manifest records requested/observed dates, adjustment settings,
tickers, retrieval timestamp, exclusions, warnings, and a fingerprint. A valid
genuine local cache may be used when the provider is unavailable, with provenance
shown. Synthetic data require an explicit user choice, are deterministic, remain
separate from genuine observations, and are labeled on pages and exports. The
synthetic demonstration is a software demonstration, not a reproduction of
historical crisis returns. Genuine observations are not bundled or licensed by
the project's software license.

Today's surviving ETF universe creates survivorship bias. Named crisis scenarios
already reveal broad historical context. Results are educational replay, not a
blind prediction experiment, investment recommendation, or guarantee.
