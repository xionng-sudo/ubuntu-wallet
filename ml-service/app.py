from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from feature_builder import build_latest_feature_row_from_klines
from model_loader import LoadedModel, load_model, predict_proba

MODEL_DIR = os.getenv("MODEL_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models")))
DATA_DIR = os.getenv("DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data")))

PROBA_LONG = float(os.getenv("ML_PROBA_LONG", "0.55"))
PROBA_SHORT = float(os.getenv("ML_PROBA_SHORT", "0.45"))

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


app = FastAPI(title="ubuntu-wallet ml-service", version="klines-featurebuilder-v2-asof")


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

    try:
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

    if mode != "proba_binary":
        raise HTTPException(status_code=500, detail=f"unsupported_predict_mode: {mode}")

    proba_up = float(p[0, 1])

    if proba_up >= PROBA_LONG:
        return PredictResponse(
            signal="LONG",
            confidence=round(proba_up, 4),
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}>= {PROBA_LONG}",
                f"feature_ts={built.feature_ts}",
                f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest",
            ],
        )

    if proba_up <= PROBA_SHORT:
        return PredictResponse(
            signal="SHORT",
            confidence=round(1.0 - proba_up, 4),
            model_version=_loaded.model_version,
            reasons=[
                f"proba_up={proba_up:.4f}<= {PROBA_SHORT}",
                f"feature_ts={built.feature_ts}",
                f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest",
            ],
        )

    return PredictResponse(
        signal="FLAT",
        confidence=round(max(proba_up, 1.0 - proba_up), 4),
        model_version=_loaded.model_version,
        reasons=[
            f"dead_zone: {PROBA_SHORT} < {proba_up:.4f} < {PROBA_LONG}",
            f"feature_ts={built.feature_ts}",
            f"as_of_ts={as_of_ts}" if as_of_ts else "as_of_ts=latest",
        ],
    )
