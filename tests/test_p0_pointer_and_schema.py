from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import joblib


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
PY_ANALYZER_DIR = os.path.join(REPO_ROOT, "python-analyzer")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
for _d in [ML_SERVICE_DIR, PY_ANALYZER_DIR, SCRIPTS_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from export_feature_schema import _rebuild_schema_from_data, _validate_inference_row  # type: ignore
from feature_builder import build_multi_tf_feature_df, get_feature_columns_like_trainer  # type: ignore
from model_loader import load_model_from_registry  # type: ignore


class _DummyModel:
    def __init__(self, n_features_in_: int):
        self.n_features_in_ = n_features_in_

    def predict_proba(self, rows):
        return [[0.3, 0.7] for _ in range(len(rows))]


class P0PointerAndSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="uw-p0-tests-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir)

    def _make_rows(self, start_dt: datetime, step_hours: int, n: int):
        rows = []
        price = 100.0
        for i in range(n):
            ts = start_dt + timedelta(hours=step_hours * i)
            drift = 0.15 * math.sin(i / 12.0) + 0.03 * i / max(n, 1)
            open_p = price
            close_p = price + 0.4 + drift
            high_p = max(open_p, close_p) + 0.8
            low_p = min(open_p, close_p) - 0.8
            volume = 1000 + i * 3
            rows.append(
                [
                    ts.isoformat().replace("+00:00", "Z"),
                    round(open_p, 6),
                    round(high_p, 6),
                    round(low_p, 6),
                    round(close_p, 6),
                    round(volume, 6),
                ]
            )
            price = close_p
        return rows

    def _write_synthetic_klines(self, data_dir: str) -> None:
        os.makedirs(data_dir, exist_ok=True)
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        payloads = {
            "klines_1h.json": self._make_rows(start, 1, 240),
            "klines_4h.json": self._make_rows(start, 4, 120),
            "klines_1d.json": self._make_rows(start, 24, 120),
        }
        for filename, rows in payloads.items():
            with open(os.path.join(data_dir, filename), "w", encoding="utf-8") as f:
                json.dump(rows, f)

    def _write_legacy_lightgbm_dir(
        self,
        model_dir: str,
        *,
        model_version: str,
        trained_at: str,
        n_features: int = 3,
    ) -> None:
        os.makedirs(model_dir, exist_ok=True)
        meta = {
            "trained_at": trained_at,
            "model_version": model_version,
            "feature_columns": [f"f{i}" for i in range(n_features)],
            "lightgbm": {"trained_at": trained_at},
        }
        with open(os.path.join(model_dir, "model_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        joblib.dump(_DummyModel(n_features), os.path.join(model_dir, "lightgbm_model.pkl"))

    def test_current_pointer_loads_archive_dir_instead_of_flat_root(self) -> None:
        model_root = os.path.join(self._tmpdir, "models")
        archive_dir = os.path.join(model_root, "archive", "v1")
        self._write_legacy_lightgbm_dir(
            model_root,
            model_version="root-version",
            trained_at="2026-03-15T00:00:00Z",
        )
        self._write_legacy_lightgbm_dir(
            archive_dir,
            model_version="archive-version",
            trained_at="2026-03-16T00:00:00Z",
        )

        with open(os.path.join(model_root, "registry.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "model_version": "archive-version",
                            "trained_at": "2026-03-16T00:00:00Z",
                            "status": "prod",
                            "archive_dir": "archive/v1",
                        }
                    ]
                },
                f,
            )
        with open(os.path.join(model_root, "current.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "model_version": "archive-version",
                    "trained_at": "2026-03-16T00:00:00Z",
                    "path": "archive/v1",
                },
                f,
            )

        loaded = load_model_from_registry(model_root)
        self.assertEqual(os.path.abspath(loaded.model_path), os.path.abspath(os.path.join(archive_dir, "lightgbm_model.pkl")))
        self.assertEqual(loaded.trained_at, "2026-03-16T00:00:00Z")

    def test_loader_rejects_pointer_meta_version_mismatch(self) -> None:
        model_root = os.path.join(self._tmpdir, "models")
        archive_dir = os.path.join(model_root, "archive", "v1")
        self._write_legacy_lightgbm_dir(
            archive_dir,
            model_version="actual-archive-version",
            trained_at="2026-03-16T00:00:00Z",
        )
        with open(os.path.join(model_root, "registry.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "model_version": "expected-prod-version",
                            "trained_at": "2026-03-16T00:00:00Z",
                            "status": "prod",
                            "archive_dir": "archive/v1",
                        }
                    ]
                },
                f,
            )
        with open(os.path.join(model_root, "current.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "model_version": "expected-prod-version",
                    "trained_at": "2026-03-16T00:00:00Z",
                    "path": "archive/v1",
                },
                f,
            )

        with self.assertRaisesRegex(RuntimeError, "loaded model_meta.json disagree"):
            load_model_from_registry(model_root)

    def test_schema_validation_closes_training_and_inference_loop(self) -> None:
        data_dir = os.path.join(self._tmpdir, "data")
        model_dir = os.path.join(self._tmpdir, "models")
        self._write_synthetic_klines(data_dir)
        os.makedirs(model_dir, exist_ok=True)

        merged = build_multi_tf_feature_df(data_dir)
        feature_cols = get_feature_columns_like_trainer(merged)
        with open(os.path.join(model_dir, "feature_columns_event_v3.json"), "w", encoding="utf-8") as f:
            json.dump(feature_cols, f)

        rebuilt_cols = _rebuild_schema_from_data(data_dir)
        inference_check = _validate_inference_row(data_dir, model_dir, feature_cols)

        self.assertEqual(rebuilt_cols, feature_cols)
        self.assertTrue(inference_check["same_columns"])
        self.assertTrue(inference_check["x_shape_ok"])
        schema_validation = inference_check["schema_validation"]
        self.assertIsNotNone(schema_validation)
        self.assertTrue(schema_validation["is_valid"])
        self.assertEqual(schema_validation["missing_columns"], [])
        self.assertEqual(inference_check["x_shape"], [1, len(feature_cols)])


if __name__ == "__main__":
    unittest.main()
