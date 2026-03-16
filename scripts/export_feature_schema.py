#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_feature_schema.py
========================
Export and validate the event_v3 feature schema from a trained model directory.

Purpose
-------
The training pipeline saves feature_columns_event_v3.json to the model directory.
The online inference pipeline (ml-service/feature_builder.py) reads the same file
to align the feature vector with training.

This script:
  1. Loads the schema from models/feature_columns_event_v3.json
  2. Optionally rebuilds the schema from klines data (--rebuild flag)
  3. Validates consistency between the file and a live data build
  4. Writes a canonical schema copy to --output if specified

Usage
-----
  # Print schema info (validate existing schema file):
  python scripts/export_feature_schema.py --model-dir models

  # Rebuild schema from klines data and compare to saved schema:
  python scripts/export_feature_schema.py \
    --model-dir models \
    --data-dir data \
    --rebuild

  # Export schema to a named file for reference:
  python scripts/export_feature_schema.py \
    --model-dir models \
    --output models/feature_schema_export.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
PY_ANALYZER_DIR = os.path.join(REPO_ROOT, "python-analyzer")
for _d in [ML_SERVICE_DIR, PY_ANALYZER_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _load_saved_schema(model_dir: str) -> List[str]:
    path = os.path.join(model_dir, "feature_columns_event_v3.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Schema file not found: {path}\n"
            "Train a model first: python python-analyzer/train_event_stack_v3.py"
        )
    with open(path, "r", encoding="utf-8") as f:
        cols = json.load(f)
    if not isinstance(cols, list) or not cols:
        raise ValueError(f"Invalid schema file (empty or not a list): {path}")
    return [str(c) for c in cols]


def _rebuild_schema_from_data(data_dir: str) -> List[str]:
    """Build feature columns from klines data using the same pipeline as training."""
    from feature_builder import build_multi_tf_feature_df, get_feature_columns_like_trainer  # type: ignore

    print("[export_schema] building multi-tf feature matrix from klines ...", flush=True)
    merged = build_multi_tf_feature_df(data_dir)
    cols = get_feature_columns_like_trainer(merged)
    print(f"[export_schema] rebuilt schema has {len(cols)} columns", flush=True)
    return cols


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export and validate event_v3 feature schema"
    )
    ap.add_argument(
        "--model-dir",
        default=os.path.join(REPO_ROOT, "models"),
        help="Model directory containing feature_columns_event_v3.json (default: <repo_root>/models)",
    )
    ap.add_argument(
        "--data-dir",
        default=None,
        help="klines data directory (required for --rebuild; default: <repo_root>/data)",
    )
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild schema from klines data and compare to saved schema",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Write exported schema JSON to this file path",
    )
    args = ap.parse_args()

    model_dir = os.path.abspath(args.model_dir)
    data_dir = os.path.abspath(args.data_dir) if args.data_dir else os.path.join(REPO_ROOT, "data")

    # 1. Load saved schema
    try:
        saved_cols = _load_saved_schema(model_dir)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", flush=True)
        return 2

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"\n{'='*60}")
    print(f"EVENT_V3 FEATURE SCHEMA  [{now}]")
    print(f"  model_dir  : {model_dir}")
    print(f"  schema_file: feature_columns_event_v3.json")
    print(f"  n_features : {len(saved_cols)}")
    print(f"\n  1h base features   : {sum(1 for c in saved_cols if not c.startswith(('tf4h_', 'tf1d_')))}")
    print(f"  4h features (tf4h_): {sum(1 for c in saved_cols if c.startswith('tf4h_'))}")
    print(f"  1d features (tf1d_): {sum(1 for c in saved_cols if c.startswith('tf1d_'))}")
    print(f"{'='*60}")

    # 2. Rebuild and compare (optional)
    if args.rebuild:
        try:
            rebuilt_cols = _rebuild_schema_from_data(data_dir)
        except Exception as e:
            print(f"\nERROR rebuilding schema: {e}", flush=True)
            return 2

        saved_set = set(saved_cols)
        rebuilt_set = set(rebuilt_cols)
        missing_from_rebuilt = sorted(saved_set - rebuilt_set)
        extra_in_rebuilt = sorted(rebuilt_set - saved_set)

        print(f"\n{'='*60}")
        print("SCHEMA CONSISTENCY CHECK")
        print(f"  saved schema  : {len(saved_cols)} columns")
        print(f"  rebuilt schema: {len(rebuilt_cols)} columns")
        if not missing_from_rebuilt and not extra_in_rebuilt:
            print("  RESULT: CONSISTENT ✓  (saved schema matches rebuilt schema)")
        else:
            print(f"  RESULT: DRIFT DETECTED")
            if missing_from_rebuilt:
                print(f"  In saved but NOT in rebuilt ({len(missing_from_rebuilt)}): "
                      f"{missing_from_rebuilt[:10]}")
            if extra_in_rebuilt:
                print(f"  In rebuilt but NOT in saved ({len(extra_in_rebuilt)}): "
                      f"{extra_in_rebuilt[:10]}")
        print(f"{'='*60}")

        if missing_from_rebuilt or extra_in_rebuilt:
            return 1

    # 3. Write output (optional)
    if args.output:
        out_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        schema_doc = {
            "exported_at": now,
            "model_dir": model_dir,
            "n_features": len(saved_cols),
            "feature_columns": saved_cols,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(schema_doc, f, indent=2)
        print(f"\nSchema exported to: {out_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
