"""Small shared helpers for the signal layer."""

from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder-style ATR using only past/current bars (causal).

    At bar ``t`` the value depends on bars ``<= t``, so it may be used when
    evaluating a setup that triggers at the close of ``t``.
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def empty_frame(columns: list[str]) -> pd.DataFrame:
    """Typed empty frame so callers never hit a missing-column KeyError."""
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def first_true(mask: np.ndarray, start: int) -> int | None:
    """Position of the first ``True`` at or after ``start`` (or ``None``)."""
    if start >= len(mask):
        return None
    idx = np.flatnonzero(mask[start:])
    return int(start + idx[0]) if idx.size else None
