from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np


@dataclass
class LoadedModel:
    name: str  # lightgbm|xgboost
    model: Any
    scaler: Optional[Any]
    feature_columns: List[str]  # meta features (may be incomplete)
    trained_at: str
    model_path: str
    scaler_path: Optional[str]
    expected_n_features: Optional[int] = None  # derived from model.n_features_in_

    @property
    def model_version(self) -> str:
        h = file_sha256(self.model_path)[:12]
        return f"{self.name}:{self.trained_at}:{h}"


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta(model_dir: str) -> Dict[str, Any]:
    meta_path = os.path.join(model_dir, "model_meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def pick_model(meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if "lightgbm" in meta:
        return "lightgbm", meta["lightgbm"]
    if "xgboost" in meta:
        return "xgboost", meta["xgboost"]
    raise RuntimeError("model_meta.json has no supported model keys (lightgbm/xgboost)")


def load_model(model_dir: str) -> LoadedModel:
    meta = load_meta(model_dir)
    name, cfg = pick_model(meta)

    features = cfg.get("features") or []
    trained_at = cfg.get("trained_at", "unknown")

    if name == "lightgbm":
        model_path = os.path.join(model_dir, "lightgbm_model.pkl")
        scaler_path = os.path.join(model_dir, "lightgbm_scaler.pkl")
    else:
        model_path = os.path.join(model_dir, "xgboost_model.pkl")
        scaler_path = os.path.join(model_dir, "xgboost_scaler.pkl")

    if not os.path.exists(model_path):
        raise FileNotFoundError(model_path)

    model = joblib.load(model_path)

    scaler = None
    if os.path.exists(scaler_path):
        try:
            scaler = joblib.load(scaler_path)
        except Exception:
            scaler = None

    exp = getattr(model, "n_features_in_", None)

    return LoadedModel(
        name=name,
        model=model,
        scaler=scaler,
        feature_columns=list(features) if isinstance(features, list) else [],
        trained_at=trained_at,
        model_path=model_path,
        scaler_path=scaler_path if os.path.exists(scaler_path) else None,
        expected_n_features=int(exp) if exp is not None else None,
    )


def predict_proba(loaded: LoadedModel, x: np.ndarray) -> Tuple[np.ndarray, str]:
    """
    Returns (proba, mode)
      - "proba_binary": proba_up is proba[:,1]
      - "proba_multiclass": softmax matrix
      - "regression": 1-col score
    """
    xx = x
    if loaded.scaler is not None:
        try:
            xx = loaded.scaler.transform(x)
        except Exception:
            xx = x

    m = loaded.model

    if hasattr(m, "predict_proba"):
        p = m.predict_proba(xx)
        p = np.asarray(p)
        if p.ndim == 2 and p.shape[1] == 2:
            return p, "proba_binary"
        return p, "proba_multiclass"

    if hasattr(m, "predict"):
        y = m.predict(xx)
        y = np.asarray(y).reshape(-1, 1)
        return y, "regression"

    raise RuntimeError("model does not support predict_proba or predict")
