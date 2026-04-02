#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decision_pipeline.py
====================
Shared decision pipeline used by **both** backtest_event_v3_http.py and
live_trader_perp_simulated.py to ensure identical per-bar side decisions.

The module provides two entry-points depending on the prediction source:

* ``decide_side_from_prediction(prediction_dict, ...)``
    Works on a raw ``/predict`` HTTP response dict.

* ``decide_side_from_cached_pred(cached_pred_dict, ...)``
    Works on a pre-computed prediction dict loaded from a pred_cache JSONL
    file produced by backtest_event_v3_http.py.  The cached dict has the same
    fields as the serialised ``CachedPred`` dataclass (selected_p_long, …).

Both functions return ``(side, debug_info)`` where *side* is one of
``"LONG"``, ``"SHORT"``, or ``"FLAT"``, **before** any multi-timeframe
filtering is applied.  Callers should then apply ``apply_mt_filter_with_context``
from ``signal_logic`` using the same ``mt_filter_mode`` parameter.

Priority order (CLI > YAML > default):
    side_source : "probs" (default) | "signal"
    mt_filter_mode : any of the modes in signal_logic.apply_mt_filter_common
    threshold : float (required)
"""

from __future__ import annotations

import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from typing import Any, Dict, Optional, Tuple

from signal_logic import (  # type: ignore
    normalize_predict_response,
    select_effective_probs,
    decide_side,
    decide_side_from_signal,
)

__all__ = [
    "decide_side_from_prediction",
    "decide_side_from_cached_pred",
]

_MT_FILTER_CHOICES = [
    "off", "long_only", "symmetric", "strict", "relaxed",
    "trend_guard", "daily_guard", "conflict", "regime", "layered",
]

# Default logic settings — these are the canonical defaults for both backtest
# and simulated replay.  Override via CLI or YAML; do NOT hardcode elsewhere.
# Canonical default values for logic parameters shared between backtest and simulated trader.
# These values are used when parameters are not specified via CLI or YAML configuration.
# Priority order: CLI > YAML > these defaults.
DEFAULTS = {
    "mt_filter_mode": "daily_guard",
    "side_source": "probs",
    "timeout_exit": "close",
    "tie_breaker": "SL",
    "position_mode": "single",
}


def decide_side_from_prediction(
    prediction: Dict[str, Any],
    *,
    side_source: str = "probs",
    threshold: float,
) -> Tuple[str, Dict[str, Any]]:
    """Convert a raw ``/predict`` response dict into a side decision.

    Parameters
    ----------
    prediction:
        Parsed JSON body from ``POST /predict``.
    side_source:
        ``"probs"`` – use effective/calibrated/raw probabilities (recommended).
        ``"signal"`` – use the ``signal`` field from the response directly.
    threshold:
        Minimum probability required for a LONG or SHORT entry (probs mode).

    Returns
    -------
    (side, debug_info)
        side : "LONG" | "SHORT" | "FLAT" (before mt_filter)
        debug_info : dict with keys signal, p_long, p_short, p_flat,
                     prob_src, side_source, threshold
    """
    snap = normalize_predict_response(prediction)
    signal_side = decide_side_from_signal(snap.signal)

    p_long, p_short, p_flat, prob_src = select_effective_probs(snap)

    if side_source == "signal":
        side = signal_side
    else:  # probs
        side = decide_side(p_long, p_short, threshold)

    debug_info: Dict[str, Any] = {
        "signal": snap.signal,
        "signal_side": signal_side,
        "p_long": p_long,
        "p_short": p_short,
        "p_flat": p_flat,
        "prob_src": prob_src,
        "side_before_filter": side,
        "threshold": threshold,
        "side_source": side_source,
    }
    return side, debug_info


def decide_side_from_cached_pred(
    cached: Dict[str, Any],
    *,
    side_source: str = "probs",
    threshold: float,
) -> Tuple[str, Dict[str, Any]]:
    """Convert a cached prediction dict (from pred_cache JSONL) into a side decision.

    The *cached* dict is the ``"pred"`` value from a line in the JSONL file
    produced by ``backtest_event_v3_http.py``.  It already contains
    ``selected_p_long``, ``selected_p_short``, ``selected_p_flat``, and
    ``selected_prob_source`` so the probability-source selection step is
    skipped (the backtest already performed it).

    Parameters
    ----------
    cached:
        Dict with at minimum the keys ``signal``, ``selected_p_long``,
        ``selected_p_short``, ``selected_p_flat``, ``selected_prob_source``.
    side_source:
        Same as :func:`decide_side_from_prediction`.
    threshold:
        Same as :func:`decide_side_from_prediction`.

    Returns
    -------
    (side, debug_info) — same schema as :func:`decide_side_from_prediction`.
    """
    signal_side = decide_side_from_signal(str(cached.get("signal", "")))

    p_long: Optional[float] = cached.get("selected_p_long")
    p_short: Optional[float] = cached.get("selected_p_short")
    p_flat: Optional[float] = cached.get("selected_p_flat")
    prob_src: str = str(cached.get("selected_prob_source", ""))

    if side_source == "signal":
        side = signal_side
    else:  # probs
        # decide_side handles None by returning "FLAT", so missing keys are safe
        side = decide_side(p_long, p_short, threshold)

    debug_info: Dict[str, Any] = {
        "signal": cached.get("signal", ""),
        "signal_side": signal_side,
        "p_long": p_long,
        "p_short": p_short,
        "p_flat": p_flat,
        "prob_src": prob_src,
        "side_before_filter": side,
        "threshold": threshold,
        "side_source": side_source,
    }
    return side, debug_info
