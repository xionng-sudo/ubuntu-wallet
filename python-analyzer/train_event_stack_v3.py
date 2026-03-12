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

Label encoding:  SHORT=0  FLAT=1  LONG=2

Usage
-----
  python python-analyzer/train_event_stack_v3.py [options]

  Options:
    --data-dir   PATH   default: <repo_root>/data
    --model-dir  PATH   default: <repo_root>/models
    --p-enter    FLOAT  default: 0.65 (stored in model_meta.json)
    --delta      FLOAT  default: 0.0  (stored in model_meta.json)
    --horizon    INT    default: 12  (forward look-ahead bars for label)
    --up-thresh  FLOAT  default: 0.015  (return threshold for LONG label)
    --down-thresh FLOAT default: 0.015  (return threshold for SHORT label)
"""
from __future__ import annotations

import argparse
import json
import os
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
for _d in [ML_SERVICE_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from feature_builder import (  # type: ignore  (resolved at runtime via sys.path)
    build_multi_tf_feature_df,
    get_feature_columns_like_trainer,
)


# ---------------------------------------------------------------------------
# Label creation
# ---------------------------------------------------------------------------

def make_labels(
    df: pd.DataFrame,
    horizon: int = 12,
    up_thresh: float = 0.015,
    down_thresh: float = 0.015,
) -> pd.Series:
    """
    Assign 3-class labels based on forward return over `horizon` bars.

      forward_return = close[t + horizon] / close[t] - 1

      LONG  (2): forward_return >= +up_thresh
      SHORT (0): forward_return <= -down_thresh
      FLAT  (1): otherwise
    """
    fwd_ret = df["close"].shift(-horizon) / df["close"] - 1
    labels = pd.Series(1, index=df.index, dtype=int)  # FLAT
    labels[fwd_ret >= up_thresh] = 2                   # LONG
    labels[fwd_ret <= -down_thresh] = 0                # SHORT
    return labels


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
) -> None:
    print(f"[train_event_v3] data_dir={data_dir}  model_dir={model_dir}")

    # --- build multi-timeframe feature matrix ---
    print("[train_event_v3] building multi-tf features ...")
    merged = build_multi_tf_feature_df(data_dir)
    feature_cols = get_feature_columns_like_trainer(merged)

    # --- create labels (drop last `horizon` rows, they have no forward return) ---
    y_all = make_labels(merged, horizon=horizon, up_thresh=up_thresh, down_thresh=down_thresh)
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

    # --- save artifacts ---
    os.makedirs(model_dir, exist_ok=True)

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

    # model_meta.json
    trained_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _update_model_meta(model_dir, trained_at, p_enter, delta)
    print(f"[train_event_v3] training complete. trained_at={trained_at}")


# ---------------------------------------------------------------------------
# model_meta.json helper
# ---------------------------------------------------------------------------

def _update_model_meta(
    model_dir: str,
    trained_at: str,
    p_enter: float,
    delta: float,
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
    parser.add_argument("--horizon", type=int, default=12,
                        help="Forward look-ahead bars for label generation")
    parser.add_argument("--up-thresh", type=float, default=0.015,
                        help="Forward return threshold for LONG label (fraction, e.g. 0.015 = 1.5%%)")
    parser.add_argument("--down-thresh", type=float, default=0.015,
                        help="Forward return threshold for SHORT label (fraction)")
    args = parser.parse_args()

    train_event_v3(
        data_dir=args.data_dir,
        model_dir=args.model_dir,
        p_enter=args.p_enter,
        delta=args.delta,
        horizon=args.horizon,
        up_thresh=args.up_thresh,
        down_thresh=args.down_thresh,
    )
