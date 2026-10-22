"""Break of Structure / Change of Character.

A break happens when a candle **closes** beyond a confirmed swing level:

* close above the most recent unbroken swing high -> bullish break;
* close below the most recent unbroken swing low  -> bearish break.

The break is classified against the prevailing structure:

* same direction as the last break -> **BOS** (continuation);
* opposite direction                -> **CHoCH** (reversal).

The event is stamped on the breaking candle, so ``confirm_index == index``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STRUCTURE_COLUMNS = [
    "index",
    "time",
    "confirm_index",
    "confirm_time",
    "kind",
    "side",
    "level",
    "swing_index",
    "swing_time",
    "swing_confirm_index",
]


def find_structure(
    df: pd.DataFrame, swings: pd.DataFrame
) -> tuple[pd.DataFrame, pd.Series]:
    """Return ``(events, bias)``.

    ``bias[t]`` is the structure direction (+1/-1/0) at the close of bar ``t``
    and is safe to read at that bar.
    """
    n_bars = len(df)
    close = df["close"].to_numpy(dtype=float)
    bias = np.zeros(n_bars, dtype=np.int8)

    if swings.empty or n_bars == 0:
        return pd.DataFrame(columns=STRUCTURE_COLUMNS), pd.Series(bias, index=df.index)

    pivots = swings.sort_values(["confirm_index", "index"], kind="stable").reset_index(
        drop=True
    )
    ptr = 0
    active_highs: list[dict] = []
    active_lows: list[dict] = []
    structure = 0
    events: list[dict] = []

    for t in range(n_bars):
        # 1) make every pivot confirmed by the close of `t` visible
        while ptr < len(pivots) and int(pivots.at[ptr, "confirm_index"]) <= t:
            row = pivots.iloc[ptr]
            item = {
                "index": int(row["index"]),
                "level": float(row["price"]),
                "confirm_index": int(row["confirm_index"]),
            }
            (active_highs if int(row["kind"]) == 1 else active_lows).append(item)
            ptr += 1

        bull = [h for h in active_highs if h["level"] < close[t]]
        bear = [l for l in active_lows if l["level"] > close[t]]

        # Extremely rare: both sides broken in one bar.  Follow the candle body.
        if bull and bear:
            if close[t] >= df["open"].iat[t]:
                bear = []
            else:
                bull = []

        if bull:
            chosen = max(bull, key=lambda h: h["index"])
            active_highs = [h for h in active_highs if h["level"] >= close[t]]
            kind = "CHoCH" if structure == -1 else "BOS"
            structure = 1
            events.append(_event(df, t, kind, 1, chosen))
        elif bear:
            chosen = max(bear, key=lambda l: l["index"])
            active_lows = [l for l in active_lows if l["level"] <= close[t]]
            kind = "CHoCH" if structure == 1 else "BOS"
            structure = -1
            events.append(_event(df, t, kind, -1, chosen))

        bias[t] = structure

    if not events:
        return pd.DataFrame(columns=STRUCTURE_COLUMNS), pd.Series(bias, index=df.index)

    out = pd.DataFrame(events)
    out = out.sort_values(["index", "side"], kind="stable").reset_index(drop=True)
    return out[STRUCTURE_COLUMNS], pd.Series(bias, index=df.index)


def _event(df: pd.DataFrame, t: int, kind: str, side: int, chosen: dict) -> dict:
    return {
        "index": t,
        "time": df.index[t],
        "confirm_index": t,
        "confirm_time": df.index[t],
        "kind": kind,
        "side": side,
        "level": chosen["level"],
        "swing_index": chosen["index"],
        "swing_time": df.index[chosen["index"]],
        "swing_confirm_index": chosen["confirm_index"],
    }
