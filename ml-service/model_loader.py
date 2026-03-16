from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np


@dataclass
class LoadedModel:
    active_model: str  # stacking|lightgbm|xgboost|event_v3

    # primary/base model (for version hash & expected_n_features)
    name: str
    model: Any
    scaler: Optional[Any]

    feature_columns: List[str]
    trained_at: str
    model_path: str
    scaler_path: Optional[str]
    expected_n_features: Optional[int] = None

    # stacking binary (old)
    stacking_model: Optional[Any] = None
    base_models: Optional[Dict[str, "LoadedModel"]] = None  # {"lightgbm":..., "xgboost":...}

    # event_v3 (3-class); holds runtime thresholds
    event_v3: Optional[Dict[str, Any]] = None  # {"p_enter": float, "delta": float}

    # Calibration artifact (CalibratedModel from calibration.py, if available)
    calibration: Optional[Any] = None

    @property
    def model_version(self) -> str:
        h = file_sha256(self.model_path)[:12] if self.model_path else "no-model"
        return f"{self.active_model}:{self.name}:{self.trained_at}:{h}"


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


def _load_joblib_if_exists(path: str) -> Any:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return joblib.load(path)


def _load_xgb_artifact(model_path: str) -> Tuple[str, Any]:
    """
    Load an XGBoost model artifact.

    Supports two formats:
      - .json  -> XGBoost native Booster format (preferred, no version warnings)
      - .pkl   -> legacy joblib/pickle format (backward compat)

    Returns (kind, model) where kind is "booster_json" or "xgb_sklearn".
    """
    import xgboost as xgb

    if not os.path.exists(model_path):
        raise FileNotFoundError(model_path)

    if model_path.endswith(".json"):
        booster = xgb.Booster()
        booster.load_model(model_path)
        booster._xgb_kind = "booster_json"  # type: ignore[attr-defined]
        return "booster_json", booster

    # Legacy pickle format (backward compat)
    model = joblib.load(model_path)
    if not hasattr(model, "_xgb_kind"):
        model._xgb_kind = "xgb_sklearn"  # type: ignore[attr-defined]
    return "xgb_sklearn", model


def _load_base(
    model_dir: str,
    name: str,
    model_file: str,
    scaler_file: str,
    trained_at: str,
    feature_columns: List[str],
) -> LoadedModel:
    model_path = os.path.join(model_dir, model_file)
    scaler_path = os.path.join(model_dir, scaler_file)

    if name == "xgboost":
        _xgb_kind, model = _load_xgb_artifact(model_path)
        # _xgb_kind attribute is already set on model by _load_xgb_artifact
        exp = getattr(model, "n_features_in_", None)
    else:
        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        model = joblib.load(model_path)
        exp = getattr(model, "n_features_in_", None)

    scaler = None
    if os.path.exists(scaler_path):
        try:
            scaler = joblib.load(scaler_path)
        except Exception:
            scaler = None

    return LoadedModel(
        active_model=name,
        name=name,
        model=model,
        scaler=scaler,
        feature_columns=list(feature_columns) if isinstance(feature_columns, list) else [],
        trained_at=trained_at,
        model_path=model_path,
        scaler_path=scaler_path if os.path.exists(scaler_path) else None,
        expected_n_features=int(exp) if exp is not None else None,
    )


def _try_load_calibration(model_dir: str) -> Any:
    """Silently load calibration artifact; return None if missing or error."""
    try:
        from calibration import load_calibration, default_calibration_path
        return load_calibration(default_calibration_path(model_dir))
    except Exception:
        return None


def load_model(model_dir: str) -> LoadedModel:
    meta = load_meta(model_dir)

    active = str(meta.get("active_model") or "").strip().lower()
    if active not in {"stacking", "lightgbm", "xgboost", "event_v3"}:
        active = ""

    # --- event_v3 (multi-timeframe 3-class stacking) ---
    if active == "event_v3":
        cfg = meta.get("event_v3") if isinstance(meta.get("event_v3"), dict) else {}
        paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}

        feat_file = str(paths.get("feature_columns") or "feature_columns_event_v3.json")
        feat_path = os.path.join(model_dir, feat_file)
        with open(feat_path, "r", encoding="utf-8") as f:
            feature_columns = json.load(f)
        if not isinstance(feature_columns, list) or not feature_columns:
            raise RuntimeError("event_v3 feature_columns file invalid/empty")

        trained_at = str(meta.get("trained_at") or cfg.get("trained_at") or "unknown")

        lgb = _load_base(
            model_dir,
            "lightgbm",
            str(paths.get("lightgbm_model") or "lightgbm_event_v3.pkl"),
            str(paths.get("lightgbm_scaler") or "lightgbm_event_v3_scaler.pkl"),
            trained_at=trained_at,
            feature_columns=feature_columns,
        )
        xgb = _load_base(
            model_dir,
            "xgboost",
            str(paths.get("xgboost_model") or "xgboost_event_v3.json"),
            str(paths.get("xgboost_scaler") or "xgboost_event_v3_scaler.pkl"),
            trained_at=trained_at,
            feature_columns=feature_columns,
        )

        stack_file = str(paths.get("stacking_model") or "stacking_event_v3.pkl")
        stacking_model = _load_joblib_if_exists(os.path.join(model_dir, stack_file))

        # primary = lightgbm (for version hash / expected_n_features)
        lm = LoadedModel(
            active_model="event_v3",
            name="lightgbm",
            model=lgb.model,
            scaler=lgb.scaler,
            feature_columns=feature_columns,
            trained_at=trained_at,
            model_path=lgb.model_path,
            scaler_path=lgb.scaler_path,
            expected_n_features=lgb.expected_n_features,
            stacking_model=stacking_model,
            base_models={"lightgbm": lgb, "xgboost": xgb},
            event_v3={
                "p_enter": float(cfg.get("p_enter", 0.65)),
                "delta": float(cfg.get("delta", 0.0)),
            },
        )
        lm.calibration = _try_load_calibration(model_dir)
        return lm

    # --- legacy stacking binary ---
    if active == "stacking":
        feature_columns = meta.get("feature_columns")
        if not isinstance(feature_columns, list):
            feature_columns = []

        base_models: Dict[str, LoadedModel] = {}
        for n in ["lightgbm", "xgboost"]:
            if n not in meta:
                continue
            cfg_n = meta[n] if isinstance(meta[n], dict) else {}
            trained_at = str(cfg_n.get("trained_at", "unknown"))
            if n == "lightgbm":
                base_models[n] = _load_base(model_dir, "lightgbm", "lightgbm_model.pkl", "lightgbm_scaler.pkl", trained_at, feature_columns)
            else:
                base_models[n] = _load_base(model_dir, "xgboost", "xgboost_model.pkl", "xgboost_scaler.pkl", trained_at, feature_columns)

        stacking_path = os.path.join(model_dir, "stacking_model.pkl")
        stacking_model = _load_joblib_if_exists(stacking_path)

        primary = base_models.get("lightgbm") or base_models.get("xgboost")
        if primary is None:
            raise RuntimeError("stacking active but no base models found")

        lm = LoadedModel(
            active_model="stacking",
            name=primary.name,
            model=primary.model,
            scaler=primary.scaler,
            feature_columns=primary.feature_columns,
            trained_at=str(meta.get("trained_at") or primary.trained_at or "unknown"),
            model_path=primary.model_path,
            scaler_path=primary.scaler_path,
            expected_n_features=primary.expected_n_features,
            stacking_model=stacking_model,
            base_models=base_models,
        )
        lm.calibration = _try_load_calibration(model_dir)
        return lm

    # --- legacy base model (prefer lightgbm) ---
    if "lightgbm" in meta:
        cfg = meta["lightgbm"] if isinstance(meta["lightgbm"], dict) else {}
        feature_columns = meta.get("feature_columns")
        if not isinstance(feature_columns, list):
            feature_columns = cfg.get("features") if isinstance(cfg.get("features"), list) else []
        lm = _load_base(
            model_dir,
            "lightgbm",
            "lightgbm_model.pkl",
            "lightgbm_scaler.pkl",
            trained_at=str(cfg.get("trained_at", "unknown")),
            feature_columns=feature_columns,
        )
        lm.active_model = "lightgbm"
        lm.calibration = _try_load_calibration(model_dir)
        return lm

    if "xgboost" in meta:
        cfg = meta["xgboost"] if isinstance(meta["xgboost"], dict) else {}
        feature_columns = meta.get("feature_columns")
        if not isinstance(feature_columns, list):
            feature_columns = cfg.get("features") if isinstance(cfg.get("features"), list) else []
        xm = _load_base(
            model_dir,
            "xgboost",
            "xgboost_model.pkl",
            "xgboost_scaler.pkl",
            trained_at=str(cfg.get("trained_at", "unknown")),
            feature_columns=feature_columns,
        )
        xm.active_model = "xgboost"
        xm.calibration = _try_load_calibration(model_dir)
        return xm

    raise RuntimeError("model_meta.json has no supported model keys (lightgbm/xgboost/event_v3)")


# ---------------------------------------------------------------------------
# Registry helpers (P0-2)
# ---------------------------------------------------------------------------

def load_registry(model_dir: str) -> Dict[str, Any]:
    """Load models/registry.json. Returns empty dict if not present."""
    path = os.path.join(model_dir, "registry.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_prod_registry_entry(model_dir: str) -> Optional[Dict[str, Any]]:
    """
    Return the 'prod' entry from registry.json, or None if registry is absent
    or no entry has status='prod'.
    """
    reg = load_registry(model_dir)
    entries = reg.get("entries", [])
    prods = [e for e in entries if e.get("status") == "prod"]
    if not prods:
        return None
    return sorted(prods, key=lambda e: e.get("trained_at", ""), reverse=True)[0]


def load_current_pointer(model_dir: str) -> Optional[Dict[str, Any]]:
    """Load models/current.json. Returns None if not present."""
    path = os.path.join(model_dir, "current.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def resolve_current_model_dir(model_dir: str) -> str:
    """
    Resolve the actual artifact directory to load.

    If models/current.json exists, use its relative/absolute path.
    Otherwise fall back to the flat model_dir for backward compatibility.
    """
    pointer = load_current_pointer(model_dir)
    if pointer is None:
        return model_dir

    raw_path = str(pointer.get("path") or "").strip()
    if not raw_path:
        raise RuntimeError("current.json exists but path is empty")

    resolved = raw_path if os.path.isabs(raw_path) else os.path.join(model_dir, raw_path)
    resolved = os.path.abspath(resolved)
    if not os.path.isdir(resolved):
        raise RuntimeError(f"current.json points to missing model dir: {resolved}")
    return resolved


def _resolve_entry_archive_dir(model_dir: str, entry: Dict[str, Any]) -> str:
    archive_dir = str(entry.get("archive_dir") or "").strip()
    if not archive_dir:
        raise RuntimeError("registry prod entry missing archive_dir")
    resolved = archive_dir if os.path.isabs(archive_dir) else os.path.join(model_dir, archive_dir)
    resolved = os.path.abspath(resolved)
    if not os.path.isdir(resolved):
        raise RuntimeError(f"registry prod entry points to missing archive dir: {resolved}")
    return resolved


def load_model_from_registry(model_dir: str) -> "LoadedModel":
    """
    Load the production model referenced by current.json / registry.json.

    Falls back to plain load_model(model_dir) if no current pointer exists,
    which preserves backward compatibility with existing deployments.
    """
    pointer = load_current_pointer(model_dir)
    entry = get_prod_registry_entry(model_dir)

    if pointer is None and entry is None:
        return load_model(model_dir)

    if pointer is None or entry is None:
        raise RuntimeError(
            "production model state is inconsistent: current.json and registry.json must either both exist or both be absent"
        )

    resolved_model_dir = resolve_current_model_dir(model_dir)
    registry_model_dir = _resolve_entry_archive_dir(model_dir, entry)
    if os.path.abspath(resolved_model_dir) != os.path.abspath(registry_model_dir):
        raise RuntimeError(
            "current.json and registry.json disagree on production model directory: "
            f"pointer={resolved_model_dir} registry={registry_model_dir}"
        )

    if pointer is not None and entry is not None:
        pointer_model_version = str(pointer.get("model_version") or "").strip()
        registry_model_version = str(entry.get("model_version") or "").strip()
        if bool(pointer_model_version) != bool(registry_model_version):
            raise RuntimeError(
                "current.json and registry.json have inconsistent production model versions: "
                f"pointer={pointer_model_version!r} registry={registry_model_version!r}"
            )
        if pointer_model_version and registry_model_version and pointer_model_version != registry_model_version:
            raise RuntimeError(
                "current.json and registry.json disagree on production model: "
                f"pointer={pointer_model_version} registry={registry_model_version}"
            )

        loaded_meta = load_meta(resolved_model_dir)
        loaded_meta_model_version = str(loaded_meta.get("model_version") or "").strip()
        if bool(pointer_model_version) != bool(loaded_meta_model_version):
            raise RuntimeError(
                "current.json and loaded model_meta.json have inconsistent production model versions: "
                f"pointer={pointer_model_version!r} loaded_meta={loaded_meta_model_version!r}"
            )
        if pointer_model_version and loaded_meta_model_version and pointer_model_version != loaded_meta_model_version:
            raise RuntimeError(
                "current.json and loaded model_meta.json disagree on production model: "
                f"pointer={pointer_model_version} loaded_meta={loaded_meta_model_version}"
            )

    return load_model(resolved_model_dir)


def _xgb_predict_proba(model: Any, Xs: np.ndarray) -> np.ndarray:
    """
    Predict class probabilities using an XGBoost model.

    Handles both:
      - XGBoost native Booster (loaded from .json) via Booster.predict(DMatrix)
        which returns the full softprob matrix (n, num_class).
      - Legacy sklearn XGBClassifier via .predict_proba()
    """
    import xgboost as xgb

    xgb_kind = getattr(model, "_xgb_kind", "xgb_sklearn")
    if xgb_kind == "booster_json":
        d = xgb.DMatrix(Xs)
        p = model.predict(d)  # softprob → shape (n, num_class)
        return np.asarray(p, dtype=float)

    # sklearn XGBClassifier
    return np.asarray(model.predict_proba(Xs), dtype=float)


def predict_proba(loaded: LoadedModel, x: np.ndarray) -> Tuple[np.ndarray, str]:
    """
    Returns (proba, mode):
      - "proba_binary":     shape (n, 2)
      - "proba_multiclass": shape (n, k)
    """
    # --- event_v3: 3-class stacking based on base proba vectors ---
    if loaded.active_model == "event_v3":
        if loaded.stacking_model is None or not loaded.base_models:
            raise RuntimeError("event_v3 active but stacking_model/base_models missing")

        lgb = loaded.base_models.get("lightgbm")
        xgb_base = loaded.base_models.get("xgboost")
        if lgb is None or xgb_base is None:
            raise RuntimeError("event_v3 requires both lightgbm and xgboost base models")

        # base proba (3-class)
        p_lgb, mode_l = predict_proba(lgb, x)
        p_xgb, mode_x = predict_proba(xgb_base, x)
        if mode_l != "proba_multiclass" or mode_x != "proba_multiclass":
            raise RuntimeError(f"event_v3 base models must be multiclass proba; got {mode_l}, {mode_x}")

        if p_lgb.shape[1] != 3 or p_xgb.shape[1] != 3:
            raise RuntimeError("event_v3 expects 3-class proba from base models")

        feats = np.hstack([p_lgb, p_xgb]).astype(np.float32)

        if not hasattr(loaded.stacking_model, "predict_proba"):
            raise RuntimeError("event_v3 stacking_model has no predict_proba")

        p = np.asarray(loaded.stacking_model.predict_proba(feats))
        return p, "proba_multiclass"

    # --- legacy stacking binary ---
    if loaded.active_model == "stacking":
        if loaded.stacking_model is None or not loaded.base_models:
            raise RuntimeError("stacking active but stacking_model/base_models missing")

        lgb = loaded.base_models.get("lightgbm")
        xgb_base = loaded.base_models.get("xgboost")
        if lgb is None and xgb_base is None:
            raise RuntimeError("stacking active but no base models")

        def _base_up(m: LoadedModel) -> float:
            p, mode = predict_proba(m, x)
            if mode != "proba_binary":
                raise RuntimeError("binary stacking expects binary base proba")
            return float(p[0, 1])

        if lgb is None:
            u = _base_up(xgb_base)
            feats = np.array([[u, u]], dtype=np.float32)
        elif xgb_base is None:
            u = _base_up(lgb)
            feats = np.array([[u, u]], dtype=np.float32)
        else:
            feats = np.array([[_base_up(lgb), _base_up(xgb_base)]], dtype=np.float32)

        p = np.asarray(loaded.stacking_model.predict_proba(feats))
        return p, "proba_binary"

    # --- base model (lightgbm / xgboost / legacy) ---
    xx = x
    if loaded.scaler is not None:
        try:
            xx = loaded.scaler.transform(x)
        except Exception:
            xx = x

    m = loaded.model

    # XGBoost: use dedicated helper that handles both Booster JSON and sklearn wrapper
    if loaded.name == "xgboost":
        p = _xgb_predict_proba(m, xx)
        if p.ndim == 2 and p.shape[1] == 2:
            return p, "proba_binary"
        return p, "proba_multiclass"

    if hasattr(m, "predict_proba"):
        p = np.asarray(m.predict_proba(xx))
        if p.ndim == 2 and p.shape[1] == 2:
            return p, "proba_binary"
        return p, "proba_multiclass"

    raise RuntimeError("model does not support predict_proba")
