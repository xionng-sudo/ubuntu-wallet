from __future__ import annotations

import random
import time
from typing import Optional

from app.market.dex.base import BaseDEXQuote, Quote

# Default mid prices (USD) used when no reference price is provided
_DEFAULT_PRICES: dict[str, float] = {
    "ETH/USDT": 3000.0,
    "BTC/USDT": 65000.0,
    "BNB/USDT": 560.0,
    "SOL/USDT": 160.0,
    "MATIC/USDT": 0.85,
}

# Simulated on-chain liquidity depth (USD) per symbol
_DEFAULT_LIQUIDITY: dict[str, float] = {
    "ETH/USDT": 5_000_000.0,
    "BTC/USDT": 8_000_000.0,
    "BNB/USDT": 1_500_000.0,
    "SOL/USDT": 2_000_000.0,
    "MATIC/USDT": 400_000.0,
}


class MockDEXQuote(BaseDEXQuote):
    """
    Simulated DEX that produces realistic prices without a real on-chain
    connection.  Prices are derived from a reference price (CEX mid or a
    hard-coded default) with:
      - a wider spread than CEX (0.3 %–1.0 %)
      - random noise of ± 0.5 %
    This creates occasional arbitrage opportunities for end-to-end testing.
    """

    def __init__(
        self,
        reference_prices: Optional[dict[str, float]] = None,
        spread_range: tuple[float, float] = (0.003, 0.010),
        noise_pct: float = 0.005,
        seed: Optional[int] = None,
    ) -> None:
        self._reference_prices = reference_prices or {}
        self._spread_min, self._spread_max = spread_range
        self._noise_pct = noise_pct
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "mock_dex"

    # ------------------------------------------------------------------
    def fetch_quotes(
        self, symbols: list[str], trade_amount_usd: float
    ) -> list[Quote]:
        results: list[Quote] = []
        for symbol in symbols:
            ref = self._reference_prices.get(
                symbol, _DEFAULT_PRICES.get(symbol, 100.0)
            )
            noise = self._rng.uniform(-self._noise_pct, self._noise_pct)
            mid = ref * (1.0 + noise)

            half_spread = self._rng.uniform(self._spread_min, self._spread_max) / 2.0
            bid = mid * (1.0 - half_spread)
            ask = mid * (1.0 + half_spread)

            liquidity = _DEFAULT_LIQUIDITY.get(symbol, 500_000.0)

            results.append(
                Quote(
                    symbol=symbol,
                    exchange=self.name,
                    exchange_type="DEX",
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    timestamp=time.time(),
                    liquidity_usd=liquidity,
                    raw={"ref_price": ref, "noise_pct": noise, "half_spread": half_spread},
                )
            )
        return results
