"""Backtesting: conservative execution model + vectorbt accounting."""

from .metrics import build_portfolio, format_stats, key_stats, stats_table
from .runner import BacktestResult, run_backtest
from .simulator import Simulation, simulate

__all__ = [
    "run_backtest",
    "BacktestResult",
    "simulate",
    "Simulation",
    "build_portfolio",
    "key_stats",
    "stats_table",
    "format_stats",
]
