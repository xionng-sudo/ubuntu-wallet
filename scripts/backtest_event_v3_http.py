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

Debug / alignment:
- --side-source signal|probs
- --mt-filter-mode off|long_only|symmetric|layered
- --debug-best prints side-count diagnostics for the best config
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

import os as _os
import sys as _sys
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SCRIPT_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPT_DIR)

from mt_filter import mt_gate, gate_allows  # noqa: E402


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
                {
                    "ts": dt,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                }
            )
        out.sort(key=lambda x: x["ts"])
        return out

    if isinstance(data[0], list):
        for r in data:
            dt = _to_utc_dt(r[0])
            out.append(
                {
                    "ts": dt,
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                }
            )
        out.sort(key=lambda x: x["ts"])
        return out

    raise ValueError("Unsupported klines_1h.json format")


@dataclass(frozen=True)
class PredictOut:
    signal: str
    confidence: float
    reasons: List[str]
    model_version: str


@dataclass(frozen=True)
class CachedPred:
    signal: str
    confidence: float
    p_long: Optional[float]
    p_short: Optional[float]
    p_flat: Optional[float]
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


def decide_side_from_signal(signal: str) -> str:
    s = (signal or "").upper().strip()
    if s in ("LONG", "SHORT", "FLAT"):
        return s
    return "FLAT"


def _sma(vals: List[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= window:
            s -= vals[i - window]
        if i + 1 < window:
            out.append(None)
        else:
            out.append(s / window)
    return out


def _trend_series(
    klines: List[Dict[str, Any]],
    fast: int = 5,
    slow: int = 20,
    eps: float = 0.001,
) -> List[str]:
    """Return per-bar trend: 'UP' / 'DOWN' / 'NEUTRAL' based on fast/slow SMA."""
    closes = [float(k["close"]) for k in klines]
    ma_fast = _sma(closes, fast)
    ma_slow = _sma(closes, slow)
    out: List[str] = []
    for f, s in zip(ma_fast, ma_slow):
        if f is None or s is None:
            out.append("NEUTRAL")
        elif f > s * (1.0 + eps):
            out.append("UP")
        elif f < s * (1.0 - eps):
            out.append("DOWN")
        else:
            out.append("NEUTRAL")
    return out


def apply_mt_filter(
    side: str,
    sig_ts: datetime,
    trend_4h_at,
    trend_1d_at,
    mode: str,
) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """
    Returns:
      (final_side, trend_4h, trend_1d, reject_reason)
    """
    mode = (mode or "off").lower().strip()

    if side not in ("LONG", "SHORT"):
        return side, None, None, None

    if mode == "off":
        return side, None, None, None

    t4 = trend_4h_at(sig_ts)
    t1d = trend_1d_at(sig_ts)

    if mode == "long_only":
        if side == "LONG":
            if t4 != "UP":
                return "FLAT", t4, t1d, "long_t4_not_up"
            if t1d == "DOWN":
                return "FLAT", t4, t1d, "long_t1d_down"
        return side, t4, t1d, None

    if mode == "symmetric":
        if side == "LONG":
            if t4 != "UP":
                return "FLAT", t4, t1d, "long_t4_not_up"
            if t1d == "DOWN":
                return "FLAT", t4, t1d, "long_t1d_down"
            return side, t4, t1d, None

        if side == "SHORT":
            if t4 != "DOWN":
                return "FLAT", t4, t1d, "short_t4_not_down"
            if t1d == "UP":
                return "FLAT", t4, t1d, "short_t1d_up"
            return side, t4, t1d, None

    if mode == "layered":
        gate = mt_gate(side, t4, t1d)
        if gate_allows(gate):
            return side, t4, t1d, None
        return "FLAT", t4, t1d, f"layered_reject_{side.lower()}"

    raise ValueError(f"Unknown mt filter mode: {mode}")


def print_backtest_summary(
    *,
    model_version: str,
    n_thresholds: int,
    n_tp: int,
    n_sl: int,
    n_bars: int,
    start_ts,
    end_ts,
    horizon: int,
    fee: float,
    slippage: float,
    timeout_exit: str,
    tie_breaker: str,
    objective: str,
    position_mode: str,
    side_source: str,
    mt_filter_mode: str,
) -> None:
    print(f"Model version: {model_version}")
    print(f"Grid: thresholds={n_thresholds} tp={n_tp} sl={n_sl}")
    print(
        f"Backtest bars: {n_bars} from {start_ts} to {end_ts}\n"
        f"Exec: horizon={horizon} bars, fee/side={fee*100:.4f}%, slippage/side={slippage*100:.4f}%, "
        f"timeout_exit={timeout_exit}, tie={tie_breaker}, objective={objective}, "
        f"position_mode={position_mode}, side_source={side_source}, mt_filter_mode={mt_filter_mode}"
    )
    print(
        "\n[回测摘要]\n"
        f"- 模型版本: {model_version}\n"
        f"- 网格规模: 阈值 {n_thresholds} 个 × TP {n_tp} 个 × SL {n_sl} 个\n"
        f"- 回测区间: 共 {n_bars} 根 1h K 线，时间 {start_ts} → {end_ts}\n"
        f"- 执行设定: 持有期 {horizon} 根K线, 单边手续费 {fee*100:.4f}%, 单边滑点 {slippage*100:.4f}%\n"
        f"           超时平仓方式={timeout_exit}, TP/SL 同时触发时优先={tie_breaker}, 目标函数={objective}, 仓位模式={position_mode}\n"
        f"           信号来源={side_source}, 多周期过滤={mt_filter_mode}\n"
    )


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

    ap.add_argument("--side-source", choices=["signal", "probs"], default="probs")
    ap.add_argument("--mt-filter-mode", choices=["off", "long_only", "symmetric", "layered"], default="long_only")
    ap.add_argument("--debug-best", action="store_true")

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

    try:
        klines_4h = load_klines_1h(f"{args.data_dir.rstrip('/')}/klines_4h.json")
        klines_1d = load_klines_1h(f"{args.data_dir.rstrip('/')}/klines_1d.json")
    except Exception as e:
        print(f"ERROR: failed to load 4h/1d klines: {e}", file=sys.stderr)
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

    pred_cache: Dict[str, CachedPred] = {}

    def get_pred(as_of_ts: str) -> CachedPred:
        if as_of_ts in pred_cache:
            return pred_cache[as_of_ts]

        out = call_predict(args.base_url, predict_payload(args.interval, as_of_ts))
        p_long, p_short, p_flat = parse_probs_from_reasons(out.reasons)

        cp = CachedPred(
            signal=out.signal,
            confidence=out.confidence,
            p_long=p_long,
            p_short=p_short,
            p_flat=p_flat,
            reasons=out.reasons,
            model_version=out.model_version,
        )
        pred_cache[as_of_ts] = cp

        if args.sleep_ms > 0:
            time.sleep(args.sleep_ms / 1000.0)
        return cp

    horizon = int(args.horizon_bars)
    usable = [i for i in indices if i >= args.warmup_bars and i + horizon < len(klines)]
    if len(usable) < 50:
        print(f"ERROR: usable bars too few: {len(usable)}", file=sys.stderr)
        return 2

    print(f"Precomputing predictions for {len(usable)} bars via {args.base_url} ...")
    model_version = ""
    for i in usable:
        as_of_ts = klines[i]["ts"].isoformat().replace("+00:00", "Z")
        cp = get_pred(as_of_ts)
        model_version = cp.model_version or model_version

    print_backtest_summary(
        model_version=model_version,
        n_thresholds=len(thresholds),
        n_tp=len(tp_grid),
        n_sl=len(sl_grid),
        n_bars=len(usable),
        start_ts=klines[usable[0]]["ts"],
        end_ts=klines[usable[-1]]["ts"],
        horizon=horizon,
        fee=args.fee,
        slippage=args.slippage,
        timeout_exit=args.timeout_exit,
        tie_breaker=args.tie_breaker,
        objective=args.objective,
        position_mode=args.position_mode,
        side_source=args.side_source,
        mt_filter_mode=args.mt_filter_mode,
    )

    best: Optional[Tuple[float, float, float, Metrics, Tuple[float, ...], Dict[str, Any]]] = None
    total_bars = len(usable)

    for thr in thresholds:
        for tp in tp_grid:
            for sl in sl_grid:
                trades: List[TradeResult] = []

                next_allowed_ts: Optional[datetime] = None

                raw_long = raw_short = raw_flat = 0
                filtered_long = filtered_short = 0
                final_long = final_short = 0

                signal_long = signal_short = signal_flat = 0
                probs_long = probs_short = probs_flat = 0

                mt_reject_reasons: Dict[str, int] = {}

                for i in usable:
                    sig_ts = klines[i]["ts"]

                    if args.position_mode == "single":
                        if next_allowed_ts is not None and sig_ts < next_allowed_ts:
                            continue

                    as_of_ts = sig_ts.isoformat().replace("+00:00", "Z")
                    cp = pred_cache[as_of_ts]

                    signal_side = decide_side_from_signal(cp.signal)
                    if signal_side == "LONG":
                        signal_long += 1
                    elif signal_side == "SHORT":
                        signal_short += 1
                    else:
                        signal_flat += 1

                    probs_side = decide_side(cp.p_long, cp.p_short, thr)
                    if probs_side == "LONG":
                        probs_long += 1
                    elif probs_side == "SHORT":
                        probs_short += 1
                    else:
                        probs_flat += 1

                    side = signal_side if args.side_source == "signal" else probs_side

                    if side == "LONG":
                        raw_long += 1
                    elif side == "SHORT":
                        raw_short += 1
                    else:
                        raw_flat += 1

                    side_before_filter = side
                    side, t4, t1d, reject_reason = apply_mt_filter(
                        side=side,
                        sig_ts=sig_ts,
                        trend_4h_at=trend_4h_at,
                        trend_1d_at=trend_1d_at,
                        mode=args.mt_filter_mode,
                    )

                    if side_before_filter == "LONG" and side == "FLAT":
                        filtered_long += 1
                    if side_before_filter == "SHORT" and side == "FLAT":
                        filtered_short += 1
                    if reject_reason:
                        mt_reject_reasons[reject_reason] = mt_reject_reasons.get(reject_reason, 0) + 1

                    if side == "LONG":
                        final_long += 1
                    elif side == "SHORT":
                        final_short += 1

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
                        next_allowed_ts = tr.exit_ts

                m = compute_metrics(trades, total_bars=total_bars)
                if m.signals_per_week < args.min_signals_per_week:
                    continue

                debug_info = {
                    "signal_side_counts": {"LONG": signal_long, "SHORT": signal_short, "FLAT": signal_flat},
                    "probs_side_counts": {"LONG": probs_long, "SHORT": probs_short, "FLAT": probs_flat},
                    "raw_side_counts": {"LONG": raw_long, "SHORT": raw_short, "FLAT": raw_flat},
                    "filtered_counts": {"LONG": filtered_long, "SHORT": filtered_short},
                    "final_side_counts": {"LONG": final_long, "SHORT": final_short},
                    "mt_reject_reasons": dict(sorted(mt_reject_reasons.items())),
                }

                score = _score_metrics(m, args.objective)
                if best is None or score > best[4]:
                    best = (thr, tp, sl, m, score, debug_info)

    if best is None:
        msg_en = "No config satisfies min signals/week constraint. Try lowering --min-signals-per-week."
        msg_zh = "没有任何参数组合满足每周最少信号数的约束，可以尝试降低 --min-signals-per-week。"
        print(msg_en, file=sys.stderr)
        print(msg_zh, file=sys.stderr)
        return 3

    thr, tp, sl, m, _score, debug_info = best
    print("\n=== BEST CONFIG (grid objective) ===")
    print(
        f"threshold={thr:.2f} tp={tp*100:.2f}% sl={sl*100:.2f}% "
        f"fee/side={args.fee*100:.4f}% slippage/side={args.slippage*100:.4f}% "
        f"horizon={horizon} timeout_exit={args.timeout_exit} tie={args.tie_breaker} "
        f"objective={args.objective} position_mode={args.position_mode} "
        f"side_source={args.side_source} mt_filter_mode={args.mt_filter_mode}"
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

    if args.debug_best:
        print("debug(best):")
        print(f"  signal_side_counts={debug_info['signal_side_counts']}")
        print(f"  probs_side_counts={debug_info['probs_side_counts']}")
        print(f"  raw_side_counts={debug_info['raw_side_counts']}")
        print(f"  filtered_counts={debug_info['filtered_counts']}")
        print(f"  final_side_counts={debug_info['final_side_counts']}")
        print(f"  mt_reject_reasons={debug_info['mt_reject_reasons']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
