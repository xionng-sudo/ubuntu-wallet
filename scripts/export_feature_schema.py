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
  2. Optionally rebuilds/checks the schema from klines data using the same training /
     walk-forward feature path (--rebuild flag)
  3. Optionally validates the online inference row contract using
     build_event_v3_feature_row() (--validate-inference-row)
  4. Writes a canonical schema copy to --output if specified

Rebuild strictness (as requested)
--------------------------------
- STRICT on missing: if any column in saved schema is not produced by the rebuild
  pipeline, exit code is 1.
- NON-STRICT on extra: columns produced by rebuild but not present in saved schema
  are reported, but do NOT cause failure. (Online inference will drop them anyway.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def _rebuild_schema_from_data(
    data_dir: str,
    saved_cols: List[str],
) -> Tuple[List[str], List[str], List[str], int]:
    """
    Rebuild/check schema against the saved trainer schema.

    Returns:
        rebuilt_cols: columns that exist in BOTH saved schema and rebuilt merged df,
                      in the same order as saved_cols (canonical).
        missing_from_rebuilt: columns present in saved schema but not produced by rebuild pipeline.
        extra_in_rebuilt: columns produced by rebuild pipeline but not present in saved schema.
        n_merged_cols: total columns in rebuilt merged df.
    """
    from feature_builder import build_multi_tf_feature_df  # type: ignore

    print("[export_schema] building multi-tf feature matrix from klines ...", flush=True)
    merged = build_multi_tf_feature_df(data_dir)
    if merged is None or merged.empty:
        raise ValueError("rebuilt merged feature df is empty")

    merged_cols = [str(c) for c in list(merged.columns)]
    merged_set = set(merged_cols)
    saved_set = set(saved_cols)

    # Canonical "rebuilt schema" should follow saved schema ordering.
    rebuilt_cols = [c for c in saved_cols if c in merged_set]

    missing_from_rebuilt = [c for c in saved_cols if c not in merged_set]
    extra_in_rebuilt = sorted([c for c in merged_cols if c not in saved_set])

    print(f"[export_schema] rebuilt merged df has {len(merged_cols)} columns", flush=True)
    print(f"[export_schema] rebuilt schema (intersection, saved order) has {len(rebuilt_cols)} columns", flush=True)
    return rebuilt_cols, missing_from_rebuilt, extra_in_rebuilt, len(merged_cols)


def _validate_inference_row(data_dir: str, model_dir: str, saved_cols: List[str]) -> Dict[str, Any]:
    """Validate build_event_v3_feature_row() against the saved schema."""
    from feature_builder import build_event_v3_feature_row  # type: ignore

    built = build_event_v3_feature_row(
        data_dir=data_dir,
        model_dir=model_dir,
        expected_n_features=len(saved_cols),
    )
    same_columns = list(built.feature_columns) == list(saved_cols)
    x_shape_ok = tuple(built.X_row.shape) == (1, len(saved_cols))
    schema_validation = built.schema_validation.to_dict() if built.schema_validation is not None else None
    return {
        "feature_ts": built.feature_ts,
        "same_columns": same_columns,
        "x_shape_ok": x_shape_ok,
        "x_shape": list(built.X_row.shape),
        "schema_validation": schema_validation,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Export and validate event_v3 feature schema")
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
        help="Rebuild/check schema from klines data and compare to saved schema "
             "(strict on missing; non-strict on extra)",
    )
    ap.add_argument(
        "--validate-inference-row",
        action="store_true",
        help="Validate that build_event_v3_feature_row() produces a 1xN row aligned to the saved schema",
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

    validation_summary: Dict[str, Any] = {}

    # 2. Rebuild and compare (optional)
    if args.rebuild:
        try:
            rebuilt_cols, missing_from_rebuilt, extra_in_rebuilt, n_merged_cols = _rebuild_schema_from_data(
                data_dir,
                saved_cols,
            )
        except Exception as e:
            print(f"\nERROR rebuilding schema: {e}", flush=True)
            return 2

        print(f"\n{'='*60}")
        print("SCHEMA CONSISTENCY CHECK")
        print(f"  saved schema  : {len(saved_cols)} columns")
        print(f"  rebuilt merged: {n_merged_cols} columns")
        print(f"  rebuilt schema: {len(rebuilt_cols)} columns (intersection, saved order)")

        if not missing_from_rebuilt:
            if extra_in_rebuilt:
                print("  RESULT: CONSISTENT ✓  (all saved columns exist in rebuild; extras present but allowed)")
            else:
                print("  RESULT: CONSISTENT ✓  (saved schema matches rebuild pipeline output)")
        else:
            print("  RESULT: DRIFT DETECTED")

        if missing_from_rebuilt:
            print(
                f"  In saved but NOT in rebuilt ({len(missing_from_rebuilt)}): "
                f"{missing_from_rebuilt[:10]}"
            )
        if extra_in_rebuilt:
            print(
                f"  In rebuilt but NOT in saved ({len(extra_in_rebuilt)}): "
                f"{extra_in_rebuilt[:10]}"
            )
        print(f"{'='*60}")

        # Strict on missing only (as requested)
        if missing_from_rebuilt:
            return 1

        validation_summary["train_walkforward_rebuild"] = {
            "all_saved_columns_present": True,
            "n_features_saved": len(saved_cols),
            "n_features_intersection": len(rebuilt_cols),
            "n_merged_columns": n_merged_cols,
            "n_extra_columns": len(extra_in_rebuilt),
        }

    # 3. Validate inference row contract (optional)
    if args.validate_inference_row:
        try:
            inference_check = _validate_inference_row(data_dir, model_dir, saved_cols)
        except Exception as e:
            print(f"\nERROR validating inference row: {e}", flush=True)
            return 2

        print(f"\n{'='*60}")
        print("INFERENCE ROW CONTRACT CHECK")
        print(f"  feature_ts        : {inference_check['feature_ts']}")
        print(f"  columns_match     : {inference_check['same_columns']}")
        print(f"  x_row_shape       : {tuple(inference_check['x_shape'])}")
        print(f"  expected_x_shape  : {(1, len(saved_cols))}")
        schema_validation = inference_check.get("schema_validation") or {}
        print(
            "  schema_validation : "
            f"is_valid={schema_validation.get('is_valid')} "
            f"missing={len(schema_validation.get('missing_columns') or [])} "
            f"extra={len(schema_validation.get('extra_columns') or [])}"
        )
        print(f"{'='*60}")

        if not inference_check["same_columns"] or not inference_check["x_shape_ok"]:
            return 1
        validation_summary["inference_row"] = inference_check

    # 4. Write output (optional)
    if args.output:
        out_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        schema_doc = {
            "exported_at": now,
            "model_dir": model_dir,
            "n_features": len(saved_cols),
            "feature_columns": saved_cols,
            "validation_summary": validation_summary,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(schema_doc, f, indent=2)
        print(f"\nSchema exported to: {out_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
