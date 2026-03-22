from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Quote:
    symbol: str           # e.g. "ETH/USDT"
    exchange: str         # e.g. "binance", "uniswap_v3"
    exchange_type: str    # "CEX" or "DEX"
    bid: float
    ask: float
    mid: float
    timestamp: float
    liquidity_usd: float = 0.0
    raw: dict = field(default_factory=dict)


class BaseDEXQuote(ABC):
    @abstractmethod
    def fetch_quotes(self, symbols: list[str], trade_amount_usd: float) -> list[Quote]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass
