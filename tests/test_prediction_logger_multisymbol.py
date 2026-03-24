"""Tests for per-symbol prediction log routing in prediction_logger.py."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from threading import Lock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
for _d in [ML_SERVICE_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _reload_logger():
    """Force re-import of prediction_logger to pick up env changes."""
    import prediction_logger  # type: ignore[import]
    importlib.reload(prediction_logger)
    return prediction_logger


def _make_ts() -> datetime:
    return datetime(2026, 3, 24, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tests: _get_per_symbol_log_path
# ---------------------------------------------------------------------------

class TestGetPerSymbolLogPath(unittest.TestCase):
    """_get_per_symbol_log_path must return DATA_DIR/<SYMBOL>/predictions_log.jsonl."""

    def test_default_base(self):
        import prediction_logger  # type: ignore[import]
        orig = os.environ.pop("DATA_DIR", None)
        try:
            path = prediction_logger._get_per_symbol_log_path("BTCUSDT")
            self.assertTrue(path.endswith(os.path.join("BTCUSDT", "predictions_log.jsonl")))
        finally:
            if orig is not None:
                os.environ["DATA_DIR"] = orig

    def test_env_data_dir(self):
        import prediction_logger  # type: ignore[import]
        orig = os.environ.get("DATA_DIR")
        try:
            os.environ["DATA_DIR"] = "/tmp/testdata"
            path = prediction_logger._get_per_symbol_log_path("ETHUSDT")
            self.assertEqual(path, "/tmp/testdata/ETHUSDT/predictions_log.jsonl")
        finally:
            if orig is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = orig

    def test_symbols_differ(self):
        import prediction_logger  # type: ignore[import]
        orig = os.environ.get("DATA_DIR")
        try:
            os.environ["DATA_DIR"] = "/tmp/testdata"
            btc = prediction_logger._get_per_symbol_log_path("BTCUSDT")
            eth = prediction_logger._get_per_symbol_log_path("ETHUSDT")
            self.assertNotEqual(btc, eth)
            self.assertIn("BTCUSDT", btc)
            self.assertIn("ETHUSDT", eth)
        finally:
            if orig is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = orig


# ---------------------------------------------------------------------------
# Tests: log_prediction writes to per-symbol path
# ---------------------------------------------------------------------------

class TestLogPredictionPerSymbol(unittest.TestCase):
    """log_prediction must write to data/<SYMBOL>/predictions_log.jsonl when symbol is given."""

    def _call_log(self, logger, tmpdir: str, symbol, **kwargs):
        defaults = dict(
            ts=_make_ts(),
            symbol=symbol,
            interval="1h",
            proba_long=0.7,
            proba_short=0.1,
            proba_flat=0.2,
            signal="LONG",
            confidence=0.7,
            model_version="event_v3:lgbm:2026-01-01:abc123",
            active_model="event_v3",
        )
        defaults.update(kwargs)
        logger.log_prediction(**defaults)

    def test_writes_to_per_symbol_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = os.environ.get("DATA_DIR")
            try:
                os.environ["DATA_DIR"] = tmpdir
                logger = _reload_logger()
                # Clear the dedupe cache so we get a fresh write
                logger._dedupe_cache.clear()

                self._call_log(logger, tmpdir, "BTCUSDT")

                expected = os.path.join(tmpdir, "BTCUSDT", "predictions_log.jsonl")
                self.assertTrue(os.path.exists(expected), f"Expected log at {expected}")
                with open(expected) as f:
                    lines = [l for l in f if l.strip()]
                self.assertEqual(len(lines), 1)
                rec = json.loads(lines[0])
                self.assertEqual(rec["symbol"], "BTCUSDT")
                self.assertEqual(rec["signal"], "LONG")
            finally:
                if orig_data_dir is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = orig_data_dir

    def test_different_symbols_write_separate_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = os.environ.get("DATA_DIR")
            try:
                os.environ["DATA_DIR"] = tmpdir
                logger = _reload_logger()
                logger._dedupe_cache.clear()

                self._call_log(logger, tmpdir, "BTCUSDT")
                self._call_log(logger, tmpdir, "ETHUSDT",
                               proba_long=0.6, signal="FLAT")

                btc_log = os.path.join(tmpdir, "BTCUSDT", "predictions_log.jsonl")
                eth_log = os.path.join(tmpdir, "ETHUSDT", "predictions_log.jsonl")
                self.assertTrue(os.path.exists(btc_log))
                self.assertTrue(os.path.exists(eth_log))

                # Each file should only contain its own symbol
                with open(btc_log) as f:
                    recs_btc = [json.loads(l) for l in f if l.strip()]
                with open(eth_log) as f:
                    recs_eth = [json.loads(l) for l in f if l.strip()]

                self.assertTrue(all(r["symbol"] == "BTCUSDT" for r in recs_btc))
                self.assertTrue(all(r["symbol"] == "ETHUSDT" for r in recs_eth))
            finally:
                if orig_data_dir is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = orig_data_dir

    def test_no_symbol_writes_root_log(self):
        """When symbol is None, log must fall back to the root-level _LOG_PATH."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = os.environ.get("DATA_DIR")
            orig_log_path = os.environ.get("PREDICTIONS_LOG_PATH")
            root_log = os.path.join(tmpdir, "predictions_log.jsonl")
            try:
                os.environ["DATA_DIR"] = tmpdir
                os.environ["PREDICTIONS_LOG_PATH"] = root_log
                logger = _reload_logger()
                logger._dedupe_cache.clear()

                self._call_log(logger, tmpdir, symbol=None)

                self.assertTrue(os.path.exists(root_log), "Root log must be created when symbol=None")
                with open(root_log) as f:
                    recs = [json.loads(l) for l in f if l.strip()]
                self.assertEqual(len(recs), 1)
                self.assertIsNone(recs[0]["symbol"])
            finally:
                if orig_data_dir is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = orig_data_dir
                if orig_log_path is None:
                    os.environ.pop("PREDICTIONS_LOG_PATH", None)
                else:
                    os.environ["PREDICTIONS_LOG_PATH"] = orig_log_path

    def test_explicit_log_path_overrides_symbol(self):
        """Explicit log_path argument must take precedence over per-symbol routing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = os.environ.get("DATA_DIR")
            try:
                os.environ["DATA_DIR"] = tmpdir
                logger = _reload_logger()
                logger._dedupe_cache.clear()

                explicit_path = os.path.join(tmpdir, "custom_log.jsonl")
                self._call_log(logger, tmpdir, "BTCUSDT", log_path=explicit_path)

                self.assertTrue(os.path.exists(explicit_path))
                # Per-symbol path should NOT have been created
                sym_path = os.path.join(tmpdir, "BTCUSDT", "predictions_log.jsonl")
                self.assertFalse(os.path.exists(sym_path))
            finally:
                if orig_data_dir is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = orig_data_dir


# ---------------------------------------------------------------------------
# Tests: PREDICTIONS_LOG_ALSO_ROOT dual-write
# ---------------------------------------------------------------------------

class TestAlsoRootDualWrite(unittest.TestCase):
    """When PREDICTIONS_LOG_ALSO_ROOT=1, per-symbol writes also appear in root log."""

    def _call_log(self, logger, symbol, **kwargs):
        defaults = dict(
            ts=_make_ts(),
            symbol=symbol,
            interval="1h",
            proba_long=0.7,
            proba_short=0.1,
            proba_flat=0.2,
            signal="LONG",
            confidence=0.7,
            model_version="event_v3:lgbm:2026-01-01:abc123",
            active_model="event_v3",
        )
        defaults.update(kwargs)
        logger.log_prediction(**defaults)

    def test_also_root_dual_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = os.environ.get("DATA_DIR")
            orig_log_path = os.environ.get("PREDICTIONS_LOG_PATH")
            orig_also_root = os.environ.get("PREDICTIONS_LOG_ALSO_ROOT")
            root_log = os.path.join(tmpdir, "predictions_log.jsonl")
            try:
                os.environ["DATA_DIR"] = tmpdir
                os.environ["PREDICTIONS_LOG_PATH"] = root_log
                os.environ["PREDICTIONS_LOG_ALSO_ROOT"] = "1"
                logger = _reload_logger()
                logger._dedupe_cache.clear()

                self._call_log(logger, "BTCUSDT")

                # Per-symbol file must exist
                sym_log = os.path.join(tmpdir, "BTCUSDT", "predictions_log.jsonl")
                self.assertTrue(os.path.exists(sym_log))

                # Root log must ALSO exist
                self.assertTrue(os.path.exists(root_log), "Root log must be written with PREDICTIONS_LOG_ALSO_ROOT=1")

                with open(root_log) as f:
                    root_recs = [json.loads(l) for l in f if l.strip()]
                self.assertEqual(len(root_recs), 1)
                self.assertEqual(root_recs[0]["symbol"], "BTCUSDT")
            finally:
                if orig_data_dir is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = orig_data_dir
                if orig_log_path is None:
                    os.environ.pop("PREDICTIONS_LOG_PATH", None)
                else:
                    os.environ["PREDICTIONS_LOG_PATH"] = orig_log_path
                if orig_also_root is None:
                    os.environ.pop("PREDICTIONS_LOG_ALSO_ROOT", None)
                else:
                    os.environ["PREDICTIONS_LOG_ALSO_ROOT"] = orig_also_root

    def test_no_also_root_by_default(self):
        """With default settings, per-symbol write must NOT touch the root log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = os.environ.get("DATA_DIR")
            orig_log_path = os.environ.get("PREDICTIONS_LOG_PATH")
            orig_also_root = os.environ.get("PREDICTIONS_LOG_ALSO_ROOT")
            root_log = os.path.join(tmpdir, "predictions_log.jsonl")
            try:
                os.environ["DATA_DIR"] = tmpdir
                os.environ["PREDICTIONS_LOG_PATH"] = root_log
                os.environ.pop("PREDICTIONS_LOG_ALSO_ROOT", None)  # ensure unset
                logger = _reload_logger()
                logger._dedupe_cache.clear()

                self._call_log(logger, "BTCUSDT")

                # Per-symbol file must exist
                sym_log = os.path.join(tmpdir, "BTCUSDT", "predictions_log.jsonl")
                self.assertTrue(os.path.exists(sym_log))

                # Root log must NOT exist
                self.assertFalse(os.path.exists(root_log), "Root log must NOT be written by default")
            finally:
                if orig_data_dir is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = orig_data_dir
                if orig_log_path is None:
                    os.environ.pop("PREDICTIONS_LOG_PATH", None)
                else:
                    os.environ["PREDICTIONS_LOG_PATH"] = orig_log_path
                if orig_also_root is None:
                    os.environ.pop("PREDICTIONS_LOG_ALSO_ROOT", None)
                else:
                    os.environ["PREDICTIONS_LOG_ALSO_ROOT"] = orig_also_root


# ---------------------------------------------------------------------------
# Tests: per-symbol model directory resolution helpers (app.py logic)
# ---------------------------------------------------------------------------

def _try_import_app():
    """Import app.py, skipping if heavy ML deps are not available."""
    try:
        import app as ml_app  # type: ignore[import]
        return ml_app
    except ImportError:
        return None


class TestResolveModelDir(unittest.TestCase):
    """_resolve_model_dir must prefer per-symbol dirs and fall back to MODEL_DIR."""

    def setUp(self):
        self.ml_app = _try_import_app()
        if self.ml_app is None:
            self.skipTest("app.py heavy deps (numpy/fastapi) not available in this environment")

    def test_resolve_uses_per_symbol_when_exists(self):
        ml_app = self.ml_app
        with tempfile.TemporaryDirectory() as tmpdir:
            sym_dir = os.path.join(tmpdir, "BTCUSDT", "current")
            os.makedirs(sym_dir)

            old_base = ml_app.MODELS_BASE_DIR
            old_model = ml_app.MODEL_DIR
            ml_app.MODELS_BASE_DIR = tmpdir
            ml_app.MODEL_DIR = os.path.join(tmpdir, "current")
            try:
                resolved = ml_app._resolve_model_dir("BTCUSDT")
                self.assertEqual(resolved, sym_dir)
            finally:
                ml_app.MODELS_BASE_DIR = old_base
                ml_app.MODEL_DIR = old_model

    def test_resolve_falls_back_when_no_per_symbol_dir(self):
        ml_app = self.ml_app
        with tempfile.TemporaryDirectory() as tmpdir:
            default_model_dir = os.path.join(tmpdir, "current")
            os.makedirs(default_model_dir)

            old_base = ml_app.MODELS_BASE_DIR
            old_model = ml_app.MODEL_DIR
            ml_app.MODELS_BASE_DIR = tmpdir
            ml_app.MODEL_DIR = default_model_dir
            try:
                resolved = ml_app._resolve_model_dir("BTCUSDT")
                self.assertEqual(resolved, default_model_dir)
            finally:
                ml_app.MODELS_BASE_DIR = old_base
                ml_app.MODEL_DIR = old_model

    def test_resolve_no_symbol_returns_model_dir(self):
        ml_app = self.ml_app
        old_model = ml_app.MODEL_DIR
        ml_app.MODEL_DIR = "/some/path"
        try:
            resolved = ml_app._resolve_model_dir(None)
            self.assertEqual(resolved, "/some/path")
        finally:
            ml_app.MODEL_DIR = old_model


class TestResolveDataDir(unittest.TestCase):
    """_resolve_data_dir must prefer per-symbol dirs and fall back to DATA_DIR."""

    def setUp(self):
        self.ml_app = _try_import_app()
        if self.ml_app is None:
            self.skipTest("app.py heavy deps (numpy/fastapi) not available in this environment")

    def test_resolve_uses_per_symbol_when_exists(self):
        ml_app = self.ml_app
        with tempfile.TemporaryDirectory() as tmpdir:
            sym_dir = os.path.join(tmpdir, "BTCUSDT")
            os.makedirs(sym_dir)

            old_data = ml_app.DATA_DIR
            ml_app.DATA_DIR = tmpdir
            try:
                resolved = ml_app._resolve_data_dir("BTCUSDT")
                self.assertEqual(resolved, sym_dir)
            finally:
                ml_app.DATA_DIR = old_data

    def test_resolve_falls_back_when_no_per_symbol_dir(self):
        ml_app = self.ml_app
        with tempfile.TemporaryDirectory() as tmpdir:
            old_data = ml_app.DATA_DIR
            ml_app.DATA_DIR = tmpdir
            try:
                resolved = ml_app._resolve_data_dir("BTCUSDT")
                self.assertEqual(resolved, tmpdir)
            finally:
                ml_app.DATA_DIR = old_data

    def test_resolve_no_symbol_returns_data_dir(self):
        ml_app = self.ml_app
        old_data = ml_app.DATA_DIR
        ml_app.DATA_DIR = "/some/data"
        try:
            resolved = ml_app._resolve_data_dir(None)
            self.assertEqual(resolved, "/some/data")
        finally:
            ml_app.DATA_DIR = old_data


if __name__ == "__main__":
    unittest.main()
