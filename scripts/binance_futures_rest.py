#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lightweight Binance USDT-M Futures REST client (PR-2A).

Uses only stdlib + requests; no official Binance SDK required.

Features:
  - PROD and TESTNET endpoint support
  - HMAC-SHA256 signed requests with timestamp / recvWindow
  - Public endpoint helpers (server time, exchangeInfo)
  - Signed endpoint helpers (account, positionRisk, orders)
  - Symbol precision cache: stepSize (LOT_SIZE), tickSize (PRICE_FILTER)
  - Quantity / price normalization with minQty validation
  - BinanceAPIError for structured error handling
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import urllib.parse
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

PROD_BASE_URL = "https://fapi.binance.com"
TESTNET_BASE_URL = "https://testnet.binancefuture.com"

DEFAULT_RECV_WINDOW = 5000  # ms


class BinanceAPIError(RuntimeError):
    """Raised when Binance REST API returns a negative code payload."""

    def __init__(self, code: int, msg: str, status_code: int = 0):
        self.code = code
        self.msg = msg
        self.status_code = status_code
        super().__init__(f"BinanceAPIError code={code} msg={msg!r} http={status_code}")


class SymbolInfo:
    """Per-symbol trading rules extracted from exchangeInfo."""

    def __init__(self, raw: Dict[str, Any]):
        self.symbol: str = raw["symbol"]
        self.status: str = raw.get("status", "UNKNOWN")
        self.base_asset: str = raw.get("baseAsset", "")
        self.quote_asset: str = raw.get("quoteAsset", "")

        # Safe defaults in case filters are missing
        self.qty_step: Decimal = Decimal("0.001")
        self.price_tick: Decimal = Decimal("0.01")
        self.min_qty: Decimal = Decimal("0.001")
        self.max_qty: Decimal = Decimal("999999")

        for f in raw.get("filters", []):
            ft = f.get("filterType", "")
            if ft == "LOT_SIZE":
                self.qty_step = Decimal(str(f.get("stepSize", "0.001")))
                self.min_qty = Decimal(str(f.get("minQty", "0.001")))
                self.max_qty = Decimal(str(f.get("maxQty", "999999")))
            elif ft == "PRICE_FILTER":
                self.price_tick = Decimal(str(f.get("tickSize", "0.01")))

        if self.qty_step <= Decimal("0"):
            raise ValueError(
                f"{self.symbol}: stepSize={self.qty_step} is invalid (must be > 0)"
            )
        if self.price_tick <= Decimal("0"):
            raise ValueError(
                f"{self.symbol}: tickSize={self.price_tick} is invalid (must be > 0)"
            )

        self.qty_precision: int = max(0, -self.qty_step.normalize().as_tuple().exponent)
        self.price_precision: int = max(0, -self.price_tick.normalize().as_tuple().exponent)

    def is_trading(self) -> bool:
        return self.status == "TRADING"

    def round_qty(self, qty: float) -> Decimal:
        """Round quantity down to the nearest stepSize."""
        d = Decimal(str(qty))
        return (d / self.qty_step).to_integral_value(rounding=ROUND_DOWN) * self.qty_step

    def round_price(self, price: float) -> Decimal:
        """Round price down to the nearest tickSize."""
        d = Decimal(str(price))
        return (d / self.price_tick).to_integral_value(rounding=ROUND_DOWN) * self.price_tick

    def validate_qty(self, qty: Decimal) -> None:
        """Raise ValueError if qty is zero/negative or below minQty."""
        if qty <= Decimal("0"):
            raise ValueError(
                f"{self.symbol}: rounded qty={qty} is zero or negative "
                f"(stepSize={self.qty_step})"
            )
        if qty < self.min_qty:
            raise ValueError(
                f"{self.symbol}: qty={qty} is below minQty={self.min_qty}"
            )


class BinanceFuturesClient:
    """
    Minimal Binance USDT-M Futures REST client.

    Args:
        api_key:     Binance API key (required for signed endpoints).
        api_secret:  Binance API secret (required for signed endpoints).
        env:         "prod" (default) or "testnet".
        recv_window: Timestamp tolerance window in ms (default 5000).
        session:     Optional requests.Session (useful for testing/mocking).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        env: str = "prod",
        recv_window: int = DEFAULT_RECV_WINDOW,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        self.base_url = TESTNET_BASE_URL if env == "testnet" else PROD_BASE_URL
        self._session = session or requests.Session()
        self._exchange_info_cache: Optional[Dict[str, SymbolInfo]] = None

    def _sign(self, params: Dict[str, Any]) -> str:
        query = urllib.parse.urlencode(params)
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return sig

    def _headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    @staticmethod
    def _server_time_ms() -> int:
        return int(time.time() * 1000)

    def _get_public(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self.base_url + path
        r = self._session.get(url, params=params or {}, timeout=10)
        return self._handle(r)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self.base_url + path
        r = self._session.get(url, params=params or {}, headers=self._headers(), timeout=10)
        return self._handle(r)

    def _signed_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        p = dict(params or {})
        p["timestamp"] = self._server_time_ms()
        p["recvWindow"] = self.recv_window
        p["signature"] = self._sign(p)
        return self._get(path, p)

    def _signed_post(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        p = dict(params or {})
        p["timestamp"] = self._server_time_ms()
        p["recvWindow"] = self.recv_window
        p["signature"] = self._sign(p)
        url = self.base_url + path
        r = self._session.post(url, params=p, headers=self._headers(), timeout=10)
        return self._handle(r)

    def _signed_delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        p = dict(params or {})
        p["timestamp"] = self._server_time_ms()
        p["recvWindow"] = self.recv_window
        p["signature"] = self._sign(p)
        url = self.base_url + path
        r = self._session.delete(url, params=p, headers=self._headers(), timeout=10)
        return self._handle(r)

    @staticmethod
    def _handle(r: requests.Response) -> Any:
        try:
            data = r.json()
        except ValueError:
            raise BinanceAPIError(-1, r.text[:200], r.status_code)
        if isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] < 0:
            raise BinanceAPIError(data["code"], data.get("msg", ""), r.status_code)
        if not r.ok:
            raise BinanceAPIError(-1, str(data)[:200], r.status_code)
        return data

    def get_server_time(self) -> int:
        """GET /fapi/v1/time — returns serverTime in ms."""
        data = self._get_public("/fapi/v1/time")
        return int(data["serverTime"])

    def get_exchange_info(self) -> Dict[str, Any]:
        """GET /fapi/v1/exchangeInfo — raw response dict."""
        return self._get_public("/fapi/v1/exchangeInfo")

    def get_mark_price(self, symbol: str) -> float:
        """GET /fapi/v1/premiumIndex — returns markPrice as float."""
        data = self._get_public("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["markPrice"])

    def load_exchange_info(self) -> Dict[str, SymbolInfo]:
        """
        Load and cache exchange info as {symbol: SymbolInfo}.
        Call refresh_exchange_info() to force a reload.
        """
        if self._exchange_info_cache is None:
            self._exchange_info_cache = self._parse_exchange_info()
        return self._exchange_info_cache

    def refresh_exchange_info(self) -> Dict[str, SymbolInfo]:
        """Force-reload exchange info and return updated cache."""
        self._exchange_info_cache = None
        return self.load_exchange_info()

    def _parse_exchange_info(self) -> Dict[str, SymbolInfo]:
        raw = self.get_exchange_info()
        result: Dict[str, SymbolInfo] = {}

        for sym_raw in raw.get("symbols", []):
            try:
                si = SymbolInfo(sym_raw)
                result[si.symbol] = si
            except Exception as exc:
                logger.warning(
                    "[BinanceFuturesClient] Skipping symbol %s due to invalid exchangeInfo: %s",
                    sym_raw.get("symbol"),
                    exc,
                )
                continue

        logger.info(
            "[BinanceFuturesClient] Loaded %d valid symbols from exchangeInfo",
            len(result),
        )
        return result

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Return SymbolInfo for a symbol (uses cached exchange info)."""
        cache = self.load_exchange_info()
        if symbol not in cache:
            raise KeyError(f"Symbol {symbol!r} not found in exchangeInfo")
        return cache[symbol]

    def normalize_qty(self, symbol: str, qty: float) -> Decimal:
        """Round qty down to stepSize and validate against minQty."""
        si = self.get_symbol_info(symbol)
        rounded = si.round_qty(qty)
        si.validate_qty(rounded)
        return rounded

    def normalize_price(self, symbol: str, price: float) -> Decimal:
        """Round price down to tickSize."""
        si = self.get_symbol_info(symbol)
        return si.round_price(price)

    def get_account(self) -> Dict[str, Any]:
        """GET /fapi/v2/account"""
        return self._signed_get("/fapi/v2/account")

    def get_position_risk(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /fapi/v2/positionRisk"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return self._signed_get("/fapi/v2/positionRisk", params)

    def place_order(self, **kwargs: Any) -> Dict[str, Any]:
        """
        POST /fapi/v1/order

        Common kwargs:
          symbol, side (BUY/SELL), type (MARKET/LIMIT), quantity,
          positionSide (LONG/SHORT for hedge mode), price, timeInForce,
          reduceOnly
        """
        return self._signed_post("/fapi/v1/order", dict(kwargs))

    def get_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        orig_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /fapi/v1/order"""
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        return self._signed_get("/fapi/v1/order", params)

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        orig_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """DELETE /fapi/v1/order"""
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        return self._signed_delete("/fapi/v1/order", params)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /fapi/v1/openOrders"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return self._signed_get("/fapi/v1/openOrders", params)

    def place_market_order(
        self,
        symbol: str,
        side: str,
        qty_usdt: float,
        current_price: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Place a MARKET order with quantity derived from notional USDT.
        """
        if current_price <= 0:
            raise ValueError(f"current_price must be positive, got {current_price}")
        raw_qty = qty_usdt / current_price
        qty = self.normalize_qty(symbol, raw_qty)

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": str(qty),
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        logger.info(
            "[BinanceFuturesClient] place_market_order %s %s qty=%s reduce_only=%s",
            side,
            symbol,
            qty,
            reduce_only,
        )
        return self.place_order(**params)

    def close_position_market(
        self,
        symbol: str,
        position_side_str: str,
        qty_usdt: float,
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Place a reduce-only MARKET order to close an existing position.
        """
        close_side = "SELL" if position_side_str == "LONG" else "BUY"
        return self.place_market_order(
            symbol=symbol,
            side=close_side,
            qty_usdt=qty_usdt,
            current_price=current_price,
            reduce_only=True,
        )
