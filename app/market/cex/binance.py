from __future__ import annotations

import os
import time

import ccxt

from app.market.dex.base import Quote


class BinanceCEXQuote:
    """Fetches CEX order-book quotes from Binance via CCXT."""

    def __init__(self) -> None:
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        config: dict = {"enableRateLimit": True}
        if api_key and api_secret and api_key != "your_binance_api_key_here":
            config["apiKey"] = api_key
            config["secret"] = api_secret
        self._exchange = ccxt.binance(config)

    # ------------------------------------------------------------------
    def fetch_quotes(
        self, symbols: list[str], trade_amount_usd: float
    ) -> list[Quote]:
        """Return one Quote per symbol fetched from the Binance order book."""
        results: list[Quote] = []
        for symbol in symbols:
            try:
                ob = self._exchange.fetch_order_book(symbol, limit=5)
                bids = ob.get("bids", [])
                asks = ob.get("asks", [])
                if not bids or not asks:
                    continue

                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                mid = (best_bid + best_ask) / 2.0

                # Rough liquidity: sum top-5 bid depth * price
                liquidity_usd = sum(float(b[0]) * float(b[1]) for b in bids[:5])

                results.append(
                    Quote(
                        symbol=symbol,
                        exchange="binance",
                        exchange_type="CEX",
                        bid=best_bid,
                        ask=best_ask,
                        mid=mid,
                        timestamp=time.time(),
                        liquidity_usd=liquidity_usd,
                        raw={"bids": bids[:5], "asks": asks[:5]},
                    )
                )
            except ccxt.NetworkError as exc:
                print(f"[BinanceCEX] network error for {symbol}: {exc}")
            except ccxt.ExchangeError as exc:
                print(f"[BinanceCEX] exchange error for {symbol}: {exc}")
            except Exception as exc:  # noqa: BLE001
                print(f"[BinanceCEX] unexpected error for {symbol}: {exc}")

        return results
