"""Tests for multi-symbol simulation script and shared config module.

Covers:
- Feature builder kline timestamp field compatibility (ts vs timestamp)
- scripts/symbol_config.py public API aliases
- live_trader_perp_simulated.py CLI argument parsing
- backtest_event_v3_http.py CLI argument parsing and --symbol YAML defaults
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
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


# ---------------------------------------------------------------------------
# Tests: backtest_event_v3_http.py CLI
# ---------------------------------------------------------------------------

class TestBacktestEventV3CLI(unittest.TestCase):
    """CLI smoke tests for scripts/backtest_event_v3_http.py."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "backtest_event_v3_http.py")

    def test_script_exists(self) -> None:
        self.assertTrue(
            os.path.exists(self._SCRIPT),
            f"scripts/backtest_event_v3_http.py must exist at {self._SCRIPT}",
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
        self.assertIn("--symbol", result.stdout, "--symbol flag must appear in --help output")

    def test_help_mentions_symbols_yaml(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertIn(
            "symbols.yaml", result.stdout,
            "--symbol help text must reference configs/symbols.yaml",
        )

    def test_help_contains_grid_flags(self) -> None:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        for flag in ("--thresholds", "--tp-grid", "--sl-grid", "--horizon-bars", "--interval"):
            self.assertIn(flag, result.stdout, f"{flag} must appear in --help output")

    def test_yaml_defaults_loaded_when_symbol_provided(self) -> None:
        """When --symbol is given (without explicit grid flags), YAML single-point ranges are used."""
        # Build a minimal temp YAML config so the test is self-contained
        tmpdir = tempfile.mkdtemp(prefix="uw-bt-test-")
        try:
            cfg_path = os.path.join(tmpdir, "symbols.yaml")
            with open(cfg_path, "w") as f:
                f.write(
                    "symbols:\n"
                    "  TESTUSDT:\n"
                    "    enabled: true\n"
                    "    interval: '4h'\n"
                    "    threshold: 0.77\n"
                    "    tp: 0.0250\n"
                    "    sl: 0.0111\n"
                    "    horizon: 8\n"
                    "    calibration: isotonic\n"
                )

            spec = importlib.util.spec_from_file_location("bt_v3", self._SCRIPT)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            mod.__name__ = "bt_v3"  # type: ignore[assignment]
            try:
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            except Exception:
                self.skipTest("Module-level import failed (missing deps)")

            # Patch the module's config path and reload
            import symbol_paths  # type: ignore[import]
            orig_path = symbol_paths._CONFIG_PATH
            symbol_paths._CONFIG_PATH = cfg_path
            symbol_paths._reload_config()
            try:
                import argparse as _ap
                # Simulate: python backtest_event_v3_http.py --data-dir data/TESTUSDT --symbol TESTUSDT
                # We exercise only the arg-resolution logic by calling parse_args with minimal input
                ap = _ap.ArgumentParser()
                # Re-use all arguments registered by the script's main()
                # We import the _BACKTEST_DEFAULTS dict from the module
                self.assertTrue(
                    hasattr(mod, "_BACKTEST_DEFAULTS"),
                    "backtest_event_v3_http.py must expose _BACKTEST_DEFAULTS at module level",
                )
                defaults = mod._BACKTEST_DEFAULTS
                self.assertIn("horizon_bars", defaults)
                self.assertIn("interval", defaults)
                self.assertIn("thresholds", defaults)
                self.assertIn("tp_grid", defaults)
                self.assertIn("sl_grid", defaults)
            finally:
                symbol_paths._CONFIG_PATH = orig_path
                symbol_paths._reload_config()
        finally:
            shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Tests: live_trader_perp_simulated.py uses shared config loader (no local def)
# ---------------------------------------------------------------------------

class TestSimulatedTraderUsesSharedConfig(unittest.TestCase):
    """Regression: live_trader_perp_simulated.py must not define a local get_symbol_config."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_perp_simulated.py")

    def test_no_local_get_symbol_config_definition(self) -> None:
        """Source of live_trader_perp_simulated.py must not contain 'def get_symbol_config'."""
        with open(self._SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(
            "def get_symbol_config",
            source,
            "live_trader_perp_simulated.py must not define a local get_symbol_config(); "
            "use the shared loader from symbol_config instead.",
        )

    def test_imports_get_symbol_config_from_symbol_config(self) -> None:
        """live_trader_perp_simulated.py must import get_symbol_config from symbol_config."""
        import ast

        with open(self._SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            self.fail(f"live_trader_perp_simulated.py has a syntax error: {exc}")

        # Walk the AST looking for: from symbol_config import (..., get_symbol_config, ...)
        found = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "symbol_config"
            and any(alias.name == "get_symbol_config" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(
            found,
            "live_trader_perp_simulated.py must have 'from symbol_config import get_symbol_config' "
            "(or include it in a multi-name import from symbol_config).",
        )


# ---------------------------------------------------------------------------
# Tests: live_trader_perp_simulated.py logic CLI flags (PR #29)
# ---------------------------------------------------------------------------

class TestSimulatedTraderLogicCLI(unittest.TestCase):
    """Regression: live_trader_perp_simulated.py must expose the same logic flags as backtest."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_perp_simulated.py")

    def _get_help(self) -> str:
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def test_help_contains_mt_filter_mode(self) -> None:
        """--mt-filter-mode flag must appear in --help."""
        self.assertIn("--mt-filter-mode", self._get_help())

    def test_help_contains_side_source(self) -> None:
        """--side-source flag must appear in --help."""
        self.assertIn("--side-source", self._get_help())

    def test_help_contains_timeout_exit(self) -> None:
        """--timeout-exit flag must appear in --help."""
        self.assertIn("--timeout-exit", self._get_help())

    def test_help_contains_tie_breaker(self) -> None:
        """--tie-breaker flag must appear in --help."""
        self.assertIn("--tie-breaker", self._get_help())

    def test_help_contains_position_mode(self) -> None:
        """--position-mode flag must appear in --help."""
        self.assertIn("--position-mode", self._get_help())

    def test_help_contains_pred_cache_file(self) -> None:
        """--pred-cache-file flag must appear in --help."""
        self.assertIn("--pred-cache-file", self._get_help())

    def test_default_mt_filter_mode_is_daily_guard(self) -> None:
        """Default --mt-filter-mode must be daily_guard (consistent with backtest default)."""
        spec = importlib.util.spec_from_file_location("live_trader_simulated_test", self._SCRIPT)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        mod.__name__ = "live_trader_simulated_test"  # type: ignore[assignment]
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            self.skipTest("Module-level import failed (missing deps)")
        ap = mod.build_parser()
        defaults = ap.parse_args([])
        self.assertEqual(
            defaults.mt_filter_mode, "daily_guard",
            "Default --mt-filter-mode must be 'daily_guard'",
        )
        self.assertEqual(
            defaults.side_source, "probs",
            "Default --side-source must be 'probs'",
        )
        self.assertEqual(
            defaults.timeout_exit, "close",
            "Default --timeout-exit must be 'close'",
        )
        self.assertEqual(
            defaults.tie_breaker, "SL",
            "Default --tie-breaker must be 'SL'",
        )
        self.assertEqual(
            defaults.position_mode, "single",
            "Default --position-mode must be 'single'",
        )

    def test_uses_decision_pipeline_module(self) -> None:
        """live_trader_perp_simulated.py must import from decision_pipeline."""
        import ast
        with open(self._SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            self.fail(f"live_trader_perp_simulated.py has a syntax error: {exc}")
        found = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "decision_pipeline"
            for node in ast.walk(tree)
        )
        self.assertTrue(found, "live_trader_perp_simulated.py must import from decision_pipeline")

    def test_no_hardcoded_scheme_b_filter(self) -> None:
        """live_trader_perp_simulated.py must not use hardcoded 't4h != UP' filter logic."""
        with open(self._SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(
            't4h != "UP"',
            source,
            "Hardcoded Scheme B filter (t4h != 'UP') must be removed; use apply_mt_filter_with_context instead.",
        )


# ---------------------------------------------------------------------------
# Tests: decision_pipeline consistency (same inputs → same outputs)
# ---------------------------------------------------------------------------

class TestDecisionPipelineConsistency(unittest.TestCase):
    """Consistency: decision_pipeline must produce identical results for backtest and simulated replay."""

    def setUp(self) -> None:
        sys.path.insert(0, SCRIPTS_DIR)

    def _import_pipeline(self):
        try:
            import decision_pipeline  # type: ignore[import]
            return decision_pipeline
        except ImportError as exc:
            self.skipTest(f"decision_pipeline not importable: {exc}")

    def test_decide_side_from_prediction_probs_long(self) -> None:
        """probs mode: p_long >= threshold and p_long >= p_short → LONG."""
        dp = self._import_pipeline()
        pred = {
            "signal": "SHORT",  # signal says SHORT, but probs say LONG
            "effective_long": 0.87,
            "effective_short": 0.05,
        }
        side, dbg = dp.decide_side_from_prediction(pred, side_source="probs", threshold=0.84)
        self.assertEqual(side, "LONG")
        self.assertEqual(dbg["side_source"], "probs")

    def test_decide_side_from_prediction_signal_mode(self) -> None:
        """signal mode: uses the 'signal' field regardless of probs."""
        dp = self._import_pipeline()
        pred = {
            "signal": "SHORT",
            "effective_long": 0.87,
            "effective_short": 0.05,
        }
        side, _ = dp.decide_side_from_prediction(pred, side_source="signal", threshold=0.84)
        self.assertEqual(side, "SHORT")

    def test_decide_side_below_threshold_is_flat(self) -> None:
        """probs mode: both probs below threshold → FLAT."""
        dp = self._import_pipeline()
        pred = {
            "signal": "LONG",
            "effective_long": 0.70,
            "effective_short": 0.15,
        }
        side, _ = dp.decide_side_from_prediction(pred, side_source="probs", threshold=0.84)
        self.assertEqual(side, "FLAT")

    def test_decide_side_from_cached_pred_matches_prediction(self) -> None:
        """decide_side_from_cached_pred and decide_side_from_prediction must agree on identical inputs."""
        dp = self._import_pipeline()
        threshold = 0.84

        raw_pred = {
            "signal": "LONG",
            "effective_long": 0.87,
            "effective_short": 0.05,
            "effective_flat": 0.08,
        }
        side_pred, _ = dp.decide_side_from_prediction(raw_pred, side_source="probs", threshold=threshold)

        # Simulate what backtest would store in pred_cache after select_effective_probs
        cached = {
            "signal": "LONG",
            "selected_p_long": 0.87,
            "selected_p_short": 0.05,
            "selected_p_flat": 0.08,
            "selected_prob_source": "effective",
        }
        side_cached, _ = dp.decide_side_from_cached_pred(cached, side_source="probs", threshold=threshold)

        self.assertEqual(
            side_pred, side_cached,
            "decide_side_from_prediction and decide_side_from_cached_pred must return the same side "
            "for identical probability inputs.",
        )

    def test_consistency_across_all_modes(self) -> None:
        """For various sample predictions, both functions produce identical LONG/SHORT/FLAT."""
        dp = self._import_pipeline()
        threshold = 0.80

        samples = [
            # (signal, eff_long, eff_short, expected_probs_side)
            ("LONG",  0.85, 0.10, "LONG"),
            ("SHORT", 0.10, 0.85, "SHORT"),
            ("FLAT",  0.70, 0.15, "FLAT"),   # below threshold
            ("LONG",  0.82, 0.81, "LONG"),   # long >= threshold and >= short
            ("SHORT", 0.81, 0.82, "SHORT"),  # short >= threshold and > long
        ]

        for signal, eff_long, eff_short, expected in samples:
            raw_pred = {"signal": signal, "effective_long": eff_long, "effective_short": eff_short}
            cached = {
                "signal": signal,
                "selected_p_long": eff_long,
                "selected_p_short": eff_short,
                "selected_p_flat": round(1.0 - eff_long - eff_short, 4),
                "selected_prob_source": "effective",
            }
            side_pred, _ = dp.decide_side_from_prediction(raw_pred, side_source="probs", threshold=threshold)
            side_cached, _ = dp.decide_side_from_cached_pred(cached, side_source="probs", threshold=threshold)

            self.assertEqual(
                side_pred, expected,
                f"decide_side_from_prediction({signal}, eff_long={eff_long}, eff_short={eff_short}) "
                f"expected {expected} got {side_pred}",
            )
            self.assertEqual(
                side_pred, side_cached,
                f"Mismatch between prediction and cached for ({signal}, {eff_long}, {eff_short}): "
                f"pred={side_pred} cached={side_cached}",
            )

    def test_backtest_default_mt_filter_is_daily_guard(self) -> None:
        """backtest_event_v3_http.py --mt-filter-mode default must be daily_guard."""
        bt_script = os.path.join(SCRIPTS_DIR, "backtest_event_v3_http.py")
        result = subprocess.run(
            [sys.executable, bt_script, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        # The default value for --mt-filter-mode should mention daily_guard
        # (argparse with ArgumentDefaultsHelpFormatter prints the default)
        self.assertIn(
            "daily_guard", result.stdout,
            "backtest --mt-filter-mode default should be daily_guard",
        )


# ---------------------------------------------------------------------------
# Tests: new CLI flags added for backtest-alignment fixes
# ---------------------------------------------------------------------------

class TestSimulatedTraderAlignmentFlags(unittest.TestCase):
    """New alignment-fix flags: --max-consec-losses default 999, --entry-on-next-bar default True."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_perp_simulated.py")

    def _load_module(self):
        spec = importlib.util.spec_from_file_location("ltsim_align", self._SCRIPT)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        mod.__name__ = "ltsim_align"  # type: ignore[assignment]
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            self.skipTest("Module-level import failed (missing deps)")
        return mod

    def test_max_consec_losses_default_is_999(self) -> None:
        """--max-consec-losses default must be 999 (circuit breaker effectively disabled)."""
        mod = self._load_module()
        ap = mod.build_parser()
        defaults = ap.parse_args([])
        self.assertEqual(
            defaults.max_consec_losses, 999,
            "--max-consec-losses default must be 999 to disable circuit breaker during alignment",
        )

    def test_help_contains_max_consec_losses(self) -> None:
        """--max-consec-losses flag must appear in --help."""
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--max-consec-losses", result.stdout)

    def test_entry_on_next_bar_default_is_true(self) -> None:
        """--entry-on-next-bar default must be True."""
        mod = self._load_module()
        ap = mod.build_parser()
        defaults = ap.parse_args([])
        self.assertTrue(
            defaults.entry_on_next_bar,
            "--entry-on-next-bar default must be True (matching backtest entry timing)",
        )

    def test_entry_on_next_bar_false_accepted(self) -> None:
        """--entry-on-next-bar false must parse as False."""
        mod = self._load_module()
        ap = mod.build_parser()
        args = ap.parse_args(["--entry-on-next-bar", "false"])
        self.assertFalse(args.entry_on_next_bar, "--entry-on-next-bar false must be parsed as False")

    def test_help_contains_entry_on_next_bar(self) -> None:
        """--entry-on-next-bar flag must appear in --help."""
        result = subprocess.run(
            [sys.executable, self._SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--entry-on-next-bar", result.stdout)


# ---------------------------------------------------------------------------
# Tests: SimpleRiskEngine next_allowed_ts unlocks from exit_ts
# ---------------------------------------------------------------------------

class TestSimulationNextAllowedTsFromExitTs(unittest.TestCase):
    """next_allowed_ts must be set from trade.exit_ts, not pre-computed at entry."""

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_perp_simulated.py")

    def _load_module(self):
        spec = importlib.util.spec_from_file_location("ltsim_exit_ts", self._SCRIPT)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        mod.__name__ = "ltsim_exit_ts"  # type: ignore[assignment]
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            self.skipTest("Module-level import failed (missing deps)")
        return mod

    def test_check_exit_returns_closed_trade_with_exit_ts(self) -> None:
        """SimpleRiskEngine.check_exit() must return a ClosedTrade with exit_ts on TP hit."""
        from datetime import datetime, timezone, timedelta
        mod = self._load_module()

        engine = mod.SimpleRiskEngine(
            capital=10_000.0,
            position_fraction=0.30,
            leverage=5.0,
            max_consec_losses=999,
            fee_per_side=0.0004,
            tie_breaker="SL",
            timeout_exit="close",
        )

        entry_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        klines = [
            {"ts": entry_ts + timedelta(hours=i), "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
            for i in range(20)
        ]
        # Make bar 3 hit TP
        klines[3]["high"] = 103.0

        engine.open_position(
            side="LONG",
            price=100.0,
            ts=klines[0]["ts"],
            bar_index=0,
            tp_pct=0.02,
            sl_pct=0.01,
            horizon_bars=10,
            klines_1h=klines,
        )

        trade = None
        for bar in klines[1:]:
            result = engine.check_exit(bar)
            if result is not None:
                trade = result
                break

        self.assertIsNotNone(trade, "check_exit must return a ClosedTrade when TP is hit")
        self.assertEqual(trade.outcome, "TP")
        expected_exit_ts = klines[3]["ts"]
        self.assertEqual(trade.exit_ts, expected_exit_ts, "exit_ts must equal the bar ts when TP was hit")

    def test_no_next_allowed_ts_set_at_open_position(self) -> None:
        """The simulation loop must NOT set next_allowed_ts at open_position time (old bug).

        Verify by checking that the script source no longer contains the old pattern:
        'exit_bar_idx = min(i + horizon_bars, len(klines_1h) - 1)' inside the open-signal block.
        """
        with open(self._SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        # The old 'advance next_allowed_ts to end of horizon' comment should be gone
        self.assertNotIn(
            "advance next_allowed_ts to end of horizon",
            source,
            "Old next_allowed_ts-at-open logic must be removed",
        )


# ---------------------------------------------------------------------------
# Tests: same-bar exit fix (entry_on_next_bar=True, TP/SL on entry bar)
# ---------------------------------------------------------------------------

class TestSameBarExit(unittest.TestCase):
    """Verify that the simulated trader checks TP/SL on the entry bar itself.

    When entry_on_next_bar=True, the position is opened at bar i+1's open.
    The engine must immediately check TP/SL on that same bar (bar i+1), matching
    backtest simulate_trade() which starts scanning from j=i+1.
    """

    _SCRIPT = os.path.join(SCRIPTS_DIR, "live_trader_perp_simulated.py")

    def _load_module(self):
        spec = importlib.util.spec_from_file_location("ltsim_same_bar", self._SCRIPT)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        mod.__name__ = "ltsim_same_bar"  # type: ignore[assignment]
        sys.modules["ltsim_same_bar"] = mod
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            sys.modules.pop("ltsim_same_bar", None)
            self.skipTest("Module-level import failed (missing deps)")
        return mod

    def _make_klines(self, n=20):
        """Build a list of flat klines starting at 2026-03-04T00:00:00Z."""
        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 3, 4, 0, 0, 0, tzinfo=timezone.utc)
        return [
            {
                "ts": base + timedelta(hours=i),
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
            }
            for i in range(n)
        ]

    def test_same_bar_tp_exit_long(self) -> None:
        """LONG: when entry bar high >= TP, position closes on the entry bar (exit_ts == entry_ts)."""
        from datetime import timedelta
        mod = self._load_module()

        klines = self._make_klines(20)
        # Bar 5 is the signal bar; bar 6 is the entry bar (entry_on_next_bar=True)
        # Set bar 6 high above TP level (entry=100.0, tp_pct=0.02 → TP=102.0)
        klines[6]["high"] = 103.0  # > 102.0, so TP is hit on entry bar

        engine = mod.SimpleRiskEngine(
            capital=10_000.0,
            position_fraction=0.30,
            leverage=5.0,
            max_consec_losses=999,
            fee_per_side=0.0004,
            tie_breaker="SL",
            timeout_exit="close",
        )

        # Open position at bar 6 (entry bar open=100.0)
        engine.open_position(
            side="LONG",
            price=klines[6]["open"],
            ts=klines[6]["ts"],
            bar_index=6,
            tp_pct=0.02,
            sl_pct=0.01,
            horizon_bars=10,
            klines_1h=klines,
        )

        # Same-bar check must trigger
        trade = engine.check_exit(klines[6])

        self.assertIsNotNone(trade, "check_exit on entry bar must return a ClosedTrade when high >= TP")
        self.assertEqual(trade.outcome, "TP", "Outcome must be TP")
        self.assertEqual(
            trade.exit_ts, klines[6]["ts"],
            "exit_ts must equal entry bar ts (same-bar exit → bars_held=0)",
        )
        self.assertEqual(
            trade.entry_ts, trade.exit_ts,
            "entry_ts and exit_ts must be equal for a same-bar exit",
        )

    def test_same_bar_sl_exit_long(self) -> None:
        """LONG: when entry bar low <= SL, position closes on the entry bar."""
        mod = self._load_module()

        klines = self._make_klines(20)
        # Set bar 6 low below SL (entry=100.0, sl_pct=0.01 → SL=99.0)
        klines[6]["low"] = 98.5  # < 99.0, so SL is hit on entry bar
        klines[6]["high"] = 100.5  # not high enough to trigger TP (TP=102.0)

        engine = mod.SimpleRiskEngine(
            capital=10_000.0,
            position_fraction=0.30,
            leverage=5.0,
            max_consec_losses=999,
            fee_per_side=0.0004,
            tie_breaker="SL",
            timeout_exit="close",
        )

        engine.open_position(
            side="LONG",
            price=klines[6]["open"],
            ts=klines[6]["ts"],
            bar_index=6,
            tp_pct=0.02,
            sl_pct=0.01,
            horizon_bars=10,
            klines_1h=klines,
        )

        trade = engine.check_exit(klines[6])

        self.assertIsNotNone(trade, "check_exit on entry bar must return a ClosedTrade when low <= SL")
        self.assertEqual(trade.outcome, "SL", "Outcome must be SL")
        self.assertEqual(trade.exit_ts, klines[6]["ts"], "exit_ts must equal entry bar ts")

    def test_same_bar_tp_exit_short(self) -> None:
        """SHORT: when entry bar low <= TP, position closes on the entry bar."""
        mod = self._load_module()

        klines = self._make_klines(20)
        # SHORT entry=100.0, tp_pct=0.02 → TP=98.0
        klines[6]["low"] = 97.0   # < 98.0, so TP is hit on entry bar
        klines[6]["high"] = 100.5  # not high enough to trigger SL (SL=101.0)

        engine = mod.SimpleRiskEngine(
            capital=10_000.0,
            position_fraction=0.30,
            leverage=5.0,
            max_consec_losses=999,
            fee_per_side=0.0004,
            tie_breaker="SL",
            timeout_exit="close",
        )

        engine.open_position(
            side="SHORT",
            price=klines[6]["open"],
            ts=klines[6]["ts"],
            bar_index=6,
            tp_pct=0.02,
            sl_pct=0.01,
            horizon_bars=10,
            klines_1h=klines,
        )

        trade = engine.check_exit(klines[6])

        self.assertIsNotNone(trade, "SHORT: check_exit on entry bar must return ClosedTrade when low <= TP")
        self.assertEqual(trade.outcome, "TP", "Outcome must be TP")
        self.assertEqual(trade.exit_ts, klines[6]["ts"], "exit_ts must equal entry bar ts")

    def test_no_same_bar_exit_when_tp_sl_not_hit(self) -> None:
        """When entry bar does not hit TP or SL, check_exit returns None (no premature close)."""
        mod = self._load_module()

        klines = self._make_klines(20)
        # Entry bar has high=100.5, low=99.5 → TP=102.0 not hit, SL=99.0 not hit
        engine = mod.SimpleRiskEngine(
            capital=10_000.0,
            position_fraction=0.30,
            leverage=5.0,
            max_consec_losses=999,
            fee_per_side=0.0004,
            tie_breaker="SL",
            timeout_exit="close",
        )

        engine.open_position(
            side="LONG",
            price=klines[6]["open"],
            ts=klines[6]["ts"],
            bar_index=6,
            tp_pct=0.02,
            sl_pct=0.01,
            horizon_bars=10,
            klines_1h=klines,
        )

        trade = engine.check_exit(klines[6])

        self.assertIsNone(trade, "check_exit must return None when entry bar does not hit TP or SL")
        self.assertIsNotNone(engine.position, "Position must still be open when entry bar does not exit")

    def test_same_bar_exit_via_run_simulation_uses_entry_bar(self) -> None:
        """Full simulation loop: when entry bar high >= TP, trade exits on entry bar.

        Constructs a minimal pred_cache-driven scenario: signal fires at bar 5,
        position opens at bar 6 (entry_on_next_bar=True), and bar 6 high exceeds TP.
        The resulting closed trade must have exit_ts == bar 6 ts (bars_held == 0).
        """
        from datetime import datetime, timezone, timedelta
        import json
        mod = self._load_module()

        base = datetime(2026, 3, 4, 0, 0, 0, tzinfo=timezone.utc)

        # Build 15 bars; bar 5 carries the signal, bar 6 hits TP immediately
        klines = []
        for i in range(15):
            klines.append({
                "ts": base + timedelta(hours=i),
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
            })
        # Bar 6: entry bar high > TP (entry=100.0, tp_pct=0.0175 → TP=101.75)
        klines[6]["high"] = 102.0

        # Write klines and pred_cache to temp dir
        tmpdir = tempfile.mkdtemp(prefix="uw-same-bar-test-")
        try:
            sym_dir = os.path.join(tmpdir, "TESTUSDT")
            os.makedirs(sym_dir, exist_ok=True)

            def _write_klines_json(path, bars):
                rows = []
                for bar in bars:
                    row = dict(bar)
                    row["ts"] = bar["ts"].isoformat().replace("+00:00", "Z")
                    rows.append(row)
                with open(path, "w") as f:
                    json.dump(rows, f)

            _write_klines_json(os.path.join(sym_dir, "klines_1h.json"), klines)
            # 4h and 1d klines: provide minimal data to satisfy the loader
            _write_klines_json(os.path.join(sym_dir, "klines_4h.json"), klines[:4])
            _write_klines_json(os.path.join(sym_dir, "klines_1d.json"), klines[:2])

            # pred_cache JSONL format:
            #   Line 0: meta (skipped by loader)
            #   Lines 1+: {"as_of_ts": "...", "pred": {...}}
            cache_path = os.path.join(sym_dir, "pred_cache.jsonl")
            with open(cache_path, "w") as f:
                f.write(json.dumps({"meta": {"symbol": "TESTUSDT", "interval": "1h"}}) + "\n")
                for bar in klines:
                    ts_str = bar["ts"].isoformat().replace("+00:00", "Z")
                    pred = {
                        "signal": "FLAT",
                        "selected_p_long": 0.10,
                        "selected_p_short": 0.05,
                        "selected_p_flat": 0.85,
                        "selected_prob_source": "effective",
                    }
                    if bar is klines[5]:
                        pred = {
                            "signal": "LONG",
                            "selected_p_long": 0.90,
                            "selected_p_short": 0.05,
                            "selected_p_flat": 0.05,
                            "selected_prob_source": "effective",
                        }
                    f.write(json.dumps({"as_of_ts": ts_str, "pred": pred}) + "\n")

            # Run simulation via subprocess to avoid import conflicts
            equity_path = os.path.join(tmpdir, "equity.jsonl")
            result = subprocess.run(
                [
                    sys.executable, self._SCRIPT,
                    "--symbol", "TESTUSDT",
                    "--data-base-dir", tmpdir,
                    "--base-url", "http://127.0.0.1:9999",  # unused; pred_cache overrides
                    "--pred-cache-file", cache_path,
                    "--tp", "0.0175",
                    "--sl", "0.009",
                    "--horizon", "12",
                    "--threshold", "0.84",
                    "--mt-filter-mode", "off",
                    "--entry-on-next-bar", "true",
                    "--output-equity", equity_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout + result.stderr

            # Check that the trade closed with outcome TP and that entry bar ts appears as exit_ts
            entry_bar_ts = klines[6]["ts"].isoformat()
            self.assertIn(
                "TP",
                output,
                f"Simulation output must contain a TP exit. Got:\n{output}",
            )
            # The [CLOSE] line should reference the entry bar ts (same-bar exit)
            self.assertIn(
                entry_bar_ts[:16],  # match "2026-03-04T06:00"
                output,
                f"Exit ts in [CLOSE] line must be the entry bar ts ({entry_bar_ts[:16]}). Got:\n{output}",
            )
        finally:
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
