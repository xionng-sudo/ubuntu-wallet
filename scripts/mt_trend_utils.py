#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from typing import Any, Dict, List, Optional


def sma(vals: List[float], window: int) -> List[Optional[float]]:
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


def trend_series(
    klines: List[Dict[str, Any]],
    fast: int = 5,
    slow: int = 20,
    eps: float = 0.001,
) -> List[str]:
    """
    给定一组 kline（包含 'close'），返回每根 bar 的趋势标签：
    'UP' / 'DOWN' / 'NEUTRAL'
    """
    closes = [float(k["close"]) for k in klines]
    ma_fast = sma(closes, fast)
    ma_slow = sma(closes, slow)
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


class MTTrendContext:
    """
    多周期趋势上下文：
    - 传入 4h / 1d 的 klines（含 'ts' / 'close'）
    - 内部算好 trend 序列，提供 trend_4h_at / trend_1d_at 查询接口
    """

    def __init__(
        self,
        klines_4h: List[Dict[str, Any]],
        klines_1d: List[Dict[str, Any]],
        fast: int = 5,
        slow: int = 20,
        eps: float = 0.001,
    ):
        self.klines_4h = klines_4h
        self.klines_1d = klines_1d

        self.trend_4h_list = trend_series(klines_4h, fast=fast, slow=slow, eps=eps)
        self.trend_1d_list = trend_series(klines_1d, fast=fast, slow=slow, eps=eps)

        self.ts_4h = [k["ts"] for k in klines_4h]
        self.ts_1d = [k["ts"] for k in klines_1d]

    def trend_4h_at(self, ts: datetime) -> str:
        idx = bisect_right(self.ts_4h, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return self.trend_4h_list[idx]

    def trend_1d_at(self, ts: datetime) -> str:
        idx = bisect_right(self.ts_1d, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return self.trend_1d_list[idx]
