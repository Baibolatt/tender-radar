"""The SMC/ICT setup: HTF bias -> liquidity sweep -> CHoCH -> FVG entry.

Everything is evaluated at candle closes and every dependency is checked
against its ``confirm_index``.  Concretely, a long needs:

1. HTF bias bullish (or the bias filter disabled);
2. a confirmed swing low swept by a wick that closes back above it;
3. a bullish CHoCH (close above the most recent swing high) within
   ``max_bars_after_sweep`` bars of the sweep;
4. price retracing into an unmitigated bullish FVG that formed after the
   sweep, within ``max_bars_after_choch`` bars;
5. entry at the FVG edge, stop behind the sweep wick, target at ``rr``.

No entry is evaluated on the bar that produced its triggering event: events
confirmed at the close of ``t`` become actionable at ``t+1``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from config import Config
from signals.detect import SignalSet
from strategy.htf import compute_htf_bias

SETUP_COLUMNS = [
    "side",
    "entry_index",
    "entry_time",
    "entry_price",
    "sl",
    "tp",
    "risk",
    "rr",
    "sweep_index",
    "sweep_time",
    "sweep_level",
    "sweep_extreme",
    "choch_index",
    "choch_time",
    "choch_level",
    "fvg_index",
    "fvg_time",
    "fvg_top",
    "fvg_bottom",
    "bias",
]


@dataclass
class Setup:
    side: int                 # +1 long, -1 short
    entry_index: int
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    tp: float
    risk: float
    rr: float
    sweep_index: int
    sweep_time: pd.Timestamp
    sweep_level: float
    sweep_extreme: float
    choch_index: int
    choch_time: pd.Timestamp
    choch_level: float
    fvg_index: int
    fvg_time: pd.Timestamp
    fvg_top: float
    fvg_bottom: float
    bias: int


def generate_setups(
    df: pd.DataFrame, signals: SignalSet, config: Config
) -> pd.DataFrame:
    """Return every valid setup as a row (may overlap; the backtest decides)."""
    n = len(df)
    if n == 0:
        return pd.DataFrame(columns=SETUP_COLUMNS)

    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = signals.atr.to_numpy(dtype=float)

    bias = compute_htf_bias(df, config).to_numpy()

    sweeps_at: dict[int, pd.Series] = {}
    for _, row in signals.sweeps.iterrows():
        sweeps_at[int(row["index"])] = row

    structure_at: dict[int, pd.Series] = {}
    for _, row in signals.structure.iterrows():
        structure_at[int(row["index"])] = row

    # FVGs sorted by confirmation, consumed in order
    fvg_rows = [
        {
            "index": int(r.index),
            "confirm_index": int(r.confirm_index),
            "side": int(r.side),
            "top": float(r.top),
            "bottom": float(r.bottom),
            "time": r.time,
        }
        for r in signals.fvgs.itertuples()
    ]
    fvg_rows.sort(key=lambda r: r["confirm_index"])
    fvg_ptr = 0
    active_fvgs: list[dict] = []

    pending: dict | None = None
    setups: list[Setup] = []

    for t in range(n):
        # ---------------------------------------------------------- entries
        # `active_fvgs` were confirmed before this bar, so a touch now is a
        # genuine intrabar limit fill.  A gap stays usable until it is filled
        # (traded through the far edge) or goes stale.
        if pending is not None and pending["phase"] == "await_fvg":
            candidates = []
            for f in active_fvgs:
                if f["side"] != pending["side"]:
                    continue
                if f["index"] < pending["sweep_index"]:
                    continue
                if t - f["confirm_index"] > config.fvg_max_age:
                    continue
                if pending["side"] == 1:
                    if f["bottom"] <= pending["sweep_extreme"] + pending["buffer"]:
                        continue
                    entry_price = _entry_price(f, config.entry_at, side=1)
                    # a buy limit must sit *below* the bar's open; otherwise it
                    # would be a marketable order, not a retracement
                    if entry_price >= open_[t]:
                        continue
                    touched = low[t] <= entry_price
                else:
                    if f["top"] >= pending["sweep_extreme"] - pending["buffer"]:
                        continue
                    entry_price = _entry_price(f, config.entry_at, side=-1)
                    if entry_price <= open_[t]:
                        continue
                    touched = high[t] >= entry_price

                if touched:
                    candidates.append((f, entry_price))

            if candidates:
                # nearest gap first: highest top on a retrace down, lowest
                # bottom on a retrace up
                key = (lambda c: -c[1]) if pending["side"] == 1 else (lambda c: c[1])
                f, entry_price = sorted(candidates, key=key)[0]
                setup = _build_setup(df, pending, f, entry_price, t, config, bias)
                if setup is not None:
                    setups.append(setup)
                    pending = None

        # ----------------------------------------------------- invalidations
        if pending is not None:
            stale = (
                pending["phase"] == "await_choch"
                and t - pending["sweep_index"] > config.max_bars_after_sweep
            ) or (
                pending["phase"] == "await_fvg"
                and t - pending["choch_index"] > config.max_bars_after_choch
            )
            beyond = False
            if config.invalidate_on_close_beyond_sweep:
                if pending["side"] == 1:
                    beyond = close[t] < pending["sweep_extreme"]
                else:
                    beyond = close[t] > pending["sweep_extreme"]
            bias_gone = config.require_htf_bias and bias[t] != pending["side"]
            if stale or beyond or bias_gone:
                pending = None

        # ----------------------------------------------------- new sweep
        sweep = sweeps_at.get(t)
        if sweep is not None and pending is None:
            side = int(sweep["side"])
            bias_ok = (bias[t] == side) if config.require_htf_bias else True
            a = atr[t]
            if bias_ok and np.isfinite(a) and a > 0:
                pending = {
                    "side": side,
                    "sweep_index": t,
                    "sweep_time": sweep["time"],
                    "sweep_level": float(sweep["level"]),
                    "sweep_extreme": float(sweep["sweep_extreme"]),
                    "buffer": config.sl_buffer_atr * float(a),
                    "phase": "await_choch",
                    "choch_index": None,
                    "choch_time": None,
                    "choch_level": None,
                }

        # ------------------------------------------------- structure events
        event = structure_at.get(t)
        if event is not None and pending is not None:
            side = int(event["side"])
            if side != pending["side"]:
                # a CHoCH against the thesis kills it
                if event["kind"] == "CHoCH":
                    pending = None
            elif (
                pending["phase"] == "await_choch"
                and t > pending["sweep_index"]
                and (event["kind"] == "CHoCH" or not config.require_choch)
            ):
                pending["phase"] = "await_fvg"
                pending["choch_index"] = t
                pending["choch_time"] = event["time"]
                pending["choch_level"] = float(event["level"])

        # ------------------------------------- make newly confirmed FVGs live
        # They are usable from the next bar onwards, never on this one.
        while fvg_ptr < len(fvg_rows) and fvg_rows[fvg_ptr]["confirm_index"] <= t:
            active_fvgs.append(dict(fvg_rows[fvg_ptr]))
            fvg_ptr += 1

        # drop stale gaps and gaps price has fully traded through
        active_fvgs = [
            f
            for f in active_fvgs
            if (t - f["confirm_index"]) <= config.fvg_max_age
            and not (f["side"] == 1 and low[t] <= f["bottom"])
            and not (f["side"] == -1 and high[t] >= f["top"])
        ]

    if not setups:
        return pd.DataFrame(columns=SETUP_COLUMNS)
    return pd.DataFrame([asdict(s) for s in setups])[SETUP_COLUMNS]


# ---------------------------------------------------------------------------
def _entry_price(fvg: dict, mode: str, side: int) -> float:
    if mode == "mid":
        return (fvg["top"] + fvg["bottom"]) / 2.0
    return fvg["top"] if side == 1 else fvg["bottom"]


def _build_setup(
    df: pd.DataFrame,
    pending: dict,
    fvg: dict,
    entry_price: float,
    t: int,
    config: Config,
    bias: np.ndarray,
) -> Setup | None:
    side = pending["side"]
    buffer = pending["buffer"]

    if side == 1:
        sl = pending["sweep_extreme"] - buffer
        risk = entry_price - sl
        tp = entry_price + config.rr * risk
    else:
        sl = pending["sweep_extreme"] + buffer
        risk = sl - entry_price
        tp = entry_price - config.rr * risk

    # sanity: entry must sit between stop and target
    if not np.isfinite(risk) or risk <= 0:
        return None
    if side == 1 and not (sl < entry_price < tp):
        return None
    if side == -1 and not (tp < entry_price < sl):
        return None

    return Setup(
        side=side,
        entry_index=t,
        entry_time=df.index[t],
        entry_price=float(entry_price),
        sl=float(sl),
        tp=float(tp),
        risk=float(risk),
        rr=float(config.rr),
        sweep_index=pending["sweep_index"],
        sweep_time=pending["sweep_time"],
        sweep_level=pending["sweep_level"],
        sweep_extreme=pending["sweep_extreme"],
        choch_index=pending["choch_index"],
        choch_time=pending["choch_time"],
        choch_level=pending["choch_level"],
        fvg_index=fvg["index"],
        fvg_time=fvg["time"],
        fvg_top=fvg["top"],
        fvg_bottom=fvg["bottom"],
        bias=int(bias[t]),
    )
