#!/usr/bin/env python3
"""Feature drift monitor: compare training distribution to recent live distribution.

Usage:
  python scripts/report_drift.py --help
  python scripts/report_drift.py --train-stats data/models/current/train_feature_stats.json \\
      --log-path data/predictions_log.jsonl --output-dir data/reports

Controlled by ENABLE_DRIFT_MONITOR env var. If set to "false", exits 0 with no-op message.

Train stats JSON format:
  {"feature_name": {"mean": float, "std": float, "missing_rate": float}, ...}
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_jsonl(path: str, last_n: int) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-last_n:] if last_n > 0 else rows


def _compute_psi(train_vals: List[float], live_vals: List[float], n_bins: int = 10) -> Optional[float]:
    """Compute Population Stability Index between two distributions."""
    if not train_vals or not live_vals:
        return None

    all_vals = train_vals + live_vals
    min_v, max_v = min(all_vals), max(all_vals)
    if max_v == min_v:
        return 0.0

    bin_width = (max_v - min_v) / n_bins
    eps = 1e-6

    def _bin_counts(vals: List[float]) -> List[float]:
        counts = [0.0] * n_bins
        for v in vals:
            idx = int((v - min_v) / bin_width)
            idx = min(idx, n_bins - 1)
            counts[idx] += 1
        total = sum(counts) or 1.0
        return [c / total for c in counts]

    train_pct = _bin_counts(train_vals)
    live_pct = _bin_counts(live_vals)

    psi = 0.0
    for tp, lp in zip(train_pct, live_pct):
        tp = max(tp, eps)
        lp = max(lp, eps)
        psi += (lp - tp) * math.log(lp / tp)

    return round(psi, 6)


# ---------------------------------------------------------------------------
# main logic
# ---------------------------------------------------------------------------

def run_drift_report(
    train_stats_path: str,
    log_path: str,
    output_dir: str,
    window_rows: int,
    dry_run: bool,
) -> Dict[str, Any]:
    with open(train_stats_path, "r", encoding="utf-8") as f:
        train_stats: Dict[str, Dict[str, float]] = json.load(f)

    rows = _load_jsonl(log_path, window_rows)
    n_live = len(rows)

    results: Dict[str, Any] = {}

    for feat, stats in train_stats.items():
        train_mean = stats.get("mean", 0.0)
        train_std = stats.get("std", 0.0)
        train_missing = stats.get("missing_rate", 0.0)

        live_vals: List[float] = []
        missing_count = 0
        for row in rows:
            # features may live in row directly or inside a "features" sub-dict
            feat_dict = row.get("features") or row
            val = feat_dict.get(feat)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                missing_count += 1
            else:
                try:
                    live_vals.append(float(val))
                except (TypeError, ValueError):
                    missing_count += 1

        live_mean = sum(live_vals) / len(live_vals) if live_vals else 0.0
        live_std = (
            math.sqrt(sum((v - live_mean) ** 2 for v in live_vals) / len(live_vals))
            if len(live_vals) > 1
            else 0.0
        )
        live_missing = missing_count / n_live if n_live > 0 else 0.0

        denom_std = max(abs(train_std), 1e-6)
        mean_drift = abs(live_mean - train_mean) / denom_std
        std_drift = abs(live_std - train_std) / denom_std

        # PSI: reconstruct approximate training distribution from mean/std (Gaussian approximation)
        import random
        rng = random.Random(42)
        if train_std > 0:
            synthetic_train = [rng.gauss(train_mean, train_std) for _ in range(max(len(live_vals), 50))]
        else:
            synthetic_train = [train_mean] * max(len(live_vals), 50)

        psi = _compute_psi(synthetic_train, live_vals) if live_vals else None

        results[feat] = {
            "train_mean": round(train_mean, 6),
            "train_std": round(train_std, 6),
            "train_missing_rate": round(train_missing, 4),
            "live_mean": round(live_mean, 6),
            "live_std": round(live_std, 6),
            "live_missing_rate": round(live_missing, 4),
            "mean_drift": round(mean_drift, 4),
            "std_drift": round(std_drift, 4),
            "psi": psi,
            "n_live_vals": len(live_vals),
        }

    today = date.today().isoformat()
    report = {
        "date": today,
        "n_live_rows": n_live,
        "window_rows": window_rows,
        "train_stats_path": train_stats_path,
        "features": results,
    }

    if dry_run:
        print("[dry-run] drift report computed, not writing files.")
        print(json.dumps(report, indent=2)[:2000])
        return report

    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"drift_{today}.json")
    md_path = os.path.join(output_dir, f"drift_{today}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Drift report JSON  → {json_path}")

    # markdown summary
    high_drift = [(k, v) for k, v in results.items() if v["mean_drift"] > 1.0]
    high_drift.sort(key=lambda x: x[1]["mean_drift"], reverse=True)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Feature Drift Report — {today}\n\n")
        f.write(f"- Live rows analysed: **{n_live}** (window={window_rows})\n")
        f.write(f"- Features monitored: **{len(results)}**\n")
        f.write(f"- Features with mean_drift > 1σ: **{len(high_drift)}**\n\n")

        f.write("## High-Drift Features (mean_drift > 1σ)\n\n")
        if high_drift:
            f.write("| Feature | mean_drift | psi | live_missing_rate |\n")
            f.write("|---------|-----------|-----|-------------------|\n")
            for feat_name, fd in high_drift[:20]:
                psi_str = f"{fd['psi']:.4f}" if fd["psi"] is not None else "n/a"
                f.write(f"| {feat_name} | {fd['mean_drift']:.4f} | {psi_str} | {fd['live_missing_rate']:.3f} |\n")
        else:
            f.write("_No features exceed 1σ drift threshold._\n")

    print(f"Drift report MD    → {md_path}")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Feature drift monitor — compare training distribution to live prediction log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--train-stats",
        required=True,
        help="JSON file with per-feature mean/std/missing_rate from training.",
    )
    parser.add_argument(
        "--log-path",
        required=True,
        help="JSONL predictions log file (data/predictions_log.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/reports",
        help="Directory to write drift_YYYY-MM-DD.{json,md} (default: data/reports).",
    )
    parser.add_argument(
        "--window-rows",
        type=int,
        default=200,
        help="Number of recent rows from prediction log to analyse (default: 200).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute report but do not write output files.",
    )
    return parser.parse_args()


def main() -> None:
    if os.environ.get("ENABLE_DRIFT_MONITOR", "false").strip().lower() == "false":
        print("ENABLE_DRIFT_MONITOR=false, skipping.")
        sys.exit(0)

    args = _parse_args()

    if not os.path.exists(args.train_stats):
        print(f"ERROR: train-stats file not found: {args.train_stats}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.log_path):
        print(f"ERROR: log-path not found: {args.log_path}", file=sys.stderr)
        sys.exit(1)

    run_drift_report(
        train_stats_path=args.train_stats,
        log_path=args.log_path,
        output_dir=args.output_dir,
        window_rows=args.window_rows,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
