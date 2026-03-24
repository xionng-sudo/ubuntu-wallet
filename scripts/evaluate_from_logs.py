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
import os
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
                    "cal_proba_long": j.get("cal_proba_long"),
                    "cal_proba_short": j.get("cal_proba_short"),
                    "cal_proba_flat": j.get("cal_proba_flat"),
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
    ap = argparse.ArgumentParser(
        description=(
            "Evaluate live predictions from a JSONL log file. "
            "When --symbol is given, --log-path, --data-dir, --threshold, --tp, --sl "
            "and --horizon-bars are derived automatically from configs/symbols.yaml "
            "unless explicitly overridden on the command line."
        )
    )
    ap.add_argument(
        "--log-path",
        default=None,
        help=(
            "JSONL predictions log. "
            "Defaults to data/<SYMBOL>/predictions_log.jsonl when --symbol is set, "
            "otherwise data/predictions_log.jsonl."
        ),
    )
    ap.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Directory containing klines_*.json files. "
            "Defaults to data/<SYMBOL> when --symbol is set, otherwise data/."
        ),
    )
    ap.add_argument("--symbol", default=None, help="e.g. BTCUSDT; filters log by symbol and sets per-symbol defaults")
    ap.add_argument("--interval", default=None, help="kline interval, e.g. 1h (default from symbol config or 1h)")
    ap.add_argument("--model-version", default=None)
    ap.add_argument("--active-model", default="event_v3")
    ap.add_argument("--since", default=None, help="ISO8601, e.g. 2026-03-01T00:00:00Z")
    ap.add_argument("--until", default=None)

    ap.add_argument("--threshold", type=float, default=None, help="p_enter threshold, e.g. 0.55 (default from symbol config)")
    ap.add_argument("--tp", type=float, default=None, help="take profit pct, e.g. 0.0175 for 1.75%% (default from symbol config)")
    ap.add_argument("--sl", type=float, default=None, help="stop loss pct, e.g. 0.009 for 0.9%% (default from symbol config)")
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--horizon-bars", type=int, default=None, help="forward look-ahead bars (default from symbol config or 6)")
    ap.add_argument("--tie-breaker", choices=["SL", "TP"], default="SL")
    ap.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")
    ap.add_argument(
        "--mt-filter-mode",
        choices=["symmetric", "layered"],
        default="symmetric",
        help=(
            "MT filter mode (default: symmetric = original behavior, unchanged from before this PR). "
            "'symmetric' matches backtest Scheme B: 4h same-direction required, 1d not opposite. "
            "'layered' uses the unified mt_gate (ALLOW_STRONG / ALLOW_WEAK / REJECT); "
            "slightly more permissive — allows 4h=NEUTRAL+1d=same-direction as ALLOW_WEAK. "
            "Only use 'layered' when explicitly opting in for comparison or gradual rollout."
        ),
    )

    args = ap.parse_args()

    # Resolve per-symbol defaults when --symbol is given
    sym_cfg: dict = {}
    if args.symbol:
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        try:
            from symbol_paths import (  # type: ignore[import]
                get_symbol_config,
                get_symbol_data_dir,
                get_symbol_log_path,
            )
            sym_cfg = get_symbol_config(args.symbol)
            if args.data_dir is None:
                args.data_dir = get_symbol_data_dir(args.symbol)
            if args.log_path is None:
                args.log_path = get_symbol_log_path(args.symbol)
        except ImportError:
            pass

    # Apply final defaults
    if args.log_path is None:
        args.log_path = "data/predictions_log.jsonl"
    if args.data_dir is None:
        args.data_dir = "data"
    if args.interval is None:
        args.interval = sym_cfg.get("interval", "1h")
    if args.threshold is None:
        args.threshold = sym_cfg.get("threshold")
        if args.threshold is None:
            ap.error("--threshold is required (or provide --symbol to derive from configs/symbols.yaml)")
    if args.tp is None:
        args.tp = sym_cfg.get("tp")
        if args.tp is None:
            ap.error("--tp is required (or provide --symbol to derive from configs/symbols.yaml)")
    if args.sl is None:
        args.sl = sym_cfg.get("sl")
        if args.sl is None:
            ap.error("--sl is required (or provide --symbol to derive from configs/symbols.yaml)")
    if args.horizon_bars is None:
        args.horizon_bars = sym_cfg.get("horizon", 6)

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

        # Use calibrated probabilities for thresholding when they were logged.
        # cal_proba_long/short are the per-class calibrated values and are the
        # correct source for re-deriving signal direction (not calibrated_confidence,
        # which is derived from the signal already chosen at prediction time).
        cal_p_long = p.get("cal_proba_long")
        cal_p_short = p.get("cal_proba_short")
        if cal_p_long is not None and cal_p_short is not None:
            eff_p_long = float(cal_p_long)
            eff_p_short = float(cal_p_short)
        else:
            eff_p_long = float(p_long) if p_long is not None else 0.0
            eff_p_short = float(p_short) if p_short is not None else 0.0

        side = decide_side(eff_p_long, eff_p_short, args.threshold)

        # Multi-timeframe filter
        if side in ("LONG", "SHORT"):
            t4 = trend_4h_at(ts)
            t1d = trend_1d_at(ts)
            if args.mt_filter_mode == "layered":
                if not gate_allows(mt_gate(side, t4, t1d)):
                    side = "FLAT"
            else:
                # symmetric (default): Scheme B consistent with backtest
                if side == "LONG":
                    if t4 != "UP" or t1d == "DOWN":
                        side = "FLAT"
                else:  # SHORT
                    if t4 != "DOWN" or t1d == "UP":
                        side = "FLAT"

        if side == "FLAT":
            skipped_side_flat += 1
            continue

        # Track confidence of triggered signals
        conf = p.get("confidence") or 0.0
        cal_conf = p.get("calibrated_confidence")
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
        wins = sum(1 for t in ts_list if t.ret_net > 0)
        tp_count = sum(1 for t in ts_list if t.outcome == "TP")
        sl_count = sum(1 for t in ts_list if t.outcome == "SL")
        to_count = sum(1 for t in ts_list if t.outcome == "TIMEOUT")
        rets = [t.ret_net for t in ts_list]
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
    # Per-outcome average returns are available in m.avg_ret_tp/sl/to

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
    print(f"    Total predictions loaded    : {n_predictions_total}")
    print(f"    Skipped (no kline match)    : {skipped_no_kline}")
    print(f"    Filtered (MT / threshold)   : {skipped_side_flat}")
    print(f"    Trades triggered            : {n_trades_triggered}")
    print(f"    Coverage (trades/preds)     : {coverage:.3f}")
    if not math.isnan(avg_confidence):
        print(f"    Avg confidence @ trigger    : {avg_confidence:.4f}")
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
    print(f"    Avg ret TP    : {m.avg_ret_tp*100:.3f}%")
    print(f"    Avg ret SL    : {m.avg_ret_sl*100:.3f}%")
    print(f"    Avg ret TO    : {m.avg_ret_to*100:.3f}%")
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
