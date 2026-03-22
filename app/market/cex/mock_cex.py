from __future__ import annotations

import random
import time
from typing import Optional

from app.market.dex.base import Quote

# Default mid prices (USD) – kept in sync with mock_dex defaults
_DEFAULT_PRICES: dict[str, float] = {
    "ETH/USDT": 3000.0,
    "BTC/USDT": 65000.0,
    "BNB/USDT": 560.0,
    "SOL/USDT": 160.0,
    "MATIC/USDT": 0.85,
}

_DEFAULT_LIQUIDITY: dict[str, float] = {
    "ETH/USDT": 10_000_000.0,
    "BTC/USDT": 20_000_000.0,
    "BNB/USDT": 3_000_000.0,
    "SOL/USDT": 4_000_000.0,
    "MATIC/USDT": 800_000.0,
}


class MockCEXQuote:
    """
    Simulated CEX (Binance-compatible interface) for demo / offline mode.
    Prices use hard-coded defaults with a tight 0.05 % spread.
    """

    def __init__(
        self,
        reference_prices: Optional[dict[str, float]] = None,
        spread_pct: float = 0.0005,
        seed: Optional[int] = None,
    ) -> None:
        self._reference_prices = reference_prices or {}
        self._spread_pct = spread_pct
        self._rng = random.Random(seed)

    def fetch_quotes(
        self, symbols: list[str], trade_amount_usd: float
    ) -> list[Quote]:
        results: list[Quote] = []
        for symbol in symbols:
            mid = self._reference_prices.get(
                symbol, _DEFAULT_PRICES.get(symbol, 100.0)
            )
            half = self._spread_pct / 2.0
            bid = mid * (1.0 - half)
            ask = mid * (1.0 + half)
            liquidity = _DEFAULT_LIQUIDITY.get(symbol, 1_000_000.0)
            results.append(
                Quote(
                    symbol=symbol,
                    exchange="mock_cex",
                    exchange_type="CEX",
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    timestamp=time.time(),
                    liquidity_usd=liquidity,
                    raw={"source": "mock"},
                )
            )
        return results
