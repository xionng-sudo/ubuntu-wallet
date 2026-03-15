import json
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

# Default path: <repo_root>/data/predictions_log.jsonl
_LOG_PATH = os.getenv(
    "PREDICTIONS_LOG_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.jsonl")),
)

_lock = Lock()


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log_prediction(
    *,
    ts: datetime,
    symbol: Optional[str],
    interval: Optional[str],
    proba_long: Optional[float],
    proba_short: Optional[float],
    proba_flat: Optional[float],
    signal: str,
    confidence: float,
    model_version: str,
    active_model: str,
    # Calibration fields
    cal_proba_long: Optional[float] = None,
    cal_proba_short: Optional[float] = None,
    cal_proba_flat: Optional[float] = None,
    calibrated_confidence: Optional[float] = None,
    calibration_method: Optional[str] = None,
    # Decision thresholds used
    threshold_long: Optional[float] = None,
    threshold_short: Optional[float] = None,
    # Multi-timeframe context
    trend_4h: Optional[str] = None,
    trend_1d: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append one prediction record to the JSONL log file.

    Fields logged:
      ts                   – feature timestamp (as_of_ts or feature_ts, UTC ISO8601)
      symbol               – trading pair, e.g. "ETHUSDT"
      interval             – bar interval, e.g. "1h"
      proba_long/short/flat – raw model probabilities
      cal_proba_*          – calibrated probabilities (None if no calibration artifact)
      calibrated_confidence – calibrated confidence for the chosen signal class
      calibration_method   – "isotonic" | "sigmoid" | None
      signal               – LONG | SHORT | FLAT
      confidence           – raw model confidence used for thresholding
      model_version        – model version string
      active_model         – model type string
      threshold_long       – p_enter threshold for LONG (for later analysis)
      threshold_short      – p_enter threshold for SHORT
      trend_4h / trend_1d  – multi-timeframe filter context at prediction time
    """
    rec: Dict[str, Any] = {
        "ts": _to_utc_iso(ts),
        "symbol": symbol,
        "interval": interval,
        "proba_long": round(proba_long, 6) if proba_long is not None else None,
        "proba_short": round(proba_short, 6) if proba_short is not None else None,
        "proba_flat": round(proba_flat, 6) if proba_flat is not None else None,
        "signal": signal,
        "confidence": round(confidence, 6),
        "model_version": model_version,
        "active_model": active_model,
    }

    # Calibration fields (only written when present)
    if cal_proba_long is not None:
        rec["cal_proba_long"] = round(cal_proba_long, 6)
    if cal_proba_short is not None:
        rec["cal_proba_short"] = round(cal_proba_short, 6)
    if cal_proba_flat is not None:
        rec["cal_proba_flat"] = round(cal_proba_flat, 6)
    if calibrated_confidence is not None:
        rec["calibrated_confidence"] = round(calibrated_confidence, 6)
    if calibration_method is not None:
        rec["calibration_method"] = calibration_method

    # Threshold context
    if threshold_long is not None:
        rec["threshold_long"] = threshold_long
    if threshold_short is not None:
        rec["threshold_short"] = threshold_short

    # Multi-timeframe filter context
    if trend_4h is not None:
        rec["trend_4h"] = trend_4h
    if trend_1d is not None:
        rec["trend_1d"] = trend_1d

    if extra:
        rec.update(extra)

    line = json.dumps(rec, ensure_ascii=False)

    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    with _lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
