#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from mt_filter import mt_gate, gate_allows


def to_optional_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


@dataclass(frozen=True)
class SignalSnapshot:
    signal: str
    confidence: Optional[float]
    calibrated_confidence: Optional[float]
    calibration_method: Optional[str]
    model_version: str

    p_long: Optional[float]
    p_short: Optional[float]
    p_flat: Optional[float]

    cal_p_long: Optional[float]
    cal_p_short: Optional[float]
    cal_p_flat: Optional[float]

    effective_long: Optional[float]
    effective_short: Optional[float]
    threshold_enter: Optional[float]

    reasons: List[str]


def parse_probs_from_reasons(reasons: List[str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    p_long = p_short = p_flat = None
    for s in reasons or []:
        if "p_long=" in s and "p_short=" in s and "p_flat=" in s:
            parts = s.replace(":", " ").replace(",", " ").split()
            for token in parts:
                if token.startswith("p_long="):
                    try:
                        p_long = float(token.split("=", 1)[1])
                    except Exception:
                        pass
                elif token.startswith("p_short="):
                    try:
                        p_short = float(token.split("=", 1)[1])
                    except Exception:
                        pass
                elif token.startswith("p_flat="):
                    try:
                        p_flat = float(token.split("=", 1)[1])
                    except Exception:
                        pass
    return p_long, p_short, p_flat


def normalize_predict_response(j: Dict[str, Any]) -> SignalSnapshot:
    reasons = list(j.get("reasons") or [])

    raw_p_long = to_optional_float(j.get("p_long"))
    raw_p_short = to_optional_float(j.get("p_short"))
    raw_p_flat = to_optional_float(j.get("p_flat"))

    if raw_p_long is None or raw_p_short is None or raw_p_flat is None:
        rp_long, rp_short, rp_flat = parse_probs_from_reasons(reasons)
        if raw_p_long is None:
            raw_p_long = rp_long
        if raw_p_short is None:
            raw_p_short = rp_short
        if raw_p_flat is None:
            raw_p_flat = rp_flat

    cal_p_long = to_optional_float(j.get("cal_p_long"))
    cal_p_short = to_optional_float(j.get("cal_p_short"))
    cal_p_flat = to_optional_float(j.get("cal_p_flat"))

    effective_long = to_optional_float(j.get("effective_long"))
    effective_short = to_optional_float(j.get("effective_short"))
    threshold_enter = to_optional_float(j.get("threshold_enter"))

    return SignalSnapshot(
        signal=str(j.get("signal") or ""),
        confidence=to_optional_float(j.get("confidence")),
        calibrated_confidence=to_optional_float(j.get("calibrated_confidence")),
        calibration_method=(str(j.get("calibration_method")) if j.get("calibration_method") is not None else None),
        model_version=str(j.get("model_version") or ""),

        p_long=raw_p_long,
        p_short=raw_p_short,
        p_flat=raw_p_flat,

        cal_p_long=cal_p_long,
        cal_p_short=cal_p_short,
        cal_p_flat=cal_p_flat,

        effective_long=effective_long,
        effective_short=effective_short,
        threshold_enter=threshold_enter,

        reasons=reasons,
    )


def normalize_log_prediction(j: Dict[str, Any]) -> SignalSnapshot:
    return SignalSnapshot(
        signal=str(j.get("signal") or ""),
        confidence=to_optional_float(j.get("confidence")),
        calibrated_confidence=to_optional_float(j.get("calibrated_confidence")),
        calibration_method=(str(j.get("calibration_method")) if j.get("calibration_method") is not None else None),
        model_version=str(j.get("model_version") or ""),

        p_long=to_optional_float(j.get("proba_long")),
        p_short=to_optional_float(j.get("proba_short")),
        p_flat=to_optional_float(j.get("proba_flat")),

        cal_p_long=to_optional_float(j.get("cal_proba_long")),
        cal_p_short=to_optional_float(j.get("cal_proba_short")),
        cal_p_flat=to_optional_float(j.get("cal_proba_flat")),

        effective_long=to_optional_float(j.get("effective_long")),
        effective_short=to_optional_float(j.get("effective_short")),
        threshold_enter=to_optional_float(j.get("threshold_enter")),

        reasons=list(j.get("reasons") or []),
    )


def select_effective_probs(snapshot: SignalSnapshot) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """Return the raw stacking probabilities (p_long, p_short, p_flat) for threshold comparison.

    IMPORTANT: effective_long/cal_p_long (calibrated) are NOT used for thresholding.
    Isotonic calibration compresses p_stack (0.09-0.52) down to (0.03-0.13), which
    is incompatible with thresholds tuned on the raw p_stack range.
    Calibrated probabilities are available via snapshot.cal_p_long for monitoring only.
    """
    # Always use raw stacking probabilities for entry decisions.
    if snapshot.p_long is not None and snapshot.p_short is not None:
        return snapshot.p_long, snapshot.p_short, snapshot.p_flat, "raw"

    # Fallback: if raw not available, try calibrated (should not happen in normal flow)
    if snapshot.cal_p_long is not None and snapshot.cal_p_short is not None:
        return snapshot.cal_p_long, snapshot.cal_p_short, snapshot.cal_p_flat, "calibrated_fallback"

    # Last resort: effective (legacy field)
    if snapshot.effective_long is not None and snapshot.effective_short is not None:
        p_flat = snapshot.cal_p_flat if snapshot.cal_p_flat is not None else snapshot.p_flat
        return snapshot.effective_long, snapshot.effective_short, p_flat, "effective_fallback"

    return None, None, None, "none"


def decide_side(p_long: Optional[float], p_short: Optional[float], threshold: float) -> str:
    if p_long is None or p_short is None:
        return "FLAT"
    if p_long >= threshold and p_long >= p_short:
        return "LONG"
    if p_short >= threshold and p_short > p_long:
        return "SHORT"
    return "FLAT"


def decide_side_from_signal(signal: str) -> str:
    s = (signal or "").upper().strip()
    if s in ("LONG", "SHORT", "FLAT"):
        return s
    return "FLAT"


def normalize_mt_mode(mode: str) -> str:
    m = (mode or "off").lower().strip()
    aliases = {
        "long_only": "strict",
        "symmetric": "strict",
    }
    return aliases.get(m, m)


def apply_mt_filter_common(
    *,
    side: str,
    t4: str,
    t1d: str,
    mode: str,
    mt_reject_reasons: Optional[Counter] = None,
) -> str:
    mode = normalize_mt_mode(mode)

    if side not in ("LONG", "SHORT"):
        return side

    if mode == "off":
        return side

    def _rej(key: str) -> str:
        if mt_reject_reasons is not None:
            mt_reject_reasons[key] += 1
        return "FLAT"

    if mode == "layered":
        gate = mt_gate(side, t4, t1d)
        if not gate_allows(gate):
            return _rej(f"layered_reject_{side.lower()}")
        return side

    if mode == "strict":
        if side == "LONG":
            if t4 != "UP":
                return _rej("long_4h_not_up")
            if t1d == "DOWN":
                return _rej("long_1d_is_down")
            return "LONG"

        if t4 != "DOWN":
            return _rej("short_4h_not_down")
        if t1d == "UP":
            return _rej("short_1d_is_up")
        return "SHORT"

    if mode == "relaxed":
        if side == "LONG":
            if t1d == "DOWN":
                return _rej("long_1d_is_down")
            if t4 == "DOWN":
                return _rej("long_4h_is_down")
            return "LONG"

        if t1d == "UP":
            return _rej("short_1d_is_up")
        if t4 == "UP":
            return _rej("short_4h_is_up")
        return "SHORT"

    if mode == "trend_guard":
        if side == "LONG":
            if t1d == "DOWN":
                return _rej("trend_guard_long_1d_down")
            if t4 == "DOWN":
                return _rej("trend_guard_long_4h_down")
            return "LONG"

        if t1d == "UP":
            return _rej("trend_guard_short_1d_up")
        if t4 == "UP":
            return _rej("trend_guard_short_4h_up")
        return "SHORT"

    if mode == "daily_guard":
        if side == "LONG":
            if t1d == "DOWN":
                return _rej("daily_guard_long_1d_down")
            return "LONG"

        if t1d == "UP":
            return _rej("daily_guard_short_1d_up")
        return "SHORT"

    if mode == "regime":
        if t1d == "UP":
            if side == "SHORT":
                return _rej("regime_1d_up_reject_short")
            return "LONG"

        if t1d == "DOWN":
            if side == "LONG":
                return _rej("regime_1d_down_reject_long")
            return "SHORT"

        if side == "LONG":
            if t4 == "DOWN":
                return _rej("regime_1d_neutral_long_4h_down")
            return "LONG"
        else:
            if t4 == "UP":
                return _rej("regime_1d_neutral_short_4h_up")
            return "SHORT"

    if mode == "conflict":
        if (t1d == "UP" and t4 == "DOWN") or (t1d == "DOWN" and t4 == "UP"):
            return _rej("conflict_1d_vs_4h")
        return apply_mt_filter_common(
            side=side,
            t4=t4,
            t1d=t1d,
            mode="relaxed",
            mt_reject_reasons=mt_reject_reasons,
        )

    raise ValueError(f"Unknown mt filter mode: {mode}")


def apply_mt_filter_with_context(
    *,
    side: str,
    sig_ts: datetime,
    trend_4h_at,
    trend_1d_at,
    mode: str,
    mt_reject_reasons: Optional[Counter] = None,
) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    if side not in ("LONG", "SHORT"):
        return side, None, None, None

    m = normalize_mt_mode(mode)
    if m == "off":
        return side, None, None, None

    t4 = trend_4h_at(sig_ts)
    t1d = trend_1d_at(sig_ts)

    before = Counter(mt_reject_reasons or {})
    final_side = apply_mt_filter_common(
        side=side,
        t4=t4,
        t1d=t1d,
        mode=m,
        mt_reject_reasons=mt_reject_reasons,
    )

    reject_reason = None
    if mt_reject_reasons is not None:
        after = mt_reject_reasons
        for k, v in after.items():
            if v > before.get(k, 0):
                reject_reason = k
                break

    return final_side, t4, t1d, reject_reason
