from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from feature_builder import build_event_v3_feature_row, build_latest_feature_row_from_klines
from model_loader import LoadedModel, load_model, load_model_from_registry, get_prod_registry_entry, predict_proba
from prediction_logger import log_prediction

MODEL_DIR = os.getenv("MODEL_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models")))
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


app = FastAPI(title="ubuntu-wallet ml-service", version="klines-featurebuilder-v3-event")


@app.on_event("startup")
def _startup():
    global _loaded
    _loaded = load_model_from_registry(MODEL_DIR)


@app.get("/healthz")
def healthz():
    if _loaded is None:
        return {"ok": False, "model_dir": MODEL_DIR, "data_dir": DATA_DIR}

    reg_entry = get_prod_registry_entry(MODEL_DIR)
    registry_info = None
    if reg_entry is not None:
        registry_info = {
            "model_version": reg_entry.get("model_version"),
            "trained_at": reg_entry.get("trained_at"),
            "status": reg_entry.get("status"),
            "n_features": reg_entry.get("n_features"),
        }

    return {
        "ok": True,
        "model_dir": MODEL_DIR,
        "data_dir": DATA_DIR,
        "model_version": _loaded.model_version,
        "model_expected_n_features": _loaded.expected_n_features,
        "calibration_available": _loaded.calibration is not None,
        "calibration_method": _loaded.calibration.method if _loaded.calibration is not None else None,
        "registry": registry_info,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if _loaded is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    interval = req.interval or "1h"
    as_of_ts = req.as_of_ts
    symbol = req.symbol  # 现在可能是 None，后面可以让 Go 填进来

    # Use the appropriate feature builder based on the active model type
    try:
        if _loaded.active_model == "event_v3":
            built = build_event_v3_feature_row(
                data_dir=DATA_DIR,
                expected_n_features=_loaded.expected_n_features,
                as_of_ts=as_of_ts,
            )
        else:
            built = build_latest_feature_row_from_klines(
                data_dir=DATA_DIR,
                interval=interval,
                expected_n_features=_loaded.expected_n_features,
                as_of_ts=as_of_ts,
            )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"feature_build_failed: {e}")

    try:
        p, mode = predict_proba(_loaded, built.X_row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"predict_failed: {e}")

    # Parse feature timestamp for logging
    from datetime import datetime
    feat_ts_raw = built.feature_ts
    try:
        if feat_ts_raw:
            feat_ts = datetime.fromisoformat(str(feat_ts_raw).replace("Z", "+00:00"))
        else:
            if as_of_ts:
                feat_ts = datetime.fromisoformat(as_of_ts.replace("Z", "+00:00"))
            else:
                feat_ts = datetime.utcnow()
    except Exception:
        feat_ts = datetime.utcnow()

    # Apply calibration if available
    cal_p, cal_method = _apply_calibration(_loaded, p)

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

        as_of_str = f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest"

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
            extra={"as_of_ts": as_of_ts},
        )

        if signal == "LONG":
            reasons = [
                f"p_long={p_long:.4f}>={p_enter} delta={p_long - p_short:.4f}>={delta}",
                f"feature_ts={built.feature_ts}",
                as_of_str,
            ]
            if cal_conf is not None:
                reasons.insert(1, f"cal_p_long={cp_long:.4f}")
        elif signal == "SHORT":
            reasons = [
                f"p_short={p_short:.4f}>={p_enter} delta={p_short - p_long:.4f}>={delta}",
                f"feature_ts={built.feature_ts}",
                as_of_str,
            ]
            if cal_conf is not None:
                reasons.insert(1, f"cal_p_short={cp_short:.4f}")
        else:
            reasons = [
                f"no_signal: p_long={p_long:.4f} p_short={p_short:.4f} p_flat={p_flat:.4f} threshold={p_enter}",
                f"feature_ts={built.feature_ts}",
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

    as_of_str_final = f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest"

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
            extra={"as_of_ts": as_of_ts},
        )
        return PredictResponse(
            signal="LONG",
            confidence=round(proba_up, 4),
            calibrated_confidence=round(cal_conf_bin, 4) if cal_conf_bin is not None else None,
            calibration_method=cal_method,
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}>= {PROBA_LONG}",
                f"feature_ts={built.feature_ts}",
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
            extra={"as_of_ts": as_of_ts},
        )
        return PredictResponse(
            signal="SHORT",
            confidence=round(confidence, 4),
            calibrated_confidence=round(cal_conf_bin, 4) if cal_conf_bin is not None else None,
            calibration_method=cal_method,
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}<= {PROBA_SHORT}",
                f"feature_ts={built.feature_ts}",
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
        extra={"as_of_ts": as_of_ts},
    )
    return PredictResponse(
        signal="FLAT",
        confidence=round(confidence, 4),
        calibrated_confidence=None,
        calibration_method=cal_method,
        model_version=_loaded.model_version,
        reasons=[
            f"dead_zone: {PROBA_SHORT} < {proba_up:.4f} < {PROBA_LONG}",
            f"feature_ts={built.feature_ts}",
            as_of_str_final,
        ],
    )
