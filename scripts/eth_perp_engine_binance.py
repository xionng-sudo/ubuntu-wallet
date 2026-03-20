#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
from datetime import datetime

from dotenv import load_dotenv

# DRY-RUN 版本：当前不依赖 binance SDK，只使用 .env 配置和内部打印
load_dotenv()


class Side(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class PositionState:
    side: Side = Side.FLAT
    notional_usdt: float = 0.0
    entry_price: float = 0.0
    open_time: Optional[datetime] = None
    position_id: Optional[str] = None  # DRY-RUN 下只是字符串，用于日志


@dataclass
class RiskState:
    consec_losses: int = 0
    trading_paused: bool = False


@dataclass
class OpenSignal:
    side: Side
    notional_usdt: float
    price: float
    ts: datetime


class EthPerpStrategyEngineBinance:
    """
    ETHUSDT 永续合约策略风控引擎（DRY-RUN 版，不真实下单）：

    - 最多 2 仓
    - 只允许同方向加仓
    - 连续 3 笔亏损熔断
    - 5x 杠杆 / 固定仓位比例（这里只用于日志，不乘到 PnL 上）
    """

    def __init__(
        self,
        strategy_funds_usdt: float,
        leverage: float = 5.0,
        position_fraction: float = 0.3,
        max_consec_losses: int = 3,
        max_positions: int = 2,
        symbol: str = "ETHUSDT",
    ):
        self.F_strategy = float(strategy_funds_usdt)
        self.leverage = float(leverage)
        self.position_fraction = float(position_fraction)
        self.max_consec_losses = int(max_consec_losses)
        self.max_positions = int(max_positions)

        self.symbol = symbol

        # 从 .env 里读取 key（当前 DRY-RUN，不强依赖）
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        if not api_key or not api_secret:
            print("[WARN] BINANCE_API_KEY / BINANCE_API_SECRET not set（DRY-RUN 模式下无所谓）")

        self.positions: List[PositionState] = []
        self.risk = RiskState()

    # --- 工具 ---

    def _current_side(self) -> Optional[Side]:
        if not self.positions:
            return None
        return self.positions[0].side

    def _get_qty_from_notional(self, notional_usdt: float, price: float) -> float:
        """
        把名义价值（USDT）转换为张数（ETH 数量），并按照合约最小精度截断。
        DRY-RUN 下仅用于日志展示。
        """
        if price <= 0:
            return 0.0
        qty = notional_usdt / price
        return float(f"{qty:.3f}")  # 例如保留 3 位小数

    def _exchange_open_position(self, side: Side, notional_usdt: float, price: float) -> str:
        """
        DRY-RUN 版本：只打印计划下单信息，不真正发单。
        等你确认逻辑后，再替换为真实 Binance 下单代码。
        """
        qty = self._get_qty_from_notional(notional_usdt, price)
        print(
            f"[DRY-RUN OPEN] side={side} symbol={self.symbol} "
            f"qty≈{qty} notional≈{notional_usdt:.2f} price≈{price}"
        )
        # 返回一个伪 order_id
        return f"DRYRUN-{side}-{datetime.utcnow().isoformat()}"

    def _exchange_close_position(self, side: Side) -> Optional[str]:
        """
        DRY-RUN 版本：只打印平仓意图，不真实下单。
        """
        print(f"[DRY-RUN CLOSE] symbol={self.symbol} side={side}")
        return f"DRYRUN-CLOSE-{datetime.utcnow().isoformat()}"

    # --- 风控 / 状态 ---

    def can_open_new_position(self, side: Side) -> bool:
        """
        允许开新仓的条件：
        - 未熔断
        - 未超过最大仓位数
        - 若已有仓位，必须同方向
        """
        if self.risk.trading_paused:
            return False
        if side == Side.FLAT:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        if not self.positions:
            return True
        return self._current_side() == side

    def on_new_signal(self, ts: datetime, side: Side, price: float, weight: float = 1.0) -> None:
        """
        收到最终方向信号（LONG/SHORT/FLAT）后，依据风控决定是否“开仓”（DRY-RUN）。
        weight 用于弱信号减仓，范围建议 0~1。
        """
        if side == Side.FLAT:
            return

        if not self.can_open_new_position(side):
            return

        weight = max(0.0, min(float(weight), 1.0))
        notional = self.position_fraction * self.F_strategy * weight

        order_id = self._exchange_open_position(side, notional, price)

        self.positions.append(
            PositionState(
                side=side,
                notional_usdt=notional,
                entry_price=price,
                open_time=ts,
                position_id=order_id,
            )
        )

        print(
            f"[OPENED] side={side} positions={len(self.positions)}/{self.max_positions} "
            f"notional={notional:.2f} weight={weight:.2f}"
        )

    def on_position_closed(self, pnl_usdt: float) -> None:
        """
        平仓后更新连续亏损统计与熔断状态。
        DRY-RUN 下你可以手动调用这个函数来模拟平仓结果。
        """
        if self.positions:
            self.positions.pop(0)

        if pnl_usdt < 0:
            self.risk.consec_losses += 1
        else:
            self.risk.consec_losses = 0

        if self.risk.consec_losses >= self.max_consec_losses:
            self.risk.trading_paused = True
            print(f"[PAUSE] consec_losses={self.risk.consec_losses}, trading_paused=True")

    def manual_reset_after_review(self) -> None:
        """
        人工复盘后决定恢复策略时调用。
        """
        self.risk.consec_losses = 0
        self.risk.trading_paused = False
        print("[RESUME] manual reset: trading_paused=False, consec_losses=0")
