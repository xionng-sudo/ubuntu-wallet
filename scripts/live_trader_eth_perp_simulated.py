#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_trader_eth_perp_simulated.py
==================================
ETHUSDT perpetual contract SIMULATED replay / DRY-RUN live trader.

Replays historical 1h klines sequentially, calling ml-service /predict
for each bar (just as the live system would), then applies:
  - Multi-timeframe filtering (4h/1d trend, Scheme B)
  - ETH perp risk engine (single position, circuit breaker)
  - Triple-barrier exits: TP / SL / horizon timeout
  - Capital / equity curve tracking

Output:
  - Per-bar log to console
  - Final PnL summary
  - Equity curve written to <data_dir>/eth_perp_sim_equity.jsonl

This script is 100% DRY-RUN (no real orders). Use it to validate strategy
logic before connecting to a live exchange.

Usage:
    # Make sure ml-service is running on port 9000
    python scripts/live_trader_eth_perp_simulated.py

    # Custom parameters:
    python scripts/live_trader_eth_perp_simulated.py \\
        --data-dir data \\
        --base-url http://127.0.0.1:9000 \\
        --tp 0.0175 \\
        --sl 0.007 \\
        --horizon 6 \\
        --capital 10000 \\
        --since 2026-02-01T00:00:00Z \\
        --until 2026-03-10T00:00:00Z

Requirements:
    - ml-service running and accessible
    - data/klines_1h.json, data/klines_4h.json, data/klines_1d.json present
    - scripts/eth_perp_engine_binance.py and scripts/mt_trend_utils.py in same directory
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mt_trend_utils import MTTrendContext  # type: ignore


# ---------------------------------------------------------------------------
# Klines loader (mirrors backtest_event_v3_http.py)
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
            ts = r.get("timestamp") or r.get("open_time") or r.get("time") or r.get("t")
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

def call_predict(base_url: str, as_of_ts: str, interval: str = "1h", timeout: int = 20) -> Dict[str, Any]:
    payload = {"interval": interval, "as_of_ts": as_of_ts}
    resp = requests.post(f"{base_url}/predict", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


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
    bar_index: int        # index in klines_1h where entry was signalled


@dataclass
class ClosedTrade:
    side: str
    entry_price: float
    exit_price: float
    entry_ts: datetime
    exit_ts: datetime
    outcome: str          # "TP" | "SL" | "TIMEOUT"
    pnl_pct: float        # signed return (before fees)
    pnl_usdt: float       # signed PnL in USDT (before fees)
    fee_usdt: float


# ---------------------------------------------------------------------------
# Risk engine (simplified from eth_perp_engine_binance.py)
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

        # Compute horizon exit timestamp
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
        """Check if current position should be exited on this bar."""
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

        # Compute PnL
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
# Main simulation loop
# ---------------------------------------------------------------------------

def run_simulation(
    data_dir: str,
    base_url: str,
    tp_pct: float = 0.0175,
    sl_pct: float = 0.007,
    horizon_bars: int = 6,
    threshold: float = 0.65,
    capital: float = 10_000.0,
    position_fraction: float = 0.30,
    leverage: float = 5.0,
    fee_per_side: float = 0.0004,
    max_consec_losses: int = 3,
    since: Optional[str] = None,
    until: Optional[str] = None,
    request_delay_s: float = 0.1,
    interval: str = "1h",
    output_equity_path: Optional[str] = None,
) -> None:
    print(f"[sim] Loading klines from {data_dir} ...")
    klines_1h = load_klines(os.path.join(data_dir, "klines_1h.json"))
    klines_4h = load_klines(os.path.join(data_dir, "klines_4h.json"))
    klines_1d = load_klines(os.path.join(data_dir, "klines_1d.json"))

    if not klines_1h:
        print("[sim] ERROR: no 1h klines found")
        return

    mt_ctx = MTTrendContext(klines_4h, klines_1d)
    engine = SimpleRiskEngine(
        capital=capital,
        position_fraction=position_fraction,
        leverage=leverage,
        max_consec_losses=max_consec_losses,
        fee_per_side=fee_per_side,
    )

    # Apply time window filters
    since_dt = _to_utc_dt(since) if since else None
    until_dt = _to_utc_dt(until) if until else None
    if since_dt:
        klines_1h = [k for k in klines_1h if k["ts"] >= since_dt]
    if until_dt:
        klines_1h = [k for k in klines_1h if k["ts"] <= until_dt]

    print(
        f"[sim] Replaying {len(klines_1h)} 1h bars. "
        f"tp={tp_pct*100:.2f}% sl={sl_pct*100:.2f}% horizon={horizon_bars}h "
        f"threshold={threshold} capital={capital:.2f}"
    )

    n_signals = 0
    n_flat_mt = 0

    for i, bar in enumerate(klines_1h):
        ts = bar["ts"]
        price = bar["close"]
        as_of_ts = ts.isoformat().replace("+00:00", "Z")

        # --- check exits for current position ---
        engine.check_exit(bar)

        # --- record equity ---
        if i % 24 == 0:
            engine.record_equity(ts)

        # --- call ml-service for signal ---
        try:
            result = call_predict(base_url, as_of_ts=as_of_ts, interval=interval)
        except Exception as e:
            print(f"  [SKIP] {as_of_ts}: predict failed: {e}")
            if request_delay_s > 0:
                time.sleep(request_delay_s)
            continue

        signal = result.get("signal", "FLAT")
        confidence = result.get("confidence", 0.0)

        # Use calibrated_confidence if available, fall back to raw confidence
        eff_confidence = result.get("calibrated_confidence") or confidence

        # --- multi-timeframe filter (Scheme B) ---
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

        # --- apply confidence threshold ---
        if signal != "FLAT" and eff_confidence < threshold:
            signal = "FLAT"

        # --- open position if allowed ---
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

    # Close any open position at end
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

    engine.record_equity(klines_1h[-1]["ts"] if klines_1h else datetime.utcnow())

    # --- print summary ---
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
        print(f"SIMULATION COMPLETE")
        print(f"  Bars replayed   : {len(klines_1h)}")
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
        print("\n[sim] No trades generated.")
        print(f"  Bars replayed: {len(klines_1h)}, signals sent: {n_signals}, MT filtered: {n_flat_mt}")

    # --- write equity curve ---
    if output_equity_path is None:
        output_equity_path = os.path.join(data_dir, "eth_perp_sim_equity.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(output_equity_path)), exist_ok=True)
    with open(output_equity_path, "w", encoding="utf-8") as f:
        for rec in engine.equity_curve:
            f.write(json.dumps(rec) + "\n")
    print(f"\n[sim] Equity curve written to {output_equity_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="ETHUSDT perp simulated replay trader (DRY-RUN)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--base-url", default="http://127.0.0.1:9000")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--tp", type=float, default=0.0175, help="Take-profit fraction (e.g. 0.0175 = 1.75%%)")
    ap.add_argument("--sl", type=float, default=0.007, help="Stop-loss fraction (e.g. 0.007 = 0.7%%)")
    ap.add_argument("--horizon", type=int, default=6, help="Max holding period in bars")
    ap.add_argument("--threshold", type=float, default=0.65, help="Min confidence to enter")
    ap.add_argument("--capital", type=float, default=10_000.0, help="Initial capital in USDT")
    ap.add_argument("--position-fraction", type=float, default=0.30,
                    help="Fraction of capital per position (e.g. 0.30 = 30%%)")
    ap.add_argument("--leverage", type=float, default=5.0, help="Leverage multiplier")
    ap.add_argument("--fee", type=float, default=0.0004, help="Fee per side (e.g. 0.0004 = 0.04%%)")
    ap.add_argument("--max-consec-losses", type=int, default=3, help="Circuit breaker: consecutive losses")
    ap.add_argument("--since", default=None, help="Start time, e.g. 2026-02-01T00:00:00Z")
    ap.add_argument("--until", default=None, help="End time, e.g. 2026-03-10T00:00:00Z")
    ap.add_argument("--delay", type=float, default=0.1,
                    help="Seconds to wait between /predict calls (reduce if too slow)")
    ap.add_argument("--output-equity", default=None,
                    help="Path for equity curve JSONL output")
    args = ap.parse_args()

    run_simulation(
        data_dir=args.data_dir,
        base_url=args.base_url,
        tp_pct=args.tp,
        sl_pct=args.sl,
        horizon_bars=args.horizon,
        threshold=args.threshold,
        capital=args.capital,
        position_fraction=args.position_fraction,
        leverage=args.leverage,
        fee_per_side=args.fee,
        max_consec_losses=args.max_consec_losses,
        since=args.since,
        until=args.until,
        request_delay_s=args.delay,
        interval=args.interval,
        output_equity_path=args.output_equity,
    )
