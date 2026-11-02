"""A causal close-to-next-close replay engine shared by Survival and Laboratory.

The full dataset stays private. Presentation code consumes :meth:`snapshot` or
:meth:`result`, both of which copy only information revealed by the session clock.
"""

from dataclasses import asdict, dataclass
import hashlib
from typing import Mapping

import numpy as np
import pandas as pd

from core.portfolio import Portfolio, validate_weights
from core.strategies import Strategy


INCEPTION = {
    "SPY": "1993-01-22", "QQQ": "1999-03-10", "AGG": "2003-09-22",
    "IEF": "2002-07-22", "TLT": "2002-07-22", "GLD": "2004-11-18", "SHY": "2002-07-22",
}
LEDGER_COLUMNS = [
    "order_id", "signal_date", "execution_date", "asset", "units", "price",
    "signed_notional", "gross_notional", "costs", "cash_after", "source",
]


@dataclass(frozen=True)
class PendingOrder:
    order_id: int
    signal_date: pd.Timestamp
    execution_date: pd.Timestamp | None
    weights: dict[str, float]
    source: str


@dataclass(frozen=True)
class BacktestResult:
    """Copied, revealed-only data; no hidden full-price frame or future events.

    history: indexed by date; equity, cash, daily_return, costs, gross_notional,
      value_{ticker}, weight_{ticker}, units_{ticker}, and weight_CASH.
    ledger: signed units/notional, positive gross_notional/costs, execution prices.
    contributions: daily USD P&L columns for each asset, CASH (zero interest),
      COSTS (negative). Its cumulative sum equals equity minus starting capital.
    """

    prices: pd.DataFrame
    history: pd.DataFrame
    ledger: pd.DataFrame
    contributions: pd.DataFrame
    decisions: pd.DataFrame
    metadata: dict


def _naive_dates(index) -> pd.DatetimeIndex:
    values = pd.DatetimeIndex(index)
    if values.tz is not None:
        values = values.tz_localize(None)
    return values.normalize()


class Simulation:
    """Explicit per-session state. No execution is triggered merely by reading it.

    Automatic targets are queued after that day's valuation and execution. A
    manual target submitted at the same close visibly replaces the pending target;
    the next automatic signal cannot overwrite it before execution. Canceling an
    order leaves holdings unchanged. Reset means constructing a new instance.
    """

    def __init__(
        self,
        prices: pd.DataFrame,
        start,
        end,
        capital: float = 100000.0,
        cost_bps: float = 5.0,
        strategy: Strategy | None = None,
        weights: Mapping[str, float] | None = None,
        sessions: pd.DatetimeIndex | None = None,
        data_fingerprint: str = "",
    ) -> None:
        if not np.isfinite(capital) or capital <= 0:
            raise ValueError("Initial capital must be finite and positive.")
        if not np.isfinite(cost_bps) or not 0 <= cost_bps < 10000:
            raise ValueError("Transaction costs must be in [0, 10,000) bps per side.")
        if prices.empty or prices.columns.duplicated().any():
            raise ValueError("Prices must contain distinct eligible asset columns.")
        self._prices = prices.copy().astype(float)
        self._prices.index = _naive_dates(self._prices.index)
        if self._prices.index.duplicated().any() or not self._prices.index.is_monotonic_increasing:
            raise ValueError("Price dates must be unique and sorted in increasing order.")
        start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        if start > end:
            raise ValueError("The start date must precede the end date.")
        if sessions is None:
            import exchange_calendars as xcals

            sessions = xcals.get_calendar("XNYS").sessions_in_range(start, end)
        sessions = _naive_dates(sessions)
        self._sessions = sessions[(sessions >= start) & (sessions <= end)]
        if self._sessions.empty or self._sessions.duplicated().any() or not self._sessions.is_monotonic_increasing:
            raise ValueError("Evaluation needs unique, increasing exchange sessions within its window.")
        missing_dates = self._sessions.difference(self._prices.index)
        if len(missing_dates):
            raise ValueError(f"Missing exchange-session prices: {missing_dates[0].date()}. Retry or revise the valid window explicitly.")
        scenario_prices = self._prices.reindex(self._sessions)
        invalid = ~np.isfinite(scenario_prices) | (scenario_prices <= 0)
        if invalid.any().any():
            row, col = np.argwhere(invalid.to_numpy())[0]
            raise ValueError(f"Missing/invalid {scenario_prices.columns[col]} close on {scenario_prices.index[row].date()}. No stale-price fill is permitted.")
        self.assets = list(self._prices.columns)
        for asset in self.assets:
            if asset in INCEPTION and self._sessions[0] < pd.Timestamp(INCEPTION[asset]):
                raise ValueError(f"{asset} did not exist at the scenario start; exclude it before starting.")
        if strategy is not None:
            strategy.validate_universe(self.assets)
        self.capital = float(capital)
        self.cost_bps = float(cost_bps)
        self.strategy = strategy
        self.portfolio = Portfolio(self.capital, dict.fromkeys(self.assets, 0.0))
        self._position = 0
        self.pending: PendingOrder | None = None
        self._next_order_id = 1
        self._executed_ids: set[int] = set()
        self._ledger: list[dict] = []
        self._decisions: list[dict] = []
        self._records: list[dict] = []
        self._contributions: list[dict] = []
        fingerprint = hashlib.sha256(pd.util.hash_pandas_object(self._prices, index=True).values.tobytes()).hexdigest()
        self.data_fingerprint = data_fingerprint or fingerprint
        self._record(0.0, 0.0, dict.fromkeys(self.assets, 0.0))
        initial = weights if weights is not None else (strategy.target(self._revealed()) if strategy else {"CASH": 1.0})
        self.submit_target(initial, source="initial")

    @property
    def current_date(self) -> pd.Timestamp:
        return self._sessions[self._position]

    @property
    def complete(self) -> bool:
        return self._position == len(self._sessions) - 1

    @property
    def history(self) -> pd.DataFrame:
        return pd.DataFrame(self._records).set_index("date").copy()

    @property
    def ledger(self) -> pd.DataFrame:
        return pd.DataFrame(self._ledger, columns=LEDGER_COLUMNS).copy()

    @property
    def decisions(self) -> pd.DataFrame:
        return pd.DataFrame(self._decisions).copy()

    @property
    def contributions(self) -> pd.DataFrame:
        return pd.DataFrame(self._contributions).set_index("date").copy()

    def _revealed(self) -> pd.DataFrame:
        return self._prices.loc[:self.current_date].copy()

    def _decision_status(self, order_id: int, status: str, reason: str = "") -> None:
        for decision in self._decisions:
            if decision["order_id"] == order_id:
                decision["status"] = status
                decision["reason"] = reason
                break

    def submit_target(self, weights: Mapping[str, float], source: str = "manual") -> str:
        """Queue one target for next close; return a visible replacement/cancel status."""
        target = validate_weights(weights, self.assets)
        replaced = self.pending is not None
        if replaced:
            self._decision_status(self.pending.order_id, "replaced", f"Replaced by {source} target at the same signal close.")
        execution_date = None if self.complete else self._sessions[self._position + 1]
        order = PendingOrder(self._next_order_id, self.current_date, execution_date, target, source)
        self._next_order_id += 1
        status = "cancelled" if self.complete else "pending"
        reason = "No execution session remains; terminal holdings are valued without liquidation." if self.complete else ""
        self._decisions.append({**asdict(order), "status": status, "reason": reason})
        self.pending = None if self.complete else order
        if self.complete:
            return reason
        return "Replaced pending target; next-session execution retained." if replaced else "Queued for the next trading-session close."

    def cancel_pending(self) -> bool:
        if self.pending is None:
            return False
        self._decision_status(self.pending.order_id, "cancelled", "Canceled before execution by user.")
        self.pending = None
        return True

    def destination(self, step: str = "day") -> pd.Timestamp:
        """Preview next day=1 session, week=5, month=next calendar month-end."""
        if step == "day":
            position = min(self._position + 1, len(self._sessions) - 1)
        elif step == "week":
            position = min(self._position + 5, len(self._sessions) - 1)
        elif step == "month":
            next_month = self.current_date.to_period("M") + 1
            candidate = np.flatnonzero(self._sessions.to_period("M") <= next_month)
            position = int(candidate[-1]) if len(candidate) else self._position
        else:
            raise ValueError("Advance step must be day, week, or month.")
        return self._sessions[position]

    def advance(self, step: str = "day") -> pd.Timestamp:
        return self.advance_to(self.destination(step))

    def advance_to(self, date) -> pd.Timestamp:
        """Process each intervening exchange session; repeated current dates are no-ops."""
        destination = pd.Timestamp(date).normalize()
        if destination < self.current_date:
            raise ValueError("The session clock cannot move backward; reset to start a new replay.")
        destination = min(destination, self._sessions[-1])
        target_position = int(self._sessions.searchsorted(destination, side="right") - 1)
        while self._position < target_position:
            self._process_next_session()
        return self.current_date

    def _process_next_session(self) -> None:
        old_date = self.current_date
        old_prices = self._prices.loc[old_date]
        old_units = self.portfolio.units.copy()
        self._position += 1
        prices = self._prices.loc[self.current_date]
        pnl = {a: old_units[a] * (float(prices[a]) - float(old_prices[a])) for a in self.assets}
        costs = gross = 0.0
        if self.pending is not None and self.pending.execution_date == self.current_date:
            order = self.pending
            if order.order_id in self._executed_ids:
                raise ArithmeticError("Duplicate execution identifier blocked.")
            trades = self.portfolio.rebalance(order.weights, prices, self.cost_bps)
            for trade in trades:
                self._ledger.append({
                    "order_id": order.order_id, "signal_date": order.signal_date,
                    "execution_date": self.current_date, **trade, "source": order.source,
                })
            costs = sum(t["costs"] for t in trades)
            gross = sum(t["gross_notional"] for t in trades)
            self._executed_ids.add(order.order_id)
            self._decision_status(order.order_id, "executed")
            self.pending = None
        self._record(costs, gross, pnl)
        next_session = None if self.complete else self._sessions[self._position + 1]
        if self.strategy is not None and self.strategy.should_signal(self.current_date, next_session):
            self.submit_target(self.strategy.target(self._revealed()), source="automatic")
        if self.complete and self.pending is not None:
            self._decision_status(self.pending.order_id, "cancelled", "No execution session remains.")
            self.pending = None

    def _record(self, costs: float, gross: float, pnl: dict[str, float]) -> None:
        prices = self._prices.loc[self.current_date]
        values = self.portfolio.values(prices)
        equity = self.portfolio.cash + sum(values.values())
        previous_equity = self._records[-1]["equity"] if self._records else equity
        record = {
            "date": self.current_date, "equity": equity, "cash": self.portfolio.cash,
            "daily_return": equity / previous_equity - 1.0 if self._records else 0.0,
            "costs": costs, "gross_notional": gross,
            "weight_CASH": self.portfolio.cash / equity,
        }
        for asset in self.assets:
            record.update({f"value_{asset}": values[asset], f"weight_{asset}": values[asset] / equity, f"units_{asset}": self.portfolio.units[asset]})
        if abs(equity - previous_equity - sum(pnl.values()) + costs) > max(1e-7, equity * 1e-10):
            raise ArithmeticError("Daily P&L attribution does not reconcile to portfolio equity.")
        self._records.append(record)
        self._contributions.append({"date": self.current_date, **pnl, "CASH": 0.0, "COSTS": -costs})

    def result(self) -> BacktestResult:
        return BacktestResult(
            prices=self._revealed(), history=self.history, ledger=self.ledger,
            contributions=self.contributions, decisions=self.decisions,
            metadata={
                "capital": self.capital, "cost_bps": self.cost_bps, "clock": str(self.current_date.date()),
                "complete": self.complete, "data_fingerprint": self.data_fingerprint,
                "cash_interest_annual": 0.0, "strategy": self.strategy.name if self.strategy else "Manual replay",
                "strategy_parameters": asdict(self.strategy) if self.strategy else {},
                "adjustment": "Adjusted-close synthetic total-return units; dividends not separately credited",
                "execution": "Signal at t close; post-cost target sized at t+1 close; no return earned before execution",
                "turnover": "Gross traded notional / mean daily equity; includes initial purchases",
            },
        )

    def snapshot(self) -> dict:
        """Safe presentation boundary. Only the explicit next pending close is future-dated."""
        result = self.result()
        close = self._prices.loc[self.current_date]
        values = self.portfolio.values(close)
        holdings = pd.DataFrame([
            {"asset": a, "units": self.portfolio.units[a], "price": close[a], "value": values[a], "weight": values[a] / result.history.equity.iloc[-1]}
            for a in self.assets
        ] + [{"asset": "CASH", "units": self.portfolio.cash, "price": 1.0, "value": self.portfolio.cash, "weight": self.portfolio.cash / result.history.equity.iloc[-1]}])
        return {
            "current_date": self.current_date, "complete": self.complete,
            "prices": result.prices, "history": result.history, "holdings": holdings,
            "ledger": result.ledger, "contributions": result.contributions,
            "decisions": result.decisions, "pending": asdict(self.pending) if self.pending else None,
            "config": result.metadata,
        }

    def run(self) -> BacktestResult:
        self.advance_to(self._sessions[-1])
        return self.result()


def run_backtest(prices: pd.DataFrame, start, end, **kwargs) -> BacktestResult:
    """Convenience entrypoint using the exact same accounting as interactive replay."""
    return Simulation(prices, start, end, **kwargs).run()
