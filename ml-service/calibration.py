#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibration.py
==============
Probability calibration layer for ml-service.

Supports isotonic regression and Platt scaling (sigmoid calibration).
Calibration artifacts are saved alongside model artifacts and loaded at
inference time.

Exported probability → calibrated probability improves decision thresholding
reliability (i.e. "a model confidence of 0.70 is genuinely closer to a 70%
true precision").

Public API
----------
    fit_calibration(y_true, y_proba, method, class_idx) → CalibratedModel
    calibrate_proba(y_proba, calibrated_model)           → calibrated np.ndarray
    save_calibration(calibrated_model, path)
    load_calibration(path)                               → CalibratedModel | None

CalibratedModel holds per-class isotonic/sigmoid calibrators (one-vs-rest).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import joblib
import numpy as np


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class CalibratedModel:
    """Holds per-class calibrators fitted with one-vs-rest strategy."""
    method: str                          # "isotonic" | "sigmoid"
    n_classes: int                       # 2 for binary, 3 for event_v3
    calibrators: List[Any]               # one calibrator per class (list of n_classes)
    trained_at: str
    base_model_version: str
    # per_class_methods stores the actual method used for each class after any
    # automatic fallback (e.g. isotonic → sigmoid when isotonic is degenerate).
    # None means all classes used ``method``.  Added in v2; defaults to None so
    # that previously-serialised artifacts continue to load without error.
    per_class_methods: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def fit_calibration(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    method: str = "isotonic",
    base_model_version: str = "unknown",
) -> CalibratedModel:
    """
    Fit a one-vs-rest calibration model.

    Args:
        y_true:   Integer labels array (n,). For 3-class: 0=SHORT 1=FLAT 2=LONG.
        y_proba:  Raw model probabilities (n, n_classes).
        method:   "isotonic" or "sigmoid".
        base_model_version: Version string of the model being calibrated.

    Returns:
        Fitted CalibratedModel.

    Notes:
        When ``method="isotonic"``, the fitter automatically detects if the
        fitted isotonic function is degenerate (i.e. its output range on the
        training data is less than 1e-6, meaning it maps all inputs to the same
        constant).  In that case it silently falls back to sigmoid (Platt
        scaling) for that class and records the actual method used in
        ``CalibratedModel.per_class_methods``.  This prevents the calibrated
        probabilities from becoming a fixed constant value at inference time
        while preserving accuracy.
    """
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from datetime import datetime, timezone

    n_classes = y_proba.shape[1]
    calibrators = []
    per_class_methods: List[str] = []

    for cls in range(n_classes):
        y_bin = (y_true == cls).astype(int)
        p_cls = y_proba[:, cls]

        if method == "isotonic":
            cal = IsotonicRegression(out_of_bounds="clip")
            cal.fit(p_cls, y_bin)
            # Detect degenerate isotonic calibrator.  There are two conditions
            # that cause constant output at inference time:
            #
            # 1. The training probabilities are concentrated in a very narrow
            #    band (< 0.01).  Even if PAVA finds a slightly varying solution
            #    within that band, new inference inputs will land in the same
            #    narrow zone and produce effectively constant output.
            #
            # 2. The fitted mapping output range on training data is already
            #    essentially zero (PAVA pooled everything into one block).
            #
            # In either case fall back to sigmoid (Platt scaling), which is
            # a parametric fit that always produces a non-constant monotone
            # mapping regardless of how narrow the input distribution is.
            if np.ptp(p_cls) < 0.01 or np.ptp(cal.predict(p_cls)) < 1e-6:
                cal = LogisticRegression(C=1e10, solver="lbfgs", max_iter=500)
                cal.fit(p_cls.reshape(-1, 1), y_bin)
                per_class_methods.append("sigmoid")
            else:
                per_class_methods.append("isotonic")
        elif method == "sigmoid":
            # Platt scaling: fit logistic regression directly on raw probabilities.
            # LogisticRegression internally learns the sigmoid mapping (scale/bias).
            cal = LogisticRegression(C=1e10, solver="lbfgs", max_iter=500)
            cal.fit(p_cls.reshape(-1, 1), y_bin)
            per_class_methods.append("sigmoid")
        else:
            raise ValueError(f"Unknown calibration method: {method!r}. Use 'isotonic' or 'sigmoid'.")

        calibrators.append(cal)

    trained_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return CalibratedModel(
        method=method,
        n_classes=n_classes,
        calibrators=calibrators,
        trained_at=trained_at,
        base_model_version=base_model_version,
        per_class_methods=per_class_methods,
    )


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def calibrate_proba(
    y_proba: np.ndarray,
    cal_model: CalibratedModel,
) -> np.ndarray:
    """
    Apply calibration to raw probabilities.

    Args:
        y_proba:   Raw probabilities (n, n_classes).
        cal_model: Fitted CalibratedModel.

    Returns:
        Calibrated probabilities (n, n_classes), rows sum approximately to 1
        after normalisation.

    Notes:
        Each class may use a different underlying calibrator (isotonic or
        sigmoid) when ``cal_model.per_class_methods`` is set.  This is the
        case when ``fit_calibration`` automatically fell back from a degenerate
        isotonic fit to sigmoid for one or more classes.  Older artifacts that
        do not carry ``per_class_methods`` (``None``) continue to work: the
        global ``cal_model.method`` is used for every class.
    """
    cal_proba = np.zeros_like(y_proba, dtype=np.float64)

    for cls, cal in enumerate(cal_model.calibrators):
        p_cls = y_proba[:, cls]
        # Determine the actual method for this class.
        if cal_model.per_class_methods is not None and cls < len(cal_model.per_class_methods):
            cls_method = cal_model.per_class_methods[cls]
        else:
            cls_method = cal_model.method

        if cls_method == "isotonic":
            cal_proba[:, cls] = cal.predict(p_cls)
        elif cls_method == "sigmoid":
            cal_proba[:, cls] = cal.predict_proba(p_cls.reshape(-1, 1))[:, 1]
        else:
            # Unknown method stored in artifact: pass through raw probability.
            cal_proba[:, cls] = p_cls

    # normalise rows so they sum to 1
    row_sums = cal_proba.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums == 0, 1.0, row_sums)
    cal_proba = cal_proba / row_sums

    return cal_proba.astype(np.float32)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_calibration(cal_model: CalibratedModel, path: str) -> None:
    """
    Save calibration model to disk.

    Saves two files:
      <path>.pkl          - joblib-serialised CalibratedModel
      <path>_meta.json    - human-readable metadata
    """
    joblib.dump(cal_model, path)
    meta = {
        "method": cal_model.method,
        "n_classes": cal_model.n_classes,
        "trained_at": cal_model.trained_at,
        "base_model_version": cal_model.base_model_version,
    }
    meta_path = path.replace(".pkl", "_meta.json")
    if not meta_path.endswith("_meta.json"):
        meta_path = path + "_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def load_calibration(path: str) -> Optional[CalibratedModel]:
    """
    Load a previously saved CalibratedModel.

    Returns None (no error) if the file does not exist, so callers can
    degrade gracefully to raw probabilities.
    """
    if not os.path.exists(path):
        return None
    try:
        cal = joblib.load(path)
        if isinstance(cal, CalibratedModel):
            return cal
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helper: default calibration artifact path
# ---------------------------------------------------------------------------

def default_calibration_path(model_dir: str) -> str:
    """Return the expected path for the event_v3 calibration artifact."""
    return os.path.join(model_dir, "calibration_event_v3.pkl")
