"""Per-symbol path resolution and configuration helpers.

This module provides a single source of truth for per-symbol directory layout
and configuration loading.  All scripts and services should import from here
rather than hard-coding paths.

Directory layout (relative to repo root):
    data/<SYMBOL>/klines_1h.json
    data/<SYMBOL>/klines_4h.json
    data/<SYMBOL>/klines_1d.json
    data/<SYMBOL>/predictions_log.jsonl
    data/<SYMBOL>/reports/

    models/<SYMBOL>/current/           <- active model artifacts
    models/<SYMBOL>/archive/           <- versioned snapshots
    models/<SYMBOL>/registry.json      <- model registry

Usage example:
    from symbol_paths import get_symbol_config, get_symbol_data_dir, get_symbol_model_dir

    cfg = get_symbol_config("BTCUSDT")
    data_dir  = get_symbol_data_dir("BTCUSDT")
    model_dir = get_symbol_model_dir("BTCUSDT")

    # Derive specific artifact paths:
    train_stats = get_symbol_train_stats_path("BTCUSDT")
    log_path    = get_symbol_log_path("BTCUSDT")
    reports_dir = get_symbol_reports_dir("BTCUSDT")
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Repository root and config path
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_PATH = os.path.join(REPO_ROOT, "configs", "symbols.yaml")

# ---------------------------------------------------------------------------
# Default per-symbol configuration values
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "interval": "1h",
    "threshold": 0.65,
    "tp": 0.0175,
    "sl": 0.009,
    "horizon": 12,
    "calibration": "isotonic",
}

# Phased rollout definition (order matters: Phase 1 first)
_PHASE1_SYMBOLS: List[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
_PHASE2_SYMBOLS: List[str] = ["XRPUSDT", "DOGEUSDT", "ADAUSDT"]
ALL_SYMBOLS: List[str] = _PHASE1_SYMBOLS + _PHASE2_SYMBOLS

# Internal config cache (populated on first call to _load_symbols_config)
_config_cache: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_symbols_config() -> Dict[str, Any]:
    """Load configs/symbols.yaml.  Returns the ``symbols`` mapping.

    Requires PyYAML (``pip install pyyaml``).  Raises ``ImportError`` with a
    clear message if PyYAML is not installed.  Returns an empty dict if the
    config file does not exist yet (graceful degradation to defaults).
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not os.path.exists(_CONFIG_PATH):
        _config_cache = {}
        return _config_cache

    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "PyYAML is required to load configs/symbols.yaml. "
            "Install it with: pip install pyyaml"
        ) from exc

    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    _config_cache = (data or {}).get("symbols", {})
    return _config_cache


def _reload_config() -> None:
    """Force reload of the symbols config (useful in tests)."""
    global _config_cache
    _config_cache = None


# ---------------------------------------------------------------------------
# Config accessors
# ---------------------------------------------------------------------------

def get_symbol_config(symbol: str) -> Dict[str, Any]:
    """Return merged configuration for *symbol* (symbol-level values override defaults).

    Returns default values for any symbol not present in configs/symbols.yaml,
    so callers never receive ``None``.
    """
    sym_cfg = _load_symbols_config().get(symbol, {})
    return {**_DEFAULTS, **sym_cfg}


def list_enabled_symbols(phase: Optional[int] = None) -> List[str]:
    """Return enabled symbols, optionally filtered to a rollout phase.

    Args:
        phase: ``1`` → BTCUSDT/ETHUSDT/SOLUSDT/BNBUSDT only;
               ``2`` → XRPUSDT/DOGEUSDT/ADAUSDT only;
               ``None`` (default) → all symbols.

    Returns:
        Ordered list of symbol strings whose ``enabled`` flag is ``True``.
    """
    if phase == 1:
        candidates = _PHASE1_SYMBOLS
    elif phase == 2:
        candidates = _PHASE2_SYMBOLS
    else:
        candidates = ALL_SYMBOLS

    cfg = _load_symbols_config()
    return [s for s in candidates if cfg.get(s, {}).get("enabled", _DEFAULTS["enabled"])]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_symbol_data_dir(
    symbol: str,
    base_data_dir: Optional[str] = None,
) -> str:
    """Return the per-symbol data directory: ``<base_data_dir>/<SYMBOL>/``.

    Args:
        symbol: Trading pair, e.g. ``"BTCUSDT"``.
        base_data_dir: Override the base data directory.  Defaults to the
            ``DATA_DIR`` environment variable, falling back to
            ``<repo_root>/data``.
    """
    if base_data_dir is None:
        base_data_dir = os.environ.get("DATA_DIR", os.path.join(REPO_ROOT, "data"))
    return os.path.join(base_data_dir, symbol)


def get_symbol_model_dir(
    symbol: str,
    base_model_dir: Optional[str] = None,
) -> str:
    """Return the per-symbol model directory: ``<base_model_dir>/<SYMBOL>/``.

    Args:
        symbol: Trading pair, e.g. ``"BTCUSDT"``.
        base_model_dir: Override the base model directory.  Defaults to the
            ``MODEL_DIR`` environment variable, falling back to
            ``<repo_root>/models``.
    """
    if base_model_dir is None:
        base_model_dir = os.environ.get("MODEL_DIR", os.path.join(REPO_ROOT, "models"))
    return os.path.join(base_model_dir, symbol)


def get_symbol_train_stats_path(
    symbol: str,
    base_model_dir: Optional[str] = None,
) -> str:
    """Return path to ``train_feature_stats.json`` for *symbol*.

    Resolves to ``<model_dir>/<SYMBOL>/current/train_feature_stats.json``.
    """
    model_dir = get_symbol_model_dir(symbol, base_model_dir)
    return os.path.join(model_dir, "current", "train_feature_stats.json")


def get_symbol_log_path(
    symbol: str,
    base_data_dir: Optional[str] = None,
) -> str:
    """Return path to ``predictions_log.jsonl`` for *symbol*.

    Resolves to ``<data_dir>/<SYMBOL>/predictions_log.jsonl``.
    """
    data_dir = get_symbol_data_dir(symbol, base_data_dir)
    return os.path.join(data_dir, "predictions_log.jsonl")


def get_symbol_reports_dir(
    symbol: str,
    base_data_dir: Optional[str] = None,
) -> str:
    """Return path to the reports directory for *symbol*.

    Resolves to ``<data_dir>/<SYMBOL>/reports/``.
    """
    data_dir = get_symbol_data_dir(symbol, base_data_dir)
    return os.path.join(data_dir, "reports")
