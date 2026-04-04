"""Shared symbol configuration and path helpers.

Stable public API for loading per-symbol configuration from configs/symbols.yaml
and resolving per-symbol directory paths.

This module re-exports from scripts.symbol_paths (canonical implementation).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# IMPORTANT: use package import so it works when running from repo root.
from scripts.symbol_paths import (  # type: ignore
    get_symbol_config as get_symbol_config,
    list_enabled_symbols as list_enabled_symbols,
    get_symbol_data_dir as _get_symbol_data_dir,
    get_symbol_model_dir as _get_symbol_model_dir,
    get_symbol_reports_dir as _get_symbol_reports_dir,
    get_symbol_log_path as _get_symbol_log_path,
    ALL_SYMBOLS as ALL_SYMBOLS,
    REPO_ROOT as REPO_ROOT,
)


def data_dir(symbol: str, base_data_dir: Optional[str] = None) -> str:
    """Return per-symbol kline data directory: ``<base_data_dir>/<SYMBOL>``."""
    return _get_symbol_data_dir(symbol, base_data_dir=base_data_dir)


def model_dir(symbol: str, base_model_dir: Optional[str] = None) -> str:
    """Return per-symbol model directory: ``<base_model_dir>/<SYMBOL>``."""
    return _get_symbol_model_dir(symbol, base_model_dir=base_model_dir)


def reports_dir(symbol: str, base_data_dir: Optional[str] = None) -> str:
    """Return per-symbol reports directory: ``data/<SYMBOL>/reports``."""
    return _get_symbol_reports_dir(symbol, base_data_dir=base_data_dir)


def predictions_log_path(symbol: str, base_data_dir: Optional[str] = None) -> str:
    """Return full path to ``data/<SYMBOL>/predictions_log.jsonl``."""
    return _get_symbol_log_path(symbol, base_data_dir=base_data_dir)
