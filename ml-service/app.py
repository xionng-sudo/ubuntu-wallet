from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from feature_builder import build_event_v3_feature_row, build_latest_feature_row_from_klines
from model_loader import LoadedModel, load_model, predict_proba
from prediction_logger import log_prediction  # 新增这一行

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

    # NEW: for historical backtests
    as_of_ts: Optional[str] = Field(default=None, description="ISO8601 cutoff, e.g. 2026-03-05T12:00:00Z")

    feature_ts: Optional[str] = None
    features: Optional[Dict[str, Any]] = None


class PredictResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    signal: str = Field(..., description="LONG|SHORT|FLAT")
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_version: str
    reasons: List[str] = []


app = FastAPI(title="ubuntu-wallet ml-service", version="klines-featurebuilder-v3-event")


@app.on_event("startup")
def _startup():
    global _loaded
    _loaded = load_model(MODEL_DIR)


@app.get("/healthz")
def healthz():
    if _loaded is None:
        return {"ok": False, "model_dir": MODEL_DIR, "data_dir": DATA_DIR}
    return {
        "ok": True,
        "model_dir": MODEL_DIR,
        "data_dir": DATA_DIR,
        "model_version": _loaded.model_version,
        "model_expected_n_features": _loaded.expected_n_features,
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

    # 为日志准备公共字段：feature_ts 转 datetime
    from datetime import datetime
    feat_ts_raw = built.feature_ts  # 这个通常是 ISO 字符串
    try:
        if feat_ts_raw:
            # 允许 "....Z" 或带 offset 的格式
            feat_ts = datetime.fromisoformat(str(feat_ts_raw).replace("Z", "+00:00"))
        else:
            # 如果没有 feature_ts，就用 as_of_ts 或当前时间
            if as_of_ts:
                feat_ts = datetime.fromisoformat(as_of_ts.replace("Z", "+00:00"))
            else:
                feat_ts = datetime.utcnow()
    except Exception:
        from datetime import datetime as dt
        feat_ts = dt.utcnow()

    # --- event_v3: 3-class multiclass output ---
    # Classes: 0=SHORT, 1=FLAT, 2=LONG
    if mode == "proba_multiclass" and _loaded.active_model == "event_v3":
        ev3 = _loaded.event_v3 or {}
        p_enter = float(os.getenv("EVENT_V3_P_ENTER", str(ev3.get("p_enter", EVENT_V3_P_ENTER))))
        delta = float(os.getenv("EVENT_V3_DELTA", str(ev3.get("delta", EVENT_V3_DELTA))))

        p_short = float(p[0, 0])
        p_flat = float(p[0, 1])
        p_long = float(p[0, 2])

        as_of_str = f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest"

        # 预设默认 signal / confidence 用于日志
        signal = "FLAT"
        confidence = max(p_long, p_short, p_flat)

        if p_long >= p_enter and (p_long - p_short) >= delta:
            signal = "LONG"
            confidence = p_long
            # 先记录日志
            log_prediction(
                ts=feat_ts,
                symbol=symbol,
                interval=interval,
                proba_long=p_long,
                proba_short=p_short,
                proba_flat=p_flat,
                signal=signal,
                confidence=round(confidence, 4),
                model_version=_loaded.model_version,
                active_model=_loaded.active_model,
                extra={"as_of_ts": as_of_ts},
            )
            return PredictResponse(
                signal="LONG",
                confidence=round(p_long, 4),
                model_version=_loaded.model_version,
                reasons=[
                    f"p_long={p_long:.4f}>={p_enter} delta={p_long - p_short:.4f}>={delta}",
                    f"feature_ts={built.feature_ts}",
                    as_of_str,
                ],
            )

        if p_short >= p_enter and (p_short - p_long) >= delta:
            signal = "SHORT"
            confidence = p_short
            log_prediction(
                ts=feat_ts,
                symbol=symbol,
                interval=interval,
                proba_long=p_long,
                proba_short=p_short,
                proba_flat=p_flat,
                signal=signal,
                confidence=round(confidence, 4),
                model_version=_loaded.model_version,
                active_model=_loaded.active_model,
                extra={"as_of_ts": as_of_ts},
            )
            return PredictResponse(
                signal="SHORT",
                confidence=round(p_short, 4),
                model_version=_loaded.model_version,
                reasons=[
                    f"p_short={p_short:.4f}>={p_enter} delta={p_short - p_long:.4f}>={delta}",
                    f"feature_ts={built.feature_ts}",
                    as_of_str,
                ],
            )

        # FLAT 情况也要记录
        log_prediction(
            ts=feat_ts,
            symbol=symbol,
            interval=interval,
            proba_long=p_long,
            proba_short=p_short,
            proba_flat=p_flat,
            signal="FLAT",
            confidence=round(confidence, 4),
            model_version=_loaded.model_version,
            active_model=_loaded.active_model,
            extra={"as_of_ts": as_of_ts},
        )
        return PredictResponse(
            signal="FLAT",
            confidence=round(confidence, 4),
            model_version=_loaded.model_version,
            reasons=[
                f"no_signal: p_long={p_long:.4f} p_short={p_short:.4f} p_flat={p_flat:.4f} threshold={p_enter}",
                f"feature_ts={built.feature_ts}",
                as_of_str,
            ],
        )

    # --- legacy binary output ---
    if mode != "proba_binary":
        raise HTTPException(status_code=500, detail=f"unsupported_predict_mode: {mode}")

    proba_up = float(p[0, 1])

    # 映射成 long/short 概率
    p_long = proba_up
    p_short = 1.0 - proba_up
    p_flat = None

    as_of_str_final = f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest"

    if proba_up >= PROBA_LONG:
        signal = "LONG"
        confidence = proba_up
        log_prediction(
            ts=feat_ts,
            symbol=symbol,
            interval=interval,
            proba_long=p_long,
            proba_short=p_short,
            proba_flat=p_flat,
            signal=signal,
            confidence=round(confidence, 4),
            model_version=_loaded.model_version,
            active_model=_loaded.active_model,
            extra={"as_of_ts": as_of_ts},
        )
        return PredictResponse(
            signal="LONG",
            confidence=round(proba_up, 4),
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}>= {PROBA_LONG}",
                f"feature_ts={built.feature_ts}",
                as_of_str_final,
            ],
        )

    if proba_up <= PROBA_SHORT:
        signal = "SHORT"
        confidence = 1.0 - proba_up
        log_prediction(
            ts=feat_ts,
            symbol=symbol,
            interval=interval,
            proba_long=p_long,
            proba_short=p_short,
            proba_flat=p_flat,
            signal=signal,
            confidence=round(confidence, 4),
            model_version=_loaded.model_version,
            active_model=_loaded.active_model,
            extra={"as_of_ts": as_of_ts},
        )
        return PredictResponse(
            signal="SHORT",
            confidence=round(confidence, 4),
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
        confidence=round(confidence, 4),
        model_version=_loaded.model_version,
        active_model=_loaded.active_model,
        extra={"as_of_ts": as_of_ts},
    )
    return PredictResponse(
        signal="FLAT",
        confidence=round(confidence, 4),
        model_version=_loaded.model_version,
        reasons=[
            f"dead_zone: {PROBA_SHORT} < {proba_up:.4f} < {PROBA_LONG}",
            f"feature_ts={built.feature_ts}",
            as_of_str_final,
        ],
    )
