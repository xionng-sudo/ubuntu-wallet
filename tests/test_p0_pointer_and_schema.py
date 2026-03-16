from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import joblib
import numpy as np


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_SERVICE_DIR = os.path.join(REPO_ROOT, "ml-service")
PY_ANALYZER_DIR = os.path.join(REPO_ROOT, "python-analyzer")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
for _d in [ML_SERVICE_DIR, PY_ANALYZER_DIR, SCRIPTS_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from export_feature_schema import _rebuild_schema_from_data, _validate_inference_row  # type: ignore
from feature_builder import build_multi_tf_feature_df, get_feature_columns_like_trainer  # type: ignore
import app as ml_app  # type: ignore
from model_loader import LoadedModel, load_model  # type: ignore


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

    def test_current_dir_loads_promoted_archive_artifacts(self) -> None:
        """Training promotes archive to models/current/; load_model() loads from there directly."""
        model_root = os.path.join(self._tmpdir, "models")
        archive_dir = os.path.join(model_root, "archive", "v1")
        current_dir = os.path.join(model_root, "current")

        # Simulate: training archived to archive/v1, then promoted to current/
        self._write_legacy_lightgbm_dir(
            archive_dir,
            model_version="archive-version",
            trained_at="2026-03-16T00:00:00Z",
        )
        shutil.copytree(archive_dir, current_dir)

        loaded = load_model(current_dir)
        self.assertEqual(
            os.path.abspath(loaded.model_path),
            os.path.abspath(os.path.join(current_dir, "lightgbm_model.pkl")),
        )
        self.assertEqual(loaded.trained_at, "2026-03-16T00:00:00Z")

    def test_rollback_replaces_current_dir_with_archive(self) -> None:
        """Rollback must overwrite models/current/ with the target archive, not write current.json."""
        model_root = os.path.join(self._tmpdir, "models")
        archive_v1 = os.path.join(model_root, "archive", "v1")
        archive_v2 = os.path.join(model_root, "archive", "v2")
        current_dir = os.path.join(model_root, "current")

        self._write_legacy_lightgbm_dir(archive_v1, model_version="v1", trained_at="2026-03-15T00:00:00Z")
        self._write_legacy_lightgbm_dir(archive_v2, model_version="v2", trained_at="2026-03-16T00:00:00Z")
        # current/ starts as v2 (the latest prod)
        shutil.copytree(archive_v2, current_dir)

        # Simulate rollback: replace current/ with archive v1
        if os.path.isdir(current_dir):
            shutil.rmtree(current_dir)
        shutil.copytree(archive_v1, current_dir)

        # After rollback, load_model(current_dir) returns v1
        loaded = load_model(current_dir)
        meta = json.load(open(os.path.join(current_dir, "model_meta.json")))
        self.assertEqual(meta["model_version"], "v1")
        self.assertEqual(loaded.trained_at, "2026-03-15T00:00:00Z")

        # current.json must NOT exist (directory-based pointer only)
        self.assertFalse(os.path.exists(os.path.join(model_root, "current.json")))

    def test_missing_current_dir_raises_on_load(self) -> None:
        """load_model() raises if models/current/ does not exist."""
        current_dir = os.path.join(self._tmpdir, "models", "current")
        with self.assertRaises((FileNotFoundError, RuntimeError, Exception)):
            load_model(current_dir)

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

    def test_training_schema_includes_formal_4h_and_1d_features(self) -> None:
        data_dir = os.path.join(self._tmpdir, "data")
        self._write_synthetic_klines(data_dir)

        merged = build_multi_tf_feature_df(data_dir)
        feature_cols = get_feature_columns_like_trainer(merged)

        self.assertTrue(any(col.startswith("tf4h_") for col in feature_cols))
        self.assertTrue(any(col.startswith("tf1d_") for col in feature_cols))
        X = merged[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).values.astype(np.float32)
        self.assertEqual(X.shape[1], len(feature_cols))

    def test_predict_uses_current_dir_schema(self) -> None:
        """MODEL_DIR=models/current/ so /predict reads schema from models/current/, not archive/ or flat root."""
        data_dir = os.path.join(self._tmpdir, "data")
        model_root = os.path.join(self._tmpdir, "models")
        current_dir = os.path.join(model_root, "current")
        self._write_synthetic_klines(data_dir)
        os.makedirs(current_dir, exist_ok=True)

        merged = build_multi_tf_feature_df(data_dir)
        feature_cols = get_feature_columns_like_trainer(merged)
        self.assertGreater(len(feature_cols), 10)

        # current/ has the full schema; flat model_root has a truncated schema
        with open(os.path.join(current_dir, "feature_columns_event_v3.json"), "w", encoding="utf-8") as f:
            json.dump(feature_cols, f)
        with open(os.path.join(model_root, "feature_columns_event_v3.json"), "w", encoding="utf-8") as f:
            json.dump(feature_cols[:-5], f)

        with open(os.path.join(current_dir, "model_meta.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "active_model": "event_v3",
                    "trained_at": "2026-03-16T00:00:00Z",
                    "model_version": "event_v3:test",
                    "event_v3": {"p_enter": 0.65, "delta": 0.0},
                    "lightgbm": {"trained_at": "2026-03-16T00:00:00Z"},
                },
                f,
            )
        model_artifact = os.path.join(current_dir, "lightgbm_event_v3.pkl")
        with open(model_artifact, "wb") as f:
            f.write(b"dummy-model")

        loaded = LoadedModel(
            active_model="event_v3",
            name="lightgbm",
            model=object(),
            scaler=None,
            feature_columns=feature_cols,
            trained_at="2026-03-16T00:00:00Z",
            model_path=model_artifact,
            scaler_path=None,
            expected_n_features=len(feature_cols),
            stacking_model=object(),
            base_models={},
            event_v3={"p_enter": 0.65, "delta": 0.0},
        )

        last_ts = merged.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        with patch.object(ml_app, "MODEL_DIR", current_dir), \
             patch.object(ml_app, "DATA_DIR", data_dir), \
             patch.object(ml_app, "_loaded", loaded), \
             patch.object(ml_app, "predict_proba", return_value=(np.array([[0.1, 0.2, 0.7]], dtype=np.float32), "proba_multiclass")), \
             patch.object(ml_app, "log_prediction", return_value=None):
            health = ml_app.healthz()
            resp = ml_app.predict(ml_app.PredictRequest(as_of_ts=last_ts))

        self.assertEqual(health["loaded_model_dir"], os.path.abspath(current_dir))
        # active_model_dir was removed from /healthz (MODEL_DIR IS the current dir now)
        self.assertNotIn("active_model_dir", health)
        self.assertEqual(resp.signal, "LONG")
        self.assertEqual(resp.model_version, loaded.model_version)


if __name__ == "__main__":
    unittest.main()
