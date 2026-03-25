"""Unit tests for per-symbol event_v3 threshold resolution.

Tests validate:
- Given a temporary YAML with ETHUSDT threshold=0.57, the helper uses 0.57.
- For an unknown symbol the helper falls back to previous env/model/default.
- Missing threshold key in YAML entry also falls back correctly.
- symbols_config.get_symbol_threshold returns None for unknown symbols.
- symbols_config.resolve_p_enter precedence order (YAML > ENV > model-meta > default).

All tests are hermetic — no running uvicorn instance is required.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
for _d in [ML_SERVICE_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import symbols_config  # type: ignore[import]
from symbols_config import get_symbol_threshold, resolve_p_enter  # type: ignore[import]


def _write_yaml(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestGetSymbolThreshold(unittest.TestCase):
    """symbols_config.get_symbol_threshold behaviour."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="uw-thresh-test-")
        self._orig_config_path = symbols_config._CONFIG_PATH
        symbols_config._reset_cache()

    def tearDown(self) -> None:
        symbols_config._CONFIG_PATH = self._orig_config_path
        symbols_config._reset_cache()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _set_yaml(self, content: str) -> None:
        cfg_path = os.path.join(self._tmpdir, "configs", "symbols.yaml")
        _write_yaml(cfg_path, content)
        symbols_config._CONFIG_PATH = cfg_path
        symbols_config._reset_cache()

    def test_known_symbol_returns_configured_threshold(self) -> None:
        self._set_yaml(
            "symbols:\n"
            "  ETHUSDT:\n"
            "    threshold: 0.57\n"
        )
        result = get_symbol_threshold("ETHUSDT")
        self.assertAlmostEqual(result, 0.57)

    def test_unknown_symbol_returns_none(self) -> None:
        self._set_yaml(
            "symbols:\n"
            "  BTCUSDT:\n"
            "    threshold: 0.65\n"
        )
        result = get_symbol_threshold("XYZUSDT")
        self.assertIsNone(result)

    def test_symbol_missing_threshold_key_returns_none(self) -> None:
        self._set_yaml(
            "symbols:\n"
            "  SOLUSDT:\n"
            "    enabled: true\n"
        )
        result = get_symbol_threshold("SOLUSDT")
        self.assertIsNone(result)

    def test_none_symbol_returns_none(self) -> None:
        self._set_yaml("symbols:\n  BTCUSDT:\n    threshold: 0.65\n")
        result = get_symbol_threshold(None)
        self.assertIsNone(result)

    def test_empty_symbol_returns_none(self) -> None:
        self._set_yaml("symbols:\n  BTCUSDT:\n    threshold: 0.65\n")
        result = get_symbol_threshold("")
        self.assertIsNone(result)

    def test_missing_yaml_file_returns_none(self) -> None:
        symbols_config._CONFIG_PATH = os.path.join(self._tmpdir, "nonexistent.yaml")
        symbols_config._reset_cache()
        result = get_symbol_threshold("BTCUSDT")
        self.assertIsNone(result)

    def test_cache_is_refreshed_when_mtime_changes(self) -> None:
        cfg_path = os.path.join(self._tmpdir, "configs", "symbols.yaml")
        _write_yaml(cfg_path, "symbols:\n  ETHUSDT:\n    threshold: 0.60\n")
        symbols_config._CONFIG_PATH = cfg_path
        symbols_config._reset_cache()

        self.assertAlmostEqual(get_symbol_threshold("ETHUSDT"), 0.60)

        # Overwrite file and force mtime forward
        import time
        time.sleep(0.05)
        _write_yaml(cfg_path, "symbols:\n  ETHUSDT:\n    threshold: 0.70\n")
        # Touch the file to ensure mtime is updated
        os.utime(cfg_path, None)

        # Cache should be refreshed automatically
        self.assertAlmostEqual(get_symbol_threshold("ETHUSDT"), 0.70)


class TestResolvePEnter(unittest.TestCase):
    """symbols_config.resolve_p_enter precedence and source labelling."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="uw-thresh-resolve-test-")
        self._orig_config_path = symbols_config._CONFIG_PATH
        symbols_config._reset_cache()

    def tearDown(self) -> None:
        symbols_config._CONFIG_PATH = self._orig_config_path
        symbols_config._reset_cache()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _set_yaml(self, content: str) -> None:
        cfg_path = os.path.join(self._tmpdir, "configs", "symbols.yaml")
        _write_yaml(cfg_path, content)
        symbols_config._CONFIG_PATH = cfg_path
        symbols_config._reset_cache()

    def test_yaml_threshold_takes_precedence(self) -> None:
        self._set_yaml("symbols:\n  ETHUSDT:\n    threshold: 0.57\n")
        p_enter, source = resolve_p_enter("ETHUSDT", {})
        self.assertAlmostEqual(p_enter, 0.57)
        self.assertEqual(source, "configs/symbols.yaml")

    def test_env_fallback_when_no_yaml_entry(self) -> None:
        self._set_yaml("symbols:\n  BTCUSDT:\n    threshold: 0.65\n")
        with unittest.mock.patch.dict(
            os.environ, {"EVENT_V3_P_ENTER": "0.72"}, clear=False
        ):
            p_enter, source = resolve_p_enter("XYZUSDT", {})
        self.assertAlmostEqual(p_enter, 0.72)
        self.assertEqual(source, "env/EVENT_V3_P_ENTER")

    def test_model_meta_fallback(self) -> None:
        self._set_yaml("symbols:\n  BTCUSDT:\n    threshold: 0.65\n")
        env_backup = os.environ.pop("EVENT_V3_P_ENTER", None)
        try:
            p_enter, source = resolve_p_enter("XYZUSDT", {"p_enter": 0.68})
        finally:
            if env_backup is not None:
                os.environ["EVENT_V3_P_ENTER"] = env_backup
        self.assertAlmostEqual(p_enter, 0.68)
        self.assertEqual(source, "model/metadata")

    def test_default_fallback(self) -> None:
        self._set_yaml("symbols:\n  BTCUSDT:\n    threshold: 0.65\n")
        env_backup = os.environ.pop("EVENT_V3_P_ENTER", None)
        try:
            p_enter, source = resolve_p_enter("XYZUSDT", {}, default=0.65)
        finally:
            if env_backup is not None:
                os.environ["EVENT_V3_P_ENTER"] = env_backup
        self.assertAlmostEqual(p_enter, 0.65)
        self.assertEqual(source, "default")

    def test_yaml_threshold_overrides_env(self) -> None:
        self._set_yaml("symbols:\n  ETHUSDT:\n    threshold: 0.57\n")
        with unittest.mock.patch.dict(
            os.environ, {"EVENT_V3_P_ENTER": "0.80"}, clear=False
        ):
            p_enter, source = resolve_p_enter("ETHUSDT", {})
        self.assertAlmostEqual(p_enter, 0.57)
        self.assertEqual(source, "configs/symbols.yaml")

    def test_none_symbol_falls_through_to_default(self) -> None:
        self._set_yaml("symbols:\n  ETHUSDT:\n    threshold: 0.57\n")
        env_backup = os.environ.pop("EVENT_V3_P_ENTER", None)
        try:
            p_enter, source = resolve_p_enter(None, {}, default=0.65)
        finally:
            if env_backup is not None:
                os.environ["EVENT_V3_P_ENTER"] = env_backup
        self.assertAlmostEqual(p_enter, 0.65)
        self.assertEqual(source, "default")


if __name__ == "__main__":
    unittest.main()
