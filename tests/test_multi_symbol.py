"""Tests for multi-symbol path resolution and per-symbol configuration loading."""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
for _d in [SCRIPTS_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_symbol_paths_cache() -> None:
    """Force symbol_paths to reload config on the next call."""
    import symbol_paths  # type: ignore[import]
    symbol_paths._reload_config()


# ---------------------------------------------------------------------------
# Tests: symbol_paths module
# ---------------------------------------------------------------------------

class TestSymbolPathsDefaults(unittest.TestCase):
    """Path helpers return correct symbol-scoped paths."""

    def setUp(self) -> None:
        _reset_symbol_paths_cache()

    def test_get_symbol_data_dir(self) -> None:
        from symbol_paths import get_symbol_data_dir  # type: ignore[import]
        path = get_symbol_data_dir("BTCUSDT", base_data_dir="/base/data")
        self.assertEqual(path, "/base/data/BTCUSDT")

    def test_get_symbol_model_dir(self) -> None:
        from symbol_paths import get_symbol_model_dir  # type: ignore[import]
        path = get_symbol_model_dir("ETHUSDT", base_model_dir="/base/models")
        self.assertEqual(path, "/base/models/ETHUSDT")

    def test_get_symbol_train_stats_path(self) -> None:
        from symbol_paths import get_symbol_train_stats_path  # type: ignore[import]
        path = get_symbol_train_stats_path("SOLUSDT", base_model_dir="/base/models")
        self.assertEqual(path, "/base/models/SOLUSDT/current/train_feature_stats.json")

    def test_get_symbol_log_path(self) -> None:
        from symbol_paths import get_symbol_log_path  # type: ignore[import]
        path = get_symbol_log_path("BNBUSDT", base_data_dir="/base/data")
        self.assertEqual(path, "/base/data/BNBUSDT/predictions_log.jsonl")

    def test_get_symbol_reports_dir(self) -> None:
        from symbol_paths import get_symbol_reports_dir  # type: ignore[import]
        path = get_symbol_reports_dir("XRPUSDT", base_data_dir="/base/data")
        self.assertEqual(path, "/base/data/XRPUSDT/reports")

    def test_paths_differ_between_symbols(self) -> None:
        """Each symbol must resolve to a distinct directory."""
        from symbol_paths import get_symbol_data_dir, get_symbol_model_dir  # type: ignore[import]
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        data_dirs = {get_symbol_data_dir(s, "/d") for s in symbols}
        model_dirs = {get_symbol_model_dir(s, "/m") for s in symbols}
        self.assertEqual(len(data_dirs), len(symbols), "All data dirs must be unique")
        self.assertEqual(len(model_dirs), len(symbols), "All model dirs must be unique")

    def test_data_dir_respects_env_override(self) -> None:
        """DATA_DIR env var must be used as the base when no explicit override."""
        import symbol_paths  # type: ignore[import]
        orig = os.environ.get("DATA_DIR")
        try:
            os.environ["DATA_DIR"] = "/env/override/data"
            path = symbol_paths.get_symbol_data_dir("BTCUSDT")
            self.assertTrue(path.startswith("/env/override/data/"))
        finally:
            if orig is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = orig

    def test_model_dir_respects_env_override(self) -> None:
        """MODEL_DIR env var must be used as the base when no explicit override."""
        import symbol_paths  # type: ignore[import]
        orig = os.environ.get("MODEL_DIR")
        try:
            os.environ["MODEL_DIR"] = "/env/override/models"
            path = symbol_paths.get_symbol_model_dir("ETHUSDT")
            self.assertTrue(path.startswith("/env/override/models/"))
        finally:
            if orig is None:
                os.environ.pop("MODEL_DIR", None)
            else:
                os.environ["MODEL_DIR"] = orig


# ---------------------------------------------------------------------------
# Tests: per-symbol config loading
# ---------------------------------------------------------------------------

class TestSymbolConfig(unittest.TestCase):
    """Per-symbol configuration is loaded correctly from configs/symbols.yaml."""

    def setUp(self) -> None:
        _reset_symbol_paths_cache()

    def test_config_file_exists(self) -> None:
        config_path = os.path.join(REPO_ROOT, "configs", "symbols.yaml")
        self.assertTrue(
            os.path.exists(config_path),
            f"configs/symbols.yaml must exist at {config_path}",
        )

    def test_all_required_symbols_present(self) -> None:
        from symbol_paths import _load_symbols_config  # type: ignore[import]
        cfg = _load_symbols_config()
        required = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]
        for sym in required:
            self.assertIn(sym, cfg, f"{sym} must be present in configs/symbols.yaml")

    def test_config_has_required_fields(self) -> None:
        from symbol_paths import get_symbol_config  # type: ignore[import]
        required_fields = ["enabled", "interval", "threshold", "tp", "sl", "horizon", "calibration"]
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            cfg = get_symbol_config(sym)
            for field in required_fields:
                self.assertIn(field, cfg, f"{sym} config must have field '{field}'")

    def test_phase1_symbols_enabled(self) -> None:
        from symbol_paths import get_symbol_config  # type: ignore[import]
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
            cfg = get_symbol_config(sym)
            self.assertTrue(cfg["enabled"], f"Phase 1 symbol {sym} should be enabled by default")

    def test_phase2_symbols_enabled(self) -> None:
        from symbol_paths import get_symbol_config  # type: ignore[import]
        for sym in ["XRPUSDT", "DOGEUSDT", "ADAUSDT"]:
            cfg = get_symbol_config(sym)
            self.assertTrue(cfg["enabled"], f"Phase 2 symbol {sym} should now be enabled")

    def test_config_values_types(self) -> None:
        from symbol_paths import get_symbol_config  # type: ignore[import]
        cfg = get_symbol_config("BTCUSDT")
        self.assertIsInstance(cfg["enabled"], bool)
        self.assertIsInstance(cfg["interval"], str)
        self.assertIsInstance(cfg["threshold"], float)
        self.assertIsInstance(cfg["tp"], float)
        self.assertIsInstance(cfg["sl"], float)
        self.assertIsInstance(cfg["horizon"], int)
        self.assertIsInstance(cfg["calibration"], str)

    def test_unknown_symbol_returns_defaults(self) -> None:
        from symbol_paths import get_symbol_config, _DEFAULTS  # type: ignore[import]
        cfg = get_symbol_config("UNKNOWNUSDT")
        self.assertEqual(cfg, _DEFAULTS)

    def test_config_custom_yaml_overrides_defaults(self) -> None:
        """A custom symbols.yaml must override the _DEFAULTS values."""
        import symbol_paths  # type: ignore[import]
        tmpdir = tempfile.mkdtemp(prefix="uw-sym-test-")
        try:
            os.makedirs(os.path.join(tmpdir, "configs"))
            cfg_path = os.path.join(tmpdir, "configs", "symbols.yaml")
            with open(cfg_path, "w") as f:
                f.write(
                    "symbols:\n"
                    "  TESTUSDT:\n"
                    "    enabled: false\n"
                    "    threshold: 0.70\n"
                    "    tp: 0.025\n"
                    "    sl: 0.012\n"
                    "    horizon: 24\n"
                    "    interval: '4h'\n"
                    "    calibration: sigmoid\n"
                )
            # Temporarily patch config path and cache
            orig_path = symbol_paths._CONFIG_PATH
            symbol_paths._CONFIG_PATH = cfg_path
            symbol_paths._reload_config()
            try:
                cfg = symbol_paths.get_symbol_config("TESTUSDT")
                self.assertFalse(cfg["enabled"])
                self.assertAlmostEqual(cfg["threshold"], 0.70)
                self.assertAlmostEqual(cfg["tp"], 0.025)
                self.assertAlmostEqual(cfg["sl"], 0.012)
                self.assertEqual(cfg["horizon"], 24)
                self.assertEqual(cfg["interval"], "4h")
                self.assertEqual(cfg["calibration"], "sigmoid")
            finally:
                symbol_paths._CONFIG_PATH = orig_path
                symbol_paths._reload_config()
        finally:
            shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Tests: list_enabled_symbols
# ---------------------------------------------------------------------------

class TestListEnabledSymbols(unittest.TestCase):

    def setUp(self) -> None:
        _reset_symbol_paths_cache()

    def test_phase1_returns_four_symbols(self) -> None:
        from symbol_paths import list_enabled_symbols  # type: ignore[import]
        symbols = list_enabled_symbols(phase=1)
        self.assertEqual(set(symbols), {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"})

    def test_phase2_returns_all_enabled(self) -> None:
        """Phase 2 symbols are now enabled; list should contain all three."""
        from symbol_paths import list_enabled_symbols  # type: ignore[import]
        symbols = list_enabled_symbols(phase=2)
        self.assertEqual(set(symbols), {"XRPUSDT", "DOGEUSDT", "ADAUSDT"})

    def test_no_phase_returns_all_enabled(self) -> None:
        from symbol_paths import list_enabled_symbols  # type: ignore[import]
        symbols = list_enabled_symbols()
        # All 7 symbols are now enabled
        self.assertEqual(
            set(symbols),
            {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"},
        )

    def test_all_symbols_constant(self) -> None:
        from symbol_paths import ALL_SYMBOLS  # type: ignore[import]
        expected = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]
        self.assertEqual(ALL_SYMBOLS, expected)


# ---------------------------------------------------------------------------
# Tests: backward compatibility — evaluate_from_logs still accepts explicit args
# ---------------------------------------------------------------------------

class TestEvaluateFromLogsBackwardCompat(unittest.TestCase):
    """evaluate_from_logs.py must still accept explicit --threshold/--tp/--sl."""

    def test_help_does_not_error(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "evaluate_from_logs.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"--help returned non-zero: {result.stderr}")
        self.assertIn("--symbol", result.stdout)
        self.assertIn("--threshold", result.stdout)
        self.assertIn("--tp", result.stdout)
        self.assertIn("--sl", result.stdout)


# ---------------------------------------------------------------------------
# Tests: backward compatibility — report_drift still accepts explicit args
# ---------------------------------------------------------------------------

class TestReportDriftBackwardCompat(unittest.TestCase):
    """report_drift.py must still work with explicit --train-stats and --log-path."""

    def test_help_does_not_error(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "report_drift.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"--help returned non-zero: {result.stderr}")
        self.assertIn("--symbol", result.stdout)
        self.assertIn("--train-stats", result.stdout)

    def test_missing_train_stats_errors(self) -> None:
        """report_drift.py should exit non-zero when train-stats file is missing."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "predictions_log.jsonl")
            with open(log_path, "w") as f:
                f.write('{"features": {"sma_7": 1.0}}\n')
            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(SCRIPTS_DIR, "report_drift.py"),
                    "--train-stats", os.path.join(tmpdir, "nonexistent.json"),
                    "--log-path", log_path,
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "ENABLE_DRIFT_MONITOR": "true"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR", result.stderr)

    def test_missing_both_required_args_errors(self) -> None:
        """report_drift.py should exit non-zero when no args are given."""
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "report_drift.py")],
            capture_output=True,
            text=True,
            env={**os.environ, "ENABLE_DRIFT_MONITOR": "true"},
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
