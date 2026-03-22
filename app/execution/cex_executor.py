from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Supported sides
# ---------------------------------------------------------------------------
# Binance market order direction constants
_SIDE_BUY  = "buy"
_SIDE_SELL = "sell"

# Order polling
_POLL_INTERVAL_SECONDS: int = 1
_POLL_MAX_ATTEMPTS:     int = 60   # wait up to 60 s for fill


@dataclass
class CEXOrderResult:
    """
    Result of a single CEX order placement.

    Attributes
    ----------
    order_id:
        Exchange-assigned order ID.
    symbol:
        Trading pair (e.g. ``"ETH/USDT"``).
    side:
        ``"buy"`` or ``"sell"``.
    amount:
        Requested base-token quantity.
    filled:
        Actual base-token quantity filled so far.
    average_price:
        Average fill price (``None`` if not yet filled).
    cost:
        Total quote-token spent / received (filled quantity × average price).
    status:
        Exchange order status string (``"closed"``, ``"open"``, ``"canceled"``
        or ``"error"``).
    success:
        ``True`` when ``status == "closed"`` and ``filled > 0``.
    error:
        Human-readable error message if the order could not be placed or
        timed out waiting for a fill.
    """
    order_id:      Optional[str]
    symbol:        str
    side:          str
    amount:        float
    filled:        float
    average_price: Optional[float]
    cost:          float
    status:        str
    success:       bool
    error:         Optional[str] = None


class BinanceCEXExecutor:
    """
    Places and tracks real market orders on Binance via CCXT.

    Prerequisites
    -------------
    * ``pip install ccxt>=4.2.70``
    * Set ``BINANCE_API_KEY`` and ``BINANCE_API_SECRET`` in ``.env``.
      The key must have **Spot trading** (create/cancel order) permission.

    Safety notes
    ------------
    * Only **market orders** are used, so fills are nearly instant.
    * The caller must verify the opportunity is PASS *immediately* before
      calling :meth:`execute_cex_leg`, because prices can change.
    * Partial fills are treated as *partial success*: ``filled > 0`` but
      ``filled < amount``.  The orchestrator (``arbitrage_executor.py``)
      decides how to handle this.

    Supported direction mapping
    ---------------------------
    * ``BUY_CEX_SELL_DEX``  → **buy** base token on CEX
    * ``BUY_DEX_SELL_CEX``  → **sell** base token on CEX
    """

    def __init__(
        self,
        poll_interval_seconds: int = _POLL_INTERVAL_SECONDS,
        poll_max_attempts: int     = _POLL_MAX_ATTEMPTS,
    ) -> None:
        self._poll_interval  = poll_interval_seconds
        self._poll_max       = poll_max_attempts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ccxt_exchange(self, api_key: str, api_secret: str):
        """Return an authenticated CCXT Binance instance."""
        try:
            import ccxt
        except ImportError as exc:
            raise ImportError(
                "The 'ccxt' package is required for BinanceCEXExecutor. "
                "Install with: pip install ccxt>=4.2.70"
            ) from exc

        if not api_key or not api_secret:
            raise ValueError(
                "BINANCE_API_KEY and BINANCE_API_SECRET are required for CEX execution. "
                "Set them in your .env file.  The API key must have Spot trading permission."
            )
        return ccxt.binance(
            {
                "apiKey":          api_key,
                "secret":          api_secret,
                "enableRateLimit": True,
            }
        )

    def _poll_order(self, exchange, symbol: str, order_id: str) -> dict:
        """
        Poll an open order until it reaches a terminal state (closed or
        canceled) or the poll limit is exceeded.

        Returns the last fetched order dict.
        """
        for _ in range(self._poll_max):
            order = exchange.fetch_order(order_id, symbol)
            if order["status"] in ("closed", "canceled"):
                return order
            time.sleep(self._poll_interval)
        # Timeout — return last known state
        return exchange.fetch_order(order_id, symbol)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def execute_cex_leg(
        self,
        opportunity,
        api_key:    str,
        api_secret: str,
    ) -> CEXOrderResult:
        """
        Place a market order for the CEX leg of an arbitrage opportunity.

        For ``BUY_CEX_SELL_DEX``  → places a **buy**  market order.
        For ``BUY_DEX_SELL_CEX``  → places a **sell** market order.

        The base quantity is derived from ``opportunity.trade_amount_usd``
        and ``opportunity.cex_price``.

        Parameters
        ----------
        opportunity:
            A ``PASS``-status ``ArbitrageOpportunity``.
        api_key:
            Binance API key (Spot trading permission required).
        api_secret:
            Binance API secret.

        Returns
        -------
        CEXOrderResult
            Contains order_id, filled quantity, average price, cost, status,
            and success flag.

        Raises
        ------
        ValueError
            If opportunity is not PASS or direction is unrecognised.
        """
        if opportunity.status != "PASS":
            raise ValueError(
                f"Opportunity status is {opportunity.status!r}. "
                "Only PASS opportunities should be executed."
            )

        direction = opportunity.direction
        if direction not in ("BUY_CEX_SELL_DEX", "BUY_DEX_SELL_CEX"):
            raise ValueError(
                f"Unrecognised direction: {direction!r}. "
                "Expected 'BUY_CEX_SELL_DEX' or 'BUY_DEX_SELL_CEX'."
            )

        side   = _SIDE_BUY  if direction == "BUY_CEX_SELL_DEX" else _SIDE_SELL
        symbol = opportunity.symbol
        price  = opportunity.cex_price
        amount = opportunity.trade_amount_usd / price if price > 0 else 0.0

        if amount <= 0:
            return CEXOrderResult(
                order_id=None, symbol=symbol, side=side,
                amount=amount, filled=0.0, average_price=None,
                cost=0.0, status="error", success=False,
                error="Computed base amount is 0; check cex_price and trade_amount_usd.",
            )

        try:
            exchange = self._ccxt_exchange(api_key, api_secret)
            order    = exchange.create_market_order(symbol, side, amount)
            order_id = order["id"]

            # Poll until terminal status
            final   = self._poll_order(exchange, symbol, order_id)
            filled  = float(final.get("filled", 0) or 0)
            cost    = float(final.get("cost",   0) or 0)
            avg_px  = float(final["average"]) if final.get("average") else None
            status  = final.get("status", "unknown")
            success = status == "closed" and filled > 0

            return CEXOrderResult(
                order_id=order_id, symbol=symbol, side=side,
                amount=amount, filled=filled, average_price=avg_px,
                cost=cost, status=status, success=success,
            )

        except Exception as exc:  # noqa: BLE001
            return CEXOrderResult(
                order_id=None, symbol=symbol, side=side,
                amount=amount, filled=0.0, average_price=None,
                cost=0.0, status="error", success=False,
                error=str(exc),
            )
