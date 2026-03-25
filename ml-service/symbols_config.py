"""Per-symbol threshold configuration loader for ml-service.

Loads ``configs/symbols.yaml`` from the repository root (one level above this
file) and caches the result in-memory.  The cache is automatically refreshed
if the file's modification time changes, so a ``systemctl reload`` is not
required for operators who tweak thresholds between restarts.

If the YAML file cannot be read or parsed the module logs a warning and all
threshold lookups return ``None``, preserving the previous env/model/default
behaviour in ``app.py``.

Usage::

    from symbols_config import get_symbol_threshold

    p_enter = get_symbol_threshold("ETHUSDT")  # float or None
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("ml-service.symbols_config")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_PATH = os.path.join(_REPO_ROOT, "configs", "symbols.yaml")

# ---------------------------------------------------------------------------
# In-memory cache with mtime-based invalidation
# ---------------------------------------------------------------------------

_cache: Optional[Dict[str, Any]] = None
_cache_mtime: Optional[float] = None


def _load() -> Dict[str, Any]:
    """Load (or reload) ``configs/symbols.yaml``.

    Returns a mapping of ``{symbol: {field: value, ...}, ...}`` (the
    ``symbols`` top-level key from the YAML).  Returns an empty dict on any
    error.
    """
    global _cache, _cache_mtime

    if not os.path.exists(_CONFIG_PATH):
        if _cache is None:
            logger.warning(
                "symbols_config: %s not found; per-symbol thresholds unavailable",
                _CONFIG_PATH,
            )
            _cache = {}
            _cache_mtime = None
        return _cache

    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        mtime = None

    if _cache is not None and mtime is not None and mtime == _cache_mtime:
        return _cache

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        logger.warning(
            "symbols_config: PyYAML not installed; per-symbol thresholds unavailable. "
            "Install with: pip install pyyaml"
        )
        _cache = {}
        _cache_mtime = None
        return _cache

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _cache = (data or {}).get("symbols", {}) or {}
        _cache_mtime = mtime
        logger.debug(
            "symbols_config: loaded %d symbols from %s", len(_cache), _CONFIG_PATH
        )
    except Exception as exc:
        logger.warning(
            "symbols_config: failed to parse %s: %s; per-symbol thresholds unavailable",
            _CONFIG_PATH,
            exc,
        )
        _cache = {}
        _cache_mtime = None

    return _cache


def get_symbol_threshold(symbol: Optional[str]) -> Optional[float]:
    """Return the configured ``threshold`` for *symbol*, or ``None``.

    Returns ``None`` when:

    * *symbol* is ``None`` or empty.
    * ``configs/symbols.yaml`` does not exist or cannot be read.
    * The symbol is not present in the config.
    * The symbol entry has no ``threshold`` key.
    * The ``threshold`` value cannot be converted to ``float``.
    """
    if not symbol:
        return None

    symbols = _load()
    sym_cfg = symbols.get(symbol)
    if not isinstance(sym_cfg, dict):
        return None

    val = sym_cfg.get("threshold")
    if val is None:
        return None

    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def resolve_p_enter(
    symbol: Optional[str],
    ev3_meta: Dict[str, Any],
    env_var_name: str = "EVENT_V3_P_ENTER",
    default: float = 0.65,
) -> "tuple[float, str]":
    """Resolve the ``p_enter`` threshold for the event_v3 model.

    Precedence (highest to lowest):

    1. Per-symbol ``threshold`` from ``configs/symbols.yaml``.
    2. Environment variable *env_var_name* (default ``EVENT_V3_P_ENTER``).
    3. ``p_enter`` field in the loaded model's ``event_v3`` metadata.
    4. Hard-coded *default* constant (default ``0.65``).

    Returns a ``(p_enter, source)`` tuple where *source* is a short string
    describing which tier was used (for logging and ``reasons``).
    """
    sym_threshold = get_symbol_threshold(symbol)
    if sym_threshold is not None:
        return sym_threshold, "configs/symbols.yaml"

    env_val = os.getenv(env_var_name)
    if env_val is not None:
        return float(env_val), f"env/{env_var_name}"

    meta_val = ev3_meta.get("p_enter")
    if meta_val is not None:
        return float(meta_val), "model/metadata"

    return default, "default"


def _reset_cache() -> None:
    """Force a reload on the next call.  Intended for use in tests only."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = None
