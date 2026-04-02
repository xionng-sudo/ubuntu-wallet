#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest event_v3 via local ml-service HTTP endpoint.

Signal rule:
  signal at time t -> enter at open[t+1]

Exit rule:
  Supports single-stage or two-stage take-profit.

Single-stage:
  within next horizon bars, hit TP or SL first; else TIMEOUT.

Two-stage:
  - initial SL active from entry
  - if TP1 hit first, partially take profit
  - remaining position gets protected by entry + be_offset
  - remaining position exits at TP2 / protected stop / timeout

Realism:
- --horizon-bars
- --slippage (adverse, per side)
- --timeout-exit
- bars_to_exit distribution
- MDD (trade-sequence), plus hourly/daily aggregated MDD
- max consecutive losses

Position mode:
- --position-mode stack  (default): open every eligible signal
- --position-mode single: only one position at a time; skip signals while in position

Optimization:
- --objective selects ranking function across grid

Debug / alignment:
- --side-source signal|probs
- --mt-filter-mode off|strict|relaxed|trend_guard|daily_guard|conflict|regime|layered
- --debug-best prints side-count diagnostics for the best config

Caching:
- Adds "prediction disk cache" so same (symbol, interval, window, model_version) does not re-hit ml-service
  for every grid config.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import os as _os
import sys as _sys

_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SCRIPT_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPT_DIR)

# Optional: load per-symbol config from configs/symbols.yaml
try:
    from symbol_config import get_symbol_config as _get_symbol_config  # type: ignore
except ImportError:
    _get_symbol_config = None  # type: ignore

# Hard-coded fallback defaults (used when --symbol is not provided and CLI flag is absent).
# Exposed at module level so tests can verify the expected default values without running
# the full backtest pipeline.
_BACKTEST_DEFAULTS: Dict[str, Any] = {
    "interval": "1h",
    "horizon_bars": 24,
    "thresholds": "0.55:0.85:0.02",
    "tp_grid": "0.005:0.030:0.0025",
    "sl_grid": "0.003:0.020:0.001",
}
# Step sizes used when a YAML single value is collapsed to a single-point grid range.
# Threshold uses 0.01 (enough precision for calibrated probabilities).
# TP/SL use 0.001 (0.1%) matching practical position sizing resolution.
_YAML_GRID_STEP_THR: float = 0.01
_YAML_GRID_STEP_TP: float = 0.001
_YAML_GRID_STEP_SL: float = 0.001

from signal_logic import (  # noqa: E402
    normalize_predict_response,
    select_effective_probs,
    decide_side,
    decide_side_from_signal,
    apply_mt_filter_with_context,
    normalize_mt_mode,
)

from decision_pipeline import decide_side_from_cached_pred  # noqa: E402


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
class CachedPred:
    signal: str
    confidence: Optional[float]
    calibrated_confidence: Optional[float]
    calibration_method: Optional[str]

    raw_p_long: Optional[float]
    raw_p_short: Optional[float]
    raw_p_flat: Optional[float]

    cal_p_long: Optional[float]
    cal_p_short: Optional[float]
    cal_p_flat: Optional[float]

    effective_long: Optional[float]
    effective_short: Optional[float]

    selected_prob_source: str
    selected_p_long: Optional[float]
    selected_p_short: Optional[float]
    selected_p_flat: Optional[float]

    threshold_enter: Optional[float]
    reasons: List[str]
    model_version: str


def predict_payload(interval: str, as_of_ts: str) -> Dict[str, Any]:
    return {"interval": interval, "as_of_ts": as_of_ts}


def call_predict(base_url: str, payload: Dict[str, Any], timeout_s: int = 60) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/predict"
    r = requests.post(url, json=payload, timeout=timeout_s)
    r.raise_for_status()
    return r.json()


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _symbol_from_data_dir(data_dir: str) -> str:
    # data_dir like "data/BTCUSDT" or "/home/.../data/BTCUSDT"
    base = os.path.basename(os.path.normpath(data_dir.rstrip("/")))
    return base


def _cache_key(
    *,
    key_mode: str,
    symbol: str,
    interval: str,
    since: Optional[str],
    until: Optional[str],
    warmup_bars: int,
    model_version: str,
) -> str:
    """
    Returns a stable cache key string, later hashed for filename safety.
    """
    key_mode = (key_mode or "").strip().lower()
    if key_mode == "interval_window":
        return f"symbol={symbol}|interval={interval}|since={since}|until={until}|warmup={warmup_bars}"
    # default: include model_version to avoid stale cache across model updates
    return f"symbol={symbol}|interval={interval}|since={since}|until={until}|warmup={warmup_bars}|model={model_version}"


def _cache_path(pred_cache_dir: Path, cache_key: str, fmt: str) -> Path:
    fmt = (fmt or "jsonl").strip().lower()
    ext = "jsonl" if fmt == "jsonl" else "json"
    return pred_cache_dir / f"pred_cache__{_sha1(cache_key)}.{ext}"


def _serialize_cached_pred(cp: CachedPred) -> Dict[str, Any]:
    return {
        "signal": cp.signal,
        "confidence": cp.confidence,
        "calibrated_confidence": cp.calibrated_confidence,
        "calibration_method": cp.calibration_method,
        "raw_p_long": cp.raw_p_long,
        "raw_p_short": cp.raw_p_short,
        "raw_p_flat": cp.raw_p_flat,
        "cal_p_long": cp.cal_p_long,
        "cal_p_short": cp.cal_p_short,
        "cal_p_flat": cp.cal_p_flat,
        "effective_long": cp.effective_long,
        "effective_short": cp.effective_short,
        "selected_prob_source": cp.selected_prob_source,
        "selected_p_long": cp.selected_p_long,
        "selected_p_short": cp.selected_p_short,
        "selected_p_flat": cp.selected_p_flat,
        "threshold_enter": cp.threshold_enter,
        "reasons": cp.reasons,
        "model_version": cp.model_version,
    }


def _deserialize_cached_pred(d: Dict[str, Any]) -> CachedPred:
    return CachedPred(
        signal=str(d.get("signal", "")),
        confidence=d.get("confidence", None),
        calibrated_confidence=d.get("calibrated_confidence", None),
        calibration_method=d.get("calibration_method", None),
        raw_p_long=d.get("raw_p_long", None),
        raw_p_short=d.get("raw_p_short", None),
        raw_p_flat=d.get("raw_p_flat", None),
        cal_p_long=d.get("cal_p_long", None),
        cal_p_short=d.get("cal_p_short", None),
        cal_p_flat=d.get("cal_p_flat", None),
        effective_long=d.get("effective_long", None),
        effective_short=d.get("effective_short", None),
        selected_prob_source=str(d.get("selected_prob_source", "")),
        selected_p_long=d.get("selected_p_long", None),
        selected_p_short=d.get("selected_p_short", None),
        selected_p_flat=d.get("selected_p_flat", None),
        threshold_enter=d.get("threshold_enter", None),
        reasons=list(d.get("reasons", [])) if d.get("reasons") is not None else [],
        model_version=str(d.get("model_version", "")),
    )


def _load_pred_cache_jsonl(path: Path) -> Dict[str, CachedPred]:
    """
    JSONL format:
    - First line: {"meta": {...}}
    - Following lines: {"as_of_ts": "...Z", "pred": {...}}
    """
    if not path.exists():
        return {}

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return {}

    out: Dict[str, CachedPred] = {}
    # ignore meta line (line0), tolerate missing meta
    for line in lines[1:] if lines else []:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        as_of_ts = str(rec["as_of_ts"])
        pred = _deserialize_cached_pred(rec["pred"])
        out[as_of_ts] = pred
    return out


def _write_pred_cache_jsonl(path: Path, meta: Dict[str, Any], pred_map: Dict[str, CachedPred]) -> None:
    # deterministic order
    items = sorted(pred_map.items(), key=lambda kv: kv[0])
    lines: List[str] = []
    lines.append(json.dumps({"meta": meta}, ensure_ascii=False))
    for as_of_ts, cp in items:
        lines.append(json.dumps({"as_of_ts": as_of_ts, "pred": _serialize_cached_pred(cp)}, ensure_ascii=False))
    _atomic_write_text(path, "\n".join(lines) + "\n")


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


def _calc_leg_ret(side: str, entry_exec: float, exit_exec: float, fee_per_side: float) -> float:
    ret_gross = (exit_exec - entry_exec) / entry_exec if side == "LONG" else (entry_exec - exit_exec) / entry_exec
    return ret_gross - 2.0 * fee_per_side


def _price_at_pct(side: str, entry: float, pct: float) -> float:
    if side == "LONG":
        return entry * (1.0 + pct)
    return entry * (1.0 - pct)


def _stop_price_at_offset(side: str, entry: float, offset: float) -> float:
    if side == "LONG":
        return entry * (1.0 + offset)
    return entry * (1.0 - offset)


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
    use_two_stage_tp: bool = False,
    tp1_ratio: float = 0.70,
    tp1_size: float = 0.60,
    be_offset: float = 0.002,
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

    last_idx = min(i + horizon_bars, len(klines) - 1)

    if not use_two_stage_tp:
        tp = _price_at_pct(side, entry, tp_pct)
        sl = _price_at_pct("SHORT" if side == "LONG" else "LONG", entry, sl_pct)

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
                ret_net = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
                return TradeResult("TP", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, j - (i + 1))

            if hit_sl:
                exit_price = sl
                exit_ts = bar["ts"]
                exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
                ret_net = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
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
        ret_net = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
        return TradeResult("TIMEOUT", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)

    tp1_pct = tp_pct * tp1_ratio
    tp1_size = max(0.0, min(1.0, tp1_size))
    rem_size = 1.0 - tp1_size

    tp1_price = _price_at_pct(side, entry, tp1_pct)
    tp2_price = _price_at_pct(side, entry, tp_pct)
    init_sl_price = _price_at_pct("SHORT" if side == "LONG" else "LONG", entry, sl_pct)
    be_stop_price = _stop_price_at_offset(side, entry, be_offset)

    tp1_hit = False
    tp1_exit_ts = None
    tp1_exit_price = None
    bars_held = 0

    for j in range(i + 1, last_idx + 1):
        bar = klines[j]
        high = float(bar["high"])
        low = float(bar["low"])
        bars_held = j - (i + 1)

        if not tp1_hit:
            if side == "LONG":
                hit_tp1 = high >= tp1_price
                hit_sl = low <= init_sl_price
            else:
                hit_tp1 = low <= tp1_price
                hit_sl = high >= init_sl_price

            if hit_tp1 and hit_sl:
                if tie_breaker.upper() == "TP":
                    hit_sl = False
                else:
                    hit_tp1 = False

            if hit_sl:
                exit_price = init_sl_price
                exit_ts = bar["ts"]
                exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
                ret_net = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
                return TradeResult("SL", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)

            if hit_tp1:
                tp1_hit = True
                tp1_exit_ts = bar["ts"]
                tp1_exit_price = tp1_price
                if rem_size <= 1e-12:
                    exit_exec = _apply_slippage_exit(side, tp1_price, slippage_per_side)
                    ret_net = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
                    return TradeResult("TP1", side, entry_ts, entry, entry_exec, tp1_exit_ts, tp1_price, exit_exec, ret_net, bars_held)
                continue

        else:
            if side == "LONG":
                hit_tp2 = high >= tp2_price
                hit_be = low <= be_stop_price
            else:
                hit_tp2 = low <= tp2_price
                hit_be = high >= be_stop_price

            if hit_tp2 and hit_be:
                if tie_breaker.upper() == "TP":
                    hit_be = False
                else:
                    hit_tp2 = False

            tp1_exit_exec = _apply_slippage_exit(side, tp1_exit_price, slippage_per_side)
            ret_part1 = _calc_leg_ret(side, entry_exec, tp1_exit_exec, fee_per_side)

            if hit_tp2:
                exit_price = tp2_price
                exit_ts = bar["ts"]
                exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
                ret_part2 = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
                ret_net = tp1_size * ret_part1 + rem_size * ret_part2
                return TradeResult("TP2", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)

            if hit_be:
                exit_price = be_stop_price
                exit_ts = bar["ts"]
                exit_exec = _apply_slippage_exit(side, exit_price, slippage_per_side)
                ret_part2 = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
                ret_net = tp1_size * ret_part1 + rem_size * ret_part2
                return TradeResult("TP1_BE", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)

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

    if not tp1_hit:
        ret_net = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
        return TradeResult("TIMEOUT", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)

    tp1_exit_exec = _apply_slippage_exit(side, tp1_exit_price, slippage_per_side)
    ret_part1 = _calc_leg_ret(side, entry_exec, tp1_exit_exec, fee_per_side)
    ret_part2 = _calc_leg_ret(side, entry_exec, exit_exec, fee_per_side)
    ret_net = tp1_size * ret_part1 + rem_size * ret_part2
    return TradeResult("TP1_TIMEOUT", side, entry_ts, entry, entry_exec, exit_ts, exit_price, exit_exec, ret_net, bars_held)


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

    tp_trades = [t for t in trades if t.outcome in ("TP", "TP1", "TP2", "TP1_BE", "TP1_TIMEOUT")]
    sl_trades = [t for t in trades if t.outcome == "SL"]
    to_trades = [t for t in trades if t.outcome in ("TIMEOUT", "TP1_TIMEOUT")]

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
    use_two_stage_tp: bool,
    tp1_ratio: float,
    tp1_size: float,
    be_offset: float,
) -> None:
    print(f"Model version: {model_version}")
    print(f"Grid: thresholds={n_thresholds} tp={n_tp} sl={n_sl}")
    print(
        f"Backtest bars: {n_bars} from {start_ts} to {end_ts}\n"
        f"Exec: horizon={horizon} bars, fee/side={fee*100:.4f}%, slippage/side={slippage*100:.4f}%, "
        f"timeout_exit={timeout_exit}, tie={tie_breaker}, objective={objective}, "
        f"position_mode={position_mode}, side_source={side_source}, mt_filter_mode={normalize_mt_mode(mt_filter_mode)}"
    )
    print(
        f"Two-stage TP: enabled={use_two_stage_tp} tp1_ratio={tp1_ratio:.2f} tp1_size={tp1_size:.2f} be_offset={be_offset*100:.2f}%"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data-dir", required=True, help="Per-symbol kline data directory (e.g. data/ETHUSDT).")
    ap.add_argument(
        "--symbol",
        default=None,
        help=(
            "Symbol name (e.g. ETHUSDT).  When provided, loads per-symbol defaults from "
            "configs/symbols.yaml for --interval, --horizon-bars, --thresholds, --tp-grid, "
            "and --sl-grid unless those flags are explicitly supplied on the CLI."
        ),
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:9000")
    ap.add_argument(
        "--interval",
        default=None,
        help="Kline interval (e.g. 1h).  Defaults to per-symbol config when --symbol is set, else '1h'.",
    )
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--min-signals-per-week", type=float, default=5.0)
    ap.add_argument("--tie-breaker", choices=["SL", "TP"], default="SL")
    ap.add_argument(
        "--horizon-bars",
        type=int,
        default=None,
        help="Max holding period in bars.  Defaults to per-symbol config when --symbol is set, else 24.",
    )
    ap.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")
    ap.add_argument("--position-mode", choices=["stack", "single"], default="stack")
    ap.add_argument("--objective", choices=["pf", "avg_ret", "avg_ret_mdd_daily", "avg_ret_mdd_hourly"], default="avg_ret_mdd_daily")
    ap.add_argument(
        "--thresholds",
        default=None,
        help=(
            "Threshold grid spec start:end:step.  When --symbol is set and this flag is omitted, "
            "defaults to a single-point range from configs/symbols.yaml (e.g. 0.84:0.84:0.01)."
        ),
    )
    ap.add_argument(
        "--tp-grid",
        default=None,
        help=(
            "TP grid spec start:end:step.  When --symbol is set and this flag is omitted, "
            "defaults to a single-point range from configs/symbols.yaml."
        ),
    )
    ap.add_argument(
        "--sl-grid",
        default=None,
        help=(
            "SL grid spec start:end:step.  When --symbol is set and this flag is omitted, "
            "defaults to a single-point range from configs/symbols.yaml."
        ),
    )
    ap.add_argument("--warmup-bars", type=int, default=200)
    ap.add_argument("--sleep-ms", type=int, default=0)

    ap.add_argument("--side-source", choices=["signal", "probs"], default="probs")
    ap.add_argument(
        "--mt-filter-mode",
        choices=["off", "long_only", "symmetric", "strict", "relaxed", "trend_guard", "daily_guard", "conflict", "regime", "layered"],
        default="daily_guard",
    )
    ap.add_argument("--use-two-stage-tp", action="store_true")
    ap.add_argument("--tp1-ratio", type=float, default=0.70)
    ap.add_argument("--tp1-size", type=float, default=0.60)
    ap.add_argument("--be-offset", type=float, default=0.002)
    ap.add_argument("--debug-best", action="store_true")

    # prediction disk cache options
    ap.add_argument("--pred-cache", choices=["on", "off"], default="on")
    ap.add_argument("--pred-cache-dir", default="data/pred_cache")
    ap.add_argument("--pred-cache-format", choices=["jsonl"], default="jsonl")
    ap.add_argument("--pred-cache-key", choices=["interval_window", "model_interval_window"], default="model_interval_window")

    args = ap.parse_args()

    # ------------------------------------------------------------------
    # Resolve per-symbol defaults from configs/symbols.yaml
    # ------------------------------------------------------------------
    _yaml_cfg: Dict[str, Any] = {}
    _param_sources: Dict[str, str] = {}

    if args.symbol is not None and _get_symbol_config is not None:
        try:
            _yaml_cfg = _get_symbol_config(args.symbol)
        except Exception as exc:
            print(
                f"WARNING: could not load symbol config for {args.symbol}: {exc}",
                file=sys.stderr,
            )

    def _resolve_str(attr: str, yaml_key: str, default_val: str) -> str:
        cli_val = getattr(args, attr)
        if cli_val is not None:
            _param_sources[attr] = "CLI"
            return cli_val
        if _yaml_cfg and yaml_key in _yaml_cfg:
            _param_sources[attr] = "YAML"
            return str(_yaml_cfg[yaml_key])
        _param_sources[attr] = "default"
        return default_val

    def _resolve_int(attr: str, yaml_key: str, default_val: int) -> int:
        cli_val = getattr(args, attr)
        if cli_val is not None:
            _param_sources[attr] = "CLI"
            return int(cli_val)
        if _yaml_cfg and yaml_key in _yaml_cfg:
            _param_sources[attr] = "YAML"
            return int(_yaml_cfg[yaml_key])
        _param_sources[attr] = "default"
        return default_val

    # Resolve interval and horizon_bars
    args.interval = _resolve_str("interval", "interval", _BACKTEST_DEFAULTS["interval"])
    args.horizon_bars = _resolve_int("horizon_bars", "horizon", _BACKTEST_DEFAULTS["horizon_bars"])

    # Resolve grid params: when coming from YAML, collapse to a single-point range
    if args.thresholds is not None:
        _param_sources["thresholds"] = "CLI"
    elif _yaml_cfg and "threshold" in _yaml_cfg:
        _thr = float(_yaml_cfg["threshold"])
        args.thresholds = f"{_thr}:{_thr}:{_YAML_GRID_STEP_THR}"
        _param_sources["thresholds"] = "YAML"
    else:
        args.thresholds = _BACKTEST_DEFAULTS["thresholds"]
        _param_sources["thresholds"] = "default"

    if args.tp_grid is not None:
        _param_sources["tp_grid"] = "CLI"
    elif _yaml_cfg and "tp" in _yaml_cfg:
        _tp = float(_yaml_cfg["tp"])
        args.tp_grid = f"{_tp}:{_tp}:{_YAML_GRID_STEP_TP}"
        _param_sources["tp_grid"] = "YAML"
    else:
        args.tp_grid = _BACKTEST_DEFAULTS["tp_grid"]
        _param_sources["tp_grid"] = "default"

    if args.sl_grid is not None:
        _param_sources["sl_grid"] = "CLI"
    elif _yaml_cfg and "sl" in _yaml_cfg:
        _sl = float(_yaml_cfg["sl"])
        args.sl_grid = f"{_sl}:{_sl}:{_YAML_GRID_STEP_SL}"
        _param_sources["sl_grid"] = "YAML"
    else:
        args.sl_grid = _BACKTEST_DEFAULTS["sl_grid"]
        _param_sources["sl_grid"] = "default"

    # Print param source summary so alignment is auditable
    _sym_label = args.symbol or _symbol_from_data_dir(args.data_dir)
    print(
        f"[config] symbol={_sym_label}  "
        + "  ".join(
            f"{k}={getattr(args, k)} ({_param_sources.get(k, '?')})"
            for k in ("interval", "horizon_bars", "thresholds", "tp_grid", "sl_grid")
        )
    )

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

    # disk cache setup (loaded AFTER we discover model_version)
    disk_cache_enabled = (args.pred_cache == "on")
    pred_cache_dir = Path(args.pred_cache_dir)
    if disk_cache_enabled:
        _safe_mkdir(pred_cache_dir)

    def get_pred_no_disk(as_of_ts: str) -> CachedPred:
        if as_of_ts in pred_cache:
            return pred_cache[as_of_ts]

        raw = call_predict(args.base_url, predict_payload(args.interval, as_of_ts))
        snap = normalize_predict_response(raw)
        sel_p_long, sel_p_short, sel_p_flat, sel_source = select_effective_probs(snap)

        cp = CachedPred(
            signal=snap.signal,
            confidence=snap.confidence,
            calibrated_confidence=snap.calibrated_confidence,
            calibration_method=snap.calibration_method,
            raw_p_long=snap.p_long,
            raw_p_short=snap.p_short,
            raw_p_flat=snap.p_flat,
            cal_p_long=snap.cal_p_long,
            cal_p_short=snap.cal_p_short,
            cal_p_flat=snap.cal_p_flat,
            effective_long=snap.effective_long,
            effective_short=snap.effective_short,
            selected_prob_source=sel_source,
            selected_p_long=sel_p_long,
            selected_p_short=sel_p_short,
            selected_p_flat=sel_p_flat,
            threshold_enter=snap.threshold_enter,
            reasons=snap.reasons,
            model_version=snap.model_version,
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

    # ---- precompute predictions (with disk cache) ----
    print(f"Precomputing predictions for {len(usable)} bars via {args.base_url} ...")

    # Step 1: call one prediction to learn model_version (so cache key can include it)
    first_ts = klines[usable[0]]["ts"].isoformat().replace("+00:00", "Z")
    first_cp = get_pred_no_disk(first_ts)
    model_version = first_cp.model_version or ""
    selected_prob_sources = Counter()
    selected_prob_sources[first_cp.selected_prob_source] += 1

    # Step 2: if disk cache enabled, try load cache file for this window
    symbol = _symbol_from_data_dir(args.data_dir)
    cache_key = _cache_key(
        key_mode=args.pred_cache_key,
        symbol=symbol,
        interval=args.interval,
        since=args.since,
        until=args.until,
        warmup_bars=int(args.warmup_bars),
        model_version=model_version,
    )
    cache_path = _cache_path(pred_cache_dir, cache_key, args.pred_cache_format)

    disk_loaded: Dict[str, CachedPred] = {}
    if disk_cache_enabled and cache_path.exists():
        try:
            disk_loaded = _load_pred_cache_jsonl(cache_path)
            # merge into in-memory cache
            pred_cache.update(disk_loaded)
            # we already have first_cp in pred_cache; that's fine
            print(f"Loaded prediction cache: {cache_path} (rows={len(disk_loaded)})")
        except Exception as e:
            print(f"WARNING: failed to read pred cache {cache_path}: {e}", file=sys.stderr)

    def get_pred(as_of_ts: str) -> CachedPred:
        # in-memory cache first (may include disk-loaded)
        if as_of_ts in pred_cache:
            return pred_cache[as_of_ts]
        # otherwise call service
        return get_pred_no_disk(as_of_ts)

    # Step 3: precompute remaining bars
    for i in usable[1:]:
        as_of_ts = klines[i]["ts"].isoformat().replace("+00:00", "Z")
        cp = get_pred(as_of_ts)
        model_version = cp.model_version or model_version
        selected_prob_sources[cp.selected_prob_source] += 1

    # Step 4: write disk cache (only if enabled)
    if disk_cache_enabled:
        try:
            meta = {
                "symbol": symbol,
                "interval": args.interval,
                "since": args.since,
                "until": args.until,
                "warmup_bars": int(args.warmup_bars),
                "model_version": model_version,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "cache_key_mode": args.pred_cache_key,
            }
            _write_pred_cache_jsonl(cache_path, meta=meta, pred_map=pred_cache)
            print(f"Wrote prediction cache: {cache_path} (rows={len(pred_cache)})")
        except Exception as e:
            print(f"WARNING: failed to write pred cache {cache_path}: {e}", file=sys.stderr)

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
        use_two_stage_tp=args.use_two_stage_tp,
        tp1_ratio=args.tp1_ratio,
        tp1_size=args.tp1_size,
        be_offset=args.be_offset,
    )
    print(f"Selected probability source counts: {dict(selected_prob_sources)}")

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

                mt_reject_reasons: Counter = Counter()

                for i in usable:
                    sig_ts = klines[i]["ts"]

                    if args.position_mode == "single":
                        if next_allowed_ts is not None and sig_ts < next_allowed_ts:
                            continue

                    as_of_ts = sig_ts.isoformat().replace("+00:00", "Z")
                    cp = pred_cache[as_of_ts]

                    # Use shared decision pipeline (same logic as live_trader_perp_simulated.py)
                    _cached_dict = {
                        "signal": cp.signal,
                        "selected_p_long": cp.selected_p_long,
                        "selected_p_short": cp.selected_p_short,
                        "selected_p_flat": cp.selected_p_flat,
                        "selected_prob_source": cp.selected_prob_source,
                    }
                    # Always compute both sides for debug statistics
                    signal_side = decide_side_from_signal(cp.signal)
                    probs_side = decide_side(cp.selected_p_long, cp.selected_p_short, thr)

                    if signal_side == "LONG":
                        signal_long += 1
                    elif signal_side == "SHORT":
                        signal_short += 1
                    else:
                        signal_flat += 1

                    if probs_side == "LONG":
                        probs_long += 1
                    elif probs_side == "SHORT":
                        probs_short += 1
                    else:
                        probs_flat += 1

                    side, _dbg = decide_side_from_cached_pred(
                        _cached_dict,
                        side_source=args.side_source,
                        threshold=thr,
                    )

                    if side == "LONG":
                        raw_long += 1
                    elif side == "SHORT":
                        raw_short += 1
                    else:
                        raw_flat += 1

                    side_before_filter = side
                    side, _t4, _t1d, _reject_reason = apply_mt_filter_with_context(
                        side=side,
                        sig_ts=sig_ts,
                        trend_4h_at=trend_4h_at,
                        trend_1d_at=trend_1d_at,
                        mode=args.mt_filter_mode,
                        mt_reject_reasons=mt_reject_reasons,
                    )

                    if side_before_filter == "LONG" and side == "FLAT":
                        filtered_long += 1
                    if side_before_filter == "SHORT" and side == "FLAT":
                        filtered_short += 1

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
                        use_two_stage_tp=args.use_two_stage_tp,
                        tp1_ratio=args.tp1_ratio,
                        tp1_size=args.tp1_size,
                        be_offset=args.be_offset,
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
        print("No config satisfies min signals/week constraint. Try lowering --min-signals-per-week.", file=sys.stderr)
        return 3

    thr, tp, sl, m, _score, debug_info = best
    print("\n=== BEST CONFIG (grid objective) ===")
    print(
        f"threshold={thr:.2f} tp={tp*100:.2f}% sl={sl*100:.2f}% "
        f"fee/side={args.fee*100:.4f}% slippage/side={args.slippage*100:.4f}% "
        f"horizon={horizon} timeout_exit={args.timeout_exit} tie={args.tie_breaker} "
        f"objective={args.objective} position_mode={args.position_mode} "
        f"side_source={args.side_source} mt_filter_mode={normalize_mt_mode(args.mt_filter_mode)} "
        f"use_two_stage_tp={args.use_two_stage_tp} tp1_ratio={args.tp1_ratio:.2f} tp1_size={args.tp1_size:.2f} be_offset={args.be_offset*100:.2f}%"
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
