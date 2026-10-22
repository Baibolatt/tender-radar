"""Fair Value Gap (imbalance) detection.

A three-candle pattern ``(i-1, i, i+1)``:

* bullish FVG  -> ``high[i-1] < low[i+1]`` and the middle candle is bullish.
  The gap is ``[high[i-1], low[i+1]]`` and acts as support.
* bearish FVG  -> ``low[i-1] > high[i+1]`` and the middle candle is bearish.
  The gap is ``[high[i+1], low[i-1]]`` and acts as resistance.

The pattern is only complete when candle ``i+1`` closes, hence
``confirm_index = i + 1``.  ``mitigated_index`` is the first later bar that
trades into the gap; ``filled_index`` the first bar that trades through the
far edge (which invalidates the gap).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FVG_COLUMNS = [
    "index",
    "time",
    "confirm_index",
    "confirm_time",
    "side",
    "top",
    "bottom",
    "mitigated_index",
    "mitigated_time",
    "filled_index",
    "filled_time",
]


def find_fvgs(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 3:
        return pd.DataFrame(columns=FVG_COLUMNS)

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    # middle bar positions 1 .. N-2
    bull = (h[:-2] < l[2:]) & (c[1:-1] > o[1:-1])
    bear = (l[:-2] > h[2:]) & (c[1:-1] < o[1:-1])

    rows: list[dict] = []
    for middle in np.flatnonzero(bull | bear):
        i = int(middle + 1)
        if bull[middle]:
            side, top, bottom = 1, float(l[i + 1]), float(h[i - 1])
        else:
            side, top, bottom = -1, float(l[i - 1]), float(h[i + 1])

        confirm_index = i + 1
        start = confirm_index + 1
        if side == 1:
            seg_low = l[start:]
            touch = int(np.argmax(seg_low <= top)) if seg_low.size else -1
            filled = int(np.argmax(seg_low <= bottom)) if seg_low.size else -1
            touch_ok = seg_low.size and seg_low[touch] <= top
            filled_ok = seg_low.size and seg_low[filled] <= bottom
        else:
            seg_high = h[start:]
            touch = int(np.argmax(seg_high >= bottom)) if seg_high.size else -1
            filled = int(np.argmax(seg_high >= top)) if seg_high.size else -1
            touch_ok = seg_high.size and seg_high[touch] >= bottom
            filled_ok = seg_high.size and seg_high[filled] >= top

        mitigated_index = start + touch if touch_ok else None
        filled_index = start + filled if filled_ok else None
        rows.append(
            {
                "index": i,
                "side": side,
                "top": top,
                "bottom": bottom,
                "confirm_index": confirm_index,
                "mitigated_index": mitigated_index,
                "filled_index": filled_index,
            }
        )

    if not rows:
        return pd.DataFrame(columns=FVG_COLUMNS)

    out = pd.DataFrame(rows)
    out["time"] = df.index.take(out["index"].to_numpy())
    out["confirm_time"] = df.index.take(out["confirm_index"].to_numpy())

    def _times(col: str) -> pd.Series:
        return out[col].map(
            lambda p: df.index[int(p)] if pd.notna(p) and p is not None else pd.NaT
        )

    out["mitigated_time"] = _times("mitigated_index")
    out["filled_time"] = _times("filled_index")
    out = out.sort_values(["confirm_index", "index"], kind="stable")
    return out.reset_index(drop=True)[FVG_COLUMNS]


def active_fvgs_at(fvgs: pd.DataFrame, position: int, max_age: int) -> pd.DataFrame:
    """FVGs knowable at ``position``, not yet filled, and not stale."""
    if fvgs.empty:
        return fvgs
    known = fvgs["confirm_index"] <= position
    not_filled = fvgs["filled_index"].isna() | (fvgs["filled_index"] > position)
    fresh = (position - fvgs["confirm_index"]) <= max_age
    return fvgs[known & not_filled & fresh]
