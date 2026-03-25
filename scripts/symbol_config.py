"""Shared symbol configuration and path helpers.

This module provides a lightweight, stable public API for loading per-symbol
configuration from ``configs/symbols.yaml`` and resolving per-symbol directory
paths.  It is the single source of truth for both the ``scripts/`` layer and
anything that imports from the scripts directory.

Under the hood it delegates to ``scripts/symbol_paths.py``, which holds all
implementation details.  Prefer importing from **this** module rather than
``symbol_paths`` directly so that callers depend on the stable public names
defined below.

Public API
----------
``list_enabled_symbols()``
    Return all enabled symbols in rollout order.

``get_symbol_config(symbol)``
    Return merged config dict for *symbol* (defaults + YAML overrides).

``data_dir(symbol)``
    Per-symbol klines / log data directory (``data/<SYMBOL>``).

``model_dir(symbol)``
    Per-symbol model directory (``models/<SYMBOL>``).

``reports_dir(symbol)``
    Per-symbol reports output directory (``data/<SYMBOL>/reports``).

``predictions_log_path(symbol)``
    Full path to ``data/<SYMBOL>/predictions_log.jsonl``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Delegate everything to the canonical implementation module.
from symbol_paths import (
    get_symbol_config as get_symbol_config,
    list_enabled_symbols as list_enabled_symbols,
    get_symbol_data_dir as _get_symbol_data_dir,
    get_symbol_model_dir as _get_symbol_model_dir,
    get_symbol_reports_dir as _get_symbol_reports_dir,
    get_symbol_log_path as _get_symbol_log_path,
    ALL_SYMBOLS as ALL_SYMBOLS,
    REPO_ROOT as REPO_ROOT,
)


# ---------------------------------------------------------------------------
# Convenience wrappers with shorter names
# ---------------------------------------------------------------------------

def data_dir(symbol: str, base_data_dir: Optional[str] = None) -> str:
    """Return per-symbol kline data directory: ``<base_data_dir>/<SYMBOL>``.

    Delegates to :func:`symbol_paths.get_symbol_data_dir`.
    """
    return _get_symbol_data_dir(symbol, base_data_dir=base_data_dir)


def model_dir(symbol: str, base_model_dir: Optional[str] = None) -> str:
    """Return per-symbol model directory: ``<base_model_dir>/<SYMBOL>``.

    Delegates to :func:`symbol_paths.get_symbol_model_dir`.
    """
    return _get_symbol_model_dir(symbol, base_model_dir=base_model_dir)


def reports_dir(symbol: str, base_data_dir: Optional[str] = None) -> str:
    """Return per-symbol reports directory: ``data/<SYMBOL>/reports``.

    Delegates to :func:`symbol_paths.get_symbol_reports_dir`.
    """
    return _get_symbol_reports_dir(symbol, base_data_dir=base_data_dir)


def predictions_log_path(symbol: str, base_data_dir: Optional[str] = None) -> str:
    """Return full path to ``data/<SYMBOL>/predictions_log.jsonl``.

    Delegates to :func:`symbol_paths.get_symbol_log_path`.
    """
    return _get_symbol_log_path(symbol, base_data_dir=base_data_dir)
