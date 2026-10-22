"""Bar-by-bar execution simulator.

vectorbt's high-level signal API cannot express "fill a limit at an exact FVG
edge while keeping the stop at an exact price", so execution is modelled here
and the resulting orders are handed to vectorbt for accounting.

Conservative intrabar rules
---------------------------
* On the **entry bar** only the stop is evaluated.  A favourable print may
  have happened before the limit filled, so awarding the target would be
  optimistic.
* If stop and target are both inside one later bar, the **stop** is taken
  first (``pessimistic_intrabar``).
* One position at a time; a setup may not enter on the bar a trade exits.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import Config

TRADE_COLUMNS = [
    "trade_id",
    "side",
    "entry_index",
    "entry_time",
    "entry_price",
    "exit_index",
    "exit_time",
    "exit_price",
    "same_bar_exit",
    "units",
    "notional",
    "sl",
    "tp",
    "risk_per_unit",
    "rr",
    "exit_reason",
    "gross_pnl",
    "sweep_index",
    "sweep_time",
    "choch_index",
    "choch_time",
    "fvg_index",
    "fvg_time",
]

ORDER_COLUMNS = [
    "order_id",
    "trade_id",
    "index",
    "time",
    "side",       # +1 buy, -1 sell
    "units",
    "price",
    "kind",       # entry / exit
    "reason",
]


@dataclass
class Simulation:
    trades: pd.DataFrame
    orders: pd.DataFrame
    skipped: int

    @property
    def empty(self) -> bool:
        return self.trades.empty


def simulate(df: pd.DataFrame, setups: pd.DataFrame, config: Config) -> Simulation:
    if setups.empty:
        return Simulation(
            pd.DataFrame(columns=TRADE_COLUMNS),
            pd.DataFrame(columns=ORDER_COLUMNS),
            0,
        )

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    trades: list[dict] = []
    orders: list[dict] = []
    equity = float(config.init_cash)
    next_free_bar = 0
    skipped = 0

    for _, row in setups.sort_values("entry_index").iterrows():
        entry_i = int(row["entry_index"])
        side = int(row["side"])

        if entry_i < next_free_bar or entry_i >= n:
            skipped += 1
            continue

        entry = float(row["entry_price"])
        sl = float(row["sl"])
        tp = float(row["tp"])
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0 or not np.isfinite(risk_per_unit):
            skipped += 1
            continue

        # --- position sizing: risk a fixed % of running equity ---------------
        units = (equity * config.risk_per_trade) / risk_per_unit
        max_units = (equity * config.max_position_pct) / entry
        units = float(min(units, max_units))
        if units <= 0 or not np.isfinite(units):
            skipped += 1
            continue

        exit_i, exit_price, reason = _find_exit(
            high, low, close, entry_i, side, sl, tp, config
        )
        if exit_i is None:
            skipped += 1
            continue

        # vectorbt nets orders that share a timestamp, so a stop taken on the
        # fill bar would disappear.  Emit that exit on the next bar at the same
        # stop price; PnL is unchanged and the flag keeps it visible.
        same_bar_exit = exit_i == entry_i
        order_exit_i = exit_i
        if same_bar_exit:
            if entry_i >= n - 1:
                skipped += 1
                continue
            order_exit_i = entry_i + 1

        gross_pnl = units * (exit_price - entry) * side
        trade_id = len(trades)

        trades.append(
            {
                "trade_id": trade_id,
                "side": side,
                "entry_index": entry_i,
                "entry_time": df.index[entry_i],
                "entry_price": entry,
                "exit_index": order_exit_i,
                "exit_time": df.index[order_exit_i],
                "exit_price": exit_price,
                "same_bar_exit": same_bar_exit,
                "units": units,
                "notional": units * entry,
                "sl": sl,
                "tp": tp,
                "risk_per_unit": risk_per_unit,
                "rr": float(row["rr"]),
                "exit_reason": reason,
                "gross_pnl": gross_pnl,
                "sweep_index": int(row["sweep_index"]),
                "sweep_time": row["sweep_time"],
                "choch_index": int(row["choch_index"]),
                "choch_time": row["choch_time"],
                "fvg_index": int(row["fvg_index"]),
                "fvg_time": row["fvg_time"],
            }
        )

        # entry: buy for a long, sell for a short
        orders.append(
            {
                "order_id": len(orders),
                "trade_id": trade_id,
                "index": entry_i,
                "time": df.index[entry_i],
                "side": side,
                "units": units,
                "price": entry,
                "kind": "entry",
                "reason": "fvg",
            }
        )
        orders.append(
            {
                "order_id": len(orders),
                "trade_id": trade_id,
                "index": order_exit_i,
                "time": df.index[order_exit_i],
                "side": -side,
                "units": units,
                "price": exit_price,
                "kind": "exit",
                "reason": reason,
            }
        )

        equity += gross_pnl
        next_free_bar = order_exit_i + 1

    return Simulation(
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        orders=pd.DataFrame(orders, columns=ORDER_COLUMNS),
        skipped=skipped,
    )


def _find_exit(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry_i: int,
    side: int,
    sl: float,
    tp: float,
    config: Config,
) -> tuple[int | None, float | None, str | None]:
    n = len(close)
    for t in range(entry_i, n):
        if side == 1:
            hit_sl = low[t] <= sl
            hit_tp = high[t] >= tp
        else:
            hit_sl = high[t] >= sl
            hit_tp = low[t] <= tp

        if t == entry_i:
            # only the stop is credible on the fill bar
            if hit_sl:
                return t, sl, "sl"
            continue

        if hit_sl and hit_tp:
            return (t, sl, "sl") if config.pessimistic_intrabar else (t, tp, "tp")
        if hit_sl:
            return t, sl, "sl"
        if hit_tp:
            return t, tp, "tp"

    if config.close_at_end:
        return n - 1, float(close[-1]), "eod"
    return None, None, None
