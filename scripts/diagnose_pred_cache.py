#!/usr/bin/env python3
"""
diagnose_pred_cache.py
======================
Diagnose probability distributions in pred_cache JSONL files.

Usage:
    python scripts/diagnose_pred_cache.py --cache-dir data/pred_cache

Output:
    For each cache file, prints:
    - selected_prob_source distribution (should be "raw" after fix)
    - raw_p_long statistics (min/p25/median/p75/max)
    - effective_long statistics
    - Count of bars where raw_p_long >= various thresholds
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from collections import Counter
from typing import List


def _load_cache(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    preds = []
    for line in lines[1:]:  # skip meta line
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            preds.append(rec.get("pred", {}))
        except Exception:
            continue
    return preds


def _pct(vals: List[float], p: float) -> float:
    if not vals:
        return float("nan")
    vals_s = sorted(vals)
    k = (len(vals_s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals_s) - 1)
    return vals_s[lo] + (vals_s[hi] - vals_s[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser(description="Diagnose probability distributions in pred_cache JSONL files.")
    ap.add_argument("--cache-dir", default="data/pred_cache")
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    files = sorted(cache_dir.glob("pred_cache__*.jsonl"))
    if not files:
        print(f"No cache files found in {cache_dir}")
        return

    for path in files:
        preds = _load_cache(path)
        if not preds:
            continue

        print(f"\n=== {path.name} (n={len(preds)}) ===")

        sources = Counter(p.get("selected_prob_source", "?") for p in preds)
        print(f"  selected_prob_source: {dict(sources)}")

        for field in ["raw_p_long", "effective_long", "cal_p_long"]:
            vals = [p[field] for p in preds if p.get(field) is not None]
            if not vals:
                print(f"  {field}: (all None)")
                continue
            print(f"  {field}: min={min(vals):.4f} p25={_pct(vals, 0.25):.4f} "
                  f"median={_pct(vals, 0.50):.4f} p75={_pct(vals, 0.75):.4f} max={max(vals):.4f}")

        raw_longs = [p["raw_p_long"] for p in preds if p.get("raw_p_long") is not None]
        if raw_longs:
            for thr in [0.35, 0.38, 0.40, 0.42, 0.45, 0.48, 0.50, 0.52, 0.55]:
                count = sum(1 for v in raw_longs if v >= thr)
                print(f"  raw_p_long >= {thr:.2f}: {count}/{len(raw_longs)} ({100 * count / len(raw_longs):.1f}%)")


if __name__ == "__main__":
    main()
