#!/usr/bin/env python3
"""Calibration quality report: reliability curve, bin statistics, Brier score.

Usage:
  python python-analyzer/calibration_report.py --help
  python python-analyzer/calibration_report.py \\
      --log-path data/predictions_log.jsonl \\
      --output-dir data/reports

Controlled by ENABLE_CALIB_REPORT env var.
If set to "false", exits 0 with a no-op message.

Prediction log fields used:
  ts, signal, confidence, calibrated_confidence (optional), outcome (optional).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_jsonl(path: str) -> List[dict]:
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
    return rows


def _brier_score(confidences: List[float], outcomes: List[int]) -> Optional[float]:
    if not confidences or not outcomes or len(confidences) != len(outcomes):
        return None
    n = len(confidences)
    return round(sum((c - o) ** 2 for c, o in zip(confidences, outcomes)) / n, 6)


def _reliability_curve(
    confidences: List[float],
    outcomes: List[int],
    n_bins: int,
) -> List[Dict[str, Any]]:
    """Bin confidences and compute fraction-positive per bin."""
    if not confidences:
        return []

    bin_width = 1.0 / n_bins
    bins: List[Dict[str, Any]] = []
    for i in range(n_bins):
        lo = i * bin_width
        hi = lo + bin_width
        idxs = [j for j, c in enumerate(confidences) if lo <= c < hi]
        if i == n_bins - 1:
            idxs = [j for j, c in enumerate(confidences) if lo <= c <= hi]
        n_bin = len(idxs)
        frac_pos = sum(outcomes[j] for j in idxs) / n_bin if n_bin > 0 else None
        mean_conf = sum(confidences[j] for j in idxs) / n_bin if n_bin > 0 else (lo + hi) / 2
        bins.append({
            "bin_lo": round(lo, 3),
            "bin_hi": round(hi, 3),
            "n": n_bin,
            "mean_confidence": round(mean_conf, 4) if n_bin > 0 else None,
            "fraction_positive": round(frac_pos, 4) if frac_pos is not None else None,
        })
    return bins


# ---------------------------------------------------------------------------
# main logic
# ---------------------------------------------------------------------------

def run_calib_report(
    log_path: str,
    output_dir: str,
    n_bins: int,
    signal_filter: str,
    dry_run: bool,
    no_plot: bool,
) -> Dict[str, Any]:
    rows = _load_jsonl(log_path)

    if signal_filter != "ALL":
        rows = [r for r in rows if r.get("signal", "").upper() == signal_filter]

    # extract confidence and outcome
    confidences: List[float] = []
    outcomes: List[int] = []
    has_outcome = False

    for row in rows:
        conf = row.get("calibrated_confidence") or row.get("confidence")
        if conf is None:
            continue
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue

        outcome = row.get("outcome")
        if outcome is not None:
            has_outcome = True
            try:
                outcomes.append(int(outcome))
            except (TypeError, ValueError):
                outcomes.append(0)
            confidences.append(conf)
        else:
            confidences.append(conf)

    if not has_outcome:
        outcomes = []

    n_rows = len(rows)
    n_conf = len(confidences)

    brier = _brier_score(confidences, outcomes) if outcomes else None
    rel_curve = _reliability_curve(confidences, outcomes, n_bins) if outcomes else []

    # confidence distribution stats
    if confidences:
        mean_conf = sum(confidences) / len(confidences)
        sorted_conf = sorted(confidences)
        median_conf = sorted_conf[len(sorted_conf) // 2]
        pct_high = sum(1 for c in confidences if c >= 0.7) / len(confidences)
    else:
        mean_conf = median_conf = pct_high = None

    today = date.today().isoformat()
    report: Dict[str, Any] = {
        "date": today,
        "signal_filter": signal_filter,
        "n_rows_total": n_rows,
        "n_rows_with_confidence": n_conf,
        "n_rows_with_outcome": len(outcomes),
        "has_outcome_data": has_outcome,
        "brier_score": brier,
        "mean_confidence": round(mean_conf, 4) if mean_conf is not None else None,
        "median_confidence": round(median_conf, 4) if median_conf is not None else None,
        "pct_confidence_gte_0_7": round(pct_high, 4) if pct_high is not None else None,
        "n_bins": n_bins,
        "reliability_curve": rel_curve,
    }

    if dry_run:
        print("[dry-run] calibration report computed, not writing files.")
        print(json.dumps(report, indent=2)[:2000])
        return report

    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"calib_report_{today}.json")
    md_path = os.path.join(output_dir, f"calib_report_{today}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Calibration report JSON → {json_path}")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Calibration Quality Report — {today}\n\n")
        f.write(f"- Signal filter: **{signal_filter}**\n")
        f.write(f"- Rows analysed: **{n_rows}** (with confidence: {n_conf}, with outcome: {len(outcomes)})\n")
        if brier is not None:
            f.write(f"- **Brier score**: {brier:.4f} (lower is better; perfect = 0)\n")
        if mean_conf is not None:
            f.write(f"- Mean confidence: {mean_conf:.4f}  Median: {median_conf:.4f}  ≥0.7: {pct_high:.1%}\n")
        f.write("\n")

        if rel_curve:
            f.write("## Reliability Curve\n\n")
            f.write("| Bin | Mean Confidence | Fraction Positive | N |\n")
            f.write("|-----|----------------|-------------------|---|\n")
            for b in rel_curve:
                mc = f"{b['mean_confidence']:.3f}" if b["mean_confidence"] is not None else "—"
                fp = f"{b['fraction_positive']:.3f}" if b["fraction_positive"] is not None else "—"
                f.write(f"| [{b['bin_lo']:.2f}, {b['bin_hi']:.2f}) | {mc} | {fp} | {b['n']} |\n")
        else:
            f.write("_No outcome data available — reliability curve cannot be computed._\n")
            f.write("\n## Confidence Distribution\n\n")
            if confidences:
                f.write(f"Mean confidence: {mean_conf:.4f}, Median: {median_conf:.4f}\n")

    print(f"Calibration report MD  → {md_path}")

    # try to save reliability curve PNG
    if not no_plot and rel_curve:
        _try_save_plot(rel_curve, output_dir, today)

    return report


def _try_save_plot(rel_curve: List[Dict[str, Any]], output_dir: str, today: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        xs = [b["mean_confidence"] for b in rel_curve if b["mean_confidence"] is not None and b["fraction_positive"] is not None]
        ys = [b["fraction_positive"] for b in rel_curve if b["mean_confidence"] is not None and b["fraction_positive"] is not None]

        fig, ax = plt.subplots(figsize=(6, 5))
        ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
        ax.scatter(xs, ys, label="Observed")
        if xs and ys:
            ax.plot(xs, ys, "b-")
        ax.set_xlabel("Mean Predicted Confidence")
        ax.set_ylabel("Fraction Positive")
        ax.set_title(f"Reliability Curve — {today}")
        ax.legend()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

        png_path = os.path.join(output_dir, f"calib_report_{today}.png")
        fig.savefig(png_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        print(f"Reliability curve PNG  → {png_path}")
    except Exception as exc:
        print(f"[warn] Could not save reliability curve plot: {exc}", file=sys.stderr)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibration quality report — reliability curve, Brier score, bin stats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--log-path",
        required=True,
        help="JSONL predictions log file (data/predictions_log.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/reports",
        help="Directory to write calib_report_YYYY-MM-DD.{json,md} (default: data/reports).",
    )
    parser.add_argument(
        "--n-bins",
        type=int,
        default=10,
        help="Number of confidence bins for reliability curve (default: 10).",
    )
    parser.add_argument(
        "--signal",
        default="ALL",
        choices=["LONG", "SHORT", "ALL"],
        help="Filter by signal type (default: ALL).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute report but do not write output files.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip reliability curve PNG generation.",
    )
    return parser.parse_args()


def main() -> None:
    if os.environ.get("ENABLE_CALIB_REPORT", "false").strip().lower() == "false":
        print("ENABLE_CALIB_REPORT=false, skipping.")
        sys.exit(0)

    args = _parse_args()

    if not os.path.exists(args.log_path):
        print(f"ERROR: log-path not found: {args.log_path}", file=sys.stderr)
        sys.exit(1)

    run_calib_report(
        log_path=args.log_path,
        output_dir=args.output_dir,
        n_bins=args.n_bins,
        signal_filter=args.signal,
        dry_run=args.dry_run,
        no_plot=args.no_plot,
    )


if __name__ == "__main__":
    main()
