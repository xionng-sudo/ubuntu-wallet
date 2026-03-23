#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_threshold_grid.py
========================
Analyze threshold trade-offs across a configurable grid.

For each threshold in the grid, compute:
  - precision (win_rate)
  - coverage (fraction of predictions that become trades after MT filter)
  - avg_return
  - max_drawdown (trade-sequence MDD)
  - LONG / SHORT separate performance

Supports CSV and JSON output.

Usage
-----
  python scripts/report_threshold_grid.py \
    --log-path data/predictions_log.jsonl \
    --data-dir data \
    --tp 0.0175 --sl 0.007 \
    --threshold-min 0.55 --threshold-max 0.75 --threshold-step 0.05 \
    --output-json reports/threshold_grid.json \
    --output-csv  reports/threshold_grid.csv

  # Or print to stdout only (no --output-* flags):
  python scripts/report_threshold_grid.py \
    --log-path data/predictions_log.jsonl \
    --data-dir data \
    --tp 0.0175 --sl 0.007
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Allow importing backtest utilities without packaging
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from backtest_event_v3_http import (
    load_klines_1h,
    simulate_trade,
    compute_metrics,
    decide_side,
    _sma,
    _trend_series,
)
from mt_filter import mt_gate, gate_allows  # noqa: E402


def _to_utc_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_predictions(
    path: str,
    symbol: Optional[str],
    interval: str,
    active_model: Optional[str],
    since: Optional[str],
    until: Optional[str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    since_dt = _to_utc_dt(since) if since else None
    until_dt = _to_utc_dt(until) if until else None

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)

            if symbol is not None and j.get("symbol") != symbol:
                continue
            if j.get("interval") != interval:
                continue
            if active_model and j.get("active_model") != active_model:
                continue

            ts_raw = j.get("ts")
            if not ts_raw:
                continue
            ts = _to_utc_dt(ts_raw)

            if since_dt and ts < since_dt:
                continue
            if until_dt and ts > until_dt:
                continue

            out.append(
                {
                    "ts": ts,
                    "proba_long": j.get("proba_long"),
                    "proba_short": j.get("proba_short"),
                    "proba_flat": j.get("proba_flat"),
                    "cal_proba_long": j.get("cal_proba_long"),
                    "cal_proba_short": j.get("cal_proba_short"),
                    "signal": j.get("signal"),
                    "confidence": j.get("confidence"),
                }
            )

    out.sort(key=lambda x: x["ts"])
    return out


def _run_one_threshold(
    preds: List[Dict[str, Any]],
    klines: List[Dict],
    idx_by_ts: Dict,
    ts_4h: List,
    trend_4h_list: List,
    ts_1d: List,
    trend_1d_list: List,
    threshold: float,
    tp_pct: float,
    sl_pct: float,
    fee: float,
    slippage: float,
    horizon: int,
    tie_breaker: str,
    timeout_exit: str,
    mt_filter: bool,
    mt_filter_mode: str = "symmetric",
) -> Dict[str, Any]:
    """Run simulation for a single threshold value and return stats dict."""

    def trend_4h_at(ts) -> str:
        idx = bisect_right(ts_4h, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_4h_list[idx]

    def trend_1d_at(ts) -> str:
        idx = bisect_right(ts_1d, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_1d_list[idx]

    n_total = len(preds)
    trades = []
    skipped_flat = 0

    for p in preds:
        ts = p["ts"]
        i = idx_by_ts.get(ts)
        if i is None or i + horizon >= len(klines):
            continue

        cal_p_long = p.get("cal_proba_long")
        cal_p_short = p.get("cal_proba_short")
        p_long_raw = p.get("proba_long")
        p_short_raw = p.get("proba_short")

        if cal_p_long is not None and cal_p_short is not None:
            eff_p_long = float(cal_p_long)
            eff_p_short = float(cal_p_short)
        else:
            eff_p_long = float(p_long_raw) if p_long_raw is not None else 0.0
            eff_p_short = float(p_short_raw) if p_short_raw is not None else 0.0

        side = decide_side(eff_p_long, eff_p_short, threshold)

        if mt_filter and side in ("LONG", "SHORT"):
            t4 = trend_4h_at(ts)
            t1d = trend_1d_at(ts)
            if mt_filter_mode == "layered":
                if not gate_allows(mt_gate(side, t4, t1d)):
                    side = "FLAT"
            else:
                # symmetric (default): Scheme B
                if side == "LONG":
                    if t4 != "UP" or t1d == "DOWN":
                        side = "FLAT"
                else:  # SHORT
                    if t4 != "DOWN" or t1d == "UP":
                        side = "FLAT"

        if side == "FLAT":
            skipped_flat += 1
            continue

        tr = simulate_trade(
            klines=klines,
            i=i,
            side=side,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            fee_per_side=fee,
            slippage_per_side=slippage,
            horizon_bars=horizon,
            tie_breaker=tie_breaker,
            timeout_exit=timeout_exit,
        )
        if tr.outcome == "NO_TRADE":
            continue
        trades.append(tr)

    n_trades = len(trades)
    coverage = n_trades / n_total if n_total > 0 else 0.0

    if not trades:
        return {
            "threshold": threshold,
            "n_predictions": n_total,
            "n_trades": 0,
            "coverage": coverage,
            "precision": None,
            "avg_return_pct": None,
            "mdd_trade_seq_pct": None,
            "n_long": 0,
            "n_short": 0,
            "long_win_rate": None,
            "long_avg_ret_pct": None,
            "short_win_rate": None,
            "short_avg_ret_pct": None,
            "tp": 0,
            "sl": 0,
            "timeout": 0,
        }

    m = compute_metrics(trades, total_bars=len(klines))

    long_trades = [t for t in trades if t.side == "LONG"]
    short_trades = [t for t in trades if t.side == "SHORT"]

    def _dir_win_rate(ts_list) -> Optional[float]:
        if not ts_list:
            return None
        return sum(1 for t in ts_list if t.ret_net > 0) / len(ts_list)

    def _dir_avg_ret(ts_list) -> Optional[float]:
        if not ts_list:
            return None
        return (sum(t.ret_net for t in ts_list) / len(ts_list)) * 100

    return {
        "threshold": threshold,
        "n_predictions": n_total,
        "n_trades": n_trades,
        "coverage": round(coverage, 4),
        "precision": round(m.win_rate, 4),
        "avg_return_pct": round(m.avg_ret * 100, 4),
        "mdd_trade_seq_pct": round(m.mdd_trade_seq * 100, 4),
        "n_long": m.n_long,
        "n_short": m.n_short,
        "long_win_rate": round(_dir_win_rate(long_trades), 4) if long_trades else None,
        "long_avg_ret_pct": round(_dir_avg_ret(long_trades), 4) if long_trades else None,
        "short_win_rate": round(_dir_win_rate(short_trades), 4) if short_trades else None,
        "short_avg_ret_pct": round(_dir_avg_ret(short_trades), 4) if short_trades else None,
        "tp": m.tp,
        "sl": m.sl,
        "timeout": m.timeout,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Threshold grid report: analyze threshold trade-offs from prediction logs"
    )
    ap.add_argument("--log-path", default="data/predictions_log.jsonl",
                    help="Path to predictions_log.jsonl (default: data/predictions_log.jsonl)")
    ap.add_argument("--data-dir", default="data",
                    help="Directory containing klines_1h.json, klines_4h.json, klines_1d.json (default: data)")
    ap.add_argument("--symbol", default=None, help="Filter by symbol (e.g. BTCUSDT); if None, no filter")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--active-model", default=None,
                    help="Filter by active_model field in log (e.g. event_v3)")
    ap.add_argument("--since", default=None, help="ISO8601 start datetime, e.g. 2026-01-01T00:00:00Z")
    ap.add_argument("--until", default=None, help="ISO8601 end datetime")

    ap.add_argument("--tp", type=float, required=True,
                    help="Take-profit fraction, e.g. 0.0175 for 1.75%%")
    ap.add_argument("--sl", type=float, required=True,
                    help="Stop-loss fraction, e.g. 0.007 for 0.7%%")
    ap.add_argument("--fee", type=float, default=0.0004, help="Fee per side (default: 0.0004)")
    ap.add_argument("--slippage", type=float, default=0.0, help="Slippage per side (default: 0.0)")
    ap.add_argument("--horizon-bars", type=int, default=6, help="Horizon bars for trade simulation (default: 6)")
    ap.add_argument("--tie-breaker", choices=["SL", "TP"], default="SL")
    ap.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")

    ap.add_argument("--threshold-min", type=float, default=0.55, help="Grid start (default: 0.55)")
    ap.add_argument("--threshold-max", type=float, default=0.75, help="Grid end inclusive (default: 0.75)")
    ap.add_argument("--threshold-step", type=float, default=0.05, help="Grid step (default: 0.05)")

    ap.add_argument("--no-mt-filter", action="store_true",
                    help="Disable 4h/1d multi-timeframe filter (default: filter is enabled)")
    ap.add_argument(
        "--mt-filter-mode",
        choices=["symmetric", "layered"],
        default="symmetric",
        help=(
            "MT filter mode when filter is enabled (default: symmetric = original behavior, unchanged). "
            "'symmetric' matches backtest Scheme B: 4h same-direction required, 1d not opposite. "
            "'layered' uses unified mt_gate (ALLOW_STRONG / ALLOW_WEAK / REJECT); "
            "slightly more permissive — allows 4h=NEUTRAL+1d=same-direction as ALLOW_WEAK. "
            "Only use 'layered' when explicitly opting in for comparison or gradual rollout."
        ),
    )

    ap.add_argument("--output-json", default=None, help="Write JSON report to this file path")
    ap.add_argument("--output-csv", default=None, help="Write CSV report to this file path")

    args = ap.parse_args()

    # Load klines
    klines_1h_path = os.path.join(args.data_dir, "klines_1h.json")
    if not os.path.exists(klines_1h_path):
        print(f"ERROR: {klines_1h_path} not found", flush=True)
        return 2

    klines = load_klines_1h(klines_1h_path)
    if not klines:
        print("ERROR: 1h klines empty", flush=True)
        return 2

    idx_by_ts = {k["ts"]: i for i, k in enumerate(klines)}
    print(f"Loaded {len(klines)} 1h klines", flush=True)

    # Load 4h / 1d klines for MT filter
    mt_filter = not args.no_mt_filter
    ts_4h: List = []
    trend_4h_list: List = []
    ts_1d: List = []
    trend_1d_list: List = []

    if mt_filter:
        try:
            klines_4h = load_klines_1h(os.path.join(args.data_dir, "klines_4h.json"))
            klines_1d = load_klines_1h(os.path.join(args.data_dir, "klines_1d.json"))
            trend_4h_list = _trend_series(klines_4h, fast=5, slow=20, eps=0.001)
            trend_1d_list = _trend_series(klines_1d, fast=5, slow=20, eps=0.001)
            ts_4h = [k["ts"] for k in klines_4h]
            ts_1d = [k["ts"] for k in klines_1d]
            print(f"Loaded 4h ({len(klines_4h)} bars) and 1d ({len(klines_1d)} bars) klines for MT filter",
                  flush=True)
        except Exception as e:
            print(f"WARNING: could not load 4h/1d klines for MT filter ({e}), MT filter disabled", flush=True)
            mt_filter = False

    # Load predictions
    if not os.path.exists(args.log_path):
        print(f"ERROR: prediction log not found: {args.log_path}", flush=True)
        return 2

    preds = _load_predictions(
        path=args.log_path,
        symbol=args.symbol,
        interval=args.interval,
        active_model=args.active_model,
        since=args.since,
        until=args.until,
    )

    if not preds:
        print("ERROR: no predictions found for given filters", flush=True)
        return 2

    print(f"Loaded {len(preds)} predictions", flush=True)

    # Build threshold grid
    thresholds: List[float] = []
    t = args.threshold_min
    while t <= args.threshold_max + 1e-9:
        thresholds.append(round(t, 4))
        t += args.threshold_step

    rows: List[Dict[str, Any]] = []
    for thr in thresholds:
        row = _run_one_threshold(
            preds=preds,
            klines=klines,
            idx_by_ts=idx_by_ts,
            ts_4h=ts_4h,
            trend_4h_list=trend_4h_list,
            ts_1d=ts_1d,
            trend_1d_list=trend_1d_list,
            threshold=thr,
            tp_pct=args.tp,
            sl_pct=args.sl,
            fee=args.fee,
            slippage=args.slippage,
            horizon=args.horizon_bars,
            tie_breaker=args.tie_breaker,
            timeout_exit=args.timeout_exit,
            mt_filter=mt_filter,
            mt_filter_mode=args.mt_filter_mode,
        )
        rows.append(row)

    # Print table
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sep = "=" * 100
    print(f"\n{sep}")
    print(f"THRESHOLD GRID REPORT  [{now_utc}]")
    print(
        f"  log={args.log_path}  symbol={args.symbol}  interval={args.interval}"
        f"  active_model={args.active_model}"
    )
    print(
        f"  tp={args.tp*100:.2f}%  sl={args.sl*100:.2f}%  fee={args.fee*100:.4f}%"
        f"  horizon={args.horizon_bars}  mt_filter={mt_filter}"
        f"  mt_filter_mode={args.mt_filter_mode if mt_filter else 'off'}"
    )
    print(sep)
    hdr = (
        f"{'thr':>6}  {'n_pred':>7}  {'n_trade':>7}  {'coverage':>9}  "
        f"{'precision':>9}  {'avg_ret%':>9}  {'mdd%':>7}  "
        f"{'n_long':>6}  {'long_wr':>8}  {'n_short':>7}  {'short_wr':>9}  "
        f"{'TP':>4}  {'SL':>4}  {'TO':>4}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        prec = f"{r['precision']:.3f}" if r["precision"] is not None else "  n/a"
        avgr = f"{r['avg_return_pct']:+.3f}" if r["avg_return_pct"] is not None else "   n/a"
        mdd = f"{r['mdd_trade_seq_pct']:.2f}" if r["mdd_trade_seq_pct"] is not None else "  n/a"
        lwr = f"{r['long_win_rate']:.3f}" if r["long_win_rate"] is not None else "    n/a"
        swr = f"{r['short_win_rate']:.3f}" if r["short_win_rate"] is not None else "     n/a"
        print(
            f"  {r['threshold']:.2f}  {r['n_predictions']:>7}  {r['n_trades']:>7}  "
            f"{r['coverage']:>9.3f}  {prec:>9}  {avgr:>9}  {mdd:>7}  "
            f"{r['n_long']:>6}  {lwr:>8}  {r['n_short']:>7}  {swr:>9}  "
            f"{r['tp']:>4}  {r['sl']:>4}  {r['timeout']:>4}"
        )
    print(sep)

    # JSON output
    if args.output_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
        report = {
            "generated_at": now_utc,
            "params": {
                "log_path": args.log_path,
                "symbol": args.symbol,
                "interval": args.interval,
                "active_model": args.active_model,
                "tp_pct": args.tp,
                "sl_pct": args.sl,
                "fee_per_side": args.fee,
                "slippage_per_side": args.slippage,
                "horizon_bars": args.horizon_bars,
                "mt_filter": mt_filter,
                "threshold_min": args.threshold_min,
                "threshold_max": args.threshold_max,
                "threshold_step": args.threshold_step,
            },
            "grid": rows,
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"JSON report written to {args.output_json}", flush=True)

    # CSV output
    if args.output_csv:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_csv)), exist_ok=True)
        fields = [
            "threshold", "n_predictions", "n_trades", "coverage", "precision",
            "avg_return_pct", "mdd_trade_seq_pct",
            "n_long", "long_win_rate", "long_avg_ret_pct",
            "n_short", "short_win_rate", "short_avg_ret_pct",
            "tp", "sl", "timeout",
        ]
        with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV report written to {args.output_csv}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
