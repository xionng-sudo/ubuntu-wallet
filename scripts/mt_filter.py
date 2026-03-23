#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mt_filter.py
============
统一多周期过滤与执行确认层（Unified Multi-Timeframe Filter & Execution Confirmation）

提供：
  1. mt_gate(side, t4, t1d)
       统一 4h/1d 趋势 gate，返回分层结果：
         ALLOW_STRONG | ALLOW_WEAK | REJECT

  2. exec_confirm_15m(side, klines_15m, enabled=True)
       15m 执行确认层（不改变 1h 主模型信号方向，只决定是否入场）：
         ENTER | WAIT | CANCEL

说明：
- mt_gate 仅根据 4h / 1d 趋势标签（'UP' / 'DOWN' / 'NEUTRAL'）判断放行级别，
  与 MTTrendContext 的趋势计算解耦，可搭配任意趋势来源使用。
- exec_confirm_15m 基于最新一批 15m K 线做轻量技术确认，不额外引入重量级依赖。
- 所有逻辑默认可关闭（enabled=True → False），方便渐进接入与 A/B 测试。

gate 规则（LONG）：
  - 1d == DOWN                      -> REJECT
  - 4h == DOWN                      -> REJECT
  - 4h == UP   and 1d == UP         -> ALLOW_STRONG
  - 4h == UP   and 1d == NEUTRAL    -> ALLOW_WEAK
  - 4h == NEUTRAL and 1d == UP      -> ALLOW_WEAK
  - other                           -> REJECT

gate 规则（SHORT，对称）：
  - 1d == UP                        -> REJECT
  - 4h == UP                        -> REJECT
  - 4h == DOWN   and 1d == DOWN     -> ALLOW_STRONG
  - 4h == DOWN   and 1d == NEUTRAL  -> ALLOW_WEAK
  - 4h == NEUTRAL and 1d == DOWN    -> ALLOW_WEAK
  - other                           -> REJECT

15m 执行确认（LONG）：
  - close > EMA(20)               +1
  - RSI(14) > 50                  +1
  - 最新一根收盘 > 前一根收盘       +1
  score >= 2  -> ENTER
  score == 0  -> CANCEL（明显逆向）
  其他        -> WAIT

15m 执行确认（SHORT，对称）：
  - close < EMA(20)               +1
  - RSI(14) < 50                  +1
  - 最新一根收盘 < 前一根收盘       +1
  score >= 2  -> ENTER
  score == 0  -> CANCEL
  其他        -> WAIT
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Gate result constants
# ---------------------------------------------------------------------------
REJECT = "REJECT"
ALLOW_WEAK = "ALLOW_WEAK"
ALLOW_STRONG = "ALLOW_STRONG"

# ---------------------------------------------------------------------------
# Execution confirmation result constants
# ---------------------------------------------------------------------------
ENTER = "ENTER"
WAIT = "WAIT"
CANCEL = "CANCEL"


# ---------------------------------------------------------------------------
# Core gate function
# ---------------------------------------------------------------------------

def mt_gate(side: str, t4: str, t1d: str) -> str:
    """
    Unified 4h/1d multi-timeframe gate.
    统一 4h/1d 多周期 gate。

    Args:
        side : Signal direction — 'LONG' or 'SHORT' (other values return REJECT).
               信号方向，'LONG' 或 'SHORT'（其他返回 REJECT）
        t4   : 4h trend label — 'UP' / 'DOWN' / 'NEUTRAL'
        t1d  : 1d trend label — 'UP' / 'DOWN' / 'NEUTRAL'

    Returns:
        ALLOW_STRONG | ALLOW_WEAK | REJECT
    """
    if side == "LONG":
        # 硬拒绝
        if t1d == "DOWN":
            return REJECT
        if t4 == "DOWN":
            return REJECT
        # 强放行：4h 同向 + 1d 同向
        if t4 == "UP" and t1d == "UP":
            return ALLOW_STRONG
        # 弱放行：一方同向，另一方中性
        if t4 == "UP" and t1d == "NEUTRAL":
            return ALLOW_WEAK
        if t4 == "NEUTRAL" and t1d == "UP":
            return ALLOW_WEAK
        return REJECT

    if side == "SHORT":
        # 硬拒绝
        if t1d == "UP":
            return REJECT
        if t4 == "UP":
            return REJECT
        # 强放行：4h 同向 + 1d 同向
        if t4 == "DOWN" and t1d == "DOWN":
            return ALLOW_STRONG
        # 弱放行：一方同向，另一方中性
        if t4 == "DOWN" and t1d == "NEUTRAL":
            return ALLOW_WEAK
        if t4 == "NEUTRAL" and t1d == "DOWN":
            return ALLOW_WEAK
        return REJECT

    # side 不是 LONG/SHORT（例如 FLAT）
    return REJECT


def gate_allows(result: str) -> bool:
    """返回 True 当 gate 结果为 ALLOW_WEAK 或 ALLOW_STRONG。"""
    return result in (ALLOW_WEAK, ALLOW_STRONG)


def gate_is_strong(result: str) -> bool:
    """返回 True 当 gate 结果为 ALLOW_STRONG。"""
    return result == ALLOW_STRONG


# ---------------------------------------------------------------------------
# 15m Execution Confirmation helpers
# ---------------------------------------------------------------------------

def _ema(values: List[float], period: int) -> List[Optional[float]]:
    """返回 EMA 序列，前 period-1 个为 None。若 values 长度小于 period，全部返回 None。"""
    if not values or period <= 0:
        return [None] * len(values)
    if len(values) < period:
        return [None] * len(values)
    out: List[Optional[float]] = [None] * (period - 1)
    k = 2.0 / (period + 1)
    ema_val = sum(values[:period]) / period
    out.append(ema_val)
    for v in values[period:]:
        ema_val = v * k + ema_val * (1.0 - k)
        out.append(ema_val)
    return out


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    """计算最后一个值对应的 RSI，需要至少 period+1 个值。"""
    if len(values) < period + 1:
        return None
    relevant = values[-(period + 1):]
    gains = []
    losses = []
    for i in range(1, len(relevant)):
        diff = relevant[i] - relevant[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# ---------------------------------------------------------------------------
# 15m Execution Confirmation
# ---------------------------------------------------------------------------

def exec_confirm_15m(
    side: str,
    klines_15m: List[Dict[str, Any]],
    ema_period: int = 20,
    rsi_period: int = 14,
    enabled: bool = True,
) -> str:
    """
    15m execution confirmation layer (does not change 1h signal direction).
    15m 执行确认层（不改变 1h 主模型信号方向，只决定是否入场）。

    Args:
        side        : Direction from 1h model — 'LONG' or 'SHORT'.
                      1h 主模型决策方向，'LONG' 或 'SHORT'
        klines_15m  : Recent 15m klines, each dict must have a 'close' field.
                      Recommend at least max(ema_period, rsi_period+1) + 5 bars.
                      最新一批 15m K 线列表，每项含 'close' 字段
        ema_period  : EMA period (default 20).  EMA 周期（默认 20）
        rsi_period  : RSI period (default 14).  RSI 周期（默认 14）
        enabled     : If False, return ENTER immediately (skip confirmation).
                      若 False，直接返回 ENTER（跳过确认逻辑）

    Returns:
        ENTER | WAIT | CANCEL
    """
    if not enabled:
        return ENTER

    if side not in ("LONG", "SHORT"):
        return ENTER

    if not klines_15m or len(klines_15m) < 2:
        # 数据不足，保守放行
        return WAIT

    closes = [float(k["close"]) for k in klines_15m]

    # --- 指标计算 ---
    ema_series = _ema(closes, ema_period)
    latest_ema = ema_series[-1]
    rsi_val = _rsi(closes, rsi_period)
    latest_close = closes[-1]
    prev_close = closes[-2]

    # --- 评分 ---
    score = 0

    if side == "LONG":
        if latest_ema is not None and latest_close > latest_ema:
            score += 1
        if rsi_val is not None and rsi_val > 50:
            score += 1
        if latest_close > prev_close:
            score += 1
    else:  # SHORT
        if latest_ema is not None and latest_close < latest_ema:
            score += 1
        if rsi_val is not None and rsi_val < 50:
            score += 1
        if latest_close < prev_close:
            score += 1

    if score >= 2:
        return ENTER
    if score == 0:
        return CANCEL
    return WAIT
