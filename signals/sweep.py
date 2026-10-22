"""Liquidity sweeps (stop hunts / "turtle soup").

A bullish sweep of lows happens when a candle wicks below a previously
confirmed swing low but closes back above it.  The wick is the liquidity grab;
the close is the rejection.  Bearish sweeps mirror this around swing highs.

``sweep_extreme`` is the wick extreme of the sweeping candle -- the level a
protective stop is placed behind.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SWEEP_COLUMNS = [
    "index",
    "time",
    "confirm_index",
    "confirm_time",
    "side",
    "level",
    "swing_index",
    "swing_time",
    "sweep_extreme",
]


def find_sweeps(
    df: pd.DataFrame,
    swings: pd.DataFrame,
    atr: pd.Series,
    min_atr: float = 0.05,
) -> pd.DataFrame:
    n_bars = len(df)
    if swings.empty or n_bars == 0:
        return pd.DataFrame(columns=SWEEP_COLUMNS)

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr_arr = atr.to_numpy(dtype=float)

    pivots = swings.sort_values(["confirm_index", "index"], kind="stable").reset_index(
        drop=True
    )
    ptr = 0
    active_highs: list[dict] = []
    active_lows: list[dict] = []
    events: list[dict] = []

    for t in range(n_bars):
        while ptr < len(pivots) and int(pivots.at[ptr, "confirm_index"]) <= t:
            row = pivots.iloc[ptr]
            item = {
                "index": int(row["index"]),
                "level": float(row["price"]),
                "confirm_index": int(row["confirm_index"]),
            }
            (active_highs if int(row["kind"]) == 1 else active_lows).append(item)
            ptr += 1

        a = atr_arr[t]
        if not np.isfinite(a):
            continue
        buffer = min_atr * a

        # A level is *consumed* the first time price trades through it, whether
        # that is a stoppage reclaim (sweep) or a clean breakdown.  Consuming on
        # the raw wick (not the ATR-filtered pierce) stops already-broken
        # levels from being reported as fresh liquidity later.
        pierced_lows = [x for x in active_lows if low[t] < x["level"]]
        reclaimed_lows = [
            x for x in pierced_lows if close[t] > x["level"] and x["level"] - low[t] >= buffer
        ]
        active_lows = [x for x in active_lows if x["level"] <= low[t]]

        pierced_highs = [x for x in active_highs if high[t] > x["level"]]
        reclaimed_highs = [
            x for x in pierced_highs if close[t] < x["level"] and high[t] - x["level"] >= buffer
        ]
        active_highs = [x for x in active_highs if x["level"] >= high[t]]

        # at most one sweep per bar; prefer the side the candle closed toward
        if reclaimed_lows and reclaimed_highs:
            if close[t] >= df["open"].iat[t]:
                reclaimed_highs = []
            else:
                reclaimed_lows = []

        if reclaimed_lows:
            chosen = max(reclaimed_lows, key=lambda x: x["index"])
            events.append(_event(df, t, 1, chosen, float(low[t])))
        elif reclaimed_highs:
            chosen = max(reclaimed_highs, key=lambda x: x["index"])
            events.append(_event(df, t, -1, chosen, float(high[t])))

    if not events:
        return pd.DataFrame(columns=SWEEP_COLUMNS)

    out = pd.DataFrame(events).sort_values("index", kind="stable").reset_index(drop=True)
    return out[SWEEP_COLUMNS]


def _event(
    df: pd.DataFrame, t: int, side: int, chosen: dict, extreme: float
) -> dict:
    return {
        "index": t,
        "time": df.index[t],
        "confirm_index": t,
        "confirm_time": df.index[t],
        "side": side,
        "level": chosen["level"],
        "swing_index": chosen["index"],
        "swing_time": df.index[chosen["index"]],
        "sweep_extreme": extreme,
    }
