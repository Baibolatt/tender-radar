"""Static SMC/ICT markup chart.

Palette follows the project's validated light/dark data-viz palette.  Bullish
candles are filled, bearish candles hollow: direction is never encoded by hue
alone (the aqua/red pair sits in the 6-8 CVD band, so shape is the secondary
channel).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.collections import LineCollection, PatchCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from config import Config  # noqa: E402
from signals.detect import SignalSet  # noqa: E402

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "page": "#f9f9f7",
        "ink": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "bull": "#1baf7a",
        "bear": "#e34948",
        "blue": "#2a78d6",
        "orange": "#eb6834",
        "violet": "#6250d6",
        "yellow": "#eda100",
        "good": "#0ca30c",
        "critical": "#d03b3b",
    },
    "dark": {
        "surface": "#1a1a19",
        "page": "#0d0d0d",
        "ink": "#f0efec",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "bull": "#199e70",
        "bear": "#e66767",
        "blue": "#3987e5",
        "orange": "#d95926",
        "violet": "#9085e9",
        "yellow": "#c98500",
        "good": "#0ca30c",
        "critical": "#d03b3b",
    },
}


def plot_markup(
    df: pd.DataFrame,
    signals: SignalSet,
    config: Config,
    setups: pd.DataFrame | None = None,
    trades: pd.DataFrame | None = None,
    last_n: int | None = None,
    start: str | None = None,
    end: str | None = None,
    theme: str = "light",
    out_path: str | Path | None = None,
) -> Path:
    """Render candles + swings + BOS/CHoCH + FVG + OB + sweeps (+ trades)."""
    theme = theme.lower()
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {list(THEMES)}")
    c = THEMES[theme]

    window, offset = _slice_window(df, last_n, start, end)
    if window.empty:
        raise ValueError("selected window has no candles")

    x0, x1 = 0, len(window) - 1
    xs = np.arange(len(window))
    dense = len(window) > 500

    fig = plt.figure(figsize=(16, 10), facecolor=c["surface"])
    gs = fig.add_gridspec(
        3, 1, height_ratios=[7.2, 1.1, 1.0], hspace=0.06,
        left=0.055, right=0.985, top=0.90, bottom=0.155,
    )
    ax = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax)
    ax_bias = fig.add_subplot(gs[2], sharex=ax)

    for a in (ax, ax_vol, ax_bias):
        a.set_facecolor(c["surface"])
        a.grid(True, color=c["grid"], linewidth=0.6, alpha=0.9, linestyle="-")
        a.tick_params(colors=c["muted"], labelsize=8, length=0)
        for spine in ("top", "right"):
            a.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            a.spines[spine].set_color(c["axis"])
            a.spines[spine].set_linewidth(0.8)

    # ------------------------------------------------------------- candles
    _draw_candles(ax, window, xs, c)

    # --------------------------------------------------------------- zones
    _draw_fvgs(ax, signals.fvgs, offset, x0, x1, c)
    _draw_order_blocks(ax, signals.order_blocks, offset, x0, x1, c)

    # ------------------------------------------------------------- structure
    _draw_structure(ax, signals.structure, offset, x0, x1, c, dense)

    # --------------------------------------------------------------- swings
    _draw_swings(ax, signals.pivots, window, offset, c, dense)

    # --------------------------------------------------------------- sweeps
    _draw_sweeps(ax, signals.sweeps, offset, x0, x1, c, dense)

    # --------------------------------------------------------------- trades
    if trades is not None and not trades.empty:
        _draw_trades(ax, trades, offset, x0, x1, c)

    # ------------------------------------------------------------- volume
    _draw_volume(ax_vol, window, xs, c)

    # --------------------------------------------------------------- bias
    _draw_bias(ax_bias, signals.bias, offset, xs, c)

    # -------------------------------------------------------------- labels
    ax.set_ylabel("price", color=c["secondary"], fontsize=9)
    ax_vol.set_ylabel("volume", color=c["secondary"], fontsize=9)
    ax_bias.set_ylabel("HTF bias", color=c["secondary"], fontsize=9)
    ax_bias.set_yticks([-1, 0, 1])
    ax_bias.set_yticklabels(["bear", "flat", "bull"], fontsize=7)

    _format_axis(ax_bias, window, xs)
    ax.tick_params(labelbottom=False)
    ax_vol.tick_params(labelbottom=False)

    counts = signals.counts
    fig.suptitle(
        f"{config.symbol}  ·  {config.timeframe}  ·  SMC/ICT markup",
        color=c["ink"], fontsize=15, fontweight="bold", x=0.055, ha="left", y=0.975,
    )
    subtitle = (
        f"{window.index[0]:%Y-%m-%d %H:%M} → {window.index[-1]:%Y-%m-%d %H:%M} UTC"
        f"   |   swings {counts['swings']}  ·  BOS/CHoCH {counts['bos_choch']}"
        f"  ·  FVG {counts['fvg']}  ·  OB {counts['order_blocks']}"
        f"  ·  sweeps {counts['sweeps']}"
        f"   |   pivots confirmed after {config.swing_length} closed bars"
    )
    fig.text(0.055, 0.945, subtitle, color=c["secondary"], fontsize=9, ha="left")

    _legend(fig, c, has_trades=trades is not None and not trades.empty)

    out = Path(out_path) if out_path else (
        config.chart_dir / f"{config.cache_stem}_markup_{len(window)}bars_{theme}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, facecolor=c["surface"], bbox_inches="tight")
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# window handling
# ---------------------------------------------------------------------------
def _slice_window(
    df: pd.DataFrame, last_n: int | None, start: str | None, end: str | None
) -> tuple[pd.DataFrame, int]:
    if start or end:
        lo = _ts(start) if start else df.index[0]
        hi = _ts(end) if end else df.index[-1]
        window = df.loc[lo:hi]
    elif last_n:
        window = df.iloc[-int(last_n) :]
    else:
        window = df
    if window.empty:
        return window, 0
    offset = int(df.index.get_loc(window.index[0]))
    return window, offset


def _ts(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts


# ---------------------------------------------------------------------------
# drawing helpers
# ---------------------------------------------------------------------------
def _draw_candles(ax, df: pd.DataFrame, xs: np.ndarray, c: dict) -> None:
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    cl = df["close"].to_numpy(float)

    wick_segments = [[(x, lo), (x, hi)] for x, lo, hi in zip(xs, l, h)]
    ax.add_collection(
        LineCollection(wick_segments, colors=c["axis"], linewidths=0.5, zorder=2)
    )

    width = 0.66
    bull_rects, bear_rects = [], []
    span = max(float(np.nanmax(h) - np.nanmin(l)), 1e-9)
    floor = span * 0.0006
    for x, oo, cc in zip(xs, o, cl):
        bottom = min(oo, cc)
        height = max(abs(cc - oo), floor)
        rect = Rectangle((x - width / 2, bottom), width, height)
        (bull_rects if cc >= oo else bear_rects).append(rect)

    ax.add_collection(
        PatchCollection(
            bull_rects, facecolor=c["bull"], edgecolor=c["bull"],
            linewidth=0.5, zorder=3,
        )
    )
    # hollow bearish bodies carry direction without relying on hue
    ax.add_collection(
        PatchCollection(
            bear_rects, facecolor=c["surface"], edgecolor=c["bear"],
            linewidth=0.9, zorder=3,
        )
    )
    ax.set_xlim(-1, len(xs))
    ax.set_ylim(float(np.nanmin(l)) - span * 0.03, float(np.nanmax(h)) + span * 0.05)


def _draw_fvgs(ax, fvgs, offset, x0, x1, c, max_forward: int = 50) -> None:
    if fvgs.empty:
        return
    for row in fvgs.itertuples():
        start = int(row.confirm_index) - offset
        if start > x1:
            continue
        natural_end = x1 if pd.isna(row.mitigated_index) else int(row.mitigated_index) - offset
        end = min(natural_end, start + max_forward, x1)
        if end < x0:
            continue
        start = max(start, x0)
        if end <= start:
            end = min(start + 1, x1)
        color = c["bull"] if int(row.side) == 1 else c["bear"]
        ax.add_patch(
            Rectangle(
                (start, float(row.bottom)),
                end - start,
                float(row.top) - float(row.bottom),
                facecolor=color,
                edgecolor=color,
                alpha=0.13,
                linewidth=0.6,
                zorder=1,
            )
        )


def _draw_order_blocks(ax, obs, offset, x0, x1, c, max_forward: int = 40) -> None:
    if obs.empty:
        return
    for row in obs.itertuples():
        start = int(row.index) - offset
        natural_end = x1 if pd.isna(row.mitigated_index) else int(row.mitigated_index) - offset
        end = min(natural_end, start + max_forward, x1)
        if start > x1 or end < x0:
            continue
        start = max(start, x0)
        if end <= start:
            end = min(start + 1, x1)
        ax.add_patch(
            Rectangle(
                (start, float(row.bottom)),
                end - start,
                float(row.top) - float(row.bottom),
                facecolor=c["violet"],
                edgecolor=c["violet"],
                alpha=0.10,
                linewidth=0.7,
                linestyle=(0, (2, 2)),
                zorder=1,
            )
        )


def _draw_structure(ax, structure, offset, x0, x1, c, dense: bool,
                    max_events: int = 24, max_labels: int = 8) -> None:
    if structure.empty:
        return
    break_x = structure["index"].to_numpy() - offset
    swing_x = structure["swing_index"].to_numpy() - offset
    visible = structure[(break_x >= x0) & (swing_x <= x1)].sort_values("index")
    if visible.empty:
        return
    visible = visible.tail(max_events)

    # choose label positions greedily from the most recent event backwards so
    # that no two labels land on top of each other
    label_idx: set[int] = set()
    if not dense:
        y_lo, y_hi = ax.get_ylim()
        min_dy = (y_hi - y_lo) * 0.045
        min_dx = max(10, (x1 - x0) // 30)
        kept: list[tuple[int, float]] = []
        first = max(0, len(visible) - max_labels)
        for pos in range(len(visible) - 1, first - 1, -1):
            rec = visible.iloc[pos]
            lx = min(int(rec["index"]) - offset, x1)
            ly = float(rec["level"])
            if any(abs(lx - kx) < min_dx and abs(ly - ky) < min_dy for kx, ky in kept):
                continue
            kept.append((lx, ly))
            label_idx.add(pos)

    for i, row in enumerate(visible.itertuples()):
        start = max(int(row.swing_index) - offset, x0)
        end = min(int(row.index) - offset, x1)
        if end < start:
            continue
        is_choch = row.kind == "CHoCH"
        color = c["orange"] if is_choch else c["blue"]
        ax.plot(
            [start, end], [float(row.level), float(row.level)],
            color=color, linewidth=1.15,
            linestyle=(0, (4, 2)) if is_choch else "-",
            zorder=4, solid_capstyle="round",
        )
        ax.plot(
            [end], [float(row.level)], marker="o", markersize=3.2,
            color=color, zorder=5,
        )
        if i in label_idx:
            up = 1 if int(row.side) == 1 else -1
            base = 5 if up == 1 else -10
            # stagger every other label further from the line
            ax.annotate(
                row.kind,
                xy=(end, float(row.level)),
                xytext=(2, base + up * 11 * (i % 2)),
                textcoords="offset points",
                fontsize=6.5, color=c["secondary"], zorder=6,
            )


def _draw_swings(ax, pivots, window, offset, c, dense: bool) -> None:
    if pivots.empty:
        return
    lo, hi = offset, offset + len(window) - 1
    view = pivots[(pivots["index"] >= lo) & (pivots["index"] <= hi)]
    if view.empty:
        return
    highs = view[view["kind"] == 1]
    lows = view[view["kind"] == -1]
    if not highs.empty:
        ax.scatter(
            highs["index"] - offset, highs["price"], marker="^", s=22,
            facecolor=c["surface"], edgecolor=c["muted"], linewidth=0.8, zorder=6,
        )
    if not lows.empty:
        ax.scatter(
            lows["index"] - offset, lows["price"], marker="v", s=22,
            facecolor=c["surface"], edgecolor=c["muted"], linewidth=0.8, zorder=6,
        )


def _draw_sweeps(ax, sweeps, offset, x0, x1, c, dense: bool,
                 max_labels: int = 10) -> None:
    if sweeps.empty:
        return
    x_pos = sweeps["index"].to_numpy() - offset
    view = sweeps[(x_pos >= x0) & (x_pos <= x1)]
    if view.empty:
        return
    view = view.tail(40)
    x_pos = view["index"].to_numpy() - offset
    ax.scatter(
        x_pos, view["sweep_extreme"].to_numpy(float), marker="X", s=42,
        color=c["yellow"], edgecolor=c["surface"], linewidth=0.8, zorder=7,
    )
    if dense or len(view) > max_labels:
        return
    for row, x in zip(view.itertuples(), x_pos):
        ax.annotate(
            "sweep",
            xy=(x, float(row.sweep_extreme)),
            xytext=(3, -11 if int(row.side) == 1 else 8),
            textcoords="offset points",
            fontsize=6.2, color=c["secondary"], zorder=8,
        )


def _draw_trades(ax, trades, offset, x0, x1, c) -> None:
    for row in trades.itertuples():
        e = int(row.entry_index) - offset
        x = int(row.exit_index) - offset
        if max(e, x) < x0 or min(e, x) > x1:
            continue
        e_c, x_c = max(e, x0), min(x, x1)
        entry, sl, tp = float(row.entry_price), float(row.sl), float(row.tp)
        side = int(row.side)

        # risk / reward boxes
        ax.add_patch(
            Rectangle(
                (e_c, min(entry, sl)), max(x_c - e_c, 1), abs(entry - sl),
                facecolor=c["critical"], alpha=0.08, linewidth=0, zorder=0,
            )
        )
        ax.add_patch(
            Rectangle(
                (e_c, min(entry, tp)), max(x_c - e_c, 1), abs(tp - entry),
                facecolor=c["good"], alpha=0.08, linewidth=0, zorder=0,
            )
        )
        ax.plot([e_c, x_c], [sl, sl], color=c["critical"], linewidth=1.0, zorder=5)
        ax.plot([e_c, x_c], [tp, tp], color=c["good"], linewidth=1.0, zorder=5)
        ax.plot([e_c, x_c], [entry, entry], color=c["ink"], linewidth=0.8,
                linestyle=(0, (1, 2)), zorder=5)
        ax.scatter(
            [e], [entry], marker="^" if side == 1 else "v", s=70,
            color=c["ink"], edgecolor=c["surface"], linewidth=1.0, zorder=9,
        )
        ax.scatter(
            [x], [float(row.exit_price)], marker="o", s=34,
            color=c["good"] if row.exit_reason == "tp" else c["critical"],
            edgecolor=c["surface"], linewidth=0.8, zorder=9,
        )


def _draw_volume(ax, df, xs, c) -> None:
    o = df["open"].to_numpy(float)
    cl = df["close"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    colors = np.where(cl >= o, c["bull"], c["bear"])
    ax.bar(xs, vol, width=0.72, color=colors, alpha=0.55, linewidth=0)
    ax.set_ylim(0, float(np.nanmax(vol)) * 1.15 if len(vol) else 1)


def _draw_bias(ax, bias: pd.Series, offset, xs, c) -> None:
    values = bias.to_numpy()[offset : offset + len(xs)]
    ax.step(xs, values, where="post", color=c["blue"], linewidth=1.1)
    ax.fill_between(xs, 0, values, step="post", color=c["blue"], alpha=0.12)
    ax.axhline(0, color=c["axis"], linewidth=0.8)
    ax.set_ylim(-1.35, 1.35)


def _format_axis(ax, window, xs) -> None:
    n = len(window)
    if n == 0:
        return
    # evenly spaced ticks (both ends included) then drop any that sit too close
    raw = np.unique(np.linspace(0, n - 1, min(8, n)).round().astype(int))
    ticks = [int(raw[0])]
    min_gap = max(4, n // 24)
    for t in raw[1:]:
        if t - ticks[-1] >= min_gap:
            ticks.append(int(t))
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [window.index[i].strftime("%m-%d\n%H:%M") for i in ticks], fontsize=7.5
    )
    ax.set_xlim(-1, n)


def _legend(fig, c, has_trades: bool) -> None:
    handles = [
        Line2D([0], [0], marker="^", color="none", markerfacecolor=c["surface"],
               markeredgecolor=c["muted"], markersize=6, label="swing high / low"),
        Line2D([0], [0], color=c["blue"], lw=1.2, label="BOS (continuation)"),
        Line2D([0], [0], color=c["orange"], lw=1.2, ls=(0, (4, 2)), label="CHoCH (reversal)"),
        Patch(facecolor=c["bull"], alpha=0.25, edgecolor=c["bull"], label="bullish FVG"),
        Patch(facecolor=c["bear"], alpha=0.25, edgecolor=c["bear"], label="bearish FVG"),
        Patch(facecolor=c["violet"], alpha=0.25, edgecolor=c["violet"],
              linestyle="--", label="order block"),
        Line2D([0], [0], marker="X", color="none", markerfacecolor=c["yellow"],
               markeredgecolor=c["surface"], markersize=8, label="liquidity sweep"),
        Patch(facecolor=c["bull"], edgecolor=c["bull"], label="bull candle (filled)"),
        Patch(facecolor=c["surface"], edgecolor=c["bear"], label="bear candle (hollow)"),
    ]
    if has_trades:
        handles += [
            Line2D([0], [0], color=c["ink"], lw=1.0, label="entry"),
            Line2D([0], [0], color=c["critical"], lw=1.0, label="stop loss"),
            Line2D([0], [0], color=c["good"], lw=1.0, label="take profit"),
        ]
    fig.legend(
        handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.008),
        ncol=6, fontsize=7.5, frameon=True,
        facecolor=c["surface"], edgecolor=c["grid"], framealpha=0.95,
        labelcolor=c["secondary"], borderpad=0.6, columnspacing=1.2,
    )
