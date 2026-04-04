"""Tests for strict YAML-driven configuration enforcement in generate_daily_report.py.

Covers:
- Symbol mode: missing YAML key with no CLI override → error (non-zero return)
- Symbol mode: YAML values used when no CLI override provided
- Symbol mode: CLI overrides YAML values
- Symbol mode: config_source per-field in report params
- Legacy explicit mode: --threshold/--tp/--sl required; built-in defaults for horizon/mt_filter_mode
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
for _d in [SCRIPTS_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    """Return a minimal Namespace suitable for _run_one_symbol tests."""
    defaults = dict(
        date="2026-03-01",
        log_path="data/predictions_log.jsonl",
        data_dir="data",
        report_dir="data/reports",
        interval="1h",
        active_model="event_v3",
        model_version=None,
        threshold=None,
        tp=None,
        sl=None,
        fee=0.0004,
        slippage=0.0,
        horizon_bars=None,
        tie_breaker="SL",
        timeout_exit="close",
        no_mt_filter=False,
        mt_filter_mode=None,
        symbol=None,
        all_symbols=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _minimal_klines(n: int = 30) -> List[Dict[str, Any]]:
    """Generate a minimal list of synthetic 1h kline records in load_klines_1h format."""
    base = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    klines = []
    for i in range(n):
        ts = base + timedelta(hours=i)
        klines.append(
            {
                "timestamp": ts.isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000.0,
            }
        )
    return klines


def _write_klines(path: str, klines: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(klines, f, default=str)


def _write_predictions_log(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Tests: strict symbol-mode parameter enforcement
# ---------------------------------------------------------------------------

class TestStrictYamlSymbolMode(unittest.TestCase):
    """generate_daily_report._run_one_symbol enforces strict YAML/CLI resolution."""

    def setUp(self) -> None:
        import symbol_paths  # type: ignore[import]
        symbol_paths._reload_config()
        self.tmpdir = tempfile.mkdtemp(prefix="uw-daily-report-test-")
        self.symbol = "TSTSYM"
        self.data_dir = os.path.join(self.tmpdir, "data", self.symbol)
        os.makedirs(self.data_dir, exist_ok=True)

        # Write minimal klines
        klines = _minimal_klines(30)
        _write_klines(os.path.join(self.data_dir, "klines_1h.json"), klines)

        # Empty prediction log
        self.log_path = os.path.join(self.data_dir, "predictions_log.jsonl")
        _write_predictions_log(self.log_path, [])

    def tearDown(self) -> None:
        import symbol_paths  # type: ignore[import]
        symbol_paths._reload_config()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_symbol_paths(self, yaml_cfg: Dict[str, Any]):
        """Patch symbol_paths so _load_symbols_config returns yaml_cfg for TSTSYM."""
        import symbol_paths  # type: ignore[import]

        orig_load = symbol_paths._load_symbols_config

        def _patched_load():
            return {self.symbol: yaml_cfg}

        symbol_paths._load_symbols_config = _patched_load
        symbol_paths._config_cache = None
        return orig_load

    def _restore_symbol_paths(self, orig_load) -> None:
        import symbol_paths  # type: ignore[import]
        symbol_paths._load_symbols_config = orig_load
        symbol_paths._config_cache = None

    def _make_symbol_args(self, **overrides) -> argparse.Namespace:
        base = dict(
            date="2026-03-01",
            log_path=self.log_path,
            data_dir=self.data_dir,   # symbol-specific dir (passed explicitly → used directly)
            report_dir=os.path.join(self.tmpdir, "reports"),
            interval="1h",
            active_model="event_v3",
            model_version=None,
            threshold=None,
            tp=None,
            sl=None,
            fee=0.0004,
            slippage=0.0,
            horizon_bars=None,
            tie_breaker="SL",
            timeout_exit="close",
            no_mt_filter=True,
            mt_filter_mode=None,
            symbol=self.symbol,
            all_symbols=False,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    # ------------------------------------------------------------------
    # Missing YAML key tests
    # ------------------------------------------------------------------

    def test_missing_threshold_in_yaml_and_cli_returns_error(self) -> None:
        """If threshold is absent from both YAML and CLI, return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        orig = self._patch_symbol_paths({"tp": 0.02, "sl": 0.01, "horizon": 12, "mt_filter_mode": "off"})
        try:
            args = self._make_symbol_args()  # threshold=None, not in YAML
            rc = _run_one_symbol(args, self.symbol)
            self.assertNotEqual(rc, 0, "Expected non-zero exit when threshold missing")
        finally:
            self._restore_symbol_paths(orig)

    def test_missing_tp_in_yaml_and_cli_returns_error(self) -> None:
        """If tp is absent from both YAML and CLI, return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        orig = self._patch_symbol_paths({"threshold": 0.67, "sl": 0.01, "horizon": 12, "mt_filter_mode": "off"})
        try:
            args = self._make_symbol_args()  # tp=None
            rc = _run_one_symbol(args, self.symbol)
            self.assertNotEqual(rc, 0, "Expected non-zero exit when tp missing")
        finally:
            self._restore_symbol_paths(orig)

    def test_missing_sl_in_yaml_and_cli_returns_error(self) -> None:
        """If sl is absent from both YAML and CLI, return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        orig = self._patch_symbol_paths({"threshold": 0.67, "tp": 0.02, "horizon": 12, "mt_filter_mode": "off"})
        try:
            args = self._make_symbol_args()  # sl=None
            rc = _run_one_symbol(args, self.symbol)
            self.assertNotEqual(rc, 0, "Expected non-zero exit when sl missing")
        finally:
            self._restore_symbol_paths(orig)

    def test_missing_horizon_in_yaml_and_cli_returns_error(self) -> None:
        """If horizon is absent from both YAML and CLI, return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        orig = self._patch_symbol_paths({"threshold": 0.67, "tp": 0.02, "sl": 0.01, "mt_filter_mode": "off"})
        try:
            args = self._make_symbol_args()  # horizon_bars=None
            rc = _run_one_symbol(args, self.symbol)
            self.assertNotEqual(rc, 0, "Expected non-zero exit when horizon missing")
        finally:
            self._restore_symbol_paths(orig)

    def test_missing_mt_filter_mode_in_yaml_and_cli_returns_error(self) -> None:
        """If mt_filter_mode is absent from both YAML and CLI, return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        orig = self._patch_symbol_paths({"threshold": 0.67, "tp": 0.02, "sl": 0.01, "horizon": 12})
        try:
            args = self._make_symbol_args()  # mt_filter_mode=None
            rc = _run_one_symbol(args, self.symbol)
            self.assertNotEqual(rc, 0, "Expected non-zero exit when mt_filter_mode missing")
        finally:
            self._restore_symbol_paths(orig)

    # ------------------------------------------------------------------
    # YAML values used when CLI not provided
    # ------------------------------------------------------------------

    def test_yaml_values_used_when_no_cli_override(self) -> None:
        """All critical params resolved from YAML when CLI not provided; report succeeds."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        yaml_cfg = {
            "threshold": 0.67,
            "tp": 0.023,
            "sl": 0.009,
            "horizon": 12,
            "mt_filter_mode": "off",
        }
        orig = self._patch_symbol_paths(yaml_cfg)
        try:
            args = self._make_symbol_args()
            rc = _run_one_symbol(args, self.symbol)
            self.assertEqual(rc, 0, "Expected zero exit when all params available from YAML")
        finally:
            self._restore_symbol_paths(orig)

    def test_config_source_yaml_in_report(self) -> None:
        """When params come from YAML, report params.config_source shows 'yaml' for each field."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        yaml_cfg = {
            "threshold": 0.67,
            "tp": 0.023,
            "sl": 0.009,
            "horizon": 12,
            "mt_filter_mode": "off",
        }
        orig = self._patch_symbol_paths(yaml_cfg)
        report_dir = os.path.join(self.tmpdir, "reports", self.symbol)
        try:
            args = self._make_symbol_args(
                report_dir=os.path.join(self.tmpdir, "reports"),
            )
            rc = _run_one_symbol(args, self.symbol)
            self.assertEqual(rc, 0)

            # Find generated JSON report
            report_files = []
            for root, _dirs, files in os.walk(self.tmpdir):
                report_files.extend(
                    os.path.join(root, f) for f in files if f.endswith(".json") and "daily_eval" in f
                )
            self.assertTrue(report_files, "Expected at least one report JSON to be written")

            with open(report_files[0]) as f:
                report = json.load(f)

            src = report["params"].get("config_source", {})
            for field in ("threshold", "tp", "sl", "horizon_bars", "mt_filter_mode"):
                self.assertEqual(src.get(field), "yaml", f"Expected config_source[{field}]='yaml', got {src.get(field)!r}")
        finally:
            self._restore_symbol_paths(orig)

    # ------------------------------------------------------------------
    # CLI overrides YAML
    # ------------------------------------------------------------------

    def test_cli_overrides_yaml_values(self) -> None:
        """CLI values take precedence over YAML; config_source shows 'cli' for overridden fields."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        yaml_cfg = {
            "threshold": 0.67,
            "tp": 0.023,
            "sl": 0.009,
            "horizon": 12,
            "mt_filter_mode": "off",
        }
        orig = self._patch_symbol_paths(yaml_cfg)
        report_dir = os.path.join(self.tmpdir, "reports")
        try:
            args = self._make_symbol_args(
                threshold=0.70,   # CLI override
                tp=0.030,         # CLI override
                sl=0.009,         # same as YAML but provided via CLI
                horizon_bars=24,  # CLI override
                mt_filter_mode="symmetric",  # CLI override
            )
            rc = _run_one_symbol(args, self.symbol)
            self.assertEqual(rc, 0)

            report_files = []
            for root, _dirs, files in os.walk(self.tmpdir):
                report_files.extend(
                    os.path.join(root, f) for f in files if f.endswith(".json") and "daily_eval" in f
                )
            self.assertTrue(report_files, "Expected at least one report JSON")

            with open(report_files[0]) as f:
                report = json.load(f)

            params = report["params"]
            self.assertAlmostEqual(params["threshold"], 0.70, places=5)
            self.assertAlmostEqual(params["tp_pct"], 0.030, places=5)
            self.assertAlmostEqual(params["horizon_bars"], 24)

            src = report["params"].get("config_source", {})
            for field in ("threshold", "tp", "sl", "horizon_bars", "mt_filter_mode"):
                self.assertEqual(src.get(field), "cli", f"Expected config_source[{field}]='cli', got {src.get(field)!r}")
        finally:
            self._restore_symbol_paths(orig)

    def test_mixed_sources(self) -> None:
        """Some params from CLI, some from YAML; config_source reflects each field's actual source."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]

        yaml_cfg = {
            "threshold": 0.67,
            "tp": 0.023,
            "sl": 0.009,
            "horizon": 12,
            "mt_filter_mode": "off",
        }
        orig = self._patch_symbol_paths(yaml_cfg)
        try:
            # Override only threshold via CLI; rest from YAML
            args = self._make_symbol_args(threshold=0.72)
            rc = _run_one_symbol(args, self.symbol)
            self.assertEqual(rc, 0)

            report_files = []
            for root, _dirs, files in os.walk(self.tmpdir):
                report_files.extend(
                    os.path.join(root, f) for f in files if f.endswith(".json") and "daily_eval" in f
                )
            self.assertTrue(report_files)

            with open(report_files[0]) as f:
                report = json.load(f)

            src = report["params"].get("config_source", {})
            self.assertEqual(src.get("threshold"), "cli")
            self.assertEqual(src.get("tp"), "yaml")
            self.assertEqual(src.get("sl"), "yaml")
            self.assertEqual(src.get("horizon_bars"), "yaml")
            self.assertEqual(src.get("mt_filter_mode"), "yaml")
        finally:
            self._restore_symbol_paths(orig)


# ---------------------------------------------------------------------------
# Tests: legacy explicit mode (no symbol)
# ---------------------------------------------------------------------------

class TestLegacyExplicitMode(unittest.TestCase):
    """generate_daily_report._run_one_symbol in legacy (no-symbol) mode."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="uw-daily-report-legacy-")
        self.data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        klines = _minimal_klines(30)
        _write_klines(os.path.join(self.data_dir, "klines_1h.json"), klines)
        self.log_path = os.path.join(self.data_dir, "predictions_log.jsonl")
        _write_predictions_log(self.log_path, [])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_legacy_args(self, **overrides) -> argparse.Namespace:
        base = dict(
            date="2026-03-01",
            log_path=self.log_path,
            data_dir=self.data_dir,
            report_dir=os.path.join(self.tmpdir, "reports"),
            interval="1h",
            active_model="event_v3",
            model_version=None,
            threshold=None,
            tp=None,
            sl=None,
            fee=0.0004,
            slippage=0.0,
            horizon_bars=None,
            tie_breaker="SL",
            timeout_exit="close",
            no_mt_filter=True,
            mt_filter_mode=None,
            symbol=None,
            all_symbols=False,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_missing_threshold_in_legacy_mode_returns_error(self) -> None:
        """In legacy mode, omitting --threshold must return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]
        args = self._make_legacy_args(tp=0.02, sl=0.01)
        rc = _run_one_symbol(args, None)
        self.assertNotEqual(rc, 0)

    def test_missing_tp_in_legacy_mode_returns_error(self) -> None:
        """In legacy mode, omitting --tp must return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]
        args = self._make_legacy_args(threshold=0.65, sl=0.01)
        rc = _run_one_symbol(args, None)
        self.assertNotEqual(rc, 0)

    def test_missing_sl_in_legacy_mode_returns_error(self) -> None:
        """In legacy mode, omitting --sl must return non-zero."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]
        args = self._make_legacy_args(threshold=0.65, tp=0.02)
        rc = _run_one_symbol(args, None)
        self.assertNotEqual(rc, 0)

    def test_legacy_mode_succeeds_with_required_params(self) -> None:
        """In legacy mode, supplying threshold/tp/sl succeeds (horizon/mt_filter_mode use defaults)."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]
        args = self._make_legacy_args(threshold=0.65, tp=0.0175, sl=0.009)
        rc = _run_one_symbol(args, None)
        self.assertEqual(rc, 0)

    def test_legacy_mode_default_horizon_and_mt_filter_mode(self) -> None:
        """In legacy mode, horizon defaults to 6 and mt_filter_mode defaults to 'layered'."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]
        args = self._make_legacy_args(threshold=0.65, tp=0.0175, sl=0.009)
        rc = _run_one_symbol(args, None)
        self.assertEqual(rc, 0)

        report_files = []
        for root, _dirs, files in os.walk(self.tmpdir):
            report_files.extend(
                os.path.join(root, f) for f in files if f.endswith(".json") and "daily_eval" in f
            )
        self.assertTrue(report_files)
        with open(report_files[0]) as f:
            report = json.load(f)
        self.assertEqual(report["params"]["horizon_bars"], 6)
        # mt_filter=False (no_mt_filter=True) so mt_filter_mode is stored as 'off'
        self.assertEqual(report["params"]["mt_filter_mode"], "off")

    def test_legacy_mode_no_config_source_in_report(self) -> None:
        """In legacy mode, report params should NOT have a config_source entry."""
        from generate_daily_report import _run_one_symbol  # type: ignore[import]
        args = self._make_legacy_args(threshold=0.65, tp=0.0175, sl=0.009)
        rc = _run_one_symbol(args, None)
        self.assertEqual(rc, 0)

        report_files = []
        for root, _dirs, files in os.walk(self.tmpdir):
            report_files.extend(
                os.path.join(root, f) for f in files if f.endswith(".json") and "daily_eval" in f
            )
        self.assertTrue(report_files)
        with open(report_files[0]) as f:
            report = json.load(f)
        self.assertNotIn("config_source", report["params"])


if __name__ == "__main__":
    unittest.main()
