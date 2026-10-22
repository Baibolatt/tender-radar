"""Download and cache OHLCV candles with ccxt.

The loader is deliberately boring: it returns a tidy, UTC-indexed DataFrame
with lowercase columns ``open, high, low, close, volume`` (the convention the
rest of the project and ``smartmoneyconcepts`` expect).

Two rules matter for the research result:

* only **closed** candles are kept -- the exchange also returns the partially
  formed current candle, which would leak live data into a backtest;
* the cache is incremental, so repeated runs do not re-download history.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import ccxt
import pandas as pd

from config import Config

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# ccxt writes timeframes as "15m", "1h", ...  Pandas >= 3 removed "m" as a
# minute alias, so parse explicitly instead of passing strings around.
_UNITS = {
    "s": ("seconds", "s"),
    "m": ("minutes", "min"),
    "h": ("hours", "h"),
    "d": ("days", "D"),
    "w": ("weeks", "W"),
}


def parse_timeframe(timeframe: str) -> pd.Timedelta:
    match = re.fullmatch(r"(\d+)\s*([smhdw])", timeframe.strip().lower())
    if not match:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    n, unit = int(match.group(1)), match.group(2)
    return pd.Timedelta(**{_UNITS[unit][0]: n})


def pandas_freq(timeframe: str) -> str:
    match = re.fullmatch(r"(\d+)\s*([smhdw])", timeframe.strip().lower())
    if not match:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    n, unit = int(match.group(1)), match.group(2)
    return f"{n}{_UNITS[unit][1]}"


def floor_ts(ts: pd.Timestamp, delta: pd.Timedelta) -> pd.Timestamp:
    """Floor a timestamp to a timeframe boundary without string offsets."""
    return pd.Timestamp((ts.value // delta.value) * delta.value, tz=ts.tz)


# ---------------------------------------------------------------------------
# exchange helpers
# ---------------------------------------------------------------------------
def _make_exchange(name: str) -> ccxt.Exchange:
    if not hasattr(ccxt, name):
        raise ValueError(f"Unknown ccxt exchange: {name!r}")
    exchange = getattr(ccxt, name)(
        {"enableRateLimit": True, "timeout": 30_000, "options": {"defaultType": "spot"}}
    )
    return exchange


def _timeframe_ms(exchange: ccxt.Exchange, timeframe: str) -> int:
    """Timeframe in milliseconds (ccxt parses "15m", "1h", "4h", "1d")."""
    return int(exchange.parse_timeframe(timeframe) * 1000)


def _tf_delta(config: Config) -> pd.Timedelta:
    return parse_timeframe(config.timeframe)


def _to_frame(rows: Iterable[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", *OHLCV_COLUMNS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    return df.astype("float64")


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
def fetch_ohlcv(
    config: Config,
    since: datetime | None = None,
    until: datetime | None = None,
    progress: bool = True,
) -> pd.DataFrame:
    """Paginate ccxt ``fetch_ohlcv`` into one DataFrame.

    Parameters
    ----------
    since, until:
        UTC datetimes.  ``since`` defaults to ``now - history_days`` and
        ``until`` to now.  Results include only fully closed candles.
    """
    exchange = _make_exchange(config.exchange)
    tf_ms = _timeframe_ms(exchange, config.timeframe)

    now = datetime.now(timezone.utc)
    until = until or now
    since = since or (until - pd.Timedelta(days=config.history_days))
    since_ms = int(pd.Timestamp(since).timestamp() * 1000)
    until_ms = int(pd.Timestamp(until).timestamp() * 1000)

    rows: list[list] = []
    cursor = since_ms
    page = 0
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(
            config.symbol, config.timeframe, since=cursor, limit=1000
        )
        if not batch:
            break
        rows.extend(batch)
        page += 1
        last = batch[-1][0]
        if last <= cursor:  # exchange stopped advancing
            break
        cursor = last + tf_ms
        if progress and page % 5 == 0:
            done = datetime.fromtimestamp(last / 1000, tz=timezone.utc)
            print(f"  fetched {page:>3} pages, at {done:%Y-%m-%d %H:%M} UTC")
        # be polite even though ccxt rate-limits
        time.sleep(exchange.rateLimit / 1000)

    if not rows:
        raise RuntimeError(
            f"{config.exchange} returned no candles for {config.symbol} "
            f"{config.timeframe}; check symbol/network/geoblocking."
        )

    df = _to_frame(rows)
    # interval start -> keep bars that have fully closed
    df = df[df.index < floor_ts(pd.Timestamp(now), _tf_delta(config))]
    df = df[~df.index.duplicated(keep="last")]
    return df


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------
def _cache_path(config: Config) -> Path:
    return config.cache_dir / f"{config.cache_stem}.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "timestamp"
    return df.sort_index()


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, float_format="%.10g")


def load_ohlcv(config: Config, refresh: bool | None = None) -> pd.DataFrame:
    """Load cached candles, fetching/updating from the exchange as needed."""
    refresh = config.refresh if refresh is None else refresh
    config.ensure_dirs()
    path = _cache_path(config)
    tf = _tf_delta(config)

    now = pd.Timestamp(datetime.now(timezone.utc))
    wanted_start = floor_ts(now - pd.Timedelta(days=config.history_days), tf)

    cached: pd.DataFrame | None = None
    if path.exists() and not refresh:
        try:
            cached = _read_cache(path)
        except Exception as exc:  # corrupted cache -> refetch
            print(f"  cache unreadable ({exc}); refetching")
            cached = None

    if cached is not None and not cached.empty:
        last = cached.index[-1]
        stale = last < now - 2 * tf
        missing_head = cached.index[0] > wanted_start + tf
        if not stale and not missing_head:
            print(f"  cache hit: {len(cached):,} candles -> {path}")
            return cached
        # incremental update
        start = cached.index[0]
        print(f"  cache partial ({len(cached):,} candles); fetching from {start:%Y-%m-%d}")
        fresh = fetch_ohlcv(config, since=start, until=now, progress=False)
        df = pd.concat([cached, fresh])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    else:
        print(f"  downloading {config.symbol} {config.timeframe} "
              f"({config.history_days}d) from {config.exchange}")
        df = fetch_ohlcv(config, since=wanted_start, until=now)

    # trim to the wanted window and validate
    df = df[df.index >= wanted_start]
    df = drop_unclosed(df, config.timeframe)
    validate_ohlcv(df)
    _write_cache(df, path)
    print(f"  saved {len(df):,} candles -> {path}")
    return df


def drop_unclosed(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Drop the in-progress candle (interval has not finished yet)."""
    if df.empty:
        return df
    now = pd.Timestamp(datetime.now(timezone.utc))
    cutoff = floor_ts(now, parse_timeframe(timeframe))
    return df[df.index < cutoff]


# ---------------------------------------------------------------------------
# resampling / validation
# ---------------------------------------------------------------------------
def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate LTF candles to a higher timeframe, left-labelled.

    A resampled candle labelled ``T`` covers ``[T, T + rule)`` and is therefore
    only *known* once that interval has closed (i.e. at ``T + rule``).  Callers
    must respect that; :func:`strategy.smc_strategy.align_htf` does.
    """
    out = df.resample(pandas_freq(rule), label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return out.dropna(subset=["open", "high", "low", "close"])


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Fail loudly on structurally broken data."""
    if df.empty:
        raise ValueError("OHLCV frame is empty")
    missing = set(OHLCV_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("index is not sorted")
    if df.index.has_duplicates:
        raise ValueError("index has duplicates")
    if df[OHLCV_COLUMNS].isna().any().any():
        raise ValueError("OHLCV contains NaN values")

    body_high = df[["open", "close"]].max(axis=1)
    body_low = df[["open", "close"]].min(axis=1)
    bad = (df["high"] < body_high) | (df["low"] > body_low) | (df["high"] < df["low"])
    if bad.any():
        first = df.index[bad][0]
        raise ValueError(f"{int(bad.sum())} invalid OHLC bars, first at {first}")
    if (df["volume"] < 0).any():
        raise ValueError("negative volume present")


def summarize(df: pd.DataFrame) -> str:
    """One-line human summary used by the CLI."""
    if df.empty:
        return "no candles"
    return (
        f"{len(df):,} candles | {df.index[0]:%Y-%m-%d %H:%M} -> "
        f"{df.index[-1]:%Y-%m-%d %H:%M} UTC | "
        f"last close {df['close'].iloc[-1]:,.2f}"
    )
