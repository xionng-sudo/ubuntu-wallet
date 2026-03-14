#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest event_v3 via local ml-service HTTP endpoint.

Signal rule:
  signal at time t -> enter at open[t+1]

Exit rule (triple barrier):
  within next horizon bars, hit TP or SL first; else TIMEOUT.

Realism:
- --horizon-bars
- --slippage (adverse, per side)
- --timeout-exit
- bars_to_exit distribution
- MDD (trade-sequence), plus hourly/daily aggregated MDD
- max consecutive losses

Position mode:
- --position-mode stack  (default): open every eligible signal (current behavior)
- --position-mode single: only one position at a time; skip signals while in position

Optimization:
- --objective selects ranking function across grid

Outputs:
- TP/SL/TO decomposition:
  avg_ret_tp, avg_ret_sl, avg_ret_to, timeout_win_rate
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


def _to_utc_dt(ts: Any) -> datetime:
    if ts is None:
        raise ValueError("timestamp is None")
    if isinstance(ts, (int, float)):
        if ts > 10_000_000_000:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(ts)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_klines_1h(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data:
        return []

    out: List[Dict[str, Any]] = []
    if isinstance(data[0], dict):
        for r in data:
            ts = r.get("timestamp") or r.get("open_time") or r.get("time") or r.get("t")
            dt = _to_utc_dt(ts)
            out.append(
                {"ts": dt, "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"])}
            )
        out.sort(key=lambda x: x["ts"])
        return out

    if isinstance(data[0], list):
        for r in data:
            dt = _to_utc_dt(r[0])
            out.append({"ts": dt, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        out.sort(key=lambda x: x["ts"])
        return out

    raise ValueError("Unsupported klines_1h.json format")


@dataclass(frozen=True)
class PredictOut:
    signal: str
    confidence: float
    reasons: List[str]
    model_version: str


def predict_payload(interval: str, as_of_ts: str) -> Dict[str, Any]:
    return {"interval": interval, "as_of_ts": as_of_ts}


def call_predict(base_url: str, payload: Dict[str, Any], timeout_s: int = 20) -> PredictOut:
    url = base_url.rstrip("/") + "/predict"
    r = requests.post(url, json=payload, timeout=timeout_s)
    r.raise_for_status()
    j = r.json()
    return PredictOut(
        signal=str(j.get("signal")),
        confidence=float(j.get("confidence", 0.0)),
        reasons=list(j.get("reasons") or []),
        model_version=str(j.get("model_version") or ""),
    )


def parse_probs_from_reasons(reasons: List[str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    p_long = p_short = p_flat = None
    for s in reasons:
        if "p_long=" in s and "p_short=" in s and "p_flat=" in s:
            parts = s.replace(":", " ").replace(",", " ").split()
            for token in parts:
                if token.startswith("p_long="):
                    try:
                        p_long = float(token.split("=", 1)[1])
                    except Exception:
                        pass
                elif token.startswith("p_short="):
                    try:
                        p_short = float(token.split("=", 1)[1])
                    except Exception:
                        pass
                elif token.startswith("p_flat="):
                    try:
                        p_flat = float(token.split("=", 1)[1])
                    except Exception:
                        pass
    return p_long, p_short, p_flat


@dataclass
class TradeResult:
    outcome: str
    side: str
    entry_ts: datetime
    entry: float
    entry_exec: float
    exit_ts: datetime
    exit_price: float
    exit_exec: float
    ret_net: float
    bars_held: int


def _apply_slippage_entry(side: str, price: float, slippage: float) -> float:
    s = max(0.0, float(slippage))
    if side == "LONG":
        return price * (1.0 + s)
    if side == "SHORT":
        return price * (1.0 - s)
    return price


def _apply_slippage_exit(side: str, price: float, slippage: float) -> float:
    s = max(0.0, float(slippage))
    if side == "LONG":
        return price * (1.0 - s)
    if side == "SHORT":
        return price * (1.0 + s)
    return price


def simulate_trade(
    klines: List[Dict[str, Any]],
    i: int,
    side: str,
    tp_pct: float,
    sl_pct: float,
    fee_per_side: float,
    slippage_per_side: float,
    horizon_bars: int,
    tie_breaker: str = "SL",
    timeout_exit: str = "close",
) -> TradeResult:
    k = klines[i]
    if side == "FLAT":
        return TradeResult("NO_TRADE", "FLAT", k["ts"], float("nan"), float("nan"), k["ts"], float("nan"), float("nan"), 0.0, 0)

    if i + 1 >= len(klines):
        return TradeResult("NO_TRADE", side, k["ts"], float("nan"), float("nan"), k["ts"], float("nan"), float("nan"), 0.0, 0)

    entry_bar = klines[i + 1]
    entry = float(entry_bar["open"])
    entry_ts = entry_bar["ts"]
    entry_exec = _apply_slippage_entry(side, entry, slippage_per_side)

    if side == "LONG":
        tp = entry * (1.0 + tp_pct)
        sl = entry * (1.0 - sl_pct)
    else:
        tp = entry * (1.0 - tp_pct)
        sl = entry * (1.0 + sl_pct)

    last_idx = min(i + horizon_bars, len(klines) - 1)

    for j in range(i + 1, last_idx + 1):
        bar = klines[j]
        high = float(bar["high"])
        low = float(bar["low"])

        if side == "LONG":
            hit_tp = high >= tp
            hit_sl = low <= sl
        else:
            hit_tp = low <= tp
            hit_sl = high >= sl

        if hit_tp and hit_sl:
            if tie_breaker.upper() == "TP":
                hit_sl = False
            else:
                hit_tp = False

        if hit_tp:
            exit_price = tp
            exit_ts = bar["ts"]
            exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
            ret_gross = (exit_exec - entry_exec) / entry_exec if side == "LONG" else (entry_exec - exit_exec) / entry_exec
            ret_net = ret_gross - 2.0 * fee_per_side
            return TradeResult("TP", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, j - (i + 1))

        if hit_sl:
            exit_price = sl
            exit_ts = bar["ts"]
            exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
            ret_gross = (exit_exec - entry_exec) / entry_exec if side == "LONG" else (entry_exec - exit_exec) / entry_exec
            ret_net = ret_gross - 2.0 * fee_per_side
            return TradeResult("SL", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, j - (i + 1))

    bar = klines[last_idx]
    exit_ts = bar["ts"]
    if timeout_exit == "open_next" and last_idx + 1 < len(klines):
        exit_price = float(klines[last_idx + 1]["open"])
        exit_ts = klines[last_idx + 1]["ts"]
        bars_held = (last_idx + 1) - (i + 1)
    else:
        exit_price = float(bar["close"])
        bars_held = last_idx - (i + 1)

    exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
    ret_gross = (exit_exec - entry_exec) / entry_exec if side == "LONG" else (entry_exec - exit_exec) / entry_exec
    ret_net = ret_gross - 2.0 * fee_per_side
    return TradeResult("TIMEOUT", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)


def _percentile(sorted_vals: List[int], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if p <= 0:
        return float(sorted_vals[0])
    if p >= 100:
        return float(sorted_vals[-1])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return float(d0 + d1)


def _compute_mdd_from_rets(rets: List[float]) -> float:
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= (1.0 + r)
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        mdd = max(mdd, dd)
    return mdd


def _max_consecutive_losses(rets: List[float]) -> int:
    best = 0
    cur = 0
    for r in rets:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _aggregate_rets_by_hour(trades: List[TradeResult]) -> List[float]:
    buckets: Dict[datetime, List[float]] = {}
    for t in trades:
        ts = t.exit_ts.astimezone(timezone.utc)
        hour_key = ts.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour_key, []).append(t.ret_net)

    out: List[float] = []
    for k in sorted(buckets.keys()):
        eq = 1.0
        for r in buckets[k]:
            eq *= (1.0 + r)
        out.append(eq - 1.0)
    return out


def _aggregate_rets_by_day(trades: List[TradeResult]) -> List[float]:
    buckets: Dict[date, List[float]] = {}
    for t in trades:
        d = t.exit_ts.astimezone(timezone.utc).date()
        buckets.setdefault(d, []).append(t.ret_net)

    out: List[float] = []
    for k in sorted(buckets.keys()):
        eq = 1.0
        for r in buckets[k]:
            eq *= (1.0 + r)
        out.append(eq - 1.0)
    return out


@dataclass
class Metrics:
    n_trade: int
    n_long: int
    n_short: int
    tp: int
    sl: int
    timeout: int

    avg_ret: float
    profit_factor: float
    win_rate: float
    signals_per_week: float

    avg_ret_tp: float
    avg_ret_sl: float
    avg_ret_to: float
    timeout_win_rate: float

    mdd_trade_seq: float
    mdd_hourly: float
    mdd_daily: float
    max_consec_losses: int

    bars_to_exit_min: Optional[int]
    bars_to_exit_median: Optional[float]
    bars_to_exit_p90: Optional[float]
    bars_to_exit_max: Optional[int]


def compute_metrics(trades: List[TradeResult], total_bars: int, bars_per_week: float = 24 * 7) -> Metrics:
    n_trade = len(trades)
    n_long = sum(1 for t in trades if t.side == "LONG")
    n_short = sum(1 for t in trades if t.side == "SHORT")

    tp_trades = [t for t in trades if t.outcome == "TP"]
    sl_trades = [t for t in trades if t.outcome == "SL"]
    to_trades = [t for t in trades if t.outcome == "TIMEOUT"]

    tp = len(tp_trades)
    sl = len(sl_trades)
    timeout = len(to_trades)

    rets = [t.ret_net for t in trades]
    avg_ret = sum(rets) / len(rets) if rets else 0.0

    gross_profit = sum(r for r in rets if r > 0)
    gross_loss = -sum(r for r in rets if r < 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = (sum(1 for r in rets if r > 0) / len(rets)) if rets else 0.0

    weeks = max(total_bars / bars_per_week, 1e-9)
    signals_per_week = n_trade / weeks

    def _avg(xs: List[float]) -> float:
        return (sum(xs) / len(xs)) if xs else 0.0

    avg_ret_tp = _avg([t.ret_net for t in tp_trades])
    avg_ret_sl = _avg([t.ret_net for t in sl_trades])
    avg_ret_to = _avg([t.ret_net for t in to_trades])
    timeout_win_rate = (sum(1 for t in to_trades if t.ret_net > 0) / len(to_trades)) if to_trades else 0.0

    mdd_trade_seq = _compute_mdd_from_rets(rets) if rets else 0.0
    mdd_hourly = _compute_mdd_from_rets(_aggregate_rets_by_hour(trades)) if trades else 0.0
    mdd_daily = _compute_mdd_from_rets(_aggregate_rets_by_day(trades)) if trades else 0.0
    max_consec_losses = _max_consecutive_losses(rets)

    bars_to_exit = sorted([t.bars_held for t in trades])
    if bars_to_exit:
        bars_min = int(bars_to_exit[0])
        bars_med = float(statistics.median(bars_to_exit))
        bars_p90 = _percentile(bars_to_exit, 90)
        bars_max = int(bars_to_exit[-1])
    else:
        bars_min = bars_max = None
        bars_med = bars_p90 = None

    return Metrics(
        n_trade=n_trade,
        n_long=n_long,
        n_short=n_short,
        tp=tp,
        sl=sl,
        timeout=timeout,
        avg_ret=avg_ret,
        profit_factor=profit_factor,
        win_rate=win_rate,
        signals_per_week=signals_per_week,
        avg_ret_tp=avg_ret_tp,
        avg_ret_sl=avg_ret_sl,
        avg_ret_to=avg_ret_to,
        timeout_win_rate=timeout_win_rate,
        mdd_trade_seq=mdd_trade_seq,
        mdd_hourly=mdd_hourly,
        mdd_daily=mdd_daily,
        max_consec_losses=max_consec_losses,
        bars_to_exit_min=bars_min,
        bars_to_exit_median=bars_med,
        bars_to_exit_p90=bars_p90,
        bars_to_exit_max=bars_max,
    )


def _score_metrics(m: Metrics, objective: str) -> Tuple[float, ...]:
    obj = objective.lower().strip()
    if obj == "avg_ret":
        return (m.avg_ret, -m.mdd_daily, -m.max_consec_losses, m.signals_per_week)
    if obj == "avg_ret_mdd_hourly":
        return (m.avg_ret - 0.50 * m.mdd_hourly, -m.mdd_hourly, -m.max_consec_losses, m.signals_per_week)
    if obj == "avg_ret_mdd_daily":
        return (m.avg_ret - 0.50 * m.mdd_daily, -m.mdd_daily, -m.max_consec_losses, m.signals_per_week)
    if obj == "pf":
        return (m.profit_factor, m.avg_ret, -m.mdd_daily, -m.max_consec_losses, m.signals_per_week)
    raise ValueError(f"Unknown objective: {objective}")


def decide_side(p_long: Optional[float], p_short: Optional[float], threshold: float) -> str:
    if p_long is None or p_short is None:
        return "FLAT"
    if p_long >= threshold and p_long >= p_short:
        return "LONG"
    if p_short >= threshold and p_short > p_long:
        return "SHORT"
    return "FLAT"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:9000")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--min-signals-per-week", type=float, default=5.0)
    ap.add_argument("--tie-breaker", choices=["SL", "TP"], default="SL")
    ap.add_argument("--horizon-bars", type=int, default=24)
    ap.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")
    ap.add_argument("--position-mode", choices=["stack", "single"], default="stack")
    ap.add_argument("--objective", choices=["pf", "avg_ret", "avg_ret_mdd_daily", "avg_ret_mdd_hourly"], default="avg_ret_mdd_daily")
    ap.add_argument("--thresholds", default="0.55:0.85:0.02")
    ap.add_argument("--tp-grid", default="0.005:0.030:0.0025")
    ap.add_argument("--sl-grid", default="0.003:0.020:0.001")
    ap.add_argument("--warmup-bars", type=int, default=200)
    ap.add_argument("--sleep-ms", type=int, default=0)
    args = ap.parse_args()

    if args.horizon_bars <= 0:
        print("ERROR: --horizon-bars must be > 0", file=sys.stderr)
        return 2
    if args.fee < 0 or args.slippage < 0:
        print("ERROR: --fee and --slippage must be >= 0", file=sys.stderr)
        return 2

    klines = load_klines_1h(f"{args.data_dir.rstrip('/')}/klines_1h.json")
    if not klines or len(klines) < args.warmup_bars + 50:
        print(f"ERROR: not enough klines rows: {len(klines)}", file=sys.stderr)
        return 2

    since_dt = _to_utc_dt(args.since) if args.since else None
    until_dt = _to_utc_dt(args.until) if args.until else None

    indices: List[int] = []
    for i, k in enumerate(klines):
        ts = k["ts"]
        if since_dt and ts < since_dt:
            continue
        if until_dt and ts > until_dt:
            continue
        indices.append(i)
    if not indices:
        print("ERROR: no bars in selected time window", file=sys.stderr)
        return 2

    def _parse_range(spec: str) -> List[float]:
        a, b, step = [float(x) for x in spec.split(":")]
        out: List[float] = []
        x = a
        while x <= b + 1e-12:
            out.append(round(x, 10))
            x += step
        return out

    thresholds = _parse_range(args.thresholds)
    tp_grid = _parse_range(args.tp_grid)
    sl_grid = _parse_range(args.sl_grid)

    pred_cache: Dict[str, Tuple[Optional[float], Optional[float], Optional[float], str]] = {}

    def get_probs(as_of_ts: str) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
        if as_of_ts in pred_cache:
            return pred_cache[as_of_ts]
        out = call_predict(args.base_url, predict_payload(args.interval, as_of_ts))
        p_long, p_short, p_flat = parse_probs_from_reasons(out.reasons)
        pred_cache[as_of_ts] = (p_long, p_short, p_flat, out.model_version)
        if args.sleep_ms > 0:
            time.sleep(args.sleep_ms / 1000.0)
        return pred_cache[as_of_ts]

    horizon = int(args.horizon_bars)
    usable = [i for i in indices if i >= args.warmup_bars and i + horizon < len(klines)]
    if len(usable) < 50:
        print(f"ERROR: usable bars too few: {len(usable)}", file=sys.stderr)
        return 2

    print(f"Precomputing predictions for {len(usable)} bars via {args.base_url} ...")
    model_version = ""
    for i in usable:
        as_of_ts = klines[i]["ts"].isoformat().replace("+00:00", "Z")
        _p_long, _p_short, _p_flat, mv = get_probs(as_of_ts)
        model_version = mv or model_version

    print(f"Model version: {model_version}")
    print(f"Grid: thresholds={len(thresholds)} tp={len(tp_grid)} sl={len(sl_grid)}")
    print(
        f"Backtest bars: {len(usable)} from {klines[usable[0]]['ts']} to {klines[usable[-1]]['ts']}\n"
        f"Exec: horizon={horizon} bars, fee/side={args.fee*100:.4f}%, slippage/side={args.slippage*100:.4f}%, "
        f"timeout_exit={args.timeout_exit}, tie={args.tie_breaker}, objective={args.objective}, position_mode={args.position_mode}"
    )

    best: Optional[Tuple[float, float, float, Metrics, Tuple[float, ...]]] = None
    total_bars = len(usable)

    for thr in thresholds:
        for tp in tp_grid:
            for sl in sl_grid:
                trades: List[TradeResult] = []

                # single-position gating
                next_allowed_ts: Optional[datetime] = None

                for i in usable:
                    sig_ts = klines[i]["ts"]

                    if args.position_mode == "single":
                        if next_allowed_ts is not None and sig_ts < next_allowed_ts:
                            continue

                    as_of_ts = sig_ts.isoformat().replace("+00:00", "Z")
                    p_long, p_short, _p_flat, _mv = pred_cache[as_of_ts]
                    side = decide_side(p_long, p_short, thr)
                    if side == "FLAT":
                        continue

                    tr = simulate_trade(
                        klines=klines,
                        i=i,
                        side=side,
                        tp_pct=tp,
                        sl_pct=sl,
                        fee_per_side=args.fee,
                        slippage_per_side=args.slippage,
                        horizon_bars=horizon,
                        tie_breaker=args.tie_breaker,
                        timeout_exit=args.timeout_exit,
                    )
                    if tr.outcome == "NO_TRADE":
                        continue

                    trades.append(tr)

                    if args.position_mode == "single":
                        # lock until exit time (inclusive-ish). Next signal must be at/after exit_ts.
                        next_allowed_ts = tr.exit_ts

                m = compute_metrics(trades, total_bars=total_bars)
                if m.signals_per_week < args.min_signals_per_week:
                    continue

                score = _score_metrics(m, args.objective)
                if best is None or score > best[4]:
                    best = (thr, tp, sl, m, score)

    if best is None:
        print("No config satisfies min signals/week constraint. Try lowering --min-signals-per-week.", file=sys.stderr)
        return 3

    thr, tp, sl, m, _score = best
    print("\n=== BEST CONFIG (grid objective) ===")
    print(
        f"threshold={thr:.2f} tp={tp*100:.2f}% sl={sl*100:.2f}% "
        f"fee/side={args.fee*100:.4f}% slippage/side={args.slippage*100:.4f}% "
        f"horizon={horizon} timeout_exit={args.timeout_exit} tie={args.tie_breaker} "
        f"objective={args.objective} position_mode={args.position_mode}"
    )
    print(
        "metrics: "
        f"signals/week={m.signals_per_week:.2f} "
        f"n_trade={m.n_trade} (long={m.n_long} short={m.n_short}) "
        f"TP={m.tp} SL={m.sl} TO={m.timeout} "
        f"win_rate={m.win_rate:.3f} "
        f"avg_ret={m.avg_ret*100:.3f}% "
        f"profit_factor={m.profit_factor:.3f}"
    )
    print(
        "decompose: "
        f"avg_ret_tp={m.avg_ret_tp*100:.3f}% "
        f"avg_ret_sl={m.avg_ret_sl*100:.3f}% "
        f"avg_ret_to={m.avg_ret_to*100:.3f}% "
        f"timeout_win_rate={m.timeout_win_rate:.3f}"
    )
    print(
        "risk/realism: "
        f"MDD(trade_seq)={m.mdd_trade_seq*100:.2f}% "
        f"MDD(hourly)={m.mdd_hourly*100:.2f}% "
        f"MDD(daily)={m.mdd_daily*100:.2f}% "
        f"max_consec_losses={m.max_consec_losses} "
        f"bars_to_exit(min/median/p90/max)="
        f"{m.bars_to_exit_min}/"
        f"{(f'{m.bars_to_exit_median:.1f}' if m.bars_to_exit_median is not None else 'na')}/"
        f"{(f'{m.bars_to_exit_p90:.1f}' if m.bars_to_exit_p90 is not None else 'na')}/"
        f"{m.bars_to_exit_max}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
