#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generic Binance USDT-M Perp live trader (event_v3 1h) — DRY-RUN by default.

Key goals (PR-1 / PR-2A):
- Unify single/multi/all symbols selection
- Provide safe DRY-RUN ↔ LIVE toggle:
  - default is DRY-RUN (safe)
  - `--mode live` requires Chinese confirmation input:
      - type exactly: xionghan  -> proceed
      - type: no               -> exit
      - anything else          -> exit
    and a 15-second countdown
- PR-2A: real Binance Futures REST execution is now wired in.
  In `--mode live`, startup self-checks run first (API key presence,
  server time, exchangeInfo, symbol validation), then each bar fetches
  the mark price and places real MARKET orders via BinanceFuturesClient.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Sequence, Set

import requests
from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# Existing repo modules (kept consistent with ETH-only script)
from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side  # noqa: E402
from mt_trend_utils import MTTrendContext  # noqa: E402
from backtest_event_v3_http import load_klines_1h  # noqa: E402
from mt_filter import mt_gate, gate_allows, gate_is_strong, exec_confirm_15m, ENTER  # noqa: E402
from binance_futures_rest import BinanceFuturesClient, BinanceAPIError  # noqa: E402


load_dotenv()

DEFAULT_ML_SERVICE_URL = "http://127.0.0.1:9000/predict"

# legacy weights (same as current ETH-only script)
WEIGHT_NEUTRAL = 0.85
WEIGHT_REVERSE = 0.70

# Clock drift threshold: warn if local clock is this many ms off from Binance server time.
MAX_ACCEPTABLE_CLOCK_DRIFT_MS = 2000


@dataclass(frozen=True)
class RuntimeMode:
    mode: str  # "dry-run" | "live"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_dry_run(self) -> bool:
        return self.mode == "dry-run"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _current_hour_bar_close() -> datetime:
    """Return current hour bar close timestamp (UTC at HH:00:00)."""
    now = _now_utc()
    return now.replace(minute=0, second=0, microsecond=0)


def call_ml_service(as_of_ts: str, base_url: str) -> dict:
    payload = {"interval": "1h", "as_of_ts": as_of_ts}
    r = requests.post(base_url, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def _fetch_klines_15m(data_dir: str) -> List[dict]:
    """Try load 15m klines from data_dir/klines_15m.json; return [] if not available."""
    path = os.path.join(data_dir, "klines_15m.json")
    try:
        # NOTE: load_klines_1h is a generic json loader in this repo; reused here.
        return load_klines_1h(path)
    except Exception:
        return []


def apply_multi_timeframe_filter(
    side_str: str,
    ts: datetime,
    mt_ctx: MTTrendContext,
    use_layered: bool = False,
) -> tuple[str, float]:
    """
    Filter rules (two modes):

    use_layered=False (legacy behavior, kept for backward compat):
      - 4h must match direction (hard)
      - 1d decides weight (soft)
      - returns (side, weight)

    use_layered=True (layered gate):
      - use unified mt_gate: ALLOW_STRONG / ALLOW_WEAK / REJECT
      - strong -> weight=1.0, weak -> weight=WEIGHT_NEUTRAL
    """
    t4 = mt_ctx.trend_4h_at(ts)
    t1d = mt_ctx.trend_1d_at(ts)

    if use_layered:
        gate = mt_gate(side_str, t4, t1d)
        if not gate_allows(gate):
            return "FLAT", 0.0
        weight = 1.0 if gate_is_strong(gate) else WEIGHT_NEUTRAL
        return side_str, weight

    # legacy behavior
    if side_str == "LONG":
        if t4 != "UP":
            return "FLAT", 0.0
        if t1d == "UP":
            return "LONG", 1.0
        if t1d == "NEUTRAL":
            return "LONG", WEIGHT_NEUTRAL
        if t1d == "DOWN":
            return "LONG", WEIGHT_REVERSE

    if side_str == "SHORT":
        if t4 != "DOWN":
            return "FLAT", 0.0
        if t1d == "DOWN":
            return "SHORT", 1.0
        if t1d == "NEUTRAL":
            return "SHORT", WEIGHT_NEUTRAL
        if t1d == "UP":
            return "SHORT", WEIGHT_REVERSE

    return "FLAT", 0.0


def _confirm_live_or_exit() -> None:
    """
    Chinese interactive confirmation:
    - input 'xionghan' to proceed
    - input 'no' to exit
    - otherwise exit
    """
    print("\n" + "=" * 72)
    print("【危险】你正在尝试开启实盘模式（LIVE）！")
    print("开启后将可能真正进行交易、产生真实盈亏。")
    print("如果你不确定，请立刻输入：no")
    print("如果你确认要开启实盘，请输入：xionghan")
    print("=" * 72)
    try:
        v = input("请输入 (xionghan/no): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[EXIT] 未确认实盘模式，已安全退出。")
        raise SystemExit(2)

    if v == "xionghan":
        return
    if v.lower() == "no":
        print("[EXIT] 你选择不启用实盘模式，已安全退出。")
        raise SystemExit(0)

    print("[EXIT] 输入不匹配（未输入 xionghan），已安全退出。")
    raise SystemExit(2)


def _countdown(seconds: int = 15) -> None:
    for i in range(seconds, 0, -1):
        print(f"[LIVE] 将在 {i:02d} 秒后开始…（Ctrl+C 可退出）", flush=True)
        time.sleep(1)
    print("[LIVE] 倒计时结束。")


def _load_all_symbols_from_configs() -> List[str]:
    """
    Load symbols from configs/symbols.yaml if present.
    We intentionally implement a very tolerant parser to avoid new dependencies.
    Expected common patterns:
      - YAML list: ["ETHUSDT", "BTCUSDT"]
      - lines like: - symbol: ETHUSDT
      - lines like: symbol: ETHUSDT
    """
    cfg_path = os.path.join(os.path.dirname(_SCRIPT_DIR), "configs", "symbols.yaml")
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            f"configs/symbols.yaml not found at expected path: {cfg_path}. "
            "Please create it or use --symbol/--symbols."
        )

    syms: List[str] = []
    with open(cfg_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            # - ETHUSDT
            if line.startswith("- "):
                cand = line[2:].strip().strip("'\"")
                if cand and cand.isalnum():
                    syms.append(cand)
                    continue

            # symbol: ETHUSDT
            if "symbol:" in line:
                # naive split, tolerant
                parts = line.split("symbol:", 1)
                cand = parts[1].strip().strip("'\"")
                # strip trailing comments
                if "#" in cand:
                    cand = cand.split("#", 1)[0].strip()
                if cand and cand.isalnum():
                    syms.append(cand)
                    continue

            # ["ETHUSDT", "BTCUSDT"]
            if "[" in line and "]" in line:
                inside = line[line.find("[") + 1 : line.rfind("]")]
                for token in inside.split(","):
                    cand = token.strip().strip("'\"")
                    if cand and cand.isalnum():
                        syms.append(cand)

    # de-dup, keep order
    seen: Set[str] = set()
    out: List[str] = []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    if not out:
        raise ValueError(
            f"No symbols parsed from {cfg_path}. "
            "Please ensure it contains symbols (e.g., - symbol: ETHUSDT)."
        )
    return out


def _parse_symbols_args(args: argparse.Namespace) -> List[str]:
    if args.all_symbols:
        return _load_all_symbols_from_configs()

    if args.symbol and args.symbols:
        raise SystemExit("Do not use --symbol and --symbols together.")

    if args.symbol:
        return [args.symbol.strip().upper()]

    if args.symbols:
        parts = [p.strip().upper() for p in args.symbols.split(",") if p.strip()]
        if not parts:
            raise SystemExit("--symbols provided but empty after parsing.")
        # de-dup keep order
        seen: Set[str] = set()
        out: List[str] = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    raise SystemExit("You must provide --symbol, --symbols, or --all-symbols.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Binance USDT-M perp trader (event_v3, 1h)")
    ap.add_argument(
        "--mode",
        choices=["dry-run", "live"],
        default="dry-run",
        help="Trading mode. Default: dry-run (safe, no real orders).",
    )
    ap.add_argument(
        "--env",
        choices=["prod", "testnet"],
        default="prod",
        help=(
            "Binance environment selector. "
            "'prod' uses fapi.binance.com (real funds). "
            "'testnet' uses testnet.binancefuture.com (test funds). "
            "Default: prod."
        ),
    )

    # symbol selection
    ap.add_argument("--symbol", default=None, help="Single symbol, e.g. ETHUSDT")
    ap.add_argument(
        "--symbols",
        default=None,
        help="Multiple symbols, comma-separated, e.g. ETHUSDT,BTCUSDT,SOLUSDT",
    )
    ap.add_argument(
        "--all-symbols",
        action="store_true",
        help="Run for all symbols found in configs/symbols.yaml",
    )

    # same flags as ETH-only script
    ap.add_argument(
        "--ml-service-url",
        default=os.getenv("ML_SERVICE_URL", DEFAULT_ML_SERVICE_URL),
        help=f"ML service /predict base URL. Default: {DEFAULT_ML_SERVICE_URL} (or env ML_SERVICE_URL).",
    )
    ap.add_argument(
        "--data-dir",
        default=os.getenv("DATA_DIR", "./data"),
        help="Base data dir containing klines_4h.json, klines_1d.json, and optional klines_15m.json",
    )
    ap.add_argument(
        "--use-layered-gate",
        action="store_true",
        help=(
            "Use unified mt_gate (ALLOW_STRONG/ALLOW_WEAK/REJECT) instead of legacy filter. "
            "Default: OFF (legacy behavior unchanged)."
        ),
    )
    ap.add_argument(
        "--use-15m-confirm",
        action="store_true",
        help=(
            "Enable 15m execution confirmation layer (requires data/klines_15m.json). "
            "Default: OFF. WARNING: this creates a live vs backtest evaluation gap."
        ),
    )

    # engine knobs (still DRY-RUN in PR-1)
    ap.add_argument("--strategy-funds-usdt", type=float, default=10_000.0, help="Strategy funds for sizing (USDT).")
    ap.add_argument("--leverage", type=float, default=5.0, help="Leverage (for sizing/logging).")
    ap.add_argument("--position-fraction", type=float, default=0.3, help="Position fraction per entry.")
    ap.add_argument("--max-consec-losses", type=int, default=3, help="Max consecutive losses (engine internal).")
    ap.add_argument("--max-positions", type=int, default=2, help="Max positions per symbol (engine internal).")

    return ap


def _run_live_self_checks(client: BinanceFuturesClient, symbols: List[str]) -> None:
    """
    Pre-flight self-checks before entering the live trading loop.
    Exits the process on any failure so we never trade with bad state.
    """
    print("\n[LIVE] Running startup self-checks…")

    # 1) Server time
    try:
        server_ts = client.get_server_time()
        local_ts = int(time.time() * 1000)
        drift_ms = abs(server_ts - local_ts)
        print(f"  [OK] Server time: {server_ts}  local={local_ts}  drift={drift_ms}ms")
        if drift_ms > MAX_ACCEPTABLE_CLOCK_DRIFT_MS:
            print(f"  [WARN] Clock drift {drift_ms}ms is large; consider syncing system clock.")
    except Exception as exc:
        print(f"  [FAIL] Cannot reach Binance server time: {exc}")
        raise SystemExit(1)

    # 2) Load exchange info
    try:
        sym_cache = client.load_exchange_info()
        print(f"  [OK] Loaded exchangeInfo ({len(sym_cache)} symbols)")
    except Exception as exc:
        print(f"  [FAIL] Cannot load exchangeInfo: {exc}")
        raise SystemExit(1)

    # 3) Validate each target symbol
    for sym in symbols:
        si = sym_cache.get(sym)
        if si is None:
            print(f"  [FAIL] Symbol {sym!r} not found in exchangeInfo")
            raise SystemExit(1)
        if not si.is_trading():
            print(f"  [FAIL] Symbol {sym!r} is not in TRADING status (status={si.status})")
            raise SystemExit(1)
        print(
            f"  [OK] {sym}: status=TRADING  stepSize={si.qty_step}  "
            f"tickSize={si.price_tick}  minQty={si.min_qty}"
        )

    print("[LIVE] All self-checks passed.\n")


def run_for_one_symbol(
    symbol: str,
    args: argparse.Namespace,
    runtime: RuntimeMode,
    exchange_client: Optional[BinanceFuturesClient] = None,
) -> None:
    # Load multi-timeframe klines
    data_dir = args.data_dir
    klines_4h = load_klines_1h(os.path.join(data_dir, "klines_4h.json"))
    klines_1d = load_klines_1h(os.path.join(data_dir, "klines_1d.json"))
    mt_ctx = MTTrendContext(klines_4h=klines_4h, klines_1d=klines_1d)

    engine = EthPerpStrategyEngineBinance(
        strategy_funds_usdt=float(args.strategy_funds_usdt),
        leverage=float(args.leverage),
        position_fraction=float(args.position_fraction),
        max_consec_losses=int(args.max_consec_losses),
        max_positions=int(args.max_positions),
        symbol=symbol,
        trading_mode=args.mode,
        exchange_client=exchange_client,
    )

    last_bar_close: Optional[datetime] = None

    mode_desc = []
    if args.use_layered_gate:
        mode_desc.append("layered-gate")
    if args.use_15m_confirm:
        mode_desc.append("15m-confirm")
    mode_str = "+".join(mode_desc) if mode_desc else "legacy"

    print(
        f"Starting {'LIVE' if runtime.is_live else 'DRY-RUN'} perp trader "
        f"(event_v3, 1h, symbol={symbol}, max_positions={args.max_positions}, filter={mode_str})..."
    )

    while True:
        bar_close = _current_hour_bar_close()

        if last_bar_close is not None and bar_close <= last_bar_close:
            time.sleep(5)
            continue

        now = _now_utc()
        if now < bar_close + timedelta(seconds=5):
            time.sleep(5)
            continue

        last_bar_close = bar_close
        as_of_ts = bar_close.isoformat().replace("+00:00", "Z")
        print(f"[{_now_utc().isoformat()}] {symbol} processing bar_close={as_of_ts}")

        j = call_ml_service(as_of_ts, base_url=args.ml_service_url)

        side_str = str(j.get("signal", "FLAT"))
        model_version = j.get("model_version")
        confidence = j.get("confidence")
        cal_conf = j.get("calibrated_confidence")

        side_str, weight = apply_multi_timeframe_filter(side_str, bar_close, mt_ctx, use_layered=args.use_layered_gate)

        exec_result = ENTER
        if side_str in ("LONG", "SHORT") and args.use_15m_confirm:
            klines_15m = _fetch_klines_15m(data_dir)
            exec_result = exec_confirm_15m(side_str, klines_15m, enabled=True)
            if exec_result != ENTER:
                print(f"  [15m confirm] symbol={symbol} side={side_str} exec_result={exec_result} -> skipping")
                side_str = "FLAT"

        if side_str == "LONG":
            side = Side.LONG
        elif side_str == "SHORT":
            side = Side.SHORT
        else:
            side = Side.FLAT

        # Fetch current mark price in LIVE mode; use 0.0 placeholder in DRY-RUN
        price = 0.0
        if runtime.is_live and exchange_client is not None and side != Side.FLAT:
            try:
                price = exchange_client.get_mark_price(symbol)
            except Exception as exc:
                print(f"  [WARN] Could not fetch mark price for {symbol}: {exc}  skipping signal")
                continue

        print(
            f"  symbol={symbol} side={side_str} weight={weight:.2f} exec={exec_result} "
            f"confidence={confidence} cal_conf={cal_conf} model_version={model_version} price={price}"
        )

        engine.on_new_signal(bar_close, side, price, weight=weight)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)

    symbols = _parse_symbols_args(args)

    runtime = RuntimeMode(mode=args.mode)

    # Key presence check (early, before confirmation)
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")

    exchange_client: Optional[BinanceFuturesClient] = None

    if runtime.is_live:
        if not api_key or not api_secret:
            print("[ERROR] BINANCE_API_KEY and BINANCE_API_SECRET must be set for LIVE mode.")
            raise SystemExit(1)

        _confirm_live_or_exit()
        _countdown(15)

        exchange_client = BinanceFuturesClient(
            api_key=api_key,
            api_secret=api_secret,
            env=args.env,
        )
        _run_live_self_checks(exchange_client, symbols)
    else:
        if not api_key or not api_secret:
            print("[WARN] BINANCE_API_KEY / BINANCE_API_SECRET not set（DRY-RUN 模式下无所谓）")

    if len(symbols) == 1:
        run_for_one_symbol(symbols[0], args, runtime, exchange_client=exchange_client)
        return

    # Multi-symbol: simple sequential loop per bar close.
    # (No threading to keep behavior deterministic & simple.)
    print(f"Starting {'LIVE' if runtime.is_live else 'DRY-RUN'} multi-symbol runner: {symbols}")

    # For multi-symbol, we keep per-symbol engines and contexts
    data_dir = args.data_dir
    klines_4h = load_klines_1h(os.path.join(data_dir, "klines_4h.json"))
    klines_1d = load_klines_1h(os.path.join(data_dir, "klines_1d.json"))
    mt_ctx = MTTrendContext(klines_4h=klines_4h, klines_1d=klines_1d)

    engines = {
        s: EthPerpStrategyEngineBinance(
            strategy_funds_usdt=float(args.strategy_funds_usdt),
            leverage=float(args.leverage),
            position_fraction=float(args.position_fraction),
            max_consec_losses=int(args.max_consec_losses),
            max_positions=int(args.max_positions),
            symbol=s,
            trading_mode=args.mode,
            exchange_client=exchange_client,
        )
        for s in symbols
    }

    last_bar_close: Optional[datetime] = None

    while True:
        bar_close = _current_hour_bar_close()

        if last_bar_close is not None and bar_close <= last_bar_close:
            time.sleep(5)
            continue

        now = _now_utc()
        if now < bar_close + timedelta(seconds=5):
            time.sleep(5)
            continue

        last_bar_close = bar_close
        as_of_ts = bar_close.isoformat().replace("+00:00", "Z")
        print(f"[{_now_utc().isoformat()}] multi-symbol processing bar_close={as_of_ts}")

        for symbol in symbols:
            j = call_ml_service(as_of_ts, base_url=args.ml_service_url)

            side_str = str(j.get("signal", "FLAT"))
            model_version = j.get("model_version")
            confidence = j.get("confidence")
            cal_conf = j.get("calibrated_confidence")

            side_str, weight = apply_multi_timeframe_filter(
                side_str, bar_close, mt_ctx, use_layered=args.use_layered_gate
            )

            exec_result = ENTER
            if side_str in ("LONG", "SHORT") and args.use_15m_confirm:
                klines_15m = _fetch_klines_15m(data_dir)
                exec_result = exec_confirm_15m(side_str, klines_15m, enabled=True)
                if exec_result != ENTER:
                    print(f"  [15m confirm] symbol={symbol} side={side_str} exec_result={exec_result} -> skipping")
                    side_str = "FLAT"

            if side_str == "LONG":
                side = Side.LONG
            elif side_str == "SHORT":
                side = Side.SHORT
            else:
                side = Side.FLAT

            # Fetch current mark price in LIVE mode; use 0.0 in DRY-RUN
            price = 0.0
            if runtime.is_live and exchange_client is not None and side != Side.FLAT:
                try:
                    price = exchange_client.get_mark_price(symbol)
                except Exception as exc:
                    print(f"  [WARN] Could not fetch mark price for {symbol}: {exc}  skipping signal")
                    continue

            print(
                f"  symbol={symbol} side={side_str} weight={weight:.2f} exec={exec_result} "
                f"confidence={confidence} cal_conf={cal_conf} model_version={model_version} price={price}"
            )

            engines[symbol].on_new_signal(bar_close, side, price, weight=weight)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[EXIT] KeyboardInterrupt, exiting.")
