#!/usr/bin/env python3
"""Feature drift monitor: compare training distribution to recent live feature distribution.

Usage (explicit paths):
  python scripts/report_drift.py --help
  python scripts/report_drift.py \
      --train-stats models/current/train_feature_stats.json \
      --live-features-path data/features/features_1h_history.jsonl \
      --output-dir data/reports

Usage (per-symbol mode — paths derived automatically from configs/symbols.yaml):
  python scripts/report_drift.py --symbol BTCUSDT
  python scripts/report_drift.py --symbol ETHUSDT --output-dir data/ETHUSDT/reports

  When --symbol is given, --train-stats defaults to
      models/<SYMBOL>/current/train_feature_stats.json
  and --live-features-path defaults to
      data/<SYMBOL>/features/features_1h_history.jsonl
  and --output-dir defaults to
      data/<SYMBOL>/reports

  For PRIMARY_SYMBOL compatibility (typically ETHUSDT), if
      data/<SYMBOL>/features/features_1h_history.jsonl
  does not exist, the script falls back to:
      data/features/features_1h_history.jsonl

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


def _psi_from_bins(train_pct: List[float], live_pct: List[float]) -> float:
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
    """PSI using actual training sample values as the baseline histogram."""
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
    """PSI using Gaussian CDF as training baseline."""
    if not live_vals:
        return None

    min_v = min(live_vals)
    max_v = max(live_vals)
    if max_v == min_v:
        return 0.0

    bin_width = (max_v - min_v) / n_bins
    eps = 1e-6

    live_counts = [0.0] * n_bins
    for v in live_vals:
        idx = int((v - min_v) / bin_width)
        idx = min(idx, n_bins - 1)
        live_counts[idx] += 1.0
    total_live = float(len(live_vals))
    live_pct = [max(c / total_live, eps) for c in live_counts]

    train_pct_raw: List[float] = []
    for i in range(n_bins):
        low = min_v + i * bin_width
        high = min_v + (i + 1) * bin_width
        p = _normal_cdf(high, train_mean, train_std) - _normal_cdf(low, train_mean, train_std)
        train_pct_raw.append(max(p, eps))

    total_train = sum(train_pct_raw)
    train_pct = [p / total_train for p in train_pct_raw]

    return _psi_from_bins(train_pct, live_pct)


def _flatten_feature_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a live feature row.

    Supports both:
      1. legacy flat format: feature keys live at top-level
      2. nested format: feature keys live under row["features"]

    Top-level keys win if duplicated.
    """
    out = dict(row)
    nested = row.get("features")
    if isinstance(nested, dict):
        for k, v in nested.items():
            out.setdefault(k, v)
    return out


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return fv


def _default_live_features_path_for_symbol(symbol: str) -> str:
    return os.path.join("data", symbol, "features", "features_1h_history.jsonl")


def _legacy_root_live_features_path() -> str:
    return os.path.join("data", "features", "features_1h_history.jsonl")


# ---------------------------------------------------------------------------
# main logic
# ---------------------------------------------------------------------------

def run_drift_report(
    train_stats_path: str,
    live_features_path: str,
    output_dir: str,
    window_rows: int,
    dry_run: bool,
) -> Dict[str, Any]:
    with open(train_stats_path, "r", encoding="utf-8") as f:
        train_stats: Dict[str, Dict[str, float]] = json.load(f)

    rows = _load_jsonl(live_features_path, window_rows)
    rows = [_flatten_feature_row(r) for r in rows]
    n_live = len(rows)

    results: Dict[str, Any] = {}

    for feat, stats in train_stats.items():
        train_mean = float(stats.get("mean", 0.0))
        train_std = float(stats.get("std", 0.0))
        train_missing = float(stats.get("missing_rate", 0.0))

        live_vals: List[float] = []
        missing_count = 0

        for row in rows:
            val = _safe_float(row.get(feat))
            if val is None:
                missing_count += 1
            else:
                live_vals.append(val)

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
        "live_features_path": live_features_path,
        "features": results,
    }

    if dry_run:
        print("[dry-run] drift report computed, not writing files.")
        print(json.dumps(report, indent=2)[:4000])
        return report

    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"drift_{today}.json")
    md_path = os.path.join(output_dir, f"drift_{today}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Drift report JSON  → {json_path}")

    high_drift = [(k, v) for k, v in results.items() if v["mean_drift"] > 1.0]
    high_drift.sort(key=lambda x: x[1]["mean_drift"], reverse=True)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Feature Drift Report — {today}\n\n")
        f.write(f"- Live rows analysed: **{n_live}** (window={window_rows})\n")
        f.write(f"- Features monitored: **{len(results)}**\n")
        f.write(f"- Live source: **{live_features_path}**\n")
        f.write(f"- Features with mean_drift > 1σ: **{len(high_drift)}**\n\n")

        f.write("## High-Drift Features (mean_drift > 1σ)\n\n")
        if high_drift:
            f.write("| Feature | mean_drift | psi | live_missing_rate |\n")
            f.write("|---------|-----------|-----|-------------------|\n")
            for feat_name, fd in high_drift[:20]:
                psi_str = f"{fd['psi']:.4f}" if fd["psi"] is not None else "n/a"
                f.write(
                    f"| {feat_name} | {fd['mean_drift']:.4f} | {psi_str} | {fd['live_missing_rate']:.3f} |\n"
                )
        else:
            f.write("_No features exceed 1σ drift threshold._\n")

    print(f"Drift report MD    → {md_path}")
    return report


def _resolve_models_base_dir(cli_arg: Optional[str] = None) -> str:
    """Resolve the models base directory deterministically."""
    if cli_arg:
        return os.path.abspath(cli_arg)

    env_base = os.environ.get("MODELS_BASE_DIR", "").strip()
    if env_base:
        return os.path.abspath(env_base)

    app_root = os.environ.get("APP_ROOT", "").strip()
    if app_root:
        return os.path.abspath(os.path.join(app_root, "models"))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, "..", "models"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Feature drift monitor — compare training distribution to live feature history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help=(
            "Trading pair symbol (e.g. BTCUSDT). When provided, --train-stats, "
            "--live-features-path and --output-dir are derived automatically unless overridden."
        ),
    )
    parser.add_argument(
        "--all-symbols",
        action="store_true",
        default=False,
        help=(
            "Run drift report for every enabled symbol listed in configs/symbols.yaml. "
            "Per-symbol paths are derived automatically."
        ),
    )
    parser.add_argument(
        "--models-base-dir",
        default=None,
        help=(
            "Base directory containing per-symbol model subdirectories. "
            "Used only with --all-symbols."
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
        "--live-features-path",
        default=None,
        help=(
            "JSONL live feature history path (recommended: data/<SYMBOL>/features/features_1h_history.jsonl). "
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
        help="Number of recent rows from live feature history to analyse (default: 200).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute report but do not write output files.",
    )
    return parser.parse_args()


def _get_symbol_paths_module():
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import symbol_paths  # type: ignore[import]
    return symbol_paths


def _resolve_live_features_path_for_symbol(symbol: str) -> str:
    """Resolve per-symbol live feature history with root fallback for primary symbol compatibility."""
    per_symbol = _default_live_features_path_for_symbol(symbol)
    if os.path.exists(per_symbol):
        return per_symbol

    legacy_root = _legacy_root_live_features_path()
    if os.path.exists(legacy_root):
        return legacy_root

    return per_symbol


def main() -> None:
    args = _parse_args()

    if os.environ.get("ENABLE_DRIFT_MONITOR", "false").strip().lower() == "false":
        print("ENABLE_DRIFT_MONITOR=false, skipping.")
        sys.exit(0)

    if args.all_symbols:
        try:
            sp = _get_symbol_paths_module()
        except ImportError as exc:
            print(f"ERROR: could not import symbol_paths: {exc}", file=sys.stderr)
            sys.exit(1)

        models_base = _resolve_models_base_dir(args.models_base_dir)
        print(f"[drift] models base dir: {models_base}")

        if not os.path.isabs(models_base):
            print(f"ERROR: resolved models base dir is not absolute: {models_base}", file=sys.stderr)
            sys.exit(1)

        if not os.path.isdir(models_base):
            print(f"ERROR: models base dir does not exist: {models_base}", file=sys.stderr)
            sys.exit(1)

        symbols = sp.list_enabled_symbols()
        if not symbols:
            print("WARNING: no enabled symbols found in configs/symbols.yaml; nothing to do.")
            sys.exit(0)

        any_failed = False
        for sym in symbols:
            train_stats = sp.get_symbol_train_stats_path(sym, base_model_dir=models_base)
            live_features_path = _resolve_live_features_path_for_symbol(sym)
            output_dir = sp.get_symbol_reports_dir(sym)

            if not os.path.exists(train_stats):
                print(f"WARNING: [{sym}] train-stats file not found, skipping drift: {train_stats}", file=sys.stderr)
                continue
            if not os.path.exists(live_features_path):
                print(f"WARNING: [{sym}] live feature history not found, skipping drift: {live_features_path}", file=sys.stderr)
                continue

            print(f"[drift] running for symbol={sym}")
            try:
                run_drift_report(
                    train_stats_path=train_stats,
                    live_features_path=live_features_path,
                    output_dir=output_dir,
                    window_rows=args.window_rows,
                    dry_run=args.dry_run,
                )
            except Exception as exc:
                print(f"ERROR: [{sym}] drift report failed: {exc}", file=sys.stderr)
                any_failed = True

        sys.exit(1 if any_failed else 0)

    train_stats = args.train_stats
    live_features_path = args.live_features_path
    output_dir = args.output_dir

    if args.symbol:
        try:
            sp = _get_symbol_paths_module()
            if train_stats is None:
                train_stats = sp.get_symbol_train_stats_path(args.symbol)
            if live_features_path is None:
                live_features_path = _resolve_live_features_path_for_symbol(args.symbol)
            if output_dir is None:
                output_dir = sp.get_symbol_reports_dir(args.symbol)
        except ImportError:
            pass

    if output_dir is None:
        output_dir = "data/reports"

    if train_stats is None:
        print(
            "ERROR: --train-stats is required (or provide --symbol / --all-symbols to derive path automatically).",
            file=sys.stderr,
        )
        sys.exit(1)

    if live_features_path is None:
        print(
            "ERROR: --live-features-path is required (or provide --symbol / --all-symbols to derive path automatically).",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(train_stats):
        print(f"ERROR: train-stats file not found: {train_stats}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(live_features_path):
        print(f"ERROR: live-features-path not found: {live_features_path}", file=sys.stderr)
        sys.exit(1)

    run_drift_report(
        train_stats_path=train_stats,
        live_features_path=live_features_path,
        output_dir=output_dir,
        window_rows=args.window_rows,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
