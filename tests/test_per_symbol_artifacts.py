"""Focused tests for per-symbol training artifact persistence and drift readiness.

Validates that:
- every enabled symbol uses the same artifact contract under models/<SYMBOL>/current/
- _promote_to_current works correctly for non-primary symbols
- report_drift.py --all-symbols loops over all enabled symbols (failure-isolated)
- train_all_symbols.sh exists, is executable, and has correct structure
- drift-monitor.service is configured for per-symbol coverage
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
PY_ANALYZER_DIR = os.path.join(REPO_ROOT, "python-analyzer")
for _d in [SCRIPTS_DIR, PY_ANALYZER_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from train_event_stack_v3 import _ARTIFACT_FILES, _promote_to_current  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_minimal_current_dir(current_dir: str, features: list) -> None:
    """Create a minimal models/<SYMBOL>/current/ directory with required drift artifacts."""
    os.makedirs(current_dir, exist_ok=True)
    stats = {
        feat: {"mean": float(i) * 0.1, "std": 1.0 + float(i) * 0.05, "missing_rate": 0.0}
        for i, feat in enumerate(features)
    }
    with open(os.path.join(current_dir, "train_feature_stats.json"), "w") as f:
        json.dump(stats, f)
    with open(os.path.join(current_dir, "model_meta.json"), "w") as f:
        json.dump({"active_model": "event_v3", "trained_at": "2026-01-01T00:00:00Z"}, f)


def _write_predictions_log(path: str, symbol: str, features: list, n_rows: int = 5) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for i in range(n_rows):
            row = {
                "symbol": symbol,
                "features": {feat: float(i) * 0.1 + idx * 0.01 for idx, feat in enumerate(features)},
            }
            f.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Test: all enabled symbols have the same required artifact contract
# ---------------------------------------------------------------------------

class TestAllEnabledSymbolsArtifactContract(unittest.TestCase):
    """Validate that every enabled symbol can receive the full artifact set."""

    def setUp(self) -> None:
        import symbol_paths  # type: ignore
        symbol_paths._reload_config()
        self._tmpdir = tempfile.mkdtemp(prefix="uw-artifact-test-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir)
        import symbol_paths  # type: ignore
        symbol_paths._reload_config()

    def test_all_enabled_symbols_are_seven(self) -> None:
        """All 7 symbols should be enabled after phase-2 rollout."""
        from symbol_paths import list_enabled_symbols  # type: ignore
        enabled = list_enabled_symbols()
        self.assertEqual(
            set(enabled),
            {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"},
            "All 7 symbols must be enabled in configs/symbols.yaml.",
        )

    def test_promote_to_current_works_for_all_enabled_symbols(self) -> None:
        """_promote_to_current must copy train_feature_stats.json for every enabled symbol."""
        from symbol_paths import list_enabled_symbols  # type: ignore
        symbols = list_enabled_symbols()
        features = ["feat_rsi", "feat_macd", "feat_atr"]

        for sym in symbols:
            with self.subTest(symbol=sym):
                model_base = os.path.join(self._tmpdir, "models")
                archive_abs = os.path.join(model_base, sym, "archive", "event_v3-20260101T000000Z")
                os.makedirs(archive_abs, exist_ok=True)

                # Write minimal archive artifacts
                stats = {f: {"mean": 1.0, "std": 0.5, "missing_rate": 0.0} for f in features}
                with open(os.path.join(archive_abs, "train_feature_stats.json"), "w") as fp:
                    json.dump(stats, fp)
                with open(os.path.join(archive_abs, "model_meta.json"), "w") as fp:
                    json.dump({"trained_at": "2026-01-01T00:00:00Z"}, fp)

                _promote_to_current(
                    model_dir=os.path.join(model_base, sym),
                    archive_abs=archive_abs,
                )

                current_stats = os.path.join(model_base, sym, "current", "train_feature_stats.json")
                self.assertTrue(
                    os.path.exists(current_stats),
                    f"[{sym}] train_feature_stats.json must exist in models/{sym}/current/ "
                    f"after _promote_to_current; not found at {current_stats}",
                )
                with open(current_stats) as fp:
                    loaded = json.load(fp)
                for feat in features:
                    self.assertIn(feat, loaded, f"[{sym}] feature '{feat}' must be in promoted stats")

    def test_artifact_files_list_is_complete(self) -> None:
        """_ARTIFACT_FILES must include train_feature_stats.json for drift to work."""
        self.assertIn(
            "train_feature_stats.json",
            _ARTIFACT_FILES,
            "_ARTIFACT_FILES must include 'train_feature_stats.json'.",
        )
        self.assertIn(
            "feature_columns_event_v3.json",
            _ARTIFACT_FILES,
            "_ARTIFACT_FILES must include 'feature_columns_event_v3.json'.",
        )
        self.assertIn(
            "model_meta.json",
            _ARTIFACT_FILES,
            "_ARTIFACT_FILES must include 'model_meta.json'.",
        )

    def test_per_symbol_current_dirs_are_isolated(self) -> None:
        """Each symbol must use a distinct models/<SYMBOL>/current/ directory."""
        from symbol_paths import get_symbol_train_stats_path, list_enabled_symbols  # type: ignore
        symbols = list_enabled_symbols()
        paths = [get_symbol_train_stats_path(sym, base_model_dir="/models") for sym in symbols]
        self.assertEqual(
            len(paths),
            len(set(paths)),
            "Every enabled symbol must resolve to a unique train_feature_stats.json path.",
        )


# ---------------------------------------------------------------------------
# Test: report_drift.py --all-symbols covers all enabled symbols
# ---------------------------------------------------------------------------

class TestReportDriftAllSymbols(unittest.TestCase):
    """report_drift.py --all-symbols must iterate all enabled symbols, skip missing."""

    def setUp(self) -> None:
        import symbol_paths  # type: ignore
        symbol_paths._reload_config()
        self._tmpdir = tempfile.mkdtemp(prefix="uw-drift-all-test-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir)
        import symbol_paths  # type: ignore
        symbol_paths._reload_config()

    def test_all_symbols_flag_exists_in_help(self) -> None:
        """report_drift.py must expose --all-symbols in --help output."""
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "report_drift.py"), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"--help returned non-zero: {result.stderr}")
        self.assertIn("--all-symbols", result.stdout, "--all-symbols must appear in --help output")

    def test_all_symbols_skips_missing_artifacts(self) -> None:
        """--all-symbols must skip symbols whose train-stats are missing (no crash)."""
        model_dir = os.path.join(self._tmpdir, "models")
        data_dir = os.path.join(self._tmpdir, "data")
        features = ["feat_a", "feat_b"]

        # Provide artifacts only for ETHUSDT
        current_dir = os.path.join(model_dir, "ETHUSDT", "current")
        _write_minimal_current_dir(current_dir, features)
        log_path = os.path.join(data_dir, "ETHUSDT", "predictions_log.jsonl")
        _write_predictions_log(log_path, "ETHUSDT", features)

        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "report_drift.py"), "--all-symbols", "--dry-run"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "ENABLE_DRIFT_MONITOR": "true",
                # MODELS_BASE_DIR is the correct env var for --all-symbols path resolution.
                # (MODEL_DIR is the single-symbol inference pointer and is intentionally
                # ignored by _resolve_models_base_dir to prevent per-symbol contamination.)
                "MODELS_BASE_DIR": model_dir,
                "DATA_DIR": data_dir,
            },
        )
        # Should exit 0 — missing symbols are warned but do not cause failure
        self.assertEqual(
            result.returncode, 0,
            f"--all-symbols should exit 0 when some symbols have missing artifacts. "
            f"stderr={result.stderr}",
        )
        # Should warn about missing symbols
        combined = result.stdout + result.stderr
        self.assertIn("WARNING", combined, "--all-symbols must warn about missing artifacts")

    def test_all_symbols_runs_report_for_complete_symbol(self) -> None:
        """--all-symbols must run run_drift_report for symbols with complete artifacts."""
        model_dir = os.path.join(self._tmpdir, "models")
        data_dir = os.path.join(self._tmpdir, "data")
        features = ["feat_a", "feat_b"]

        # Provide artifacts for BTCUSDT
        _write_minimal_current_dir(os.path.join(model_dir, "BTCUSDT", "current"), features)
        _write_predictions_log(
            os.path.join(data_dir, "BTCUSDT", "predictions_log.jsonl"), "BTCUSDT", features
        )

        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "report_drift.py"), "--all-symbols", "--dry-run"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "ENABLE_DRIFT_MONITOR": "true",
                # MODELS_BASE_DIR is the correct env var for --all-symbols path resolution.
                "MODELS_BASE_DIR": model_dir,
                "DATA_DIR": data_dir,
            },
        )
        self.assertEqual(result.returncode, 0, f"Should succeed for BTCUSDT. stderr={result.stderr}")
        self.assertIn(
            "BTCUSDT",
            result.stdout + result.stderr,
            "Output should mention BTCUSDT when running drift for it",
        )

    def test_all_symbols_respects_enable_drift_monitor_false(self) -> None:
        """--all-symbols must respect ENABLE_DRIFT_MONITOR=false."""
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "report_drift.py"), "--all-symbols"],
            capture_output=True, text=True,
            env={**os.environ, "ENABLE_DRIFT_MONITOR": "false"},
        )
        self.assertEqual(result.returncode, 0, "--all-symbols must exit 0 when ENABLE_DRIFT_MONITOR=false")
        self.assertIn("skipping", result.stdout, "Should print skipping message")


# ---------------------------------------------------------------------------
# Test: train_all_symbols.sh script integrity
# ---------------------------------------------------------------------------

class TestTrainAllSymbolsScript(unittest.TestCase):
    """train_all_symbols.sh must exist, be executable, and have correct structure."""

    def _script_path(self) -> str:
        return os.path.join(SCRIPTS_DIR, "train_all_symbols.sh")

    def test_script_exists(self) -> None:
        self.assertTrue(
            os.path.exists(self._script_path()),
            "scripts/train_all_symbols.sh must exist.",
        )

    def test_script_is_executable(self) -> None:
        path = self._script_path()
        self.assertTrue(os.path.exists(path), "train_all_symbols.sh must exist")
        mode = os.stat(path).st_mode
        self.assertTrue(
            bool(mode & stat.S_IXUSR),
            "scripts/train_all_symbols.sh must be executable (chmod +x).",
        )

    def test_script_calls_train_symbol_sh(self) -> None:
        """train_all_symbols.sh must delegate to train_symbol.sh."""
        with open(self._script_path()) as f:
            content = f.read()
        self.assertIn(
            "train_symbol.sh",
            content,
            "train_all_symbols.sh must call train_symbol.sh for per-symbol training.",
        )

    def test_script_uses_list_enabled_symbols(self) -> None:
        """train_all_symbols.sh must derive the symbol list from list_enabled_symbols."""
        with open(self._script_path()) as f:
            content = f.read()
        self.assertIn(
            "list_enabled_symbols",
            content,
            "train_all_symbols.sh must use list_enabled_symbols() to get the symbol list.",
        )

    def test_script_dry_run_succeeds(self) -> None:
        """train_all_symbols.sh --dry-run must exit 0 without running actual training."""
        result = subprocess.run(
            ["bash", self._script_path(), "--dry-run"],
            capture_output=True, text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(
            result.returncode, 0,
            f"train_all_symbols.sh --dry-run should exit 0. stderr={result.stderr}",
        )
        # Should mention at least one symbol
        combined = result.stdout + result.stderr
        self.assertTrue(
            any(sym in combined for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
            "dry-run output should mention at least one enabled symbol",
        )


# ---------------------------------------------------------------------------
# Test: drift-monitor.service uses per-symbol mode
# ---------------------------------------------------------------------------

class TestDriftMonitorServicePerSymbolMode(unittest.TestCase):
    """drift-monitor.service must be configured for per-symbol drift coverage."""

    def _read_service(self) -> str:
        path = os.path.join(REPO_ROOT, "systemd", "drift-monitor.service")
        with open(path) as f:
            return f.read()

    def test_service_uses_all_symbols_flag(self) -> None:
        """drift-monitor.service must pass --all-symbols to report_drift.py."""
        content = self._read_service()
        self.assertIn(
            "--all-symbols",
            content,
            "drift-monitor.service must use --all-symbols for per-symbol coverage.",
        )

    def test_service_does_not_use_flat_root_train_stats(self) -> None:
        """drift-monitor.service must not hardcode the flat-root train_feature_stats path."""
        content = self._read_service()
        self.assertNotIn(
            "models/current/train_feature_stats.json",
            content,
            "drift-monitor.service must not reference the legacy flat-root "
            "models/current/train_feature_stats.json path.",
        )

    def test_service_does_not_use_data_models_path(self) -> None:
        """drift-monitor.service must not reference the stale data/models/ path."""
        content = self._read_service()
        self.assertNotIn(
            "data/models/current",
            content,
            "drift-monitor.service must not reference the stale data/models/current/ path.",
        )


if __name__ == "__main__":
    unittest.main()
