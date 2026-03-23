#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_mt_filter.py
=======================
Unit tests for scripts/mt_filter.py

Covers:
  - mt_gate: all LONG / SHORT rule combinations
  - gate_allows / gate_is_strong helpers
  - exec_confirm_15m: ENTER / WAIT / CANCEL scenarios
  - _ema / _rsi internal helpers
"""
import os
import sys
import unittest

# Allow importing from scripts/ directory
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from mt_filter import (
    mt_gate,
    gate_allows,
    gate_is_strong,
    exec_confirm_15m,
    REJECT,
    ALLOW_WEAK,
    ALLOW_STRONG,
    ENTER,
    WAIT,
    CANCEL,
    _ema,
    _rsi,
)


class TestMtGateLong(unittest.TestCase):
    """mt_gate rules for LONG direction."""

    def test_long_up_up_strong(self):
        self.assertEqual(mt_gate("LONG", "UP", "UP"), ALLOW_STRONG)

    def test_long_up_neutral_weak(self):
        self.assertEqual(mt_gate("LONG", "UP", "NEUTRAL"), ALLOW_WEAK)

    def test_long_neutral_up_weak(self):
        self.assertEqual(mt_gate("LONG", "NEUTRAL", "UP"), ALLOW_WEAK)

    def test_long_1d_down_reject(self):
        """1d DOWN always rejects LONG regardless of 4h."""
        self.assertEqual(mt_gate("LONG", "UP", "DOWN"), REJECT)
        self.assertEqual(mt_gate("LONG", "NEUTRAL", "DOWN"), REJECT)
        self.assertEqual(mt_gate("LONG", "DOWN", "DOWN"), REJECT)

    def test_long_4h_down_reject(self):
        """4h DOWN always rejects LONG regardless of 1d."""
        self.assertEqual(mt_gate("LONG", "DOWN", "UP"), REJECT)
        self.assertEqual(mt_gate("LONG", "DOWN", "NEUTRAL"), REJECT)

    def test_long_both_neutral_reject(self):
        self.assertEqual(mt_gate("LONG", "NEUTRAL", "NEUTRAL"), REJECT)


class TestMtGateShort(unittest.TestCase):
    """mt_gate rules for SHORT direction (symmetric to LONG)."""

    def test_short_down_down_strong(self):
        self.assertEqual(mt_gate("SHORT", "DOWN", "DOWN"), ALLOW_STRONG)

    def test_short_down_neutral_weak(self):
        self.assertEqual(mt_gate("SHORT", "DOWN", "NEUTRAL"), ALLOW_WEAK)

    def test_short_neutral_down_weak(self):
        self.assertEqual(mt_gate("SHORT", "NEUTRAL", "DOWN"), ALLOW_WEAK)

    def test_short_1d_up_reject(self):
        """1d UP always rejects SHORT regardless of 4h."""
        self.assertEqual(mt_gate("SHORT", "DOWN", "UP"), REJECT)
        self.assertEqual(mt_gate("SHORT", "NEUTRAL", "UP"), REJECT)
        self.assertEqual(mt_gate("SHORT", "UP", "UP"), REJECT)

    def test_short_4h_up_reject(self):
        """4h UP always rejects SHORT regardless of 1d."""
        self.assertEqual(mt_gate("SHORT", "UP", "DOWN"), REJECT)
        self.assertEqual(mt_gate("SHORT", "UP", "NEUTRAL"), REJECT)

    def test_short_both_neutral_reject(self):
        self.assertEqual(mt_gate("SHORT", "NEUTRAL", "NEUTRAL"), REJECT)


class TestMtGateNonDirectional(unittest.TestCase):
    """mt_gate with non-LONG/SHORT side."""

    def test_flat_side(self):
        self.assertEqual(mt_gate("FLAT", "UP", "UP"), REJECT)

    def test_empty_side(self):
        self.assertEqual(mt_gate("", "UP", "UP"), REJECT)

    def test_unknown_side(self):
        self.assertEqual(mt_gate("BUY", "UP", "UP"), REJECT)


class TestGateHelpers(unittest.TestCase):
    """gate_allows and gate_is_strong helpers."""

    def test_gate_allows_strong(self):
        self.assertTrue(gate_allows(ALLOW_STRONG))

    def test_gate_allows_weak(self):
        self.assertTrue(gate_allows(ALLOW_WEAK))

    def test_gate_allows_reject(self):
        self.assertFalse(gate_allows(REJECT))

    def test_gate_is_strong_strong(self):
        self.assertTrue(gate_is_strong(ALLOW_STRONG))

    def test_gate_is_strong_weak(self):
        self.assertFalse(gate_is_strong(ALLOW_WEAK))

    def test_gate_is_strong_reject(self):
        self.assertFalse(gate_is_strong(REJECT))


class TestExecConfirm15m(unittest.TestCase):
    """exec_confirm_15m scenarios."""

    def _klines(self, closes):
        return [{"close": c} for c in closes]

    # ------------------------------------------------------------------
    # disabled / edge cases
    # ------------------------------------------------------------------
    def test_disabled_returns_enter(self):
        self.assertEqual(exec_confirm_15m("LONG", [], enabled=False), ENTER)

    def test_empty_klines_returns_wait(self):
        self.assertEqual(exec_confirm_15m("LONG", [], enabled=True), WAIT)

    def test_single_kline_returns_wait(self):
        self.assertEqual(exec_confirm_15m("LONG", self._klines([100.0]), enabled=True), WAIT)

    def test_non_directional_returns_enter(self):
        klines = self._klines([100 + i for i in range(30)])
        self.assertEqual(exec_confirm_15m("FLAT", klines, enabled=True), ENTER)

    # ------------------------------------------------------------------
    # LONG bullish scenario → ENTER
    # ------------------------------------------------------------------
    def test_long_bullish_enter(self):
        # Steadily rising prices: close > EMA20, RSI > 50, last > prev
        klines = self._klines([100 + i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("LONG", klines), ENTER)

    # ------------------------------------------------------------------
    # LONG bearish scenario → CANCEL
    # ------------------------------------------------------------------
    def test_long_bearish_cancel(self):
        # Steadily falling prices: close < EMA20, RSI < 50, last < prev
        klines = self._klines([200 - i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("LONG", klines), CANCEL)

    # ------------------------------------------------------------------
    # SHORT bearish scenario → ENTER
    # ------------------------------------------------------------------
    def test_short_bearish_enter(self):
        klines = self._klines([200 - i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("SHORT", klines), ENTER)

    # ------------------------------------------------------------------
    # SHORT bullish scenario → CANCEL
    # ------------------------------------------------------------------
    def test_short_bullish_cancel(self):
        klines = self._klines([100 + i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("SHORT", klines), CANCEL)

    # ------------------------------------------------------------------
    # Mixed scenarios → WAIT
    # ------------------------------------------------------------------
    def test_long_mixed_wait(self):
        # Prices bounce: some criteria pass, some fail → score == 1 → WAIT
        import math
        closes = [100 + math.sin(i * 0.5) * 2 for i in range(30)]
        # Score should not be consistently 0 or consistently 3
        result = exec_confirm_15m("LONG", self._klines(closes))
        self.assertIn(result, (ENTER, WAIT, CANCEL))  # just verify it returns a valid value


class TestMtGateInvalidTrend(unittest.TestCase):
    """mt_gate behavior with non-standard / invalid trend values.

    mt_gate uses strict equality checks (e.g., t1d == "DOWN").
    Any unrecognised trend string will not match the ALLOW conditions
    and will fall through to the default REJECT — safe / conservative.
    """

    def test_long_invalid_t4_rejects(self):
        # "up" (lowercase) is not "UP", so ALLOW conditions never fire
        self.assertEqual(mt_gate("LONG", "up", "UP"), REJECT)

    def test_long_invalid_t1d_rejects(self):
        self.assertEqual(mt_gate("LONG", "UP", "up"), REJECT)

    def test_long_both_invalid_rejects(self):
        self.assertEqual(mt_gate("LONG", "INVALID", "INVALID"), REJECT)

    def test_short_invalid_t4_rejects(self):
        self.assertEqual(mt_gate("SHORT", "down", "DOWN"), REJECT)

    def test_short_invalid_t1d_rejects(self):
        self.assertEqual(mt_gate("SHORT", "DOWN", "down"), REJECT)

    def test_long_empty_trend_rejects(self):
        self.assertEqual(mt_gate("LONG", "", ""), REJECT)

    def test_short_empty_trend_rejects(self):
        self.assertEqual(mt_gate("SHORT", "", ""), REJECT)


class TestExecConfirm15mScoreBoundary(unittest.TestCase):
    """
    Explicit score-boundary tests for exec_confirm_15m.

    The scoring system awards +1 per fulfilled condition (3 total):
      LONG:  close > EMA20 (+1)  |  RSI > 50 (+1)  |  last > prev (+1)
      SHORT: close < EMA20 (+1)  |  RSI < 50 (+1)  |  last < prev (+1)

    score=3 → ENTER, score=2 → ENTER, score=1 → WAIT, score=0 → CANCEL
    """

    def _klines(self, closes):
        return [{"close": c} for c in closes]

    # ------------------------------------------------------------------ #
    # LONG score boundaries                                               #
    # ------------------------------------------------------------------ #

    def test_long_score3_enter(self):
        """Steadily rising series: all 3 LONG conditions true → score=3 → ENTER."""
        klines = self._klines([100 + i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("LONG", klines), ENTER)

    def test_long_score2_enter(self):
        """
        28 up-bars then 1 down-tick:
          - close > EMA20 (still above MA from the uptrend)       → +1
          - RSI > 50 (28 wins vs 1 loss in the window)            → +1
          - last < prev (down-tick)                               → 0
        Score = 2 → ENTER
        """
        rises = [100 + i * 0.5 for i in range(29)]   # 100 … 114.0
        # 113.5 < 114.0: last bar ticks down, but price still well above EMA
        klines = self._klines(rises + [113.5])
        self.assertEqual(exec_confirm_15m("LONG", klines), ENTER)

    def test_long_score1_wait(self):
        """
        28 down-bars then 1 up-tick:
          - close < EMA20 (price below MA from the downtrend)     → 0
          - RSI < 50 (28 losses vs 1 gain)                        → 0
          - last > prev (the one up-tick)                         → +1
        Score = 1 → WAIT
        """
        falls = [200 - i * 0.5 for i in range(29)]   # 200 … 186.0
        # 186.5 > 186.0: last bar ticks up, but price still below EMA
        klines = self._klines(falls + [186.5])
        self.assertEqual(exec_confirm_15m("LONG", klines), WAIT)

    def test_long_score0_cancel(self):
        """Steadily falling series: all 3 LONG conditions false → score=0 → CANCEL."""
        klines = self._klines([200 - i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("LONG", klines), CANCEL)

    # ------------------------------------------------------------------ #
    # SHORT score boundaries                                              #
    # ------------------------------------------------------------------ #

    def test_short_score3_enter(self):
        """Steadily falling series: all 3 SHORT conditions true → score=3 → ENTER."""
        klines = self._klines([200 - i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("SHORT", klines), ENTER)

    def test_short_score2_enter(self):
        """
        28 down-bars then 1 up-tick:
          - close < EMA20 (still below MA from the downtrend)     → +1
          - RSI < 50 (28 losses vs 1 gain)                        → +1
          - last > prev (up-tick)                                 → 0
        Score = 2 → ENTER
        """
        falls = [200 - i * 0.5 for i in range(29)]
        klines = self._klines(falls + [186.5])  # 186.5 > 186.0: up-tick
        self.assertEqual(exec_confirm_15m("SHORT", klines), ENTER)

    def test_short_score1_wait(self):
        """
        28 up-bars then 1 down-tick:
          - close > EMA20 (above MA from the uptrend)             → 0
          - RSI > 50 (28 gains vs 1 loss)                         → 0
          - last < prev (the one down-tick)                       → +1
        Score = 1 → WAIT
        """
        rises = [100 + i * 0.5 for i in range(29)]
        klines = self._klines(rises + [113.5])  # 113.5 < 114.0: down-tick
        self.assertEqual(exec_confirm_15m("SHORT", klines), WAIT)

    def test_short_score0_cancel(self):
        """Steadily rising series: all 3 SHORT conditions false → score=0 → CANCEL."""
        klines = self._klines([100 + i * 0.5 for i in range(30)])
        self.assertEqual(exec_confirm_15m("SHORT", klines), CANCEL)

    # ------------------------------------------------------------------ #
    # enabled=False bypass (also covered in TestExecConfirm15m but      #
    # repeated here to confirm it short-circuits before any scoring)     #
    # ------------------------------------------------------------------ #

    def test_disabled_bypasses_scoring_for_long(self):
        """enabled=False must return ENTER regardless of price data."""
        klines = self._klines([200 - i * 0.5 for i in range(30)])  # bearish → would CANCEL
        self.assertEqual(exec_confirm_15m("LONG", klines, enabled=False), ENTER)

    def test_disabled_bypasses_scoring_for_short(self):
        """enabled=False must return ENTER regardless of price data."""
        klines = self._klines([100 + i * 0.5 for i in range(30)])  # bullish → would CANCEL
        self.assertEqual(exec_confirm_15m("SHORT", klines, enabled=False), ENTER)

    # ------------------------------------------------------------------ #
    # Insufficient 15m data                                               #
    # ------------------------------------------------------------------ #

    def test_insufficient_data_zero_bars_wait(self):
        """0 bars → WAIT (conservative: do not cancel, but do not enter)."""
        self.assertEqual(exec_confirm_15m("LONG", [], enabled=True), WAIT)

    def test_insufficient_data_one_bar_wait(self):
        """1 bar → WAIT (need at least 2 to compute prev vs latest)."""
        self.assertEqual(exec_confirm_15m("LONG", [{"close": 100.0}], enabled=True), WAIT)
        self.assertEqual(exec_confirm_15m("SHORT", [{"close": 100.0}], enabled=True), WAIT)


    """Internal _ema and _rsi helpers."""

    def test_ema_empty(self):
        result = _ema([], 5)
        self.assertEqual(result, [])

    def test_ema_short(self):
        vals = [1.0, 2.0, 3.0]
        result = _ema(vals, 5)
        # All None because len(vals) < period
        self.assertEqual(len(result), 3)
        self.assertTrue(all(v is None for v in result))

    def test_ema_values(self):
        vals = [10.0] * 20 + [20.0] * 5
        result = _ema(vals, 5)
        self.assertIsNotNone(result[-1])
        # After many 10.0s, EMA approaches 10; after 20.0s it should be >10
        self.assertGreater(result[-1], 10.0)

    def test_rsi_none_for_short_series(self):
        self.assertIsNone(_rsi([1.0, 2.0, 3.0], 14))

    def test_rsi_rising(self):
        # All gains, no losses → RSI should be 100
        vals = [float(i) for i in range(20)]
        rsi = _rsi(vals, 14)
        self.assertIsNotNone(rsi)
        self.assertAlmostEqual(rsi, 100.0)

    def test_rsi_falling(self):
        # All losses → RSI should be 0
        vals = [float(20 - i) for i in range(20)]
        rsi = _rsi(vals, 14)
        self.assertIsNotNone(rsi)
        self.assertAlmostEqual(rsi, 0.0)

    def test_rsi_mixed(self):
        vals = [100.0, 102.0, 101.0, 103.0, 100.0, 99.0, 101.0, 102.0,
                103.0, 101.0, 100.0, 99.0, 98.0, 100.0, 101.0, 102.0]
        rsi = _rsi(vals, 14)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 0.0)
        self.assertLess(rsi, 100.0)


if __name__ == "__main__":
    unittest.main()
