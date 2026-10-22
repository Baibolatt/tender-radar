"""vectorbt accounting and performance reporting."""

from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt
from vectorbt.portfolio.enums import Direction, SizeType

from config import Config
from data.loader import pandas_freq


def build_portfolio(
    df: pd.DataFrame, orders: pd.DataFrame, config: Config
) -> vbt.Portfolio:
    """Replay explicit orders through vectorbt (fees & slippage applied)."""
    size = pd.Series(0.0, index=df.index)
    price = pd.Series(np.nan, index=df.index)

    for order in orders.itertuples():
        i = int(order.index)
        size.iat[i] += float(order.side) * float(order.units)
        price.iat[i] = float(order.price)

    return vbt.Portfolio.from_orders(
        close=df["close"],
        size=size,
        size_type=SizeType.Amount,
        price=price,
        direction=Direction.Both,
        fees=config.fees,
        slippage=config.slippage,
        init_cash=config.init_cash,
        freq=pandas_freq(config.timeframe),
        cash_sharing=False,
    )


def stats_table(portfolio: vbt.Portfolio) -> pd.Series:
    return portfolio.stats()


def key_stats(portfolio: vbt.Portfolio) -> dict[str, float | str]:
    s = portfolio.stats()

    def get(*names, default=np.nan):
        for name in names:
            if name in s.index:
                return s[name]
        return default

    return {
        "Start": get("Start"),
        "End": get("End"),
        "Period": get("Period"),
        "Start Value": get("Start Value"),
        "End Value": get("End Value"),
        "Total Return [%]": get("Total Return [%]"),
        "Buy & Hold [%]": get("Benchmark Return [%]"),
        "Max Drawdown [%]": get("Max Drawdown [%]"),
        "Total Trades": get("Total Trades"),
        "Win Rate [%]": get("Win Rate [%]"),
        "Profit Factor": get("Profit Factor"),
        "Expectancy": get("Expectancy"),
        "Avg Win [%]": get("Avg Winning Trade [%]"),
        "Avg Loss [%]": get("Avg Losing Trade [%]"),
        "Best Trade [%]": get("Best Trade [%]"),
        "Worst Trade [%]": get("Worst Trade [%]"),
        "Sharpe": get("Sharpe Ratio"),
        "Sortino": get("Sortino Ratio"),
        "Calmar": get("Calmar Ratio"),
        "Total Fees": get("Total Fees Paid"),
    }


def format_stats(stats: dict) -> str:
    lines = ["performance"]
    for key, value in stats.items():
        if isinstance(value, (int, np.integer)):
            lines.append(f"  {key:<20} {value}")
        elif isinstance(value, (float, np.floating)):
            lines.append(f"  {key:<20} {value:,.2f}")
        else:
            lines.append(f"  {key:<20} {value}")
    return "\n".join(lines)
