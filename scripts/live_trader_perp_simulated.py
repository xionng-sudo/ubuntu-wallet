#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_trader_perp_simulated.py
==============================
Generic perpetual contract SIMULATED replay / DRY-RUN live trader.

Replays historical 1h klines sequentially for any configured symbol, calling
ml-service /predict for each bar (just as the live system would), then applies:
  - Multi-timeframe filtering (4h/1d trend, Scheme B)
  - Single-position risk engine (circuit breaker)
  - Triple-barrier exits: TP / SL / horizon timeout
  - Capital / equity curve tracking

All per-symbol parameters (tp, sl, horizon, threshold, interval) are loaded
automatically from ``configs/symbols.yaml`` when not overridden on the CLI.

Output:
  - Per-bar log to console
  - Final PnL summary
  - Equity curve written to data/<SYMBOL>/sim_equity.jsonl

This script is 100% DRY-RUN (no real orders).

Usage:
    # Single symbol (reads config from configs/symbols.yaml):
    python scripts/live_trader_perp_simulated.py --symbol ETHUSDT

    # All enabled symbols (sequential):
    python scripts/live_trader_perp_simulated.py --all-symbols

    # Override specific params for one symbol:
    python scripts/live_trader_perp_simulated.py \\
        --symbol BTCUSDT \\
        --tp 0.020 \\
        --sl 0.010 \\
        --horizon 12 \\
        --capital 10000 \\
        --since 2026-01-01T00:00:00Z \\
        --until 2026-03-01T00:00:00Z

Requirements:
    - ml-service running and accessible (default: http://127.0.0.1:9000)
    - data/<SYMBOL>/klines_1h.json, klines_4h.json, klines_1d.json present
    - scripts/mt_trend_utils.py in the same directory
    - configs/symbols.yaml present (optional; falls back to hardcoded defaults)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mt_trend_utils import MTTrendContext  # type: ignore

try:
    from symbol_config import (  # type: ignore
        get_symbol_config,
        list_enabled_symbols,
        data_dir as _data_dir,
    )
except ImportError:
    # Graceful fallback when symbol_config is not available
    def get_symbol_config(symbol: str) -> Dict[str, Any]:  # type: ignore[misc]
        return {
            "enabled": True,
            "interval": "1h",
            "threshold": 0.65,
            "tp": 0.0175,
            "sl": 0.009,
            "horizon": 12,
            "calibration": "isotonic",
        }

    def list_enabled_symbols() -> List[str]:  # type: ignore[misc]
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]

    def _data_dir(symbol: str, base_data_dir: Optional[str] = None) -> str:  # type: ignore[misc]
        base = base_data_dir or os.path.join(REPO_ROOT, "data")
        return os.path.join(base, symbol)


# ---------------------------------------------------------------------------
# Klines loader
# ---------------------------------------------------------------------------

def _to_utc_dt(ts: Any) -> datetime:
    if isinstance(ts, (int, float)):
        if ts > 10_000_000_000:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(ts)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_klines(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data:
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(data[0], dict):
        for r in data:
            ts = r.get("timestamp") or r.get("ts") or r.get("open_time") or r.get("time") or r.get("t")
            out.append({
                "ts": _to_utc_dt(ts),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
            })
    elif isinstance(data[0], list):
        for r in data:
            out.append({
                "ts": _to_utc_dt(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
            })
    out.sort(key=lambda x: x["ts"])
    return out


# ---------------------------------------------------------------------------
# ml-service client
# ---------------------------------------------------------------------------

_WARMUP_PHRASES = (
    "not enough",
    "klines rows",
    "warmup",
    "insufficient",
)


def call_predict(
    base_url: str,
    symbol: str,
    as_of_ts: str,
    interval: str = "1h",
    timeout: int = 20,
) -> Dict[str, Any]:
    """Call /predict and raise on error.  Returns parsed JSON body."""
    payload = {"symbol": symbol, "interval": interval, "as_of_ts": as_of_ts}
    resp = requests.post(f"{base_url}/predict", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def is_warmup_error(exc: Exception) -> bool:
    """Return True if *exc* represents a model warmup / insufficient-data 503."""
    msg = str(exc).lower()
    if "503" not in msg:
        return False
    return any(phrase in msg for phrase in _WARMUP_PHRASES)


def get_warmup_detail(exc: Exception) -> Optional[str]:
    """Try to extract the detail string from a requests HTTPError response body."""
    try:
        resp = exc.response  # type: ignore[attr-defined]
        body = resp.json()
        return str(body.get("detail", ""))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Position / trade tracking
# ---------------------------------------------------------------------------

@dataclass
class Position:
    side: str             # "LONG" | "SHORT"
    entry_price: float
    entry_ts: datetime
    tp_price: float
    sl_price: float
    horizon_exit_ts: Optional[datetime]
    notional_usdt: float
    bar_index: int


@dataclass
class ClosedTrade:
    side: str
    entry_price: float
    exit_price: float
    entry_ts: datetime
    exit_ts: datetime
    outcome: str          # "TP" | "SL" | "TIMEOUT"
    pnl_pct: float
    pnl_usdt: float
    fee_usdt: float


# ---------------------------------------------------------------------------
# Risk engine
# ---------------------------------------------------------------------------

class SimpleRiskEngine:
    """Single-position, circuit-breaker risk shell for simulated trading."""

    def __init__(
        self,
        capital: float,
        position_fraction: float = 0.30,
        leverage: float = 5.0,
        max_consec_losses: int = 3,
        fee_per_side: float = 0.0004,
    ):
        self.capital = capital
        self.initial_capital = capital
        self.position_fraction = position_fraction
        self.leverage = leverage
        self.max_consec_losses = max_consec_losses
        self.fee_per_side = fee_per_side

        self.position: Optional[Position] = None
        self.consec_losses: int = 0
        self.paused: bool = False
        self.closed_trades: List[ClosedTrade] = []
        self.equity_curve: List[Dict[str, Any]] = []

    def can_open(self) -> bool:
        return not self.paused and self.position is None

    def open_position(
        self,
        side: str,
        price: float,
        ts: datetime,
        bar_index: int,
        tp_pct: float,
        sl_pct: float,
        horizon_bars: int,
        klines_1h: List[Dict[str, Any]],
    ) -> None:
        if not self.can_open():
            return
        notional = self.position_fraction * self.capital
        if side == "LONG":
            tp = price * (1.0 + tp_pct)
            sl = price * (1.0 - sl_pct)
        else:  # SHORT
            tp = price * (1.0 - tp_pct)
            sl = price * (1.0 + sl_pct)

        exit_bar_idx = min(bar_index + horizon_bars, len(klines_1h) - 1)
        horizon_exit_ts = klines_1h[exit_bar_idx]["ts"]

        self.position = Position(
            side=side,
            entry_price=price,
            entry_ts=ts,
            tp_price=tp,
            sl_price=sl,
            horizon_exit_ts=horizon_exit_ts,
            notional_usdt=notional,
            bar_index=bar_index,
        )
        print(
            f"  [OPEN]  {side} @ {price:.4f}  TP={tp:.4f}  SL={sl:.4f}  "
            f"notional={notional:.2f} USDT  ts={ts.isoformat()}"
        )

    def check_exit(self, kline: Dict[str, Any]) -> Optional[ClosedTrade]:
        if self.position is None:
            return None

        pos = self.position
        h = kline["high"]
        lo = kline["low"]
        ts = kline["ts"]

        outcome = None
        exit_price = None

        if pos.side == "LONG":
            hit_tp = h >= pos.tp_price
            hit_sl = lo <= pos.sl_price
            if hit_tp and hit_sl:
                outcome = "SL"
                exit_price = pos.sl_price
            elif hit_tp:
                outcome = "TP"
                exit_price = pos.tp_price
            elif hit_sl:
                outcome = "SL"
                exit_price = pos.sl_price
            elif pos.horizon_exit_ts and ts >= pos.horizon_exit_ts:
                outcome = "TIMEOUT"
                exit_price = kline["close"]
        else:  # SHORT
            hit_tp = lo <= pos.tp_price
            hit_sl = h >= pos.sl_price
            if hit_tp and hit_sl:
                outcome = "SL"
                exit_price = pos.sl_price
            elif hit_tp:
                outcome = "TP"
                exit_price = pos.tp_price
            elif hit_sl:
                outcome = "SL"
                exit_price = pos.sl_price
            elif pos.horizon_exit_ts and ts >= pos.horizon_exit_ts:
                outcome = "TIMEOUT"
                exit_price = kline["close"]

        if outcome is None:
            return None

        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

        fee = self.fee_per_side * 2 * pos.notional_usdt
        pnl_usdt = pnl_pct * pos.notional_usdt * self.leverage - fee

        trade = ClosedTrade(
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_ts=pos.entry_ts,
            exit_ts=ts,
            outcome=outcome,
            pnl_pct=pnl_pct,
            pnl_usdt=pnl_usdt,
            fee_usdt=fee,
        )

        self.capital += pnl_usdt
        self.position = None
        self.closed_trades.append(trade)

        if pnl_usdt < 0:
            self.consec_losses += 1
            if self.consec_losses >= self.max_consec_losses:
                self.paused = True
                print(f"  [CIRCUIT-BREAKER] {self.consec_losses} consecutive losses → trading paused")
        else:
            self.consec_losses = 0

        symbol = "✓" if pnl_usdt >= 0 else "✗"
        print(
            f"  [CLOSE] {symbol} {trade.side} {outcome} "
            f"entry={trade.entry_price:.4f} exit={exit_price:.4f} "
            f"pnl={pnl_pct*100:.3f}% pnl_usdt={pnl_usdt:.2f} "
            f"capital={self.capital:.2f} ts={ts.isoformat()}"
        )

        return trade

    def record_equity(self, ts: datetime) -> None:
        self.equity_curve.append({
            "ts": ts.isoformat().replace("+00:00", "Z"),
            "capital": round(self.capital, 4),
            "n_trades": len(self.closed_trades),
        })


# ---------------------------------------------------------------------------
# Main simulation loop (single symbol)
# ---------------------------------------------------------------------------

def run_simulation(
    symbol: str,
    data_dir: str,
    base_url: str,
    tp_pct: float,
    sl_pct: float,
    horizon_bars: int,
    threshold: float,
    capital: float = 10_000.0,
    position_fraction: float = 0.30,
    leverage: float = 5.0,
    fee_per_side: float = 0.0004,
    max_consec_losses: int = 3,
    since: Optional[str] = None,
    until: Optional[str] = None,
    request_delay_s: float = 0.0,
    interval: str = "1h",
    output_equity_path: Optional[str] = None,
) -> None:
    print(f"\n[sim:{symbol}] Loading klines from {data_dir} ...")
    try:
        klines_1h = load_klines(os.path.join(data_dir, "klines_1h.json"))
        klines_4h = load_klines(os.path.join(data_dir, "klines_4h.json"))
        klines_1d = load_klines(os.path.join(data_dir, "klines_1d.json"))
    except FileNotFoundError as exc:
        print(f"[sim:{symbol}] ERROR: klines file not found — {exc}")
        return

    if not klines_1h:
        print(f"[sim:{symbol}] ERROR: no 1h klines found")
        return

    mt_ctx = MTTrendContext(klines_4h, klines_1d)
    engine = SimpleRiskEngine(
        capital=capital,
        position_fraction=position_fraction,
        leverage=leverage,
        max_consec_losses=max_consec_losses,
        fee_per_side=fee_per_side,
    )

    since_dt = _to_utc_dt(since) if since else None
    until_dt = _to_utc_dt(until) if until else None
    if since_dt:
        klines_1h = [k for k in klines_1h if k["ts"] >= since_dt]
    if until_dt:
        klines_1h = [k for k in klines_1h if k["ts"] <= until_dt]

    print(
        f"[sim:{symbol}] Replaying {len(klines_1h)} 1h bars. "
        f"tp={tp_pct*100:.2f}% sl={sl_pct*100:.2f}% horizon={horizon_bars}h "
        f"threshold={threshold} capital={capital:.2f}"
    )

    n_signals = 0
    n_flat_mt = 0
    n_warmup = 0

    for i, bar in enumerate(klines_1h):
        ts = bar["ts"]
        price = bar["close"]
        as_of_ts = ts.isoformat().replace("+00:00", "Z")

        engine.check_exit(bar)

        if i % 24 == 0:
            engine.record_equity(ts)

        try:
            result = call_predict(
                base_url=base_url,
                symbol=symbol,
                as_of_ts=as_of_ts,
                interval=interval,
            )
        except Exception as e:
            if is_warmup_error(e):
                n_warmup += 1
                detail = get_warmup_detail(e) or str(e)
                if n_warmup == 1:
                    # Only print on first warmup so we don't spam
                    print(f"  [WARMUP] {as_of_ts}: {detail} (subsequent warmup bars suppressed)")
                if request_delay_s > 0:
                    time.sleep(request_delay_s)
                continue
            print(f"  [SKIP] {as_of_ts}: predict failed: {e}")
            if request_delay_s > 0:
                time.sleep(request_delay_s)
            continue

        signal = result.get("signal", "FLAT")
        confidence = result.get("confidence", 0.0)
        eff_confidence = result.get("calibrated_confidence") or confidence

        if signal == "LONG":
            t4h = mt_ctx.trend_4h_at(ts)
            t1d = mt_ctx.trend_1d_at(ts)
            if t4h != "UP":
                n_flat_mt += 1
                signal = "FLAT"
            elif t1d == "DOWN":
                n_flat_mt += 1
                signal = "FLAT"
        elif signal == "SHORT":
            t4h = mt_ctx.trend_4h_at(ts)
            t1d = mt_ctx.trend_1d_at(ts)
            if t4h != "DOWN":
                n_flat_mt += 1
                signal = "FLAT"
            elif t1d == "UP":
                n_flat_mt += 1
                signal = "FLAT"

        if signal != "FLAT" and eff_confidence < threshold:
            signal = "FLAT"

        if signal in ("LONG", "SHORT") and engine.can_open():
            n_signals += 1
            engine.open_position(
                side=signal,
                price=price,
                ts=ts,
                bar_index=i,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                horizon_bars=horizon_bars,
                klines_1h=klines_1h,
            )

        if request_delay_s > 0:
            time.sleep(request_delay_s)

    # Close any open position at end of replay
    if engine.position is not None and klines_1h:
        last = klines_1h[-1]
        exit_price = last["close"]
        pos = engine.position
        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price
        fee = fee_per_side * 2 * pos.notional_usdt
        pnl_usdt = pnl_pct * pos.notional_usdt * leverage - fee
        engine.capital += pnl_usdt
        print(
            f"  [EOD-CLOSE] {pos.side} @ {exit_price:.4f} "
            f"pnl={pnl_pct*100:.3f}% pnl_usdt={pnl_usdt:.2f} capital={engine.capital:.2f}"
        )
        engine.position = None

    if klines_1h:
        engine.record_equity(klines_1h[-1]["ts"])

    # Summary
    trades = engine.closed_trades
    n = len(trades)
    if n > 0:
        tp_count = sum(1 for t in trades if t.outcome == "TP")
        sl_count = sum(1 for t in trades if t.outcome == "SL")
        to_count = sum(1 for t in trades if t.outcome == "TIMEOUT")
        wins = sum(1 for t in trades if t.pnl_usdt > 0)
        win_rate = wins / n
        avg_ret = sum(t.pnl_pct for t in trades) / n
        total_pnl = sum(t.pnl_usdt for t in trades)
        eq = [e["capital"] for e in engine.equity_curve]
        max_cap = max(eq)
        mdd = max((max_cap - c) / max_cap for c in eq) if eq else 0.0

        print("\n" + "=" * 60)
        print(f"SIMULATION COMPLETE  [{symbol}]")
        print(f"  Bars replayed   : {len(klines_1h)}")
        print(f"  Warmup skipped  : {n_warmup}")
        print(f"  Signals sent    : {n_signals}")
        print(f"  MT filtered     : {n_flat_mt}")
        print(f"  Trades          : {n} (TP={tp_count} SL={sl_count} TO={to_count})")
        print(f"  Win rate        : {win_rate:.3f}")
        print(f"  Avg return/trade: {avg_ret*100:.3f}%")
        print(f"  Total PnL       : {total_pnl:.2f} USDT")
        print(f"  Initial capital : {engine.initial_capital:.2f} USDT")
        print(f"  Final capital   : {engine.capital:.2f} USDT")
        print(f"  Return          : {(engine.capital/engine.initial_capital - 1)*100:.2f}%")
        print(f"  Max drawdown    : {mdd*100:.2f}%")
        print("=" * 60)
    else:
        print(f"\n[sim:{symbol}] No trades generated.")
        print(
            f"  Bars replayed: {len(klines_1h)}, warmup skipped: {n_warmup}, "
            f"signals sent: {n_signals}, MT filtered: {n_flat_mt}"
        )

    # Write equity curve
    if output_equity_path is None:
        output_equity_path = os.path.join(data_dir, "sim_equity.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(output_equity_path)), exist_ok=True)
    with open(output_equity_path, "w", encoding="utf-8") as f:
        for rec in engine.equity_curve:
            f.write(json.dumps(rec) + "\n")
    print(f"\n[sim:{symbol}] Equity curve written to {output_equity_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Perpetual contract simulated replay trader (DRY-RUN). "
            "Per-symbol parameters (tp, sl, horizon, threshold, interval) are loaded "
            "automatically from configs/symbols.yaml unless overridden."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sym_group = ap.add_mutually_exclusive_group()
    sym_group.add_argument(
        "--symbol",
        default="ETHUSDT",
        help="Trading pair symbol to simulate (e.g. BTCUSDT, ETHUSDT).",
    )
    sym_group.add_argument(
        "--all-symbols",
        action="store_true",
        default=False,
        help=(
            "Run simulation for every enabled symbol in configs/symbols.yaml "
            "(sequential; inherits per-symbol parameters)."
        ),
    )
    ap.add_argument(
        "--data-base-dir",
        default=os.path.join(REPO_ROOT, "data"),
        help=(
            "Base data directory.  Per-symbol data is read from "
            "<data-base-dir>/<SYMBOL>/klines_*.json."
        ),
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:9000")
    # Optional overrides (if not supplied, value is taken from configs/symbols.yaml)
    ap.add_argument(
        "--interval",
        default=None,
        help="Kline interval override (e.g. 1h).  Defaults to per-symbol config.",
    )
    ap.add_argument(
        "--tp",
        type=float,
        default=None,
        help="Take-profit fraction override (e.g. 0.0175 = 1.75%%).  Defaults to per-symbol config.",
    )
    ap.add_argument(
        "--sl",
        type=float,
        default=None,
        help="Stop-loss fraction override (e.g. 0.009 = 0.9%%).  Defaults to per-symbol config.",
    )
    ap.add_argument(
        "--horizon",
        type=int,
        default=None,
        help="Max holding period in bars override.  Defaults to per-symbol config.",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Min confidence to enter override.  Defaults to per-symbol config.",
    )
    ap.add_argument("--capital", type=float, default=10_000.0, help="Initial capital in USDT.")
    ap.add_argument(
        "--position-fraction",
        type=float,
        default=0.30,
        help="Fraction of capital per position (e.g. 0.30 = 30%%).",
    )
    ap.add_argument("--leverage", type=float, default=5.0, help="Leverage multiplier.")
    ap.add_argument("--fee", type=float, default=0.0004, help="Fee per side (e.g. 0.0004 = 0.04%%).")
    ap.add_argument("--max-consec-losses", type=int, default=3, help="Circuit breaker: consecutive losses.")
    ap.add_argument("--since", default=None, help="Start time, e.g. 2026-02-01T00:00:00Z.")
    ap.add_argument("--until", default=None, help="End time, e.g. 2026-03-10T00:00:00Z.")
    ap.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between /predict calls (set >0 to reduce load on ml-service).",
    )
    ap.add_argument("--output-equity", default=None, help="Path for equity curve JSONL output.")
    return ap


def _run_for_symbol(symbol: str, args: argparse.Namespace) -> None:
    """Run simulation for a single symbol, merging CLI overrides with symbol config."""
    cfg = get_symbol_config(symbol)
    tp_pct = args.tp if args.tp is not None else float(cfg["tp"])
    sl_pct = args.sl if args.sl is not None else float(cfg["sl"])
    horizon_bars = args.horizon if args.horizon is not None else int(cfg["horizon"])
    threshold = args.threshold if args.threshold is not None else float(cfg["threshold"])
    interval = args.interval if args.interval is not None else str(cfg.get("interval", "1h"))

    sym_data_dir = _data_dir(symbol, base_data_dir=args.data_base_dir)
    output_equity = args.output_equity  # None → derived per-symbol inside run_simulation

    run_simulation(
        symbol=symbol,
        data_dir=sym_data_dir,
        base_url=args.base_url,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        horizon_bars=horizon_bars,
        threshold=threshold,
        capital=args.capital,
        position_fraction=args.position_fraction,
        leverage=args.leverage,
        fee_per_side=args.fee,
        max_consec_losses=args.max_consec_losses,
        since=args.since,
        until=args.until,
        request_delay_s=args.delay,
        interval=interval,
        output_equity_path=output_equity,
    )


if __name__ == "__main__":
    ap = _build_parser()
    args = ap.parse_args()

    if args.all_symbols:
        symbols = list_enabled_symbols()
        if not symbols:
            print("ERROR: no enabled symbols found in configs/symbols.yaml", file=sys.stderr)
            sys.exit(1)
        print(f"[sim] Running for all {len(symbols)} enabled symbols: {', '.join(symbols)}")
        for sym in symbols:
            _run_for_symbol(sym, args)
    else:
        _run_for_symbol(args.symbol, args)
