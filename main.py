#!/usr/bin/env python3
"""SMC/ICT research bot - command line entry point.

    python main.py fetch                 # download/cache BTC/USDT 15m
    python main.py markup --last 400     # annotated chart
    python main.py signals               # signal counts + recent events
    python main.py backtest              # strategy backtest
    python main.py validate              # smartmoneyconcepts cross-check
    python main.py all --last 400        # everything, including a chart

No live orders are ever placed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from backtest import format_stats, run_backtest
from config import CONFIG, Config
from data.loader import load_ohlcv, summarize
from signals import detect
from signals.reference import compare as smc_compare
from strategy.htf import compute_htf_bias
from strategy.smc_strategy import generate_setups
from viz.chart import plot_markup


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--symbol", default=CONFIG.symbol)
    common.add_argument("--timeframe", default=CONFIG.timeframe)
    common.add_argument("--htf", default=CONFIG.htf_timeframe)
    common.add_argument("--days", type=int, default=CONFIG.history_days)
    common.add_argument("--exchange", default=CONFIG.exchange)
    common.add_argument("--swing", type=int, default=CONFIG.swing_length,
                        help="bars on each side of a pivot (confirmation lag)")
    common.add_argument("--rr", type=float, default=CONFIG.rr)
    common.add_argument("--refresh", action="store_true", help="ignore the cache")
    common.add_argument("--no-bias-filter", action="store_true",
                        help="allow setups against the HTF bias")

    parser = argparse.ArgumentParser(
        description="SMC/ICT research bot (signals and backtests only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", parents=[common], help="download/cache candles")

    p_sig = sub.add_parser("signals", parents=[common], help="show detected signals")
    p_sig.add_argument("--rows", type=int, default=12)

    p_bt = sub.add_parser("backtest", parents=[common], help="run the backtest")
    p_bt.add_argument("--entry", choices=["edge", "mid"], default=CONFIG.entry_at)
    p_bt.add_argument("--risk", type=float, default=CONFIG.risk_per_trade)
    p_bt.add_argument("--fees", type=float, default=CONFIG.fees)
    p_bt.add_argument("--slippage", type=float, default=CONFIG.slippage)
    p_bt.add_argument("--cash", type=float, default=CONFIG.init_cash)
    p_bt.add_argument("--save", action="store_true", help="write CSV reports")

    p_mark = sub.add_parser("markup", parents=[common], help="annotated chart")
    p_mark.add_argument("--last", type=int, default=None, help="last N candles")
    p_mark.add_argument("--start", default=None)
    p_mark.add_argument("--end", default=None)
    p_mark.add_argument("--theme", choices=["light", "dark"], default="light")
    p_mark.add_argument("--out", default=None)
    p_mark.add_argument("--trades", action="store_true",
                        help="also draw entries / SL / TP")

    sub.add_parser("validate", parents=[common],
                   help="compare detectors with smartmoneyconcepts")

    p_all = sub.add_parser("all", parents=[common], help="signals + backtest + chart")
    p_all.add_argument("--last", type=int, default=None)
    p_all.add_argument("--theme", choices=["light", "dark"], default="light")
    p_all.add_argument("--out", default=None)
    p_all.add_argument("--save", action="store_true")

    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    return replace(
        CONFIG,
        symbol=args.symbol,
        timeframe=args.timeframe,
        htf_timeframe=args.htf,
        history_days=args.days,
        exchange=args.exchange,
        swing_length=args.swing,
        rr=args.rr,
        refresh=args.refresh,
        require_htf_bias=not args.no_bias_filter,
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_fetch(cfg: Config) -> None:
    df = load_ohlcv(cfg, refresh=cfg.refresh)
    print(summarize(df))


def cmd_signals(cfg: Config, rows: int) -> None:
    df = load_ohlcv(cfg)
    signals = detect(df, cfg)
    print(f"\n{summarize(df)}")
    print(signals.summary())
    print()

    for title, frame, cols in [
        (
            "recent structure",
            signals.structure,
            ["time", "kind", "side", "level"],
        ),
        (
            "recent sweeps",
            signals.sweeps,
            ["time", "side", "level", "sweep_extreme"],
        ),
        (
            "recent FVGs",
            signals.fvgs,
            ["time", "side", "top", "bottom", "mitigated_time"],
        ),
    ]:
        print(f"{title}:")
        if frame.empty:
            print("  none\n")
            continue
        print(frame.tail(rows)[cols].to_string(index=False))
        print()

    bias = compute_htf_bias(df, cfg)
    print(f"HTF bias ({cfg.htf_timeframe}, {cfg.htf_bias_mode}): "
          f"bull {int((bias == 1).sum())} bars, bear {int((bias == -1).sum())}, "
          f"flat {int((bias == 0).sum())}")
    setups = generate_setups(df, signals, cfg)
    print(f"setups: {len(setups)}")
    if not setups.empty:
        print(
            setups[["side", "entry_time", "entry_price", "sl", "tp"]]
            .tail(rows)
            .to_string(index=False)
        )


def cmd_backtest(cfg: Config, args: argparse.Namespace) -> None:
    cfg = replace(
        cfg,
        entry_at=args.entry,
        risk_per_trade=args.risk,
        fees=args.fees,
        slippage=args.slippage,
        init_cash=args.cash,
    )
    df = load_ohlcv(cfg)
    signals = detect(df, cfg)
    setups = generate_setups(df, signals, cfg)
    result = run_backtest(df, setups, cfg)

    print(f"\n{summarize(df)}")
    print(signals.summary())
    print(f"setups: {len(setups)}  |  simulated trades: {len(result.trades)}"
          f"  |  skipped: {result.simulation.skipped}")
    print()
    if result.empty:
        print("no trades")
        return
    print(format_stats(result.stats))
    print("\ntrades:")
    cols = [
        c for c in [
            "trade_id", "side", "entry_time", "entry_price", "exit_time",
            "exit_price", "exit_reason", "net_pnl", "net_return_pct",
        ] if c in result.trades.columns
    ]
    print(result.trades[cols].to_string(index=False))

    if args.save:
        _save_reports(cfg, signals, setups, result)


def cmd_markup(cfg: Config, args: argparse.Namespace) -> None:
    df = load_ohlcv(cfg)
    signals = detect(df, cfg)
    trades = None
    if args.trades:
        setups = generate_setups(df, signals, cfg)
        trades = run_backtest(df, setups, cfg).trades

    path = plot_markup(
        df,
        signals,
        cfg,
        setups=None,
        trades=trades,
        last_n=args.last,
        start=args.start,
        end=args.end,
        theme=args.theme,
        out_path=args.out,
    )
    print(f"\n{summarize(df)}")
    print(signals.summary())
    print(f"chart -> {path}")


def cmd_validate(cfg: Config) -> None:
    df = load_ohlcv(cfg)
    signals = detect(df, cfg)
    print(f"\n{summarize(df)}")
    print(signals.summary())
    print()
    print(smc_compare(df, signals, cfg.swing_length))


def cmd_all(cfg: Config, args: argparse.Namespace) -> None:
    df = load_ohlcv(cfg)
    signals = detect(df, cfg)
    setups = generate_setups(df, signals, cfg)
    result = run_backtest(df, setups, cfg)

    print(f"\n{summarize(df)}")
    print(signals.summary())
    print(f"setups: {len(setups)}  |  trades: {len(result.trades)}")
    print()
    print(format_stats(result.stats) if not result.empty else "no trades")
    print()

    path = plot_markup(
        df,
        signals,
        cfg,
        trades=result.trades if not result.empty else None,
        last_n=args.last,
        theme=args.theme,
        out_path=args.out,
    )
    print(f"chart -> {path}")

    if args.save:
        _save_reports(cfg, signals, setups, result)


# ---------------------------------------------------------------------------
def _save_reports(cfg: Config, signals, setups, result) -> None:
    out = Path("reports")
    out.mkdir(exist_ok=True)
    stem = cfg.cache_stem
    signals.structure.to_csv(out / f"{stem}_structure.csv", index=False)
    signals.fvgs.to_csv(out / f"{stem}_fvg.csv", index=False)
    signals.order_blocks.to_csv(out / f"{stem}_ob.csv", index=False)
    signals.sweeps.to_csv(out / f"{stem}_sweeps.csv", index=False)
    signals.pivots.to_csv(out / f"{stem}_swings.csv", index=False)
    setups.to_csv(out / f"{stem}_setups.csv", index=False)
    if not result.empty:
        result.trades.to_csv(out / f"{stem}_trades.csv", index=False)
        result.equity.rename("equity").to_csv(out / f"{stem}_equity.csv")
        pd.Series(result.stats).to_csv(out / f"{stem}_stats.csv")
    print(f"reports -> {out.resolve()}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = config_from_args(args)

    try:
        if args.command == "fetch":
            cmd_fetch(cfg)
        elif args.command == "signals":
            cmd_signals(cfg, args.rows)
        elif args.command == "backtest":
            cmd_backtest(cfg, args)
        elif args.command == "markup":
            cmd_markup(cfg, args)
        elif args.command == "validate":
            cmd_validate(cfg)
        elif args.command == "all":
            cmd_all(cfg, args)
        else:  # pragma: no cover
            parser.error(f"unknown command {args.command}")
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
