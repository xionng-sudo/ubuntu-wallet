#!/usr/bin/env python3
"""Feature drift monitor: compare training distribution to recent live distribution.

Usage (explicit paths):
  python scripts/report_drift.py --help
  python scripts/report_drift.py --train-stats models/current/train_feature_stats.json \\
      --log-path data/predictions_log.jsonl --output-dir data/reports

Usage (per-symbol mode — paths derived automatically from configs/symbols.yaml):
  python scripts/report_drift.py --symbol BTCUSDT
  python scripts/report_drift.py --symbol ETHUSDT --output-dir data/ETHUSDT/reports

  When --symbol is given, --train-stats defaults to
      models/<SYMBOL>/current/train_feature_stats.json
  and --log-path defaults to
      data/<SYMBOL>/predictions_log.jsonl
  and --output-dir defaults to
      data/<SYMBOL>/reports

Controlled by ENABLE_DRIFT_MONITOR env var. If set to "false", exits 0 with no-op message.

Train stats JSON format (two variants are supported):
  Bootstrap baseline (preferred):
    {"feature_name": {"mean": float, "std": float, "missing_rate": float,
                      "values": [float, ...]}, ...}
  Summary-only (Gaussian CDF fallback):
    {"feature_name": {"mean": float, "std": float, "missing_rate": float}, ...}

When "values" is present PSI is computed against the actual training histogram.
When absent the Gaussian CDF derived from mean/std is used as the expected baseline.
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


def _normal_cdf(x: float, mean: float, std: float) -> float:
    """Cumulative distribution function of the normal distribution."""
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + math.erf((x - mean) / (std * math.sqrt(2.0))))


def _psi_from_bins(
    train_pct: List[float],
    live_pct: List[float],
) -> float:
    """Compute PSI given two lists of per-bin proportions (same length, same bins)."""
    eps = 1e-6
    psi = 0.0
    for tp, lp in zip(train_pct, live_pct):
        tp = max(tp, eps)
        lp = max(lp, eps)
        psi += (lp - tp) * math.log(lp / tp)
    return round(psi, 6)


def _compute_psi_bootstrap(
    train_vals: List[float],
    live_vals: List[float],
    n_bins: int = 10,
) -> Optional[float]:
    """PSI using actual training sample values as the baseline histogram.

    Bin edges are computed from the union of both distributions so the same
    bins are used for expected and observed proportions.
    """
    if not train_vals or not live_vals:
        return None

    all_vals = train_vals + live_vals
    min_v, max_v = min(all_vals), max(all_vals)
    if max_v == min_v:
        return 0.0

    bin_width = (max_v - min_v) / n_bins
    eps = 1e-6

    def _proportions(vals: List[float]) -> List[float]:
        counts = [0.0] * n_bins
        for v in vals:
            idx = int((v - min_v) / bin_width)
            idx = min(idx, n_bins - 1)
            counts[idx] += 1.0
        total = float(len(vals))
        return [max(c / total, eps) for c in counts]

    return _psi_from_bins(_proportions(train_vals), _proportions(live_vals))


def _compute_psi_cdf_baseline(
    train_mean: float,
    train_std: float,
    live_vals: List[float],
    n_bins: int = 10,
) -> Optional[float]:
    """PSI using Gaussian CDF as training baseline (fallback when values unavailable).

    Bin edges are derived from the live-data range.  Expected training proportions
    are computed analytically from the Gaussian CDF so the result is deterministic.
    """
    if not live_vals:
        return None

    min_v = min(live_vals)
    max_v = max(live_vals)
    if max_v == min_v:
        return 0.0

    bin_width = (max_v - min_v) / n_bins
    eps = 1e-6

    # Observed (live) proportions
    live_counts = [0.0] * n_bins
    for v in live_vals:
        idx = int((v - min_v) / bin_width)
        idx = min(idx, n_bins - 1)
        live_counts[idx] += 1.0
    total_live = float(len(live_vals))
    live_pct = [max(c / total_live, eps) for c in live_counts]

    # Expected (training) proportions from Gaussian CDF
    train_pct_raw: List[float] = []
    for i in range(n_bins):
        low = min_v + i * bin_width
        high = min_v + (i + 1) * bin_width
        p = _normal_cdf(high, train_mean, train_std) - _normal_cdf(low, train_mean, train_std)
        train_pct_raw.append(max(p, eps))

    # Normalise so proportions sum to 1 (CDF endpoints may not span exactly [0,1])
    total_train = sum(train_pct_raw)
    train_pct = [p / total_train for p in train_pct_raw]

    return _psi_from_bins(train_pct, live_pct)


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

        # PSI: use actual training values when available (bootstrap baseline),
        # otherwise fall back to Gaussian CDF derived from mean/std.
        train_values: Optional[List[float]] = None
        raw_vals = stats.get("values")
        if raw_vals and isinstance(raw_vals, list):
            try:
                train_values = [float(v) for v in raw_vals if v is not None]
            except (TypeError, ValueError):
                train_values = None

        if train_values:
            psi = _compute_psi_bootstrap(train_values, live_vals) if live_vals else None
        else:
            psi = _compute_psi_cdf_baseline(train_mean, train_std, live_vals) if live_vals else None

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
            "psi_baseline": "bootstrap" if train_values else "gaussian_cdf",
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
        "--symbol",
        default=None,
        help=(
            "Trading pair symbol (e.g. BTCUSDT).  When provided, --train-stats, "
            "--log-path and --output-dir are derived automatically from "
            "configs/symbols.yaml path conventions unless explicitly overridden."
        ),
    )
    parser.add_argument(
        "--all-symbols",
        action="store_true",
        default=False,
        help=(
            "Run drift report for every enabled symbol listed in configs/symbols.yaml. "
            "Per-symbol paths are derived automatically.  Symbols whose train-stats or "
            "prediction log are missing are skipped with a warning (failure-isolated). "
            "Mutually exclusive with --symbol / --train-stats / --log-path."
        ),
    )
    parser.add_argument(
        "--train-stats",
        default=None,
        help=(
            "JSON file with per-feature mean/std/missing_rate from training. "
            "Required unless --symbol or --all-symbols is given."
        ),
    )
    parser.add_argument(
        "--log-path",
        default=None,
        help=(
            "JSONL predictions log file. "
            "Required unless --symbol or --all-symbols is given."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write drift_YYYY-MM-DD.{json,md} (default: data/reports or data/<SYMBOL>/reports).",
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


def _get_symbol_paths_module():
    """Import symbol_paths from the scripts directory."""
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import symbol_paths  # type: ignore[import]
    return symbol_paths


def main() -> None:
    args = _parse_args()

    if os.environ.get("ENABLE_DRIFT_MONITOR", "false").strip().lower() == "false":
        print("ENABLE_DRIFT_MONITOR=false, skipping.")
        sys.exit(0)

    # --all-symbols: loop over every enabled symbol; skip symbols with missing artifacts
    if args.all_symbols:
        try:
            sp = _get_symbol_paths_module()
        except ImportError as exc:
            print(f"ERROR: could not import symbol_paths: {exc}", file=sys.stderr)
            sys.exit(1)

        symbols = sp.list_enabled_symbols()
        if not symbols:
            print("WARNING: no enabled symbols found in configs/symbols.yaml; nothing to do.")
            sys.exit(0)

        any_failed = False
        for sym in symbols:
            train_stats = sp.get_symbol_train_stats_path(sym)
            log_path = sp.get_symbol_log_path(sym)
            output_dir = sp.get_symbol_reports_dir(sym)

            if not os.path.exists(train_stats):
                print(
                    f"WARNING: [{sym}] train-stats file not found, skipping drift: {train_stats}",
                    file=sys.stderr,
                )
                continue
            if not os.path.exists(log_path):
                print(
                    f"WARNING: [{sym}] prediction log not found, skipping drift: {log_path}",
                    file=sys.stderr,
                )
                continue

            print(f"[drift] running for symbol={sym}")
            try:
                run_drift_report(
                    train_stats_path=train_stats,
                    log_path=log_path,
                    output_dir=output_dir,
                    window_rows=args.window_rows,
                    dry_run=args.dry_run,
                )
            except Exception as exc:
                print(f"ERROR: [{sym}] drift report failed: {exc}", file=sys.stderr)
                any_failed = True

        sys.exit(1 if any_failed else 0)

    # Single-symbol or explicit-path mode
    train_stats = args.train_stats
    log_path = args.log_path
    output_dir = args.output_dir

    if args.symbol:
        try:
            sp = _get_symbol_paths_module()
            if train_stats is None:
                train_stats = sp.get_symbol_train_stats_path(args.symbol)
            if log_path is None:
                log_path = sp.get_symbol_log_path(args.symbol)
            if output_dir is None:
                output_dir = sp.get_symbol_reports_dir(args.symbol)
        except ImportError:
            pass  # symbol_paths not available; fall through to error below

    # Apply legacy default for output_dir when no symbol was given
    if output_dir is None:
        output_dir = "data/reports"

    if train_stats is None:
        print(
            "ERROR: --train-stats is required (or provide --symbol / --all-symbols to derive path automatically).",
            file=sys.stderr,
        )
        sys.exit(1)

    if log_path is None:
        print(
            "ERROR: --log-path is required (or provide --symbol / --all-symbols to derive path automatically).",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(train_stats):
        print(f"ERROR: train-stats file not found: {train_stats}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(log_path):
        print(f"ERROR: log-path not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    run_drift_report(
        train_stats_path=train_stats,
        log_path=log_path,
        output_dir=output_dir,
        window_rows=args.window_rows,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
