"""Causality tests: nothing may depend on a bar it could not have seen.

The core check is a *prefix test*.  We compute every detector and the strategy
on the full series and again on a truncated series.  Anything whose
``confirm_index`` lies before the cut must be bit-for-bit identical: adding
future bars may only add new events, never change old ones.

Run with:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

import numpy as np
import pandas as pd

from config import CONFIG, Config
from signals.detect import detect
from signals.swing import confirmed_by, find_swings
from strategy.htf import compute_htf_bias
from strategy.smc_strategy import generate_setups

# 15m -> 1h is four bars; keeping cuts aligned avoids a half-formed HTF candle.
BARS = 3000
CUTS = (1200, 2076)


def make_ohlcv(n: int = BARS, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic candles with trends, swings and gaps."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    drift = np.cumsum(rng.normal(0.0, 55.0, n))
    cycle = 1400.0 * np.sin(np.linspace(0.0, 10.0 * np.pi, n))
    close = 30_000.0 + drift + cycle
    open_ = np.concatenate(([close[0]], close[:-1])) + rng.normal(0.0, 12.0, n)
    wick = rng.uniform(4.0, 90.0, n)
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    volume = rng.uniform(10.0, 600.0, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _prefix(frame: pd.DataFrame, cut: int, cols: list[str]) -> pd.DataFrame:
    out = frame[frame["confirm_index"] < cut][cols]
    return out.reset_index(drop=True)


class TestSwingConfirmation(unittest.TestCase):
    def setUp(self) -> None:
        self.df = make_ohlcv()
        self.n = CONFIG.swing_length

    def test_confirm_index_is_index_plus_lag(self) -> None:
        swings = find_swings(self.df, self.n)
        self.assertFalse(swings.empty)
        self.assertTrue((swings["confirm_index"] == swings["index"] + self.n).all())
        expected = self.df.index[swings["confirm_index"]]
        self.assertTrue((swings["confirm_time"].to_numpy() == expected.to_numpy()).all())

    def test_pivot_is_unknown_before_confirmation(self) -> None:
        swings = find_swings(self.df, self.n)
        for row in swings.head(200).itertuples():
            key = (int(row.index), int(row.kind))
            before = confirmed_by(swings, int(row.index) + self.n - 1)
            self.assertNotIn(key, set(zip(before["index"], before["kind"])))
            after = confirmed_by(swings, int(row.confirm_index))
            self.assertIn(key, set(zip(after["index"], after["kind"])))

    def test_last_pivots_need_future_bars(self) -> None:
        swings = find_swings(self.df, self.n)
        self.assertTrue((swings["confirm_index"] < len(self.df)).all())


class TestDetectorPrefixInvariance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.df = make_ohlcv()
        cls.full = detect(cls.df, CONFIG)

    def test_data_is_signal_rich(self) -> None:
        c = self.full.counts
        self.assertGreater(c["swings"], 100)
        self.assertGreater(c["bos_choch"], 50)
        self.assertGreater(c["fvg"], 100)
        self.assertGreater(c["sweeps"], 10)

    def test_detectors_match_on_truncated_data(self) -> None:
        cases = {
            "pivots": (self.full.pivots, ["index", "kind", "price", "confirm_index"]),
            "structure": (
                self.full.structure,
                ["index", "confirm_index", "kind", "side", "level", "swing_index"],
            ),
            "fvgs": (
                self.full.fvgs,
                ["index", "confirm_index", "side", "top", "bottom"],
            ),
            "order_blocks": (
                self.full.order_blocks,
                ["index", "confirm_index", "side", "top", "bottom", "break_index"],
            ),
            "sweeps": (
                self.full.sweeps,
                ["index", "side", "level", "sweep_extreme", "swing_index"],
            ),
        }
        for cut in CUTS:
            trunc = detect(self.df.iloc[:cut], CONFIG)
            for name, (frame, cols) in cases.items():
                with self.subTest(cut=cut, detector=name):
                    pd.testing.assert_frame_equal(
                        _prefix(frame, cut, cols),
                        _prefix(getattr(trunc, name), cut, cols),
                        check_dtype=False,
                    )

    def test_atr_matches_on_prefix(self) -> None:
        for cut in CUTS:
            trunc = detect(self.df.iloc[:cut], CONFIG)
            pd.testing.assert_series_equal(
                self.full.atr.iloc[:cut], trunc.atr, check_names=False
            )

    def test_ltf_bias_matches_on_prefix(self) -> None:
        for cut in CUTS:
            trunc = detect(self.df.iloc[:cut], CONFIG)
            pd.testing.assert_series_equal(
                self.full.bias.iloc[:cut], trunc.bias, check_names=False
            )


class TestHtfBiasCausality(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.df = make_ohlcv()
        cls.full = compute_htf_bias(cls.df, CONFIG)

    def test_bias_matches_on_prefix(self) -> None:
        for cut in CUTS:
            trunc = compute_htf_bias(self.df.iloc[:cut], CONFIG)
            pd.testing.assert_series_equal(
                self.full.iloc[:cut], trunc, check_names=False
            )

    def test_bias_takes_only_three_values(self) -> None:
        self.assertTrue(set(self.full.unique()).issubset({-1, 0, 1}))


class TestStrategyCausality(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.df = make_ohlcv()
        cls.signals = detect(cls.df, CONFIG)
        cls.setups = generate_setups(cls.df, cls.signals, CONFIG)

    def test_setups_are_signal_rich_enough_to_mean_something(self) -> None:
        # If this ever fails the prefix test below is vacuous; loosen the
        # synthetic data generator rather than deleting the assertion.
        self.assertGreater(len(self.setups), 0)

    def test_setups_match_on_truncated_data(self) -> None:
        cols = [
            "side", "entry_index", "entry_price", "sl", "tp", "rr",
            "sweep_index", "choch_index", "fvg_index", "bias",
        ]
        for cut in CUTS:
            trunc_df = self.df.iloc[:cut]
            trunc = generate_setups(trunc_df, detect(trunc_df, CONFIG), CONFIG)
            with self.subTest(cut=cut):
                pd.testing.assert_frame_equal(
                    self.setups[self.setups["entry_index"] < cut][cols]
                    .reset_index(drop=True),
                    trunc[trunc["entry_index"] < cut][cols].reset_index(drop=True),
                    check_dtype=False,
                )

    def test_entry_never_precedes_its_events(self) -> None:
        if self.setups.empty:
            self.skipTest("no setups in synthetic data")
        s = self.setups
        self.assertTrue((s["entry_index"] > s["sweep_index"]).all())
        self.assertTrue((s["entry_index"] > s["choch_index"]).all())
        # an FVG is only actionable the bar after it is confirmed; the origin
        # bar is index, confirmation is index + 1, so entry must be >= index + 2
        self.assertTrue((s["entry_index"] >= s["fvg_index"] + 2).all())

    def test_geometry_is_valid(self) -> None:
        if self.setups.empty:
            self.skipTest("no setups in synthetic data")
        s = self.setups
        long = s[s["side"] == 1]
        short = s[s["side"] == -1]
        self.assertTrue((long["sl"] < long["entry_price"]).all())
        self.assertTrue((long["entry_price"] < long["tp"]).all())
        self.assertTrue((short["tp"] < short["entry_price"]).all())
        self.assertTrue((short["entry_price"] < short["sl"]).all())


class TestConfig(unittest.TestCase):
    def test_frozen(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            CONFIG.rr = 3.0  # type: ignore[misc]

    def test_defaults(self) -> None:
        fresh = Config()
        self.assertTrue(fresh.cache_dir.is_absolute())
        self.assertEqual(fresh.cache_stem, "BTC_USDT_15m")
        self.assertEqual(fresh.rr, 2.0)


if __name__ == "__main__":
    unittest.main()
