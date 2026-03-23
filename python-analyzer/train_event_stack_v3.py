"""
train_event_stack_v3.py
=======================
Training pipeline for the event_v3 multi-timeframe 3-class stacking model.

Architecture
------------
  Base models (3-class, trained independently):
    1. LightGBM classifier
    2. XGBoost classifier  →  saved as native .json (no version-mismatch warnings)

  Stacking model:
    LogisticRegression trained on out-of-fold base probabilities
    input  : [p_lgb_short, p_lgb_flat, p_lgb_long, p_xgb_short, p_xgb_flat, p_xgb_long]
    output : class probabilities (3-class)

  Calibration (optional):
    Isotonic or Platt scaling fitted on the held-out test set.
    Saved to models/calibration_event_v3.pkl alongside other artifacts.

Label encoding:  SHORT=0  FLAT=1  LONG=2

Usage
-----
  python python-analyzer/train_event_stack_v3.py [options]

  Options:
    --data-dir    PATH    default: <repo_root>/data
    --model-dir   PATH    default: <repo_root>/models
    --p-enter     FLOAT   default: 0.65 (stored in model_meta.json)
    --delta       FLOAT   default: 0.0  (stored in model_meta.json)
    --label-method STRING default: ternary (ternary | triple_barrier)
    --horizon     INT     default: 12  (forward look-ahead bars for label)
    --up-thresh   FLOAT   default: 0.015  (return threshold for LONG label)
    --down-thresh FLOAT   default: 0.015  (return threshold for SHORT label)
    --tp-pct      FLOAT   default: 0.0175 (take-profit for triple_barrier label)
    --sl-pct      FLOAT   default: 0.009  (stop-loss for triple_barrier label)
    --calibration STRING  default: isotonic (isotonic | sigmoid | none)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import List, Tuple

import joblib
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup: add ml-service so feature_builder can be imported directly.
# ml-service/feature_builder.py in turn adds python-analyzer to sys.path
# for TechnicalAnalyzer, so we do NOT need to add it here separately.
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
PY_ANALYZER_DIR = os.path.dirname(__file__)
for _d in [ML_SERVICE_DIR, PY_ANALYZER_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from feature_builder import (  # type: ignore  (resolved at runtime via sys.path)
    build_multi_tf_feature_df,
    get_feature_columns_like_trainer,
)
from labeling import make_labels as _make_labels_dispatch, LabelConfig  # type: ignore


# ---------------------------------------------------------------------------
# Label creation (delegates to labeling.py)
# ---------------------------------------------------------------------------

def make_labels(
    df: pd.DataFrame,
    horizon: int = 12,
    up_thresh: float = 0.015,
    down_thresh: float = 0.015,
    label_method: str = "ternary",
    tp_pct: float = 0.0175,
    sl_pct: float = 0.009,
) -> pd.Series:
    """
    Assign 3-class labels. Delegates to labeling.py.

    label_method="ternary" (default):
        forward_return = close[t + horizon] / close[t] - 1
        LONG  (2): forward_return >= +up_thresh
        SHORT (0): forward_return <= -down_thresh
        FLAT  (1): otherwise

    label_method="triple_barrier":
        Uses TP / SL / horizon barriers aligned with live strategy.
        LONG  (2): TP hit first
        SHORT (0): SL hit first
        FLAT  (1): timeout
    """
    cfg = LabelConfig(
        method=label_method,
        horizon=horizon,
        up_thresh=up_thresh,
        down_thresh=down_thresh,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
    )
    return _make_labels_dispatch(df, cfg)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_event_v3(
    data_dir: str,
    model_dir: str,
    p_enter: float = 0.65,
    delta: float = 0.0,
    horizon: int = 12,
    up_thresh: float = 0.015,
    down_thresh: float = 0.015,
    label_method: str = "ternary",
    tp_pct: float = 0.0175,
    sl_pct: float = 0.009,
    calibration_method: str = "isotonic",
) -> None:
    print(f"[train_event_v3] data_dir={data_dir}  model_dir={model_dir}")
    print(f"[train_event_v3] label_method={label_method}  horizon={horizon}  "
          f"up_thresh={up_thresh}  down_thresh={down_thresh}  "
          f"tp_pct={tp_pct}  sl_pct={sl_pct}")

    # --- build multi-timeframe feature matrix ---
    print("[train_event_v3] building multi-tf features ...")
    merged = build_multi_tf_feature_df(data_dir)
    feature_cols = get_feature_columns_like_trainer(merged)
    n_base = sum(1 for c in feature_cols if not c.startswith(("tf4h_", "tf1d_")))
    n_tf4h = sum(1 for c in feature_cols if c.startswith("tf4h_"))
    n_tf1d = sum(1 for c in feature_cols if c.startswith("tf1d_"))
    print(
        "[train_event_v3] feature groups: "
        f"base_1h={n_base} tf4h={n_tf4h} tf1d={n_tf1d}",
        flush=True,
    )

    # --- create labels ---
    print(f"[train_event_v3] creating labels using method={label_method} ...")
    y_all = make_labels(
        merged,
        horizon=horizon,
        up_thresh=up_thresh,
        down_thresh=down_thresh,
        label_method=label_method,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
    )
    valid_idx = y_all.dropna().index
    merged_valid = merged.loc[valid_idx]
    y = y_all.loc[valid_idx].astype(int)

    X_all = merged_valid[feature_cols].values.astype(np.float32)
    y_arr = y.values

    n_samples = len(X_all)
    n_features = len(feature_cols)
    label_counts = dict(zip(*np.unique(y_arr, return_counts=True)))
    print(f"[train_event_v3] samples={n_samples}, features={n_features}")
    print(f"[train_event_v3] label distribution: {label_counts}")

    if n_samples < 200:
        raise ValueError(f"Too few training samples: {n_samples} (need >= 200)")

    # --- train/test split (time-ordered, no shuffle) ---
    split = int(n_samples * 0.8)
    X_train, X_test = X_all[:split], X_all[split:]
    y_train, y_test = y_arr[:split], y_arr[split:]

    # --- LightGBM base model ---
    from lightgbm import LGBMClassifier
    from sklearn.preprocessing import StandardScaler

    lgb_scaler = StandardScaler()
    X_lgb_train = lgb_scaler.fit_transform(X_train)
    X_lgb_test = lgb_scaler.transform(X_test)

    lgb_model = LGBMClassifier(
        n_estimators=500,
        max_depth=8,
        learning_rate=0.05,
        num_leaves=63,
        objective="multiclass",
        num_class=3,
        n_jobs=-1,
        random_state=42,
        verbose=-1,
    )
    lgb_model.fit(
        X_lgb_train,
        y_train,
        eval_set=[(X_lgb_test, y_test)],
    )
    p_lgb_test = lgb_model.predict_proba(X_lgb_test)
    lgb_test_acc = (lgb_model.predict(X_lgb_test) == y_test).mean()
    print(f"[train_event_v3] lgb test accuracy={lgb_test_acc:.4f}  proba shape={p_lgb_test.shape}")

    # --- XGBoost base model ---
    import xgboost as xgb

    xgb_scaler = StandardScaler()
    X_xgb_train = xgb_scaler.fit_transform(X_train)
    X_xgb_test = xgb_scaler.transform(X_test)

    xgb_model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=8,
        learning_rate=0.05,
        objective="multi:softprob",
        num_class=3,
        n_jobs=-1,
        random_state=42,
        eval_metric="mlogloss",
        verbosity=0,
    )
    xgb_model.fit(
        X_xgb_train,
        y_train,
        eval_set=[(X_xgb_test, y_test)],
        verbose=False,
    )
    p_xgb_test = xgb_model.predict_proba(X_xgb_test)
    xgb_test_acc = (xgb_model.predict(X_xgb_test) == y_test).mean()
    print(f"[train_event_v3] xgb test accuracy={xgb_test_acc:.4f}  proba shape={p_xgb_test.shape}")

    # --- out-of-fold stacking features (proper cross-validation on training set) ---
    print("[train_event_v3] building out-of-fold stacking features ...")
    from sklearn.model_selection import StratifiedKFold

    n_splits = 5
    p_lgb_oof = np.zeros((len(X_train), 3), dtype=np.float32)
    p_xgb_oof = np.zeros((len(X_train), 3), dtype=np.float32)

    skf = StratifiedKFold(n_splits=n_splits, shuffle=False)
    for fold_idx, (tr_idx, val_idx) in enumerate(skf.split(X_train, y_train)):
        x_tr_lgb = X_lgb_train[tr_idx]
        x_val_lgb = X_lgb_train[val_idx]
        x_tr_xgb = X_xgb_train[tr_idx]
        x_val_xgb = X_xgb_train[val_idx]
        y_tr = y_train[tr_idx]
        y_val = y_train[val_idx]

        lgb_fold = LGBMClassifier(
            n_estimators=300,
            max_depth=8,
            learning_rate=0.05,
            num_leaves=63,
            objective="multiclass",
            num_class=3,
            n_jobs=-1,
            random_state=42,
            verbose=-1,
        )
        lgb_fold.fit(x_tr_lgb, y_tr)
        p_lgb_oof[val_idx] = lgb_fold.predict_proba(x_val_lgb)

        xgb_fold = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=8,
            learning_rate=0.05,
            objective="multi:softprob",
            num_class=3,
            n_jobs=-1,
            random_state=42,
            eval_metric="mlogloss",
            verbosity=0,
        )
        xgb_fold.fit(x_tr_xgb, y_tr)
        p_xgb_oof[val_idx] = xgb_fold.predict_proba(x_val_xgb)

        fold_acc = (np.argmax(p_lgb_oof[val_idx], axis=1) == y_val).mean()
        print(f"  fold {fold_idx + 1}/{n_splits}: lgb_val_acc={fold_acc:.4f}")

    stack_X_train = np.hstack([p_lgb_oof, p_xgb_oof]).astype(np.float32)
    stack_X_test = np.hstack([p_lgb_test, p_xgb_test]).astype(np.float32)

    # --- stacking meta-model ---
    from sklearn.linear_model import LogisticRegression

    stack_model = LogisticRegression(
        C=1.0,
        max_iter=1000,
        multi_class="multinomial",
        solver="lbfgs",
        random_state=42,
    )
    stack_model.fit(stack_X_train, y_train)
    stack_test_acc = (stack_model.predict(stack_X_test) == y_test).mean()
    print(f"[train_event_v3] stacking test accuracy={stack_test_acc:.4f}")

    # --- compute test set metrics for metadata ---
    p_stack_test = np.asarray(stack_model.predict_proba(stack_X_test))
    y_pred_test = np.argmax(p_stack_test, axis=1)
    try:
        from sklearn.metrics import f1_score, roc_auc_score, brier_score_loss, precision_score, recall_score
        f1 = float(f1_score(y_test, y_pred_test, average="macro", zero_division=0))
        prec = float(precision_score(y_test, y_pred_test, average="macro", zero_division=0))
        rec = float(recall_score(y_test, y_pred_test, average="macro", zero_division=0))
        try:
            auc = float(roc_auc_score(y_test, p_stack_test, multi_class="ovr", average="macro"))
        except Exception:
            auc = float("nan")
        brier_vals = []
        for c in range(3):
            if c in np.unique(y_test):
                brier_vals.append(brier_score_loss((y_test == c).astype(int), p_stack_test[:, c]))
        brier = float(np.mean(brier_vals)) if brier_vals else float("nan")
        summary_metrics = {
            "test_accuracy": round(float(stack_test_acc), 4),
            "test_f1_macro": round(f1, 4),
            "test_precision_macro": round(prec, 4),
            "test_recall_macro": round(rec, 4),
            "test_auc_macro": round(auc, 4) if not np.isnan(auc) else None,
            "test_brier_score": round(brier, 4) if not np.isnan(brier) else None,
            "n_train": int(len(X_train)),
            "n_test": int(len(X_test)),
        }
        print(
            f"[train_event_v3] test metrics: "
            f"accuracy={stack_test_acc:.4f} f1={f1:.4f} prec={prec:.4f} rec={rec:.4f} "
            f"auc={auc:.4f} brier={brier:.4f}"
        )
    except Exception as e:
        print(f"[train_event_v3] warning: could not compute extended metrics: {e}")
        summary_metrics = {"test_accuracy": round(float(stack_test_acc), 4)}

    # --- save model artifacts ---
    os.makedirs(model_dir, exist_ok=True)

    # --- train feature stats (for drift monitoring) ---
    # Computed from the raw (pre-scaling) training portion of the feature DataFrame
    # so that live feature distributions can be compared against training baselines.
    _train_df = merged_valid.iloc[:split][feature_cols]
    _train_stats: dict = {}
    for _col in feature_cols:
        _col_series = _train_df[_col]
        _n_total = len(_col_series)
        _valid_vals = _col_series.dropna()
        _train_stats[_col] = {
            "mean": round(float(_valid_vals.mean()), 6) if not _valid_vals.empty else 0.0,
            "std": round(float(_valid_vals.std(ddof=0)), 6) if not _valid_vals.empty else 0.0,  # population std
            "missing_rate": round(float(_col_series.isna().sum()) / _n_total, 6) if _n_total > 0 else 0.0,
        }
    _train_stats_path = os.path.join(model_dir, "train_feature_stats.json")
    with open(_train_stats_path, "w", encoding="utf-8") as _f:
        json.dump(_train_stats, _f, indent=2)
    print(f"[train_event_v3] saved {_train_stats_path} ({len(feature_cols)} features)")

    # LightGBM: joblib pkl (no version-mismatch issue for lgb)
    lgb_model_path = os.path.join(model_dir, "lightgbm_event_v3.pkl")
    lgb_scaler_path = os.path.join(model_dir, "lightgbm_event_v3_scaler.pkl")
    joblib.dump(lgb_model, lgb_model_path)
    joblib.dump(lgb_scaler, lgb_scaler_path)
    print(f"[train_event_v3] saved {lgb_model_path}")

    # XGBoost: native Booster JSON (eliminates 'older version configuration' warning)
    xgb_json_path = os.path.join(model_dir, "xgboost_event_v3.json")
    xgb_scaler_path = os.path.join(model_dir, "xgboost_event_v3_scaler.pkl")
    xgb_model.get_booster().save_model(xgb_json_path)
    joblib.dump(xgb_scaler, xgb_scaler_path)
    print(f"[train_event_v3] saved {xgb_json_path} (XGBoost native format)")

    # Stacking LogisticRegression: joblib pkl
    stack_path = os.path.join(model_dir, "stacking_event_v3.pkl")
    joblib.dump(stack_model, stack_path)
    print(f"[train_event_v3] saved {stack_path}")

    # Feature columns list
    feat_col_path = os.path.join(model_dir, "feature_columns_event_v3.json")
    with open(feat_col_path, "w", encoding="utf-8") as f:
        json.dump(feature_cols, f, indent=2)
    print(f"[train_event_v3] saved {feat_col_path} ({len(feature_cols)} columns)")

    # Capture trained_at now so it's consistent across all artifacts
    trained_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # --- fit and save calibration (single block, after trained_at is available) ---
    if calibration_method and calibration_method.lower() != "none":
        try:
            from calibration import fit_calibration, save_calibration, default_calibration_path  # type: ignore
            cal_model = fit_calibration(
                y_true=y_test,
                y_proba=p_stack_test,
                method=calibration_method.lower(),
                base_model_version=f"event_v3:lightgbm:{trained_at}",
            )
            cal_path = default_calibration_path(model_dir)
            save_calibration(cal_model, cal_path)
            print(f"[train_event_v3] saved calibration ({calibration_method}) to {cal_path}")
        except Exception as e:
            print(f"[train_event_v3] warning: calibration failed: {e}")

    _update_model_meta(
        model_dir=model_dir,
        trained_at=trained_at,
        p_enter=p_enter,
        delta=delta,
        label_method=label_method,
        horizon=horizon,
        up_thresh=up_thresh,
        down_thresh=down_thresh,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        calibration_method=calibration_method,
        summary_metrics=summary_metrics,
        n_features=len(feature_cols),
        train_start=str(merged_valid.index[0]),
        test_start=str(merged_valid.index[split]),
        test_end=str(merged_valid.index[-1]),
    )

    _register_model(
        model_dir=model_dir,
        model_version=f"event_v3:lightgbm:{trained_at}",
        trained_at=trained_at,
        label_config={
            "method": label_method,
            "horizon": horizon,
            "up_thresh": up_thresh,
            "down_thresh": down_thresh,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
        },
        threshold_config={"p_enter": p_enter, "delta": delta},
        calibration_method=calibration_method if calibration_method and calibration_method.lower() != "none" else None,
        summary_metrics=summary_metrics,
        train_periods={
            "train_start": str(merged_valid.index[0]),
            "train_end": str(merged_valid.index[split]),
            "val_start": str(merged_valid.index[split]),
            "val_end": str(merged_valid.index[-1]),
        },
        n_features=len(feature_cols),
    )
    print(f"[train_event_v3] training complete. trained_at={trained_at}")


# ---------------------------------------------------------------------------
# model_meta.json helper
# ---------------------------------------------------------------------------

def _update_model_meta(
    model_dir: str,
    trained_at: str,
    p_enter: float,
    delta: float,
    label_method: str = "ternary",
    horizon: int = 12,
    up_thresh: float = 0.015,
    down_thresh: float = 0.015,
    tp_pct: float = 0.0175,
    sl_pct: float = 0.009,
    calibration_method: str = "isotonic",
    summary_metrics: dict = None,
    n_features: int = 0,
    train_start: str = "",
    test_start: str = "",
    test_end: str = "",
) -> None:
    meta_path = os.path.join(model_dir, "model_meta.json")
    meta: dict = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    meta["active_model"] = "event_v3"
    meta["trained_at"] = trained_at
    meta["model_version"] = f"event_v3:lightgbm:{trained_at}"
    meta["feature_schema_version"] = "multi_tf_v1"
    meta["n_features"] = n_features

    meta["label_config"] = {
        "method": label_method,
        "horizon": horizon,
        "up_thresh": up_thresh,
        "down_thresh": down_thresh,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
    }

    meta["threshold_config"] = {
        "p_enter": p_enter,
        "delta": delta,
    }

    meta["calibration_info"] = {
        "method": calibration_method if calibration_method and calibration_method.lower() != "none" else None,
        "artifact": "calibration_event_v3.pkl" if calibration_method and calibration_method.lower() != "none" else None,
    }

    meta["train_periods"] = {
        "train_start": train_start,
        "train_end": test_start,
        "val_start": test_start,
        "val_end": test_end,
    }

    if summary_metrics:
        meta["summary_metrics"] = summary_metrics

    meta["event_v3"] = {
        "trained_at": trained_at,
        "p_enter": p_enter,
        "delta": delta,
        "paths": {
            "lightgbm_model": "lightgbm_event_v3.pkl",
            "lightgbm_scaler": "lightgbm_event_v3_scaler.pkl",
            # XGBoost stored as native JSON → no pickle version warnings
            "xgboost_model": "xgboost_event_v3.json",
            "xgboost_scaler": "xgboost_event_v3_scaler.pkl",
            "stacking_model": "stacking_event_v3.pkl",
            "feature_columns": "feature_columns_event_v3.json",
        },
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"[train_event_v3] updated {meta_path}")


# ---------------------------------------------------------------------------
# Model registry helper
# ---------------------------------------------------------------------------

# Artifact files to archive (copy from model_dir to archive/<version>/)
_ARTIFACT_FILES = [
    "model_meta.json",
    "lightgbm_event_v3.pkl",
    "lightgbm_event_v3_scaler.pkl",
    "xgboost_event_v3.json",
    "xgboost_event_v3_scaler.pkl",
    "stacking_event_v3.pkl",
    "feature_columns_event_v3.json",
    "calibration_event_v3.pkl",
    "train_feature_stats.json",
]


def _promote_to_current(model_dir: str, archive_abs: str) -> None:
    """
    Promote the newly archived model to models/current/.

    Replaces the contents of models/current/ with a fresh copy of the archive,
    so ml-service can load from models/current/ directly without any JSON
    pointer resolution.
    """
    current_dir = os.path.join(model_dir, "current")
    if os.path.isdir(current_dir):
        shutil.rmtree(current_dir)
    shutil.copytree(archive_abs, current_dir)
    print(f"[train_event_v3] promoted {archive_abs} -> {current_dir}")


def _register_model(
    model_dir: str,
    model_version: str,
    trained_at: str,
    label_config: dict,
    threshold_config: dict,
    calibration_method: str | None,
    summary_metrics: dict | None,
    train_periods: dict,
    n_features: int,
) -> None:
    """
    Archive the newly trained model artifacts and update models/registry.json.

    - Copies current model artifacts to models/archive/<sanitized_version>/
    - Marks all previous 'prod' entries as 'archived'
    - Appends a new 'prod' entry for the new model
    """
    # Sanitize version string for use as directory name
    safe_ver = trained_at.replace(":", "").replace("+", "").replace(" ", "T")
    archive_rel = f"archive/event_v3-{safe_ver}"
    archive_abs = os.path.join(model_dir, archive_rel)
    os.makedirs(archive_abs, exist_ok=True)

    # Copy artifact files to archive
    archived_files = []
    for fname in _ARTIFACT_FILES:
        src = os.path.join(model_dir, fname)
        if os.path.exists(src):
            dst = os.path.join(archive_abs, fname)
            shutil.copy2(src, dst)
            archived_files.append(fname)
    print(f"[train_event_v3] archived {len(archived_files)} artifacts to {archive_abs}")

    # Load or initialize registry
    registry_path = os.path.join(model_dir, "registry.json")
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = {}
    else:
        registry = {}

    entries: list = registry.get("entries", [])

    # Mark all previous 'prod' entries as 'archived'
    for entry in entries:
        if entry.get("status") == "prod":
            entry["status"] = "archived"

    # Append new prod entry
    entries.append(
        {
            "model_version": model_version,
            "trained_at": trained_at,
            "status": "prod",
            "archive_dir": archive_rel,
            "label_config": label_config,
            "threshold_config": threshold_config,
            "calibration_method": calibration_method,
            "summary_metrics": summary_metrics,
            "n_features": n_features,
            "train_periods": train_periods,
            "created_at": trained_at,
        }
    )

    registry["entries"] = entries
    registry["updated_at"] = trained_at

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    print(f"[train_event_v3] updated registry.json ({len(entries)} entries, current prod: {model_version})")
    _promote_to_current(model_dir=model_dir, archive_abs=archive_abs)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train event_v3 multi-tf stacking model")
    parser.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"),
                        help="Directory containing klines_1h.json, klines_4h.json, klines_1d.json")
    parser.add_argument("--model-dir", default=os.path.join(REPO_ROOT, "models"),
                        help="Output directory for model artifacts")
    parser.add_argument("--p-enter", type=float, default=0.65,
                        help="Minimum per-class probability to enter (stored in model_meta.json)")
    parser.add_argument("--delta", type=float, default=0.0,
                        help="Minimum margin (p_long - p_short) required (stored in model_meta.json)")
    parser.add_argument("--label-method", choices=["ternary", "triple_barrier"], default="ternary",
                        help="Label generation method: 'ternary' (forward return) or 'triple_barrier' (TP/SL/horizon)")
    parser.add_argument("--horizon", type=int, default=12,
                        help="Forward look-ahead bars for label generation")
    parser.add_argument("--up-thresh", type=float, default=0.015,
                        help="Forward return threshold for LONG label (fraction, e.g. 0.015 = 1.5%%). Used by ternary method.")
    parser.add_argument("--down-thresh", type=float, default=0.015,
                        help="Forward return threshold for SHORT label (fraction). Used by ternary method.")
    parser.add_argument("--tp-pct", type=float, default=0.0175,
                        help="Take-profit fraction for triple_barrier label (e.g. 0.0175 = 1.75%%). Used by triple_barrier method.")
    parser.add_argument("--sl-pct", type=float, default=0.009,
                        help="Stop-loss fraction for triple_barrier label (e.g. 0.009 = 0.9%%). Used by triple_barrier method.")
    parser.add_argument("--calibration", choices=["isotonic", "sigmoid", "none"], default="isotonic",
                        help="Probability calibration method to apply after training. Use 'none' to skip.")
    args = parser.parse_args()

    train_event_v3(
        data_dir=args.data_dir,
        model_dir=args.model_dir,
        p_enter=args.p_enter,
        delta=args.delta,
        horizon=args.horizon,
        up_thresh=args.up_thresh,
        down_thresh=args.down_thresh,
        label_method=args.label_method,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        calibration_method=args.calibration,
    )
