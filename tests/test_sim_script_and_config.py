"""Tests for multi-symbol simulation script and shared config module.

Covers:
- Feature builder kline timestamp field compatibility (ts vs timestamp)
- scripts/symbol_config.py public API aliases
- live_trader_perp_simulated.py CLI argument parsing
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")

for _d in [SCRIPTS_DIR, ML_SERVICE_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)


# ---------------------------------------------------------------------------
# Tests: feature builder kline timestamp field compatibility
# ---------------------------------------------------------------------------

class TestFeatureBuilderKlineTimestamp(unittest.TestCase):
    """feature_builder.load_klines_json must accept both ``timestamp`` and ``ts`` fields."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="uw-fb-ts-test-")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir)

    def _write_klines(self, path: str, rows: list) -> None:
        import json
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(rows, f)

    def _make_row(self, ts_field: str, ts_value: str, i: int = 0) -> dict:
        return {
            ts_field: ts_value,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1000.0 + i,
            "symbol": "TESTUSDT",
            "interval": "1h",
        }

    def test_load_klines_json_accepts_timestamp_field(self) -> None:
        """load_klines_json must parse rows that use 'timestamp' (not 'ts') as the time key."""
        try:
            from feature_builder import load_klines_json  # type: ignore[import]
        except ImportError:
            self.skipTest("feature_builder not importable (missing dependencies)")

        rows = [self._make_row("timestamp", f"2026-01-{i+1:02d}T00:00:00Z", i) for i in range(5)]
        path = os.path.join(self._tmpdir, "klines_1h.json")
        self._write_klines(path, rows)

        df = load_klines_json(path)
        self.assertFalse(df.empty, "DataFrame must not be empty when using 'timestamp' field")
        self.assertEqual(len(df), 5, "All 5 rows should be loaded")
        self.assertIn("close", df.columns)

    def test_load_klines_json_accepts_ts_field(self) -> None:
        """load_klines_json must parse rows that use 'ts' as the time key."""
        try:
            from feature_builder import load_klines_json  # type: ignore[import]
        except ImportError:
            self.skipTest("feature_builder not importable (missing dependencies)")

        rows = [self._make_row("ts", f"2026-01-{i+1:02d}T00:00:00Z", i) for i in range(5)]
        path = os.path.join(self._tmpdir, "klines_1h.json")
        self._write_klines(path, rows)

        df = load_klines_json(path)
        self.assertFalse(df.empty, "DataFrame must not be empty when using 'ts' field")
        self.assertEqual(len(df), 5)

    def test_load_klines_json_accepts_open_time_field(self) -> None:
        """load_klines_json must parse rows that use 'open_time' as the time key."""
        try:
            from feature_builder import load_klines_json  # type: ignore[import]
        except ImportError:
            self.skipTest("feature_builder not importable (missing dependencies)")

        rows = [self._make_row("open_time", f"2026-01-{i+1:02d}T00:00:00Z", i) for i in range(5)]
        path = os.path.join(self._tmpdir, "klines_1h.json")
        self._write_klines(path, rows)

        df = load_klines_json(path)
        self.assertFalse(df.empty, "DataFrame must not be empty when using 'open_time' field")
        self.assertEqual(len(df), 5)

    def test_load_klines_json_sorted_by_timestamp(self) -> None:
        """Loaded DataFrame must be sorted by timestamp (ascending)."""
        try:
            from feature_builder import load_klines_json  # type: ignore[import]
        except ImportError:
            self.skipTest("feature_builder not importable (missing dependencies)")

        # Write rows in reverse order
        rows = [self._make_row("timestamp", f"2026-01-{5 - i:02d}T00:00:00Z", i) for i in range(5)]
        path = os.path.join(self._tmpdir, "klines_1h.json")
        self._write_klines(path, rows)

        df = load_klines_json(path)
        self.assertEqual(len(df), 5)
        # Index should be strictly increasing
        ts_vals = df.index.tolist()
        self.assertEqual(ts_vals, sorted(ts_vals), "Index must be sorted ascending")


# ---------------------------------------------------------------------------
# Tests: symbol_config.py public API
# ---------------------------------------------------------------------------

class TestSymbolConfigModule(unittest.TestCase):
    """scripts/symbol_config.py must expose the expected public API."""

    def test_module_importable(self) -> None:
        try:
            import symbol_config  # type: ignore[import]
        except ImportError as exc:
            self.fail(f"scripts/symbol_config.py failed to import: {exc}")

    def test_list_enabled_symbols(self) -> None:
        import symbol_config  # type: ignore[import]
        symbols = symbol_config.list_enabled_symbols()
        self.assertIsInstance(symbols, list)
        self.assertGreater(len(symbols), 0)
        # All 7 configured symbols should be enabled
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
            self.assertIn(sym, symbols, f"{sym} should be in enabled symbols")

    def test_get_symbol_config(self) -> None:
        import symbol_config  # type: ignore[import]
        cfg = symbol_config.get_symbol_config("ETHUSDT")
        self.assertIsInstance(cfg, dict)
        for key in ["enabled", "interval", "threshold", "tp", "sl", "horizon", "calibration"]:
            self.assertIn(key, cfg, f"Config for ETHUSDT must have key '{key}'")

    def test_data_dir(self) -> None:
        import symbol_config  # type: ignore[import]
        path = symbol_config.data_dir("BTCUSDT", base_data_dir="/base/data")
        self.assertEqual(path, "/base/data/BTCUSDT")

    def test_model_dir(self) -> None:
        import symbol_config  # type: ignore[import]
        path = symbol_config.model_dir("ETHUSDT", base_model_dir="/base/models")
        self.assertEqual(path, "/base/models/ETHUSDT")

    def test_reports_dir(self) -> None:
        import symbol_config  # type: ignore[import]
        path = symbol_config.reports_dir("SOLUSDT", base_data_dir="/base/data")
        self.assertEqual(path, "/base/data/SOLUSDT/reports")

    def test_predictions_log_path(self) -> None:
        import symbol_config  # type: ignore[import]
        path = symbol_config.predictions_log_path("BNBUSDT", base_data_dir="/base/data")
        self.assertEqual(path, "/base/data/BNBUSDT/predictions_log.jsonl")

    def test_all_symbols_exported(self) -> None:
        import symbol_config  # type: ignore[import]
        self.assertTrue(hasattr(symbol_config, "ALL_SYMBOLS"))
        self.assertIn("ETHUSDT", symbol_config.ALL_SYMBOLS)

    def test_dirs_are_symbol_specific(self) -> None:
        """Different symbols must resolve to different directories."""
        import symbol_config  # type: ignore[import]
        paths = {symbol_config.data_dir(s, "/d") for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}
        self.assertEqual(len(paths), 3, "Each symbol must map to a unique data dir")


# ---------------------------------------------------------------------------
# Tests: live_trader_perp_simulated.py CLI
# ---------------------------------------------------------------------------

class TestLiveTraderPerpSimulatedCLI(unittest.TestCase):
    """CLI smoke tests for scripts/live_trader_perp_simulated.py."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_perp_simulated.py")

    def test_script_exists(self) -> None:
        self.assertTrue(
            os.path.exists(self._SCRIPT),
            f"scripts/live_trader_perp_simulated.py must exist at {self._SCRIPT}",
        )

    def test_help_returns_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"--help returned non-zero: {result.stderr}")

    def test_help_contains_symbol_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertIn("--symbol", result.stdout, "--symbol flag must appear in --help")

    def test_help_contains_all_symbols_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertIn("--all-symbols", result.stdout, "--all-symbols flag must appear in --help")

    def test_help_contains_tp_sl_horizon_threshold(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        for flag in ("--tp", "--sl", "--horizon", "--threshold"):
            self.assertIn(flag, result.stdout, f"{flag} must appear in --help")

    def test_symbol_and_all_symbols_mutually_exclusive(self) -> None:
        """--symbol and --all-symbols must be mutually exclusive."""
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--symbol", "BTCUSDT", "--all-symbols"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0, "Should fail when both --symbol and --all-symbols are given")

    def test_argparse_default_symbol_is_eth(self) -> None:
        """Default --symbol must be ETHUSDT."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("ltsim", self._SCRIPT)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        # Only load the module up to the if __name__ == "__main__" guard
        # by patching __name__ so the entry point is not executed.
        mod.__name__ = "ltsim"  # type: ignore[assignment]
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            self.skipTest("Module-level import failed (missing deps)")
        ap = mod.build_parser()
        defaults = ap.parse_args([])
        self.assertEqual(defaults.symbol, "ETHUSDT", "Default symbol must be ETHUSDT")
        self.assertFalse(defaults.all_symbols, "Default --all-symbols must be False")


# ---------------------------------------------------------------------------
# Tests: live_trader_eth_perp_simulated.py backward compat (help works)
# ---------------------------------------------------------------------------

class TestLegacyEthWrapperCLI(unittest.TestCase):
    """Legacy ETH wrapper must still accept --help without errors."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_eth_perp_simulated.py")

    def test_help_returns_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"--help returned non-zero: {result.stderr}")

    def test_help_mentions_deprecated(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        # Either the help text or deprecation warning should mention the new script
        combined = result.stdout + result.stderr
        self.assertTrue(
            "live_trader_perp_simulated" in combined or "deprecated" in combined.lower(),
            "Legacy wrapper should reference live_trader_perp_simulated.py or warn about deprecation",
        )


if __name__ == "__main__":
    unittest.main()
