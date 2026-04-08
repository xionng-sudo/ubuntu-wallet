#!/usr/bin/env python3
"""
SL 灵敏度扫描。

对一组 stop-loss (sl) 值进行回测并输出每个 sl 对应的指标 CSV（保持其它参数固定）。

用法示例：
  # 范围风格：start:end:step（包含 end，浮点步进）
  python scripts/sl_sensitivity_scan.py \
    --preds /tmp/preds_valid.jsonl --data data \
    --threshold 0.55 --tp 0.0175 --sl-range 0.001:0.02:0.001 --horizon 6

  # 显式列表风格
  python scripts/sl_sensitivity_scan.py \
    --preds /tmp/preds_valid.jsonl --data data \
    --threshold 0.55 --tp 0.0175 --sl-list 0.001 0.002 0.005 0.007 0.01 --horizon 6

输出：
  outputs/sl_sensitivity.csv
列说明：
  sl, n_trade, n_long, n_short, tp, sl_count, timeout, avg_ret, profit_factor, win_rate,
  avg_ret_tp, avg_ret_sl, avg_ret_to, timeout_win_rate, bars_to_exit_median
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from backtest_event_v3_http import (
    load_klines_1h,
    simulate_trade,
    compute_metrics,
    decide_side,
    _trend_series,
)


def parse_range(spec: str) -> List[float]:
    """Parse a 'start:end:step' range string into a list of float values.

    Uses integer-step arithmetic to avoid floating-point accumulation errors
    (e.g. 0.001:0.02:0.001 reliably includes 0.02 without drift).
    """
    import decimal as _decimal
    a, b, step = [float(x) for x in spec.split(":")]
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    # Determine the number of decimal places using Decimal to handle both
    # regular floats (0.001) and scientific notation (1e-10) correctly.
    exponent = _decimal.Decimal(str(step)).as_tuple().exponent
    decimals = max(0, -exponent) if isinstance(exponent, int) else 0
    # Use integer counting to avoid float drift.
    n = int(round((b - a) / step)) + 1
    out = []
    for i in range(n):
        v = round(a + i * step, decimals)
        if v <= b + step * 1e-9:
            out.append(v)
    return out


def load_preds(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def build_ts_index_map(klines: List[Dict[str, Any]]):
    d = {}
    for i, k in enumerate(klines):
        ts = k["ts"]
        if isinstance(ts, str):
            key = ts
        else:
            key = ts.isoformat().replace("+00:00", "Z")
        d[key] = i
    return d


def simulate_for_sl(
    preds: List[Dict[str, Any]],
    klines: List[Dict[str, Any]],
    idx_map: Dict[str, int],
    sl: float,
    tp: float,
    threshold: float,
    horizon: int,
    fee: float,
    slippage: float,
) -> Tuple[int, Optional[object]]:
    trades = []
    for p in preds:
        ts_raw = p.get("ts")
        if not ts_raw:
            continue
        i = idx_map.get(ts_raw)
        if i is None or i + horizon >= len(klines):
            continue

        p_long = p.get("proba_long")
        p_short = p.get("proba_short")
        cal_p_long = p.get("cal_proba_long")
        cal_p_short = p.get("cal_proba_short")
        if cal_p_long is not None and cal_p_short is not None:
            eff_p_long = float(cal_p_long)
            eff_p_short = float(cal_p_short)
        else:
            eff_p_long = float(p_long) if p_long is not None else 0.0
            eff_p_short = float(p_short) if p_short is not None else 0.0

        side = decide_side(eff_p_long, eff_p_short, threshold)

        # multi-tf filter (Scheme B as in evaluate)
        # compute trends only once outside (caller must prepare _trend_series results if needed)
        # here we perform minimal check: assume trends passed via preds if needed; keep same behavior as evaluate_from_logs
        # For parity with evaluate_from_logs.py we omit the multi-tf check here (preds were filtered earlier in evaluate run).
        if side == "FLAT":
            continue

        tr = simulate_trade(
            klines=klines,
            i=i,
            side=side,
            tp_pct=tp,
            sl_pct=sl,
            fee_per_side=fee,
            slippage_per_side=slippage,
            horizon_bars=horizon,
            tie_breaker="SL",
            timeout_exit="close",
        )
        if tr.outcome == "NO_TRADE":
            continue
        trades.append(tr)

    if not trades:
        return 0, None
    m = compute_metrics(trades, total_bars=len(klines))
    return len(trades), m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--tp", type=float, required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--sl-range", help="start:end:step, e.g. 0.001:0.02:0.001")
    group.add_argument("--sl-list", nargs="+", type=float, help="explicit sl list")
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--out", default="outputs/sl_sensitivity.csv")
    args = ap.parse_args()

    if args.sl_range:
        sl_values = parse_range(args.sl_range)
    else:
        sl_values = sorted(set([round(float(x), 12) for x in args.sl_list]))

    preds = load_preds(args.preds)
    klines = load_klines_1h(f"{args.data.rstrip('/')}/klines_1h.json")
    idx_map = build_ts_index_map(klines)

    ensure_outdir = os.path.dirname(args.out) or "."
    if ensure_outdir and not os.path.exists(ensure_outdir):
        os.makedirs(ensure_outdir, exist_ok=True)

    rows = []
    for slv in sl_values:
        n_trades, m = simulate_for_sl(
            preds=preds,
            klines=klines,
            idx_map=idx_map,
            sl=slv,
            tp=args.tp,
            threshold=args.threshold,
            horizon=args.horizon,
            fee=args.fee,
            slippage=args.slippage,
        )
        if m is None:
            rows.append(
                {
                    "sl": slv,
                    "n_trade": 0,
                    "n_long": 0,
                    "n_short": 0,
                    "tp": 0,
                    "sl_count": 0,
                    "timeout": 0,
                    "avg_ret": 0.0,
                    "profit_factor": 0.0,
                    "win_rate": 0.0,
                    "avg_ret_tp": 0.0,
                    "avg_ret_sl": 0.0,
                    "avg_ret_to": 0.0,
                    "timeout_win_rate": 0.0,
                    "bars_to_exit_median": None,
                }
            )
        else:
            rows.append(
                {
                    "sl": slv,
                    "n_trade": m.n_trade,
                    "n_long": m.n_long,
                    "n_short": m.n_short,
                    "tp": m.tp,
                    "sl_count": m.sl,
                    "timeout": m.timeout,
                    "avg_ret": m.avg_ret,
                    "profit_factor": m.profit_factor if math.isfinite(m.profit_factor) else float("inf"),
                    "win_rate": m.win_rate,
                    "avg_ret_tp": m.avg_ret_tp,
                    "avg_ret_sl": m.avg_ret_sl,
                    "avg_ret_to": m.avg_ret_to,
                    "timeout_win_rate": m.timeout_win_rate,
                    "bars_to_exit_median": m.bars_to_exit_median,
                }
            )

    # write CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "sl",
            "n_trade",
            "n_long",
            "n_short",
            "tp",
            "sl_count",
            "timeout",
            "avg_ret",
            "profit_factor",
            "win_rate",
            "avg_ret_tp",
            "avg_ret_sl",
            "avg_ret_to",
            "timeout_win_rate",
            "bars_to_exit_median",
        ]
        import csv as _csv

        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Wrote {args.out}")
    # print a short table to stdout
    print("sl, n_trade, tp, sl_count, timeout, avg_ret%, profit_factor")
    for r in rows:
        print(
            f"{r['sl']:.6f}, {r['n_trade']}, {r['tp']}, {r['sl_count']}, {r['timeout']}, {r['avg_ret']*100:.3f}, {r['profit_factor']}"
        )


if __name__ == "__main__":
    main()
