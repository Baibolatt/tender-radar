"""Order Blocks (simplified ICT definition).

A bullish order block is the last bearish candle before an impulsive move that
breaks structure to the upside; a bearish order block is the last bullish
candle before a downside break.  The zone is the candle's full range.

``index`` is the origin candle (drawn on the chart) but the block is only
*confirmed* on the structure-break candle (``confirm_index``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

OB_COLUMNS = [
    "index",
    "time",
    "confirm_index",
    "confirm_time",
    "break_index",
    "side",
    "top",
    "bottom",
    "mitigated_index",
    "mitigated_time",
    "filled_index",
    "filled_time",
]


def find_order_blocks(
    df: pd.DataFrame,
    structure: pd.DataFrame,
    lookback: int = 20,
    use_body: bool = False,
) -> pd.DataFrame:
    if structure.empty or len(df) == 0:
        return pd.DataFrame(columns=OB_COLUMNS)

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    seen: set[int] = set()
    rows: list[dict] = []

    for _, ev in structure.iterrows():
        b = int(ev["index"])
        side = int(ev["side"])
        start = max(0, b - lookback)
        origin = None

        if side == 1:
            # last down candle before the bullish break
            for j in range(b - 1, start - 1, -1):
                if c[j] < o[j]:
                    origin = j
                    break
        else:
            for j in range(b - 1, start - 1, -1):
                if c[j] > o[j]:
                    origin = j
                    break

        if origin is None or origin in seen:
            continue
        seen.add(origin)

        top = float(max(o[origin], c[origin])) if use_body else float(h[origin])
        bottom = float(min(o[origin], c[origin])) if use_body else float(l[origin])

        confirm_index = b
        fill_start = b + 1
        if side == 1:
            seg_low = l[fill_start:]
            touch_ok = seg_low.size > 0 and bool((seg_low <= top).any())
            fill_ok = seg_low.size > 0 and bool((seg_low <= bottom).any())
            mitigated = fill_start + int(np.argmax(seg_low <= top)) if touch_ok else None
            filled = fill_start + int(np.argmax(seg_low <= bottom)) if fill_ok else None
        else:
            seg_high = h[fill_start:]
            touch_ok = seg_high.size > 0 and bool((seg_high >= bottom).any())
            fill_ok = seg_high.size > 0 and bool((seg_high >= top).any())
            mitigated = (
                fill_start + int(np.argmax(seg_high >= bottom)) if touch_ok else None
            )
            filled = fill_start + int(np.argmax(seg_high >= top)) if fill_ok else None

        rows.append(
            {
                "index": origin,
                "time": df.index[origin],
                "confirm_index": confirm_index,
                "confirm_time": df.index[confirm_index],
                "break_index": b,
                "side": side,
                "top": top,
                "bottom": bottom,
                "mitigated_index": mitigated,
                "filled_index": filled,
            }
        )

    if not rows:
        return pd.DataFrame(columns=OB_COLUMNS)

    out = pd.DataFrame(rows)

    def _times(col: str) -> pd.Series:
        return out[col].map(
            lambda p: df.index[int(p)] if pd.notna(p) and p is not None else pd.NaT
        )

    out["mitigated_time"] = _times("mitigated_index")
    out["filled_time"] = _times("filled_index")
    out = out.sort_values(["confirm_index", "index"], kind="stable")
    return out.reset_index(drop=True)[OB_COLUMNS]
