"""Run all six rules across all three windows; synthetic unless --historical.

Usage from the repository root: python scripts/validate_scenarios.py [--historical]
This prints computed aggregates only; downloaded prices remain in ignored cache.
"""

from pathlib import Path
import argparse
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
from core.backtest_engine import Simulation
from core.data_loader import load_market_data
from core.metrics import performance_metrics
from core.scenarios import SCENARIOS
from core.strategies import STRATEGIES,make_strategy


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical",action="store_true")
    args=parser.parse_args()
    source="historical" if args.historical else "synthetic"
    print(f"SOURCE: {source.upper()}")
    for key,scenario in SCENARIOS.items():
        data=load_market_data(scenario.start,scenario.end,source=source,cache_dir=Path("data/cache"))
        reference=None
        for name in STRATEGIES:
            result=Simulation(data.prices,scenario.start,scenario.end,strategy=make_strategy(name),data_fingerprint=data.fingerprint).run()
            history=result.history
            if reference is None:
                reference=history.equity
            assert history.index.equals(reference.index)
            assert (history.cash>=-1e-8).all()
            assert np.allclose(result.contributions.sum(axis=1).cumsum(),history.equity-100000)
            assert np.isclose(result.ledger.costs.sum(),history.costs.sum())
            stats=performance_metrics(history.equity,benchmark=reference,trades=result.ledger)
            print(f"{key:10} {name:24} {len(history):4} sessions | return {stats['total_return']:+.4%} | costs ${stats['total_costs']:,.2f}")
    print("PASS: 18 runs; matching windows, nonnegative cash, cost and P&L reconciliation.")


if __name__=="__main__":
    main()
