"""Causal swing-high / swing-low detection.

A pivot at bar ``i`` is a local extreme of the window ``[i-n, i+n]``.  It can
therefore only be *known* once ``i+n`` has closed.  The returned frame exposes

    index           positional bar of the pivot (for drawing)
    confirm_index   positional bar at which the pivot becomes known
    confirm_time    timestamp of that bar

Every downstream module filters on ``confirm_index``, never on ``index``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SWING_COLUMNS = ["index", "time", "kind", "price", "confirm_index", "confirm_time"]


def _collapse_consecutive(mask: np.ndarray) -> list[int]:
    """Keep the first bar of every consecutive ``True`` run.

    Equal highs/lows form plateaus; without this they would emit one pivot per
    bar of the plateau.
    """
    positions: list[int] = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            positions.append(i)
            while i + 1 < n and mask[i + 1]:
                i += 1
        i += 1
    return positions


def find_swings(df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """Return confirmed swing highs/lows.

    ``n`` bars must exist on both sides of the pivot.  The confirmation lag is
    exactly ``n`` bars; with ``n=1`` a pivot is known one bar later, with
    ``n=3`` three bars later, and so on.
    """
    if n < 1:
        raise ValueError("swing length must be >= 1")
    if len(df) < 2 * n + 1:
        return pd.DataFrame(columns=SWING_COLUMNS)

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    window = 2 * n + 1

    roll_max = pd.Series(high).rolling(
        window, center=True, min_periods=window
    ).max().to_numpy()
    roll_min = pd.Series(low).rolling(
        window, center=True, min_periods=window
    ).min().to_numpy()

    # A bar is a pivot if it *is* the extreme of its window.  Roll produces NaN
    # at the edges, so comparisons there are False (guarded below).
    with np.errstate(invalid="ignore"):
        is_high = np.isfinite(roll_max) & (high == roll_max)
        is_low = np.isfinite(roll_min) & (low == roll_min)

    rows: list[dict] = []
    for i in _collapse_consecutive(is_high):
        rows.append(
            {
                "index": i,
                "kind": 1,
                "price": float(high[i]),
            }
        )
    for i in _collapse_consecutive(is_low):
        rows.append(
            {
                "index": i,
                "kind": -1,
                "price": float(low[i]),
            }
        )

    if not rows:
        return pd.DataFrame(columns=SWING_COLUMNS)

    out = pd.DataFrame(rows)
    out["time"] = df.index.take(out["index"].to_numpy())
    out["confirm_index"] = out["index"] + n
    out["confirm_time"] = df.index.take(out["confirm_index"].to_numpy())
    out = out.sort_values(["confirm_index", "index", "kind"], kind="stable")
    return out.reset_index(drop=True)[SWING_COLUMNS]


def confirmed_by(swings: pd.DataFrame, position: int) -> pd.DataFrame:
    """Pivots knowable at ``position`` (inclusive).  Convenience for tests."""
    if swings.empty:
        return swings
    return swings[swings["confirm_index"] <= position]
