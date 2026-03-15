#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
walkforward_cv.py
=================
Walk-forward / rolling time-series cross-validation for the event_v3 model.

Key design choices
------------------
- **No temporal leakage**: each validation fold only uses data that would
  have been available at that point in time.
- **Gap between train and val**: configurable `gap` bars are excluded between
  the end of the training window and the start of the validation window to
  avoid look-ahead from the label horizon.
- **Expanding window** (default) or **rolling window** mode.
- Outputs per-fold metrics:
    AUC (OvR macro), F1 (macro), precision/recall per class,
    Brier score, precision@confidence_threshold, coverage.

Usage (CLI)
-----------
    python python-analyzer/walkforward_cv.py \\
        --data-dir data \\
        --n-splits 5 \\
        --gap-bars 12 \\
        --min-train-bars 500 \\
        --label-method ternary \\
        --horizon 12 \\
        --up-thresh 0.015 \\
        --down-thresh 0.015 \\
        --confidence-threshold 0.65 \\
        --output-csv /tmp/cv_report.csv

Usage (library)
---------------
    from walkforward_cv import run_walkforward_cv, WalkForwardConfig
    results = run_walkforward_cv(df_features, y, WalkForwardConfig())
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional, Dict, Any

import numpy as np
import pandas as pd

# Allow importing from ml-service (feature_builder) and python-analyzer
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
for _d in [ML_SERVICE_DIR, os.path.dirname(__file__)]:
    if _d not in sys.path:
        sys.path.insert(0, _d)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardConfig:
    """Parameters controlling the walk-forward CV procedure."""
    n_splits: int = 5
    gap_bars: int = 12         # bars excluded between train end and val start
    min_train_bars: int = 500  # minimum training window size
    expanding: bool = True     # True = expanding window; False = rolling
    rolling_train_bars: int = 2000  # used when expanding=False
    confidence_threshold: float = 0.65   # for precision@threshold metric
    label_method: str = "ternary"        # "ternary" | "triple_barrier"
    horizon: int = 12
    up_thresh: float = 0.015
    down_thresh: float = 0.015
    tp_pct: float = 0.0175
    sl_pct: float = 0.009


@dataclass
class FoldMetrics:
    fold: int
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    n_train: int
    n_val: int
    label_dist_train: Dict[str, int]
    label_dist_val: Dict[str, int]

    # Classification metrics
    auc_macro: float
    f1_macro: float
    precision_macro: float
    recall_macro: float

    # Per-class metrics
    precision_per_class: Dict[str, float]
    recall_per_class: Dict[str, float]
    f1_per_class: Dict[str, float]

    # Calibration
    brier_score: float

    # High-confidence signal quality
    precision_at_threshold: float  # precision for predicted class when max_proba >= threshold
    coverage: float                # fraction of val samples with max_proba >= threshold

    # Model test accuracy
    accuracy: float


# ---------------------------------------------------------------------------
# Time-series split generator
# ---------------------------------------------------------------------------

def _time_series_splits(
    n: int,
    cfg: WalkForwardConfig,
):
    """
    Yield (train_indices, val_indices) tuples for walk-forward CV.

    Splits n samples into cfg.n_splits folds.
    """
    val_size = max(1, (n - cfg.min_train_bars - cfg.gap_bars) // cfg.n_splits)
    if val_size < 10:
        raise ValueError(
            f"Too few samples ({n}) for {cfg.n_splits} folds with "
            f"min_train_bars={cfg.min_train_bars}. Reduce n_splits or min_train_bars."
        )

    for fold in range(cfg.n_splits):
        val_end = n - fold * val_size
        val_start = val_end - val_size
        if val_start < cfg.min_train_bars + cfg.gap_bars:
            break

        train_end = val_start - cfg.gap_bars
        if cfg.expanding:
            train_start = 0
        else:
            train_start = max(0, train_end - cfg.rolling_train_bars)

        train_idx = np.arange(train_start, train_end)
        val_idx = np.arange(val_start, val_end)

        yield fold, train_idx, val_idx


# ---------------------------------------------------------------------------
# Single fold evaluation
# ---------------------------------------------------------------------------

def _evaluate_fold(
    fold: int,
    X: np.ndarray,
    y: np.ndarray,
    index: pd.Index,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    cfg: WalkForwardConfig,
) -> FoldMetrics:
    from lightgbm import LGBMClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        roc_auc_score,
        f1_score,
        precision_score,
        recall_score,
        brier_score_loss,
        accuracy_score,
    )

    X_tr, y_tr = X[train_idx], y[train_idx]
    X_val, y_val = X[val_idx], y[val_idx]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_val_s = scaler.transform(X_val)

    clf = LGBMClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        num_leaves=31,
        objective="multiclass",
        num_class=3,
        n_jobs=-1,
        random_state=42,
        verbose=-1,
    )
    clf.fit(X_tr_s, y_tr)

    y_pred = clf.predict(X_val_s)
    y_proba = clf.predict_proba(X_val_s)  # shape (n, 3)

    # --- basic metrics ---
    acc = float(accuracy_score(y_val, y_pred))
    f1 = float(f1_score(y_val, y_pred, average="macro", zero_division=0))
    prec_macro = float(precision_score(y_val, y_pred, average="macro", zero_division=0))
    rec_macro = float(recall_score(y_val, y_pred, average="macro", zero_division=0))

    # AUC (need at least 2 classes present)
    classes_present = np.unique(y_val)
    try:
        if len(classes_present) >= 2:
            auc = float(roc_auc_score(y_val, y_proba, multi_class="ovr", average="macro"))
        else:
            auc = float("nan")
    except Exception:
        auc = float("nan")

    # Brier score (average across classes using one-vs-rest)
    try:
        brier_vals = []
        for c in range(3):
            if c in classes_present:
                brier_vals.append(brier_score_loss((y_val == c).astype(int), y_proba[:, c]))
        brier = float(np.mean(brier_vals)) if brier_vals else float("nan")
    except Exception:
        brier = float("nan")

    # Per-class metrics
    prec_per = precision_score(y_val, y_pred, average=None, labels=[0, 1, 2], zero_division=0)
    rec_per = recall_score(y_val, y_pred, average=None, labels=[0, 1, 2], zero_division=0)
    f1_per = f1_score(y_val, y_pred, average=None, labels=[0, 1, 2], zero_division=0)
    class_names = {0: "SHORT", 1: "FLAT", 2: "LONG"}
    prec_per_dict = {class_names[i]: float(prec_per[i]) for i in range(3)}
    rec_per_dict = {class_names[i]: float(rec_per[i]) for i in range(3)}
    f1_per_dict = {class_names[i]: float(f1_per[i]) for i in range(3)}

    # Precision @ confidence threshold
    max_proba = y_proba.max(axis=1)
    high_conf_mask = max_proba >= cfg.confidence_threshold
    coverage = float(high_conf_mask.mean())
    if high_conf_mask.sum() > 0:
        prec_at_thr = float((y_pred[high_conf_mask] == y_val[high_conf_mask]).mean())
    else:
        prec_at_thr = float("nan")

    # Label distributions
    def _dist(arr):
        u, c = np.unique(arr.astype(int), return_counts=True)
        return {class_names.get(int(k), str(k)): int(v) for k, v in zip(u, c)}

    tr_start_ts = str(index[train_idx[0]]) if hasattr(index, "__getitem__") else ""
    tr_end_ts = str(index[train_idx[-1]]) if hasattr(index, "__getitem__") else ""
    val_start_ts = str(index[val_idx[0]]) if hasattr(index, "__getitem__") else ""
    val_end_ts = str(index[val_idx[-1]]) if hasattr(index, "__getitem__") else ""

    return FoldMetrics(
        fold=fold + 1,
        train_start=tr_start_ts,
        train_end=tr_end_ts,
        val_start=val_start_ts,
        val_end=val_end_ts,
        n_train=len(train_idx),
        n_val=len(val_idx),
        label_dist_train=_dist(y_tr),
        label_dist_val=_dist(y_val),
        auc_macro=auc,
        f1_macro=f1,
        precision_macro=prec_macro,
        recall_macro=rec_macro,
        precision_per_class=prec_per_dict,
        recall_per_class=rec_per_dict,
        f1_per_class=f1_per_dict,
        brier_score=brier,
        precision_at_threshold=prec_at_thr,
        coverage=coverage,
        accuracy=acc,
    )


# ---------------------------------------------------------------------------
# Main CV runner
# ---------------------------------------------------------------------------

def run_walkforward_cv(
    X: np.ndarray,
    y: np.ndarray,
    index: pd.Index,
    cfg: WalkForwardConfig,
) -> List[FoldMetrics]:
    """
    Run walk-forward cross-validation.

    Args:
        X:     Feature matrix (n, p), sorted chronologically.
        y:     Integer labels (n,): 0=SHORT, 1=FLAT, 2=LONG.
        index: Pandas DatetimeIndex aligned with X/y rows.
        cfg:   WalkForwardConfig.

    Returns:
        List of FoldMetrics, one per fold (chronological order).
    """
    results: List[FoldMetrics] = []
    splits = list(_time_series_splits(len(X), cfg))
    # reverse so fold 1 = earliest validation set
    splits = list(reversed(splits))

    for enum_i, (fold, train_idx, val_idx) in enumerate(splits):
        fold_num = enum_i + 1
        print(
            f"[walkforward_cv] fold {fold_num}/{len(splits)}: "
            f"train [{train_idx[0]}:{train_idx[-1]}] "
            f"val [{val_idx[0]}:{val_idx[-1]}]",
            flush=True,
        )
        metrics = _evaluate_fold(fold_num - 1, X, y, index, train_idx, val_idx, cfg)
        # override fold number to be 1-indexed chronologically
        metrics_dict = asdict(metrics)
        metrics_dict["fold"] = fold_num
        results.append(FoldMetrics(**metrics_dict))

        print(
            f"  auc={metrics.auc_macro:.4f} f1={metrics.f1_macro:.4f} "
            f"prec={metrics.precision_macro:.4f} rec={metrics.recall_macro:.4f} "
            f"brier={metrics.brier_score:.4f} "
            f"prec@{cfg.confidence_threshold:.2f}={metrics.precision_at_threshold:.4f} "
            f"coverage={metrics.coverage:.3f}",
            flush=True,
        )

    return results


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_cv_summary(results: List[FoldMetrics], cfg: WalkForwardConfig) -> None:
    print("\n" + "=" * 72)
    print(f"Walk-Forward CV Summary  ({len(results)} folds)")
    print(f"  label_method={cfg.label_method}  horizon={cfg.horizon}")
    print(f"  confidence_threshold={cfg.confidence_threshold}")
    print("=" * 72)

    header = (
        f"{'Fold':>4}  {'Val period':>32}  "
        f"{'AUC':>6}  {'F1':>6}  {'Prec':>6}  {'Rec':>6}  "
        f"{'Brier':>6}  {'P@thr':>6}  {'Cov':>5}"
    )
    print(header)
    print("-" * len(header))

    auc_vals, f1_vals, prec_vals, rec_vals = [], [], [], []
    brier_vals, p_thr_vals, cov_vals = [], [], []

    for r in results:
        period = f"{r.val_start[:10]} → {r.val_end[:10]}"
        _auc = r.auc_macro if not np.isnan(r.auc_macro) else float("nan")
        auc_vals.append(_auc)
        f1_vals.append(r.f1_macro)
        prec_vals.append(r.precision_macro)
        rec_vals.append(r.recall_macro)
        brier_vals.append(r.brier_score)
        if not np.isnan(r.precision_at_threshold):
            p_thr_vals.append(r.precision_at_threshold)
        cov_vals.append(r.coverage)

        def _fmt(v):
            return f"{v:.4f}" if not np.isnan(v) else "  NaN"

        print(
            f"{r.fold:>4}  {period:>32}  "
            f"{_fmt(_auc):>6}  {_fmt(r.f1_macro):>6}  "
            f"{_fmt(r.precision_macro):>6}  {_fmt(r.recall_macro):>6}  "
            f"{_fmt(r.brier_score):>6}  {_fmt(r.precision_at_threshold):>6}  "
            f"{r.coverage:.3f}"
        )

    def _mean(lst):
        valid = [v for v in lst if not np.isnan(v)]
        return np.mean(valid) if valid else float("nan")

    print("-" * len(header))
    print(
        f"{'MEAN':>4}  {'':>32}  "
        f"{_mean(auc_vals):.4f}  {_mean(f1_vals):.4f}  "
        f"{_mean(prec_vals):.4f}  {_mean(rec_vals):.4f}  "
        f"{_mean(brier_vals):.4f}  {_mean(p_thr_vals) if p_thr_vals else float('nan'):.4f}  "
        f"{_mean(cov_vals):.3f}"
    )
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Walk-forward time-series CV for event_v3 model",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--gap-bars", type=int, default=12,
                    help="Bars between train end and val start (prevents label leakage)")
    ap.add_argument("--min-train-bars", type=int, default=500)
    ap.add_argument("--expanding", action="store_true", default=True,
                    help="Use expanding window (default). --no-expanding for rolling.")
    ap.add_argument("--no-expanding", dest="expanding", action="store_false")
    ap.add_argument("--rolling-train-bars", type=int, default=2000)
    ap.add_argument("--label-method", choices=["ternary", "triple_barrier"], default="ternary")
    ap.add_argument("--horizon", type=int, default=12)
    ap.add_argument("--up-thresh", type=float, default=0.015)
    ap.add_argument("--down-thresh", type=float, default=0.015)
    ap.add_argument("--tp-pct", type=float, default=0.0175)
    ap.add_argument("--sl-pct", type=float, default=0.009)
    ap.add_argument("--confidence-threshold", type=float, default=0.65)
    ap.add_argument("--output-csv", default=None, help="Path to save per-fold metrics CSV")
    args = ap.parse_args()

    from feature_builder import build_multi_tf_feature_df, get_feature_columns_like_trainer
    from labeling import make_labels, LabelConfig

    cfg = WalkForwardConfig(
        n_splits=args.n_splits,
        gap_bars=args.gap_bars,
        min_train_bars=args.min_train_bars,
        expanding=args.expanding,
        rolling_train_bars=args.rolling_train_bars,
        confidence_threshold=args.confidence_threshold,
        label_method=args.label_method,
        horizon=args.horizon,
        up_thresh=args.up_thresh,
        down_thresh=args.down_thresh,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
    )

    print(f"[walkforward_cv] loading multi-tf features from {args.data_dir} ...")
    merged = build_multi_tf_feature_df(args.data_dir)
    feature_cols = get_feature_columns_like_trainer(merged)

    label_cfg = LabelConfig(
        method=cfg.label_method,
        horizon=cfg.horizon,
        up_thresh=cfg.up_thresh,
        down_thresh=cfg.down_thresh,
        tp_pct=cfg.tp_pct,
        sl_pct=cfg.sl_pct,
    )
    y_all = make_labels(merged, label_cfg)
    valid_mask = y_all.notna()
    merged_v = merged.loc[valid_mask]
    y_v = y_all.loc[valid_mask].astype(int).values
    X_v = merged_v[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).values.astype(np.float32)

    print(f"[walkforward_cv] samples={len(X_v)}, features={len(feature_cols)}")
    unique, counts = np.unique(y_v, return_counts=True)
    print(f"[walkforward_cv] label distribution: { {int(u): int(c) for u, c in zip(unique, counts)} }")

    results = run_walkforward_cv(X_v, y_v, merged_v.index, cfg)
    print_cv_summary(results, cfg)

    if args.output_csv:
        import csv
        import dataclasses
        rows = []
        for r in results:
            d = dataclasses.asdict(r)
            # flatten nested dicts
            for k in ["label_dist_train", "label_dist_val", "precision_per_class",
                      "recall_per_class", "f1_per_class"]:
                nested = d.pop(k, {})
                for nk, nv in nested.items():
                    d[f"{k}_{nk}"] = nv
            rows.append(d)
        if rows:
            with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"[walkforward_cv] saved fold metrics to {args.output_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
