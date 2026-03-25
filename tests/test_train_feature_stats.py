"""Tests for train_feature_stats.json generation, archiving, and drift-monitor compatibility."""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY_ANALYZER_DIR = os.path.join(REPO_ROOT, "python-analyzer")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
for _d in [PY_ANALYZER_DIR, SCRIPTS_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from train_event_stack_v3 import _ARTIFACT_FILES, _promote_to_current  # type: ignore
from report_drift import run_drift_report  # type: ignore


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_train_stats(features: list[str]) -> dict:
    """Build a minimal flat train_feature_stats dict accepted by report_drift."""
    return {
        feat: {"mean": float(i) * 0.1, "std": 1.0 + float(i) * 0.05, "missing_rate": 0.0}
        for i, feat in enumerate(features)
    }


def _make_predictions_log(tmp_dir: str, features: list[str], n_rows: int = 20) -> str:
    """Write a minimal predictions_log.jsonl with synthetic feature values."""
    log_path = os.path.join(tmp_dir, "predictions_log.jsonl")
    with open(log_path, "w", encoding="utf-8") as f:
        for i in range(n_rows):
            row = {feat: float(i) * 0.1 + idx * 0.01 for idx, feat in enumerate(features)}
            f.write(json.dumps({"features": row}) + "\n")
    return log_path


# ---------------------------------------------------------------------------
# Test: _ARTIFACT_FILES includes train_feature_stats.json
# ---------------------------------------------------------------------------

class TestArtifactFilesIncludesTrainStats(unittest.TestCase):
    def test_artifact_list_contains_train_feature_stats(self) -> None:
        """_ARTIFACT_FILES must list train_feature_stats.json for archive/promote."""
        self.assertIn(
            "train_feature_stats.json",
            _ARTIFACT_FILES,
            "_ARTIFACT_FILES must include 'train_feature_stats.json' so the file "
            "is copied to archive and promoted to models/current/.",
        )


# ---------------------------------------------------------------------------
# Test: archive and promote pipeline copies train_feature_stats.json
# ---------------------------------------------------------------------------

class TestPromoteToCurrentIncludesTrainStats(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="uw-stats-tests-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir)

    def _write_artifact_dir(self, path: str, features: list[str]) -> None:
        os.makedirs(path, exist_ok=True)
        # Write a minimal set of artifacts (only the files used by drift monitor)
        stats = _make_train_stats(features)
        with open(os.path.join(path, "train_feature_stats.json"), "w") as f:
            json.dump(stats, f)
        with open(os.path.join(path, "model_meta.json"), "w") as f:
            json.dump({"active_model": "event_v3", "trained_at": "2026-01-01T00:00:00Z"}, f)

    def test_promote_copies_train_feature_stats_to_current(self) -> None:
        """_promote_to_current must copy train_feature_stats.json into models/current/."""
        model_dir = os.path.join(self._tmpdir, "models")
        archive_abs = os.path.join(model_dir, "archive", "event_v3-20260101T000000Z")
        self._write_artifact_dir(archive_abs, ["feat_a", "feat_b"])

        _promote_to_current(model_dir=model_dir, archive_abs=archive_abs)

        current_stats = os.path.join(model_dir, "current", "train_feature_stats.json")
        self.assertTrue(
            os.path.exists(current_stats),
            f"train_feature_stats.json should exist in models/current/ after promotion; "
            f"not found at {current_stats}",
        )

    def test_promoted_train_stats_content_is_valid(self) -> None:
        """Promoted train_feature_stats.json must be valid JSON with per-feature stats."""
        model_dir = os.path.join(self._tmpdir, "models")
        archive_abs = os.path.join(model_dir, "archive", "event_v3-20260101T000000Z")
        features = ["feat_a", "feat_b", "feat_c"]
        self._write_artifact_dir(archive_abs, features)

        _promote_to_current(model_dir=model_dir, archive_abs=archive_abs)

        current_stats = os.path.join(model_dir, "current", "train_feature_stats.json")
        with open(current_stats, "r", encoding="utf-8") as f:
            stats = json.load(f)

        for feat in features:
            self.assertIn(feat, stats)
            self.assertIn("mean", stats[feat])
            self.assertIn("std", stats[feat])
            self.assertIn("missing_rate", stats[feat])
            self.assertIsInstance(stats[feat]["mean"], float)
            self.assertIsInstance(stats[feat]["std"], float)
            self.assertIsInstance(stats[feat]["missing_rate"], float)


# ---------------------------------------------------------------------------
# Test: drift-monitor path convention (models/current/, NOT data/models/current/)
# ---------------------------------------------------------------------------

class TestDriftMonitorPathConvention(unittest.TestCase):
    def _read_file(self, rel_path: str) -> str:
        abs_path = os.path.join(REPO_ROOT, rel_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_service_uses_all_symbols_mode(self) -> None:
        """drift-monitor.service must use --all-symbols for per-symbol drift coverage."""
        content = self._read_file("systemd/drift-monitor.service")
        self.assertIn(
            "--all-symbols",
            content,
            "drift-monitor.service must pass --all-symbols to report_drift.py so every "
            "enabled symbol gets drift coverage.",
        )
        self.assertNotIn(
            "data/models/current/train_feature_stats",
            content,
            "drift-monitor.service must not reference the stale data/models/current/ path.",
        )

    def test_report_drift_docstring_uses_correct_path(self) -> None:
        """report_drift.py usage example must reference models/current/, not data/models/current/."""
        content = self._read_file("scripts/report_drift.py")
        self.assertNotIn(
            "data/models/current/train_feature_stats",
            content,
            "report_drift.py usage example still references the stale data/models/current/ path.",
        )


# ---------------------------------------------------------------------------
# Test: run_drift_report produces correct output from flat train_stats JSON
# ---------------------------------------------------------------------------

class TestRunDriftReportWithFlatStats(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="uw-drift-tests-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir)

    def _write_train_stats(self, features: list[str]) -> str:
        stats = _make_train_stats(features)
        path = os.path.join(self._tmpdir, "train_feature_stats.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f)
        return path

    def test_drift_report_runs_with_generated_stats_format(self) -> None:
        """run_drift_report must succeed with the flat train_feature_stats format."""
        features = ["feat_a", "feat_b"]
        stats_path = self._write_train_stats(features)
        log_path = _make_predictions_log(self._tmpdir, features)
        output_dir = os.path.join(self._tmpdir, "reports")

        report = run_drift_report(
            train_stats_path=stats_path,
            log_path=log_path,
            output_dir=output_dir,
            window_rows=50,
            dry_run=True,
        )

        self.assertIn("features", report)
        for feat in features:
            self.assertIn(feat, report["features"])
            self.assertIn("mean_drift", report["features"][feat])
            self.assertIn("psi", report["features"][feat])

    def test_missing_train_stats_file_exits_with_error(self) -> None:
        """report_drift must print a clear ERROR when train-stats file is absent."""
        import subprocess

        log_path = _make_predictions_log(self._tmpdir, ["feat_a"])
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(SCRIPTS_DIR, "report_drift.py"),
                "--train-stats", os.path.join(self._tmpdir, "nonexistent.json"),
                "--log-path", log_path,
                "--output-dir", self._tmpdir,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "ENABLE_DRIFT_MONITOR": "true"},
        )
        self.assertNotEqual(result.returncode, 0, "Should exit non-zero when stats file is missing")
        self.assertIn(
            "ERROR",
            result.stderr,
            "Should print ERROR to stderr when train-stats file is not found",
        )
        self.assertIn(
            "train-stats file not found",
            result.stderr,
            "Error message should clearly indicate the train-stats file is missing",
        )


if __name__ == "__main__":
    unittest.main()
