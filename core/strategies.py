"""Causal allocation rules. Callers pass only history known at the signal close."""

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from core.portfolio import validate_weights


STRATEGIES = (
    "Buy and Hold", "Traditional 60/40", "Moving Average Trend",
    "Momentum Rotation", "Inverse Volatility", "User-Defined Allocation",
)
FREQUENCIES = ("None", "Daily", "Monthly", "Quarterly", "Annually")


@dataclass(frozen=True)
class Strategy:
    """A stateless strategy configuration, independent of the replay clock."""

    name: str
    weights: Mapping[str, float] = field(default_factory=lambda: {"SPY": 1.0})
    frequency: str = "Monthly"
    lookback: int = 126
    exposure_cap: float = 1.0
    momentum_top_n: int = 1

    def __post_init__(self) -> None:
        if self.name not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {self.name}.")
        if self.frequency not in FREQUENCIES:
            raise ValueError(f"Unsupported rebalance frequency: {self.frequency}.")
        if self.lookback < 2:
            raise ValueError("Lookback must be at least two trading sessions.")
        if not np.isfinite(self.exposure_cap) or not 0 < self.exposure_cap <= 1:
            raise ValueError("Per-asset exposure cap must be in (0, 1].")
        if self.momentum_top_n < 1:
            raise ValueError("Momentum top N must be positive.")

    def validate_universe(self, assets: list[str]) -> None:
        """Reject incompatible benchmark/strategy allocations before a run starts."""
        if self.name in ("Buy and Hold", "User-Defined Allocation"):
            validate_weights(self.weights, assets)
        if self.name == "Traditional 60/40" and not {"SPY", "AGG"}.issubset(assets):
            raise ValueError("Traditional 60/40 requires eligible SPY and AGG data for the entire run.")
        if self.name == "Moving Average Trend" and "SPY" not in assets:
            raise ValueError("Moving Average Trend requires eligible SPY data.")

    def target(self, revealed: pd.DataFrame) -> dict[str, float]:
        """Compute a target using trailing values only; missing warmup stays in cash."""
        assets = list(revealed.columns)
        self.validate_universe(assets)
        if self.name in ("Buy and Hold", "User-Defined Allocation"):
            return validate_weights(self.weights, assets)
        if self.name == "Traditional 60/40":
            return validate_weights({"SPY": 0.6, "AGG": 0.4}, assets)
        if self.name == "Moving Average Trend":
            window = revealed["SPY"].iloc[-self.lookback:]
            if len(window) < self.lookback or window.isna().any():
                return {"CASH": 1.0}
            return {"SPY": 1.0} if window.iloc[-1] > window.mean() else {"CASH": 1.0}
        if self.name == "Momentum Rotation":
            if len(revealed) <= self.lookback:
                return {"CASH": 1.0}
            window = revealed.iloc[-self.lookback - 1:]
            eligible = window.columns[window.notna().all() & (window > 0).all()]
            scores = {a: float(window[a].iloc[-1] / window[a].iloc[0] - 1) for a in eligible}
            ranked = sorted((a for a in scores if scores[a] > 0), key=lambda a: (-scores[a], a))
            chosen = ranked[:self.momentum_top_n]
            if not chosen:
                return {"CASH": 1.0}
            return {a: 1.0 / len(chosen) for a in chosen}
        # 63 returns require 64 closes. sample std, no implicit pandas fill.
        if len(revealed) <= self.lookback:
            return {"CASH": 1.0}
        window = revealed.iloc[-self.lookback - 1:]
        returns = window.pct_change(fill_method=None).iloc[1:]
        vol = returns.std(ddof=1)
        good = vol.index[(returns.notna().all()) & np.isfinite(vol) & (vol > 1e-12)]
        if not len(good):
            return {"CASH": 1.0}
        inverse = 1.0 / vol[good]
        weights = (inverse / inverse.sum()).clip(upper=self.exposure_cap).to_dict()
        weights["CASH"] = max(0.0, 1.0 - sum(weights.values()))
        return weights

    def should_signal(self, current: pd.Timestamp, next_session: pd.Timestamp | None) -> bool:
        """Signal at the final actual exchange session of a completed period."""
        if self.frequency == "None":
            return False
        if self.frequency == "Daily":
            return True
        if next_session is None:
            return True  # A final decision is canceled, never given a fictitious fill.
        if self.frequency == "Monthly":
            return current.to_period("M") != next_session.to_period("M")
        if self.frequency == "Quarterly":
            return current.to_period("Q") != next_session.to_period("Q")
        return current.year != next_session.year

    @property
    def description(self) -> str:
        descriptions = {
            "Buy and Hold": "Queue the chosen allocation at the first close, then hold without rebalancing.",
            "Traditional 60/40": "60% SPY / 40% AGG; signal at month-end and execute next session close.",
            "Moving Average Trend": f"100% SPY above its trailing {self.lookback}-close average; otherwise 0%-interest cash. Daily signals.",
            "Momentum Rotation": f"At month-end rank {self.lookback}-session returns; top {self.momentum_top_n} positive assets, equal weight; ticker-order ties; otherwise cash.",
            "Inverse Volatility": f"Monthly inverse sample volatility over {self.lookback} daily returns; per-asset cap {self.exposure_cap:.0%}; residual cash.",
            "User-Defined Allocation": f"Custom long-only target; {self.frequency.lower()} rebalancing with next-session execution.",
        }
        return descriptions[self.name]


def make_strategy(
    name: str,
    weights: Mapping[str, float] | None = None,
    frequency: str = "Monthly",
    lookback: int | None = None,
    exposure_cap: float = 1.0,
    momentum_top_n: int = 1,
) -> Strategy:
    """Construct one of six rules with documented defaults and validated settings."""
    fixed_frequency = {
        "Buy and Hold": "None", "Traditional 60/40": "Monthly",
        "Moving Average Trend": "Daily", "Momentum Rotation": "Monthly", "Inverse Volatility": "Monthly",
    }
    default_lookback = {"Moving Average Trend": 200, "Momentum Rotation": 126, "Inverse Volatility": 63}
    return Strategy(
        name=name, weights=dict(weights) if weights is not None else {"SPY": 1.0},
        frequency=fixed_frequency.get(name, frequency),
        lookback=lookback if lookback is not None else default_lookback.get(name, 126),
        exposure_cap=exposure_cap, momentum_top_n=momentum_top_n,
    )
