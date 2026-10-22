"""End-to-end backtest: simulate execution, replay in vectorbt, merge results."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.metrics import build_portfolio, key_stats
from backtest.simulator import Simulation, simulate
from config import Config


@dataclass
class BacktestResult:
    simulation: Simulation
    trades: pd.DataFrame
    equity: pd.Series
    portfolio: object
    stats: dict

    @property
    def empty(self) -> bool:
        return self.simulation.empty

    def summary(self) -> str:
        s = self.stats
        if self.empty:
            return "no trades"
        return (
            f"{int(s['Total Trades'])} trades | "
            f"return {s['Total Return [%]']:.2f}% | "
            f"win {s['Win Rate [%]']:.1f}% | "
            f"PF {s['Profit Factor']:.2f} | "
            f"maxDD {s['Max Drawdown [%]']:.2f}%"
        )


def run_backtest(df: pd.DataFrame, setups: pd.DataFrame, config: Config) -> BacktestResult:
    sim = simulate(df, setups, config)
    if sim.empty:
        return BacktestResult(
            simulation=sim,
            trades=sim.trades,
            equity=pd.Series(dtype=float),
            portfolio=None,
            stats={},
        )

    portfolio = build_portfolio(df, sim.orders, config)
    trades = _merge_vectorbt_trades(sim.trades, portfolio)
    equity = portfolio.value()

    return BacktestResult(
        simulation=sim,
        trades=trades,
        equity=equity,
        portfolio=portfolio,
        stats=key_stats(portfolio),
    )


def _merge_vectorbt_trades(trades: pd.DataFrame, portfolio) -> pd.DataFrame:
    """Add vectorbt's fee/slippage-adjusted PnL to our execution records."""
    try:
        records = portfolio.trades.records_readable
    except Exception:
        return trades
    if len(records) != len(trades):
        # an order was rejected or a position was left open; keep gross figures
        return trades

    out = trades.copy()
    out["net_pnl"] = records["PnL"].to_numpy()
    out["net_return_pct"] = records["Return"].to_numpy() * 100.0
    out["vbt_entry_price"] = records["Avg Entry Price"].to_numpy()
    out["vbt_exit_price"] = records["Avg Exit Price"].to_numpy()
    out["vbt_status"] = records["Status"].to_numpy()
    return out
