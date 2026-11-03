"""Long-only fractional total-return units and deterministic cost-aware allocation."""

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd


WEIGHT_TOLERANCE = 1e-8


def validate_weights(weights: Mapping[str, float], assets: list[str]) -> dict[str, float]:
    """Require finite, nonnegative weights summing to one (absolute tolerance 1e-8)."""
    if not weights:
        raise ValueError("Allocation is empty; explicitly assign 100% to CASH if desired.")
    converted = {str(k): float(v) for k, v in weights.items()}
    if any(not np.isfinite(v) or v < 0 for v in converted.values()):
        raise ValueError("Weights must be finite and nonnegative; leverage and shorts are disabled.")
    # Allocation editors may retain a zero-valued row for an excluded asset.
    # Zero is no exposure, so discard only exactly-zero unavailable names. Even
    # a tiny positive unknown weight remains invalid, rather than being rounded
    # away or silently transferred to the eligible universe.
    unknown = {asset for asset in set(converted) - set(assets) - {"CASH"} if converted[asset] != 0.0}
    if unknown:
        raise ValueError(f"Assets unavailable in this run: {', '.join(sorted(unknown))}.")
    total = sum(converted.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"Weights must sum to 100% (received {total:.6%}).")
    # Normalize only floating-point noise within the documented strict tolerance.
    return {asset: converted.get(asset, 0.0) / total for asset in [*assets, "CASH"]}


@dataclass
class Portfolio:
    """Cash and synthetic adjusted-close units, with no borrowing or cash interest."""

    cash: float
    units: dict[str, float] = field(default_factory=dict)

    def values(self, prices: pd.Series) -> dict[str, float]:
        values = {}
        for asset, units in self.units.items():
            price = float(prices.get(asset, np.nan))
            if units and (not np.isfinite(price) or price <= 0):
                raise ValueError(f"Cannot value held {asset}: missing or invalid close.")
            values[asset] = float(units * price) if units else 0.0
        return values

    def equity(self, prices: pd.Series) -> float:
        return float(self.cash + sum(self.values(prices).values()))

    def rebalance(self, weights: Mapping[str, float], prices: pd.Series, cost_bps: float) -> list[dict]:
        """Size at the execution close against post-cost equity; sells fund buys.

        Solve E + c * sum(abs(w_i * E - old_value_i)) = old_equity by
        monotone bisection. Cash weights receive residual floating-point amounts.
        The lower feasible root bounds guarantee purchases plus costs fit cash.
        """
        assets = list(self.units)
        target = validate_weights(weights, assets)
        if not np.isfinite(cost_bps) or not 0 <= cost_bps < 10000:
            raise ValueError("Transaction costs must be finite and between 0 and 10,000 bps exclusive.")
        prices = prices.reindex(assets)
        if not np.isfinite(prices.to_numpy(dtype=float)).all() or (prices <= 0).any():
            raise ValueError("Execution requires a valid positive close for every eligible asset.")
        old_values = self.values(prices)
        total = self.equity(prices)
        if total <= 0:
            raise ValueError("Portfolio equity must remain positive.")
        rate = cost_bps / 10000.0

        def residual(equity: float) -> float:
            gross = sum(abs(target[a] * equity - old_values[a]) for a in assets)
            return equity + rate * gross - total

        low, high = 0.0, total
        for _ in range(100):
            middle = (low + high) / 2.0
            if residual(middle) <= 0:
                low = middle
            else:
                high = middle
        deltas = {a: target[a] * low - old_values[a] for a in assets}
        # Negative notionals first, then positive; deterministic alphabetical order.
        ordered = sorted(assets, key=lambda a: (deltas[a] >= 0, a))
        trades = []
        for asset in ordered:
            notional = float(deltas[asset])
            if abs(notional) <= max(1e-10, total * 1e-14):
                continue
            cost = abs(notional) * rate
            units = notional / float(prices[asset])
            if notional + cost > self.cash + max(1e-8, total * 1e-12):
                raise ArithmeticError("Cost-aware allocation failed available-cash reconciliation.")
            self.cash -= notional + cost
            self.units[asset] += units
            if self.cash < 0 and self.cash > -max(1e-8, total * 1e-12):
                self.cash = 0.0
            if abs(self.units[asset]) < 1e-12:
                self.units[asset] = 0.0
            trades.append({
                "asset": asset, "units": units, "price": float(prices[asset]),
                "signed_notional": notional, "gross_notional": abs(notional),
                "costs": cost, "cash_after": self.cash,
            })
        if self.cash < -1e-8 or any(v < -1e-10 for v in self.units.values()):
            raise ArithmeticError("Long-only cash accounting invariant failed.")
        return trades
