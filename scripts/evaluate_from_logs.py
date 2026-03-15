#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Evaluate live/logged predictions from data/predictions_log.jsonl using historical klines.

- Reuses triple-barrier logic (simulate_trade) and Metrics from backtest_event_v3_http.py
- Uses the same (threshold, tp, sl, horizon) as chosen in offline grid search.
- Includes multi-timeframe filtering (4h / 1d) consistent with backtest (Scheme B).
- Reports extended metrics: precision@threshold, coverage, per-direction stats,
  TP/SL/TIMEOUT distribution, and calibrated vs raw confidence comparison.

Run on a schedule (e.g. every 6 hours) via systemd/evaluate-predictions.timer.
"""

from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backtest_event_v3_http import (
    load_klines_1h,
    simulate_trade,
    compute_metrics,
    decide_side,
    _sma,
    _trend_series,
)


def _to_utc_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_predictions(
    path: str,
    symbol: Optional[str],
    interval: str,
    model_version: Optional[str] = None,
    active_model: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
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
            if model_version and j.get("model_version") != model_version:
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
                    "signal": j.get("signal"),
                    "confidence": j.get("confidence"),
                    "calibrated_confidence": j.get("calibrated_confidence"),
                    "calibration_method": j.get("calibration_method"),
                    "model_version": j.get("model_version"),
                    "active_model": j.get("active_model"),
                    "trend_4h": j.get("trend_4h"),
                    "trend_1d": j.get("trend_1d"),
                }
            )

    out.sort(key=lambda x: x["ts"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-path", default="data/predictions_log.jsonl")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--symbol", default=None, help="e.g. BTCUSDT; if None, do not filter by symbol")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--model-version", default=None)
    ap.add_argument("--active-model", default="event_v3")
    ap.add_argument("--since", default=None, help="ISO8601, e.g. 2026-03-01T00:00:00Z")
    ap.add_argument("--until", default=None)

    ap.add_argument("--threshold", type=float, required=True, help="p_enter threshold, e.g. 0.55")
    ap.add_argument("--tp", type=float, required=True, help="take profit pct, e.g. 0.0175 for 1.75%")
    ap.add_argument("--sl", type=float, required=True, help="stop loss pct, e.g. 0.009 for 0.9%")
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--horizon-bars", type=int, default=6)
    ap.add_argument("--tie-breaker", choices=["SL", "TP"], default="SL")
    ap.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")

    args = ap.parse_args()

    # 1h K 线
    klines = load_klines_1h(f"{args.data_dir.rstrip('/')}/klines_1h.json")
    if not klines:
        print("ERROR: no klines_1h.json data", flush=True)
        return 2

    idx_by_ts: Dict[datetime, int] = {k["ts"]: i for i, k in enumerate(klines)}
    print(f"Loaded {len(klines)} 1h klines", flush=True)

    # 4h / 1d K 线与趋势（与 backtest 一致）
    try:
        klines_4h = load_klines_1h(f"{args.data_dir.rstrip('/')}/klines_4h.json")
        klines_1d = load_klines_1h(f"{args.data_dir.rstrip('/')}/klines_1d.json")
    except Exception as e:
        print(f"ERROR: failed to load 4h/1d klines: {e}", flush=True)
        return 2

    trend_4h_list = _trend_series(klines_4h, fast=5, slow=20, eps=0.001)
    trend_1d_list = _trend_series(klines_1d, fast=5, slow=20, eps=0.001)
    ts_4h = [k["ts"] for k in klines_4h]
    ts_1d = [k["ts"] for k in klines_1d]

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

    # 预测日志
    preds = load_predictions(
        path=args.log_path,
        symbol=args.symbol,
        interval=args.interval,
        model_version=args.model_version,
        active_model=args.active_model,
        since=args.since,
        until=args.until,
    )
    print(f"Loaded {len(preds)} predictions from log", flush=True)

    if not preds:
        print("ERROR: no predictions in log for given filters", flush=True)
        return 2

    trades = []
    horizon = int(args.horizon_bars)

    skipped_no_kline = 0
    skipped_side_flat = 0

    # Track confidence of triggered signals for precision@threshold analysis
    triggered_confidences: List[float] = []
    triggered_cal_confidences: List[float] = []

    for p in preds:
        ts = p["ts"]
        i = idx_by_ts.get(ts)
        if i is None or i + horizon >= len(klines):
            skipped_no_kline += 1
            continue

        p_long = p["proba_long"]
        p_short = p["proba_short"]

        # Use calibrated confidence for thresholding if available
        cal_conf = p.get("calibrated_confidence")
        eff_p_long = p_long
        eff_p_short = p_short
        if cal_conf is not None:
            # When calibrated confidence is logged, use it for thresholding
            # by scaling the effective probabilities
            if p.get("signal") == "LONG" and p_long is not None:
                eff_p_long = cal_conf
            elif p.get("signal") == "SHORT" and p_short is not None:
                eff_p_short = cal_conf

        side = decide_side(eff_p_long, eff_p_short, args.threshold)

        # Multi-timeframe filter (Scheme B, consistent with backtest)
        if side == "LONG":
            t4 = trend_4h_at(ts)
            t1d = trend_1d_at(ts)
            if t4 != "UP":
                side = "FLAT"
            elif t1d == "DOWN":
                side = "FLAT"
        elif side == "SHORT":
            t4 = trend_4h_at(ts)
            t1d = trend_1d_at(ts)
            if t4 != "DOWN":
                side = "FLAT"
            elif t1d == "UP":
                side = "FLAT"

        if side == "FLAT":
            skipped_side_flat += 1
            continue

        # Track confidence of triggered signals
        conf = p.get("confidence") or 0.0
        triggered_confidences.append(float(conf))
        if cal_conf is not None:
            triggered_cal_confidences.append(float(cal_conf))

        tr = simulate_trade(
            klines=klines,
            i=i,
            side=side,
            tp_pct=args.tp,
            sl_pct=args.sl,
            fee_per_side=args.fee,
            slippage_per_side=args.slippage,
            horizon_bars=horizon,
            tie_breaker=args.tie_breaker,
            timeout_exit=args.timeout_exit,
        )
        if tr.outcome == "NO_TRADE":
            continue
        trades.append(tr)

    if not trades:
        print(
            f"No trades generated from logged predictions + params + MT filtering "
            f"(loaded={len(preds)}, skipped_no_kline={skipped_no_kline}, skipped_side_flat={skipped_side_flat})",
            flush=True,
        )
        return 0

    m = compute_metrics(trades, total_bars=len(klines))

    # --- Coverage: fraction of predictions that became trades (after MT filter) ---
    n_predictions_total = len(preds)
    n_trades_triggered = len(trades)
    coverage = n_trades_triggered / n_predictions_total if n_predictions_total > 0 else 0.0

    # --- Precision @ confidence threshold ---
    # Among trades with confidence >= args.threshold (already filtered), what's the win rate?
    if triggered_confidences:
        avg_confidence = sum(triggered_confidences) / len(triggered_confidences)
    else:
        avg_confidence = float("nan")

    # Per-direction breakdown
    long_trades = [t for t in trades if t.side == "LONG"]
    short_trades = [t for t in trades if t.side == "SHORT"]

    def _dir_stats(ts_list):
        if not ts_list:
            return {}
        wins = sum(1 for t in ts_list if getattr(t, "net_ret", getattr(t, "ret", 0)) > 0)
        tp_count = sum(1 for t in ts_list if t.outcome == "TP")
        sl_count = sum(1 for t in ts_list if t.outcome == "SL")
        to_count = sum(1 for t in ts_list if t.outcome == "TIMEOUT")
        rets = [getattr(t, "net_ret", getattr(t, "ret", 0)) for t in ts_list]
        return {
            "n": len(ts_list),
            "win_rate": wins / len(ts_list),
            "tp": tp_count,
            "sl": sl_count,
            "timeout": to_count,
            "avg_ret_pct": (sum(rets) / len(rets)) * 100 if rets else 0.0,
        }

    long_stats = _dir_stats(long_trades)
    short_stats = _dir_stats(short_trades)

    # --- Per-outcome average returns ---
    tp_trades = [t for t in trades if t.outcome == "TP"]
    sl_trades = [t for t in trades if t.outcome == "SL"]
    to_trades = [t for t in trades if t.outcome == "TIMEOUT"]

    def _avg_ret(ts_list):
        if not ts_list:
            return float("nan")
        rets = [getattr(t, "net_ret", getattr(t, "ret", 0)) for t in ts_list]
        return sum(rets) / len(rets) * 100

    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"LIVE / LOGGED EVAL (with 4h/1d filter, Scheme B)  [{now_utc}]")
    print(sep)
    print(
        f"  interval={args.interval}  symbol={args.symbol}  "
        f"active_model={args.active_model}"
    )
    if args.model_version:
        print(f"  model_version={args.model_version}")
    print(
        f"  params: threshold={args.threshold:.2f}  "
        f"tp={args.tp*100:.2f}%  sl={args.sl*100:.2f}%  "
        f"fee/side={args.fee*100:.4f}%  slippage/side={args.slippage*100:.4f}%  "
        f"horizon={horizon}  timeout_exit={args.timeout_exit}  tie={args.tie_breaker}"
    )
    print()
    print("  SIGNAL STATS")
    print(f"    Total predictions loaded   : {n_predictions_total}")
    print(f"    Skipped (no kline match)   : {skipped_no_kline}")
    print(f"    Filtered (MT / threshold)  : {skipped_side_flat}")
    print(f"    Trades triggered           : {n_trades_triggered}")
    print(f"    Coverage (trades/preds)    : {coverage:.3f}")
    if not math.isnan(avg_confidence):
        print(f"    Avg confidence @ trigger   : {avg_confidence:.4f}")
    if triggered_cal_confidences:
        avg_cal_conf = sum(triggered_cal_confidences) / len(triggered_cal_confidences)
        print(f"    Avg cal_confidence @ trigger: {avg_cal_conf:.4f}")
    print()
    print("  STRATEGY METRICS")
    print(
        f"    Signals/week  : {m.signals_per_week:.2f}  "
        f"n_trade={m.n_trade} (long={m.n_long} short={m.n_short})"
    )
    print(f"    TP={m.tp}  SL={m.sl}  TIMEOUT={m.timeout}")
    print(f"    Win rate      : {m.win_rate:.3f}")
    print(f"    Avg return    : {m.avg_ret*100:.3f}%")
    print(f"    Profit factor : {m.profit_factor:.3f}")
    print(f"    Avg ret TP    : {_avg_ret(tp_trades):.3f}%")
    print(f"    Avg ret SL    : {_avg_ret(sl_trades):.3f}%")
    print(f"    Avg ret TO    : {_avg_ret(to_trades):.3f}%")
    print()
    print("  RISK / DRAWDOWN")
    print(
        f"    MDD(trade_seq)={m.mdd_trade_seq*100:.2f}%  "
        f"MDD(hourly)={m.mdd_hourly*100:.2f}%  "
        f"MDD(daily)={m.mdd_daily*100:.2f}%"
    )
    print(f"    Max consec losses: {m.max_consec_losses}")
    print()
    if long_stats:
        ls = long_stats
        print(
            f"  LONG  : n={ls['n']}  win={ls['win_rate']:.3f}  "
            f"TP={ls['tp']}  SL={ls['sl']}  TO={ls['timeout']}  "
            f"avg_ret={ls['avg_ret_pct']:.3f}%"
        )
    if short_stats:
        ss = short_stats
        print(
            f"  SHORT : n={ss['n']}  win={ss['win_rate']:.3f}  "
            f"TP={ss['tp']}  SL={ss['sl']}  TO={ss['timeout']}  "
            f"avg_ret={ss['avg_ret_pct']:.3f}%"
        )
    print(sep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
