"""Central configuration for the SMC/ICT research bot.

Everything here is intentionally explicit so that the backtest assumptions are
auditable.  Change values here (or pass CLI flags) rather than editing modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    # ------------------------------------------------------------------ data
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    htf_timeframe: str = "1h"
    exchange: str = "binance"
    history_days: int = 180
    cache_dir: Path = field(default_factory=lambda: ROOT / "data" / "cache")
    chart_dir: Path = field(default_factory=lambda: ROOT / "charts")
    refresh: bool = False

    # -------------------------------------------------------- swing detection
    # A pivot is confirmed only after `swing_length` bars have closed on the
    # right-hand side.  This is the single most important anti-lookahead knob.
    swing_length: int = 3
    htf_swing_length: int = 3

    # ------------------------------------------------------------- HTF bias
    # "structure" -> direction of the last confirmed HTF BOS/CHoCH
    # "ema"       -> close above/below an EMA on the HTF
    htf_bias_mode: str = "structure"
    htf_ema_period: int = 50

    # --------------------------------------------------------------- signals
    atr_period: int = 14
    # Wick must pierce the level by at least this fraction of ATR to count as a
    # sweep (filters one-tick noise).
    sweep_min_atr: float = 0.05
    # An FVG older than this many bars is considered stale for entries.
    fvg_max_age: int = 200

    # ------------------------------------------------------------ strategy
    rr: float = 2.0                     # take-profit = rr * risk
    entry_at: str = "edge"              # "edge" (near edge) or "mid" of the FVG
    require_choch: bool = True          # strictly require a CHoCH (not a BOS)
    max_bars_after_sweep: int = 40      # CHoCH must arrive within this window
    max_bars_after_choch: int = 20      # FVG fill must happen within this window
    sl_buffer_atr: float = 0.25         # stop goes this many ATR behind the sweep
    # Only take trades in the direction of the HTF bias (no counter-trend).
    require_htf_bias: bool = True
    # Give up on a setup if price closes beyond the sweep level.
    invalidate_on_close_beyond_sweep: bool = True

    # -------------------------------------------------------------- backtest
    init_cash: float = 10_000.0
    fees: float = 0.0004               # 0.04 % per side (Binance taker-ish)
    slippage: float = 0.0002           # 0.02 % per side
    risk_per_trade: float = 0.01       # 1 % of running equity risked per trade
    max_position_pct: float = 0.95     # never deploy more than 95 % of equity
    close_at_end: bool = True          # mark open trade to market on last bar
    # Conservative intrabar assumption: if both SL and TP are inside one bar,
    # count the stop first.
    pessimistic_intrabar: bool = True

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.chart_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ names
    @property
    def cache_stem(self) -> str:
        return f"{self.symbol.replace('/', '_')}_{self.timeframe}"


CONFIG = Config()
