from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from feature_builder import build_event_v3_feature_row, build_latest_feature_row_from_klines
from model_loader import (
    LoadedModel,
    get_prod_registry_entry,
    load_model,
    predict_proba,
    find_registry_path,
)
from prediction_logger import log_prediction

MODEL_DIR = os.getenv("MODEL_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "current")))
DATA_DIR = os.getenv("DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data")))

# Thresholds for legacy binary models
PROBA_LONG = float(os.getenv("ML_PROBA_LONG", "0.55"))
PROBA_SHORT = float(os.getenv("ML_PROBA_SHORT", "0.45"))

# Thresholds for event_v3 3-class stacking model
# EVENT_V3_P_ENTER: minimum per-class probability to enter a position
# EVENT_V3_DELTA:   minimum margin (p_long - p_short) or (p_short - p_long) required
EVENT_V3_P_ENTER = float(os.getenv("EVENT_V3_P_ENTER", "0.65"))
EVENT_V3_DELTA = float(os.getenv("EVENT_V3_DELTA", "0.0"))

_loaded: Optional[LoadedModel] = None

logger = logging.getLogger("ml-service")
logger.setLevel(logging.INFO)


class PredictRequest(BaseModel):
    # accept whatever Go sends
    model_config = {"extra": "allow"}
    symbol: Optional[str] = None
    interval: Optional[str] = "1h"

    # for historical backtests
    as_of_ts: Optional[str] = Field(default=None, description="ISO8601 cutoff, e.g. 2026-03-05T12:00:00Z")

    feature_ts: Optional[str] = None
    features: Optional[Dict[str, Any]] = None


class PredictResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    signal: str = Field(..., description="LONG|SHORT|FLAT")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Raw model confidence")
    calibrated_confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Calibrated confidence (if calibration artifact available)"
    )
    calibration_method: Optional[str] = Field(
        default=None, description="Calibration method used: isotonic | sigmoid | None"
    )
    model_version: str
    reasons: List[str] = []


def _apply_calibration(
    loaded: LoadedModel,
    p: np.ndarray,
) -> tuple[Optional[np.ndarray], Optional[str]]:
    """
    Apply calibration if a calibration artifact is present on the model.

    Returns:
        (calibrated_proba, method_name) or (None, None) if not available.
    """
    if loaded.calibration is None:
        return None, None
    try:
        from calibration import calibrate_proba
        cal_p = calibrate_proba(p, loaded.calibration)
        return cal_p, loaded.calibration.method
    except Exception:
        return None, None


def _active_model_dir() -> str:
    return MODEL_DIR


app = FastAPI(title="ubuntu-wallet ml-service", version="klines-featurebuilder-v3-event")


@app.on_event("startup")
def _startup():
    global _loaded
    _loaded = load_model(MODEL_DIR)


def _latest_report_path(data_dir: str, pattern: str) -> Optional[str]:
    """Return the path of the most recent file matching pattern under data_dir/reports/."""
    import glob as _glob
    reports_dir = os.path.join(data_dir, "reports")
    matches = _glob.glob(os.path.join(reports_dir, pattern))
    if not matches:
        return None
    return max(matches)


def _latest_exog_ts(data_dir: str, symbol: str = "ETHUSDT") -> Optional[str]:
    """Return the timestamp of the latest exog snapshot for symbol, or None."""
    import json as _json
    path = os.path.join(data_dir, "raw", f"exog_{symbol}.jsonl")
    if not os.path.exists(path):
        return None
    last_line = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
    except Exception:
        return None
    if last_line is None:
        return None
    try:
        row = _json.loads(last_line)
        return row.get("timestamp")
    except Exception:
        return None


@app.get("/healthz")
def healthz():
    """
    Defensive health endpoint:
    - Ensure registry_info is always initialized to avoid UnboundLocalError
    - Catch exceptions when reading the registry and include error info in response
    """
    import json
    import traceback

    if _loaded is None:
        return {"ok": False, "model_dir": MODEL_DIR, "data_dir": DATA_DIR}

    # Initialize so it's always present
    registry_info: Dict[str, Any] = {"note": "registry unknown"}

    MODELS_ROOT = os.path.abspath(os.path.join(MODEL_DIR, ".."))
    try:
        # Use the model_loader helpers to find registry.json and prod entry robustly.
        registry_path = find_registry_path(MODEL_DIR)
        if registry_path:
            prod_entry = get_prod_registry_entry(MODEL_DIR)
            if prod_entry:
                registry_info = {
                    "note": "prod registry entry found",
                    "registry_path": registry_path,
                    "model_version": prod_entry.get("model_version"),
                    "trained_at": prod_entry.get("trained_at"),
                    "status": prod_entry.get("status"),
                    "n_features": prod_entry.get("n_features"),
                }
            else:
                registry_info = {
                    "note": "registry.json found but no prod entry",
                    "registry_path": registry_path,
                    "registry_root": MODELS_ROOT,
                }
        else:
            registry_info = {"note": "no prod registry entry found", "registry_root": MODELS_ROOT}
    except Exception as e:
        # Do not raise ? include error info in response for debugging
        try:
            # If a logger exists, log the exception
            logger = globals().get("logger")
            if logger is not None:
                logger.exception("healthz: failed to read registry.json")
        except Exception:
            pass
        registry_info = {"error": str(e), "traceback": traceback.format_exc()}

    # Safely extract attributes from _loaded to avoid further exceptions
    try:
        loaded_model_dir = os.path.dirname(_loaded.model_path) if getattr(_loaded, "model_path", None) else None
    except Exception:
        loaded_model_dir = None
    try:
        loaded_model_version = getattr(_loaded, "model_version", None)
        loaded_trained_at = getattr(_loaded, "trained_at", None)
        expected_n = getattr(_loaded, "expected_n_features", None)
        calibration_available = getattr(_loaded, "calibration", None) is not None
        calibration_method = getattr(_loaded, "calibration", None).method if calibration_available else None
    except Exception:
        loaded_model_version = loaded_trained_at = expected_n = calibration_available = calibration_method = None

    return {
        "ok": True,
        "model_dir": MODEL_DIR,
        "loaded_model_dir": loaded_model_dir,
        "data_dir": DATA_DIR,
        "model_version": loaded_model_version,
        "loaded_model_trained_at": loaded_trained_at,
        "model_expected_n_features": expected_n,
        "calibration_available": calibration_available,
        "calibration_method": calibration_method,
        "registry": registry_info,
        "flags": {
            "ENABLE_EXOG_FEATURES": os.environ.get("ENABLE_EXOG_FEATURES", "false"),
            "ENABLE_DRIFT_MONITOR": os.environ.get("ENABLE_DRIFT_MONITOR", "false"),
            "ENABLE_CALIB_REPORT": os.environ.get("ENABLE_CALIB_REPORT", "false"),
        },
        "latest_drift_report": _latest_report_path(DATA_DIR, "drift_*.json"),
        "latest_calib_report": _latest_report_path(DATA_DIR, "calib_report_*.json"),
        "exog_data_ts": _latest_exog_ts(DATA_DIR),
    }


def _parse_iso_to_utc(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if _loaded is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    interval = req.interval or "1h"
    as_of_ts = req.as_of_ts
    symbol = req.symbol  # 现在可能是 None，后面可以让 Go 填进来

    # Extract feature_ts if provided either at top-level or inside features object.
    request_feature_ts = None
    if getattr(req, "feature_ts", None):
        request_feature_ts = req.feature_ts
    else:
        # req.features may be a dict (from Go) and may contain its own feature_ts
        try:
            feats = req.features
            if isinstance(feats, dict):
                request_feature_ts = feats.get("feature_ts") or feats.get("feature_ts_utc")
        except Exception:
            request_feature_ts = None

    # Fallback order for as_of cutoff for building features:
    # request_feature_ts (from caller) -> req.as_of_ts -> None (means latest available)
    effective_as_of = request_feature_ts or as_of_ts

    # Use the appropriate feature builder based on the active model type
    try:
        if _loaded.active_model == "event_v3":
            built = build_event_v3_feature_row(
                data_dir=DATA_DIR,
                model_dir=_active_model_dir(),
                expected_n_features=_loaded.expected_n_features,
                as_of_ts=effective_as_of,
            )
        else:
            built = build_latest_feature_row_from_klines(
                data_dir=DATA_DIR,
                interval=interval,
                expected_n_features=_loaded.expected_n_features,
                as_of_ts=effective_as_of,
            )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"feature_build_failed: {e}")

    try:
        p, mode = predict_proba(_loaded, built.X_row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"predict_failed: {e}")

    # Determine feature timestamp used for logging.
    # Preference order:
    # 1) request.feature_ts (if provided, top-level or inside features)
    # 2) built.feature_ts (from feature builder)
    # 3) request.as_of_ts (if provided)
    # 4) current UTC time
    feat_ts = None
    # priority: request_feature_ts -> built.feature_ts -> as_of_ts -> now
    if request_feature_ts:
        feat_ts = _parse_iso_to_utc(request_feature_ts)
    if feat_ts is None and getattr(built, "feature_ts", None):
        feat_ts = _parse_iso_to_utc(str(built.feature_ts))
    if feat_ts is None and as_of_ts:
        feat_ts = _parse_iso_to_utc(as_of_ts)
    if feat_ts is None:
        feat_ts = datetime.utcnow().astimezone(timezone.utc)

    # Apply calibration if available
    cal_p, cal_method = _apply_calibration(_loaded, p)

    # Prepare chosen/built ts strings for responses/logging
    chosen_ts_str = feat_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    built_ts = getattr(built, "feature_ts", None)
    logger.info(f"predict: req_feature_ts={request_feature_ts} built_feature_ts={built_ts} chosen_ts={chosen_ts_str}")

    # --- event_v3: 3-class multiclass output ---
    # Classes: 0=SHORT, 1=FLAT, 2=LONG
    if mode == "proba_multiclass" and _loaded.active_model == "event_v3":
        ev3 = _loaded.event_v3 or {}
        p_enter = float(os.getenv("EVENT_V3_P_ENTER", str(ev3.get("p_enter", EVENT_V3_P_ENTER))))
        delta = float(os.getenv("EVENT_V3_DELTA", str(ev3.get("delta", EVENT_V3_DELTA))))

        p_short = float(p[0, 0])
        p_flat = float(p[0, 1])
        p_long = float(p[0, 2])

        # Calibrated probabilities (use for thresholding if available)
        if cal_p is not None:
            cp_short = float(cal_p[0, 0])
            cp_flat = float(cal_p[0, 1])
            cp_long = float(cal_p[0, 2])
        else:
            cp_short = cp_flat = cp_long = None

        # Use calibrated probabilities for thresholding when available
        eff_long = cp_long if cp_long is not None else p_long
        eff_short = cp_short if cp_short is not None else p_short

        as_of_str = f"as_of_ts={effective_as_of}" if effective_as_of else "as_of_ts=latest"

        signal = "FLAT"
        confidence = max(p_long, p_short, p_flat)
        cal_conf = max(cp_long, cp_short, cp_flat) if cp_long is not None else None

        if eff_long >= p_enter and (eff_long - eff_short) >= delta:
            signal = "LONG"
            confidence = p_long
            cal_conf = cp_long
        elif eff_short >= p_enter and (eff_short - eff_long) >= delta:
            signal = "SHORT"
            confidence = p_short
            cal_conf = cp_short

        # include both requested fields in extra for traceability
        extra_meta = {"as_of_ts_requested": as_of_ts, "feature_ts_requested": request_feature_ts, "effective_as_of_used": effective_as_of}

        log_prediction(
            ts=feat_ts,
            symbol=symbol,
            interval=interval,
            proba_long=p_long,
            proba_short=p_short,
            proba_flat=p_flat,
            signal=signal,
            confidence=round(confidence, 6),
            model_version=_loaded.model_version,
            active_model=_loaded.active_model,
            cal_proba_long=cp_long,
            cal_proba_short=cp_short,
            cal_proba_flat=cp_flat,
            calibrated_confidence=round(cal_conf, 6) if cal_conf is not None else None,
            calibration_method=cal_method,
            threshold_long=p_enter,
            threshold_short=p_enter,
            extra=extra_meta,
        )

        if signal == "LONG":
            reasons = [
                f"p_long={p_long:.4f}>={p_enter} delta={p_long - p_short:.4f}>={delta}",
                f"feature_ts_built={built_ts} chosen_ts={chosen_ts_str}",
                as_of_str,
            ]
            if cal_conf is not None:
                reasons.insert(1, f"cal_p_long={cp_long:.4f}")
        elif signal == "SHORT":
            reasons = [
                f"p_short={p_short:.4f}>={p_enter} delta={p_short - p_long:.4f}>={delta}",
                f"feature_ts_built={built_ts} chosen_ts={chosen_ts_str}",
                as_of_str,
            ]
            if cal_conf is not None:
                reasons.insert(1, f"cal_p_short={cp_short:.4f}")
        else:
            reasons = [
                f"no_signal: p_long={p_long:.4f} p_short={p_short:.4f} p_flat={p_flat:.4f} threshold={p_enter}",
                f"feature_ts_built={built_ts} chosen_ts={chosen_ts_str}",
                as_of_str,
            ]

        return PredictResponse(
            signal=signal,
            confidence=round(confidence, 4),
            calibrated_confidence=round(cal_conf, 4) if cal_conf is not None else None,
            calibration_method=cal_method,
            model_version=_loaded.model_version,
            reasons=reasons,
        )

    # --- legacy binary output ---
    if mode != "proba_binary":
        raise HTTPException(status_code=500, detail=f"unsupported_predict_mode: {mode}")

    proba_up = float(p[0, 1])

    # Map to long/short probabilities
    p_long = proba_up
    p_short = 1.0 - proba_up
    p_flat = None

    # Calibrated probabilities for binary case
    if cal_p is not None:
        cp_long_bin = float(cal_p[0, 1])
        cp_short_bin = 1.0 - cp_long_bin
        eff_proba_up = cp_long_bin
    else:
        cp_long_bin = cp_short_bin = None
        eff_proba_up = proba_up

    as_of_str_final = f"as_of_ts={effective_as_of}" if effective_as_of else "as_of_ts=latest"

    extra_meta_bin = {"as_of_ts_requested": as_of_ts, "feature_ts_requested": request_feature_ts, "effective_as_of_used": effective_as_of}

    if eff_proba_up >= PROBA_LONG:
        signal = "LONG"
        confidence = proba_up
        cal_conf_bin = cp_long_bin
        log_prediction(
            ts=feat_ts,
            symbol=symbol,
            interval=interval,
            proba_long=p_long,
            proba_short=p_short,
            proba_flat=p_flat,
            signal=signal,
            confidence=round(confidence, 6),
            model_version=_loaded.model_version,
            active_model=_loaded.active_model,
            cal_proba_long=cp_long_bin,
            cal_proba_short=cp_short_bin,
            calibrated_confidence=round(cal_conf_bin, 6) if cal_conf_bin is not None else None,
            calibration_method=cal_method,
            threshold_long=PROBA_LONG,
            threshold_short=PROBA_SHORT,
            extra=extra_meta_bin,
        )
        return PredictResponse(
            signal="LONG",
            confidence=round(proba_up, 4),
            calibrated_confidence=round(cal_conf_bin, 4) if cal_conf_bin is not None else None,
            calibration_method=cal_method,
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}>= {PROBA_LONG}",
                f"feature_ts_built={built_ts} chosen_ts={chosen_ts_str}",
                as_of_str_final,
            ],
        )

    if eff_proba_up <= PROBA_SHORT:
        signal = "SHORT"
        confidence = 1.0 - proba_up
        cal_conf_bin = cp_short_bin
        log_prediction(
            ts=feat_ts,
            symbol=symbol,
            interval=interval,
            proba_long=p_long,
            proba_short=p_short,
            proba_flat=p_flat,
            signal=signal,
            confidence=round(confidence, 6),
            model_version=_loaded.model_version,
            active_model=_loaded.active_model,
            cal_proba_long=cp_long_bin,
            cal_proba_short=cp_short_bin,
            calibrated_confidence=round(cal_conf_bin, 6) if cal_conf_bin is not None else None,
            calibration_method=cal_method,
            threshold_long=PROBA_LONG,
            threshold_short=PROBA_SHORT,
            extra=extra_meta_bin,
        )
        return PredictResponse(
            signal="SHORT",
            confidence=round(confidence, 4),
            calibrated_confidence=round(cal_conf_bin, 4) if cal_conf_bin is not None else None,
            calibration_method=cal_method,
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}<= {PROBA_SHORT}",
                f"feature_ts_built={built_ts} chosen_ts={chosen_ts_str}",
                as_of_str_final,
            ],
        )

    # FLAT
    signal = "FLAT"
    confidence = max(proba_up, 1.0 - proba_up)
    log_prediction(
        ts=feat_ts,
        symbol=symbol,
        interval=interval,
        proba_long=p_long,
        proba_short=p_short,
        proba_flat=p_flat,
        signal=signal,
        confidence=round(confidence, 6),
        model_version=_loaded.model_version,
        active_model=_loaded.active_model,
        cal_proba_long=cp_long_bin,
        cal_proba_short=cp_short_bin,
        calibrated_confidence=None,
        calibration_method=cal_method,
        threshold_long=PROBA_LONG,
        threshold_short=PROBA_SHORT,
        extra=extra_meta_bin,
    )
    return PredictResponse(
        signal="FLAT",
        confidence=round(confidence, 4),
        calibrated_confidence=None,
        calibration_method=cal_method,
        model_version=_loaded.model_version,
        reasons=[
            f"dead_zone: {PROBA_SHORT} < {proba_up:.4f} < {PROBA_LONG}",
            f"feature_ts_built={built_ts} chosen_ts={chosen_ts_str}",
            as_of_str_final,
        ],
    )
