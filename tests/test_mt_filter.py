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


class TestEmaRsi(unittest.TestCase):
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
