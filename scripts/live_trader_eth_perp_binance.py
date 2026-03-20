#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ETHUSDT 永续合约实盘骨架（event_v3 1h，DRY-RUN 版，不依赖 binance SDK）

当前版本：
- 每小时在 K 线收盘时调用 ml-service /predict 获取 event_v3 signal
- 直接使用服务端返回的 signal（LONG / SHORT / FLAT）
- 使用 4h/1d 多周期过滤：
  - 4h 硬过滤
  - 1d 软过滤
- 1d 中性：weight=0.85
- 1d 反向：weight=0.70
- 最多 2 仓
- 只允许同方向加仓
- 价格先用 0.0 占位（不真下单，仅用于日志）
- 调用 EthPerpStrategyEngineBinance（最多 2 仓 + 5x + 连续 3 亏损熔断），但其内部也是 DRY-RUN，只打印不下单

等你确认整个决策 + 风控流程都符合预期后，再接入真实 Binance API。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv

from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side
from mt_trend_utils import MTTrendContext
from backtest_event_v3_http import load_klines_1h

# 加载 .env
load_dotenv()

ML_SERVICE_URL = "http://127.0.0.1:9000/predict"
SYMBOL = "ETHUSDT"

# 软过滤权重
WEIGHT_NEUTRAL = 0.85
WEIGHT_REVERSE = 0.70


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _current_hour_bar_close() -> datetime:
    """返回当前小时 bar 的收盘时间（UTC 整点）"""
    now = _now_utc()
    return now.replace(minute=0, second=0, microsecond=0)


def call_ml_service(as_of_ts: str) -> dict:
    payload = {"interval": "1h", "as_of_ts": as_of_ts}
    r = requests.post(ML_SERVICE_URL, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def apply_multi_timeframe_filter(side_str: str, ts: datetime, mt_ctx: MTTrendContext) -> tuple[str, float]:
    """
    过滤规则：
    - 4h 必须同向（硬过滤）
    - 1d 作为软过滤
    - 返回 (side, weight)
    """
    t4 = mt_ctx.trend_4h_at(ts)
    t1d = mt_ctx.trend_1d_at(ts)

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


def main():
    # 这里只是 DRY-RUN，不强依赖真实 Binance 连接
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        print("[WARN] BINANCE_API_KEY / BINANCE_API_SECRET not set（当前为 DRY-RUN，不会真下单）")

    # 多周期趋势上下文：用 data/klines_4h.json / klines_1d.json
    data_dir = os.getenv("DATA_DIR", "./data")
    klines_4h = load_klines_1h(os.path.join(data_dir, "klines_4h.json"))
    klines_1d = load_klines_1h(os.path.join(data_dir, "klines_1d.json"))
    mt_ctx = MTTrendContext(klines_4h=klines_4h, klines_1d=klines_1d)

    # 初始化风控引擎
    engine = EthPerpStrategyEngineBinance(
        strategy_funds_usdt=10_000.0,
        leverage=5.0,
        position_fraction=0.3,
        max_consec_losses=3,
        max_positions=2,
        symbol=SYMBOL,
    )

    last_bar_close: Optional[datetime] = None

    print("Starting DRY-RUN ETHUSDT perp trader (event_v3, 1h, max 2 positions, same direction only)...")

    while True:
        try:
            bar_close = _current_hour_bar_close()

            # 避免同一根 bar 重复处理
            if last_bar_close is not None and bar_close <= last_bar_close:
                time.sleep(5)
                continue

            now = _now_utc()
            # 简单地等到这一小时收盘后 5 秒钟（实际可以更精细）
            if now < bar_close + timedelta(seconds=5):
                time.sleep(5)
                continue

            last_bar_close = bar_close
            as_of_ts = bar_close.isoformat().replace("+00:00", "Z")
            print(f"[{_now_utc().isoformat()}] Processing bar_close={as_of_ts}")

            # 1) 调用 ml-service 获取���测
            j = call_ml_service(as_of_ts)

            # 2) 直接使用服务端 signal
            side_str = str(j.get("signal", "FLAT"))
            model_version = j.get("model_version")
            confidence = j.get("confidence")
            cal_conf = j.get("calibrated_confidence")

            # 3) 多周期过滤 + 软权重
            side_str, weight = apply_multi_timeframe_filter(side_str, bar_close, mt_ctx)

            if side_str == "LONG":
                side = Side.LONG
            elif side_str == "SHORT":
                side = Side.SHORT
            else:
                side = Side.FLAT

            print(
                f"  signal side={side_str} weight={weight:.2f} "
                f"confidence={confidence} cal_conf={cal_conf} "
                f"model_version={model_version} price=0.0"
            )

            # 4) 交给风控引擎，让它依据最多2仓 + 同方向加仓 + 熔断决定是否开仓
            engine.on_new_signal(bar_close, side, 0.0, weight=weight)

            # 平仓逻辑暂不实现，避免误操作；将来参考 backtest_event_v3_http.py 的 simulate_trade 来做 TP/SL/horizon 监控

        except Exception as e:
            print(f"[ERROR] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
