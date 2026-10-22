"""Cross-check our detectors against the ``smartmoneyconcepts`` package.

The package is a *retrospective* marker: swings are stamped at the pivot bar
and FVGs at the middle bar, both using future candles.  It is therefore great
for comparison but unsafe as a signal source.  We compare *locations*, not
timing, to validate that our causal detectors find the same market facts.
"""

from __future__ import annotations

import pandas as pd

from signals.detect import SignalSet


def _smc():
    from smartmoneyconcepts import smc

    return smc


def smc_swings(df: pd.DataFrame, swing_length: int) -> pd.DataFrame:
    raw = _smc().swing_highs_lows(df, swing_length=swing_length)
    frame = pd.DataFrame(
        {
            "index": range(len(raw)),
            "kind": raw["HighLow"].to_numpy(),
            "price": raw["Level"].to_numpy(),
        }
    )
    return frame.dropna(subset=["kind", "price"]).reset_index(drop=True)


def smc_fvgs(df: pd.DataFrame) -> pd.DataFrame:
    raw = _smc().fvg(df, join_consecutive=False)
    frame = pd.DataFrame(
        {
            "index": range(len(raw)),
            "side": raw["FVG"].to_numpy(),
            "top": raw["Top"].to_numpy(),
            "bottom": raw["Bottom"].to_numpy(),
        }
    )
    return frame.dropna(subset=["side"]).reset_index(drop=True)


def compare(df: pd.DataFrame, signals: SignalSet, swing_length: int) -> str:
    """Return a human-readable agreement report."""
    ours_sw = signals.pivots
    theirs_sw = smc_swings(df, swing_length)
    n = len(df)
    # ignore the edges: smartmoneyconcepts flips the first/last pivot
    ours_sw = ours_sw[(ours_sw["index"] >= swing_length) & (ours_sw["index"] < n - swing_length)]
    theirs_sw = theirs_sw[(theirs_sw["index"] >= swing_length) & (theirs_sw["index"] < n - swing_length)]

    def match(ours: pd.DataFrame, theirs: pd.DataFrame) -> tuple[int, int, int]:
        theirs_idx = {(int(r.index), int(r.kind)) for r in theirs.itertuples()}
        hits = 0
        for r in ours.itertuples():
            if any((int(r.index) + d, int(r.kind)) in theirs_idx for d in (-1, 0, 1)):
                hits += 1
        return hits, len(ours), len(theirs)

    h, a, b = match(ours_sw, theirs_sw)
    ours_fv = signals.fvgs
    theirs_fv = smc_fvgs(df)
    theirs_fv_idx = {(int(r.index), int(r.side)) for r in theirs_fv.itertuples()}
    fv_hits = sum(
        1 for r in ours_fv.itertuples() if (int(r.index), int(r.side)) in theirs_fv_idx
    )

    return (
        "smartmoneyconcepts cross-check\n"
        f"  swings : ours={a:>5}  smc={b:>5}  matched={h:>5} "
        f"({h / max(a, 1):.0%} recall)\n"
        f"  FVG    : ours={len(ours_fv):>5}  smc={len(theirs_fv):>5}  "
        f"exact={fv_hits:>5} ({fv_hits / max(len(ours_fv), 1):.0%})\n"
        "  (small differences are expected: smc keeps plateau/edge pivots\n"
        "   that our causal dedup drops.)"
    )
