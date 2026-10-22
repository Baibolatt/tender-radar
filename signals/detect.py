"""Run every causal detector once and bundle the results."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config import Config
from signals.fvg import find_fvgs
from signals.ob import find_order_blocks
from signals.structure import find_structure
from signals.sweep import find_sweeps
from signals.swing import find_swings
from signals.utils import atr as atr_series


@dataclass
class SignalSet:
    df: pd.DataFrame
    pivots: pd.DataFrame
    structure: pd.DataFrame
    fvgs: pd.DataFrame
    order_blocks: pd.DataFrame
    sweeps: pd.DataFrame
    atr: pd.Series
    bias: pd.Series

    @property
    def counts(self) -> dict[str, int]:
        return {
            "swings": len(self.pivots),
            "bos_choch": len(self.structure),
            "fvg": len(self.fvgs),
            "order_blocks": len(self.order_blocks),
            "sweeps": len(self.sweeps),
        }

    def summary(self) -> str:
        c = self.counts
        bull_bos = int(((self.structure["kind"] == "BOS") & (self.structure["side"] == 1)).sum())
        bull_choch = int(((self.structure["kind"] == "CHoCH") & (self.structure["side"] == 1)).sum())
        return (
            f"swings={c['swings']}  BOS/CHoCH={c['bos_choch']} "
            f"(bull BOS {bull_bos}, bull CHoCH {bull_choch})  "
            f"FVG={c['fvg']}  OB={c['order_blocks']}  sweeps={c['sweeps']}"
        )


def detect(df: pd.DataFrame, config: Config) -> SignalSet:
    """Compute pivots -> structure -> FVG/OB/sweeps in dependency order.

    All returned frames are stamped with ``confirm_index``; no detector reads a
    bar beyond the one it is reporting.
    """
    pivots = find_swings(df, config.swing_length)
    structure, bias = find_structure(df, pivots)
    fvgs = find_fvgs(df)
    order_blocks = find_order_blocks(df, structure)
    atr = atr_series(df, config.atr_period)
    sweeps = find_sweeps(df, pivots, atr, config.sweep_min_atr)

    return SignalSet(
        df=df,
        pivots=pivots,
        structure=structure,
        fvgs=fvgs,
        order_blocks=order_blocks,
        sweeps=sweeps,
        atr=atr,
        bias=bias,
    )
