"""Higher-timeframe bias, aligned to the execution timeframe without leakage.

A resampled candle labelled ``T`` covers ``[T, T + htf)`` and closes at
``T + htf``.  Its bias may therefore only be applied to LTF bars whose open is
at or after ``T + htf``.  We re-index by the *close* time and forward-fill.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import Config
from data.loader import parse_timeframe, resample_ohlcv
from signals.structure import find_structure
from signals.swing import find_swings


def compute_htf_bias(df: pd.DataFrame, config: Config) -> pd.Series:
    """Return a per-LTF-bar bias series in {-1, 0, +1}."""
    htf = resample_ohlcv(df, config.htf_timeframe)
    if len(htf) < 3:
        return pd.Series(0, index=df.index, dtype="int8")

    if config.htf_bias_mode == "ema":
        ema = htf["close"].ewm(
            span=config.htf_ema_period,
            adjust=False,
            min_periods=config.htf_ema_period,
        ).mean()
        raw = np.sign(htf["close"] - ema).astype("int8")
    elif config.htf_bias_mode == "structure":
        pivots = find_swings(htf, config.htf_swing_length)
        _, raw = find_structure(htf, pivots)
    else:
        raise ValueError(f"unknown htf_bias_mode: {config.htf_bias_mode!r}")

    # map each HTF candle to the instant it closes
    close_times = htf.index + parse_timeframe(config.htf_timeframe)
    raw = pd.Series(raw.to_numpy(), index=close_times).sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]

    aligned = raw.reindex(df.index, method="ffill")
    return aligned.fillna(0).astype("int8")


def htf_frame(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Convenience accessor for plotting/reporting."""
    return resample_ohlcv(df, config.htf_timeframe)
