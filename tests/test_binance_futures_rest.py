#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_binance_futures_rest.py
====================================
Unit tests for scripts/binance_futures_rest.py

Covers:
  - BinanceAPIError construction
  - SymbolInfo precision parsing (LOT_SIZE / PRICE_FILTER)
  - SymbolInfo.round_qty / round_price
  - SymbolInfo.validate_qty
  - BinanceFuturesClient.normalize_qty / normalize_price
  - BinanceFuturesClient._sign (HMAC signature)
  - BinanceFuturesClient._handle (error detection)
  - BinanceFuturesClient high-level helpers (place_market_order, close_position_market)
    using a mocked session
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import unittest
import urllib.parse
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from binance_futures_rest import (
    BinanceAPIError,
    BinanceFuturesClient,
    PROD_BASE_URL,
    TESTNET_BASE_URL,
    SymbolInfo,
)


# ---------------------------------------------------------------------------
# Helper: build minimal raw symbol dict for SymbolInfo
# ---------------------------------------------------------------------------

def _make_raw_symbol(
    symbol: str = "ETHUSDT",
    status: str = "TRADING",
    step_size: str = "0.001",
    min_qty: str = "0.001",
    max_qty: str = "10000",
    tick_size: str = "0.01",
) -> dict:
    return {
        "symbol": symbol,
        "status": status,
        "baseAsset": "ETH",
        "quoteAsset": "USDT",
        "filters": [
            {
                "filterType": "LOT_SIZE",
                "stepSize": step_size,
                "minQty": min_qty,
                "maxQty": max_qty,
            },
            {
                "filterType": "PRICE_FILTER",
                "tickSize": tick_size,
                "minPrice": "0.01",
                "maxPrice": "100000",
            },
        ],
    }


def _make_exchange_info(symbols: list) -> dict:
    return {"symbols": symbols}


def _make_client(
    api_key: str = "testkey",
    api_secret: str = "testsecret",
    env: str = "prod",
    mock_exchange_info: dict | None = None,
) -> BinanceFuturesClient:
    """Return a client with a mocked session."""
    session = MagicMock()
    client = BinanceFuturesClient(
        api_key=api_key,
        api_secret=api_secret,
        env=env,
        session=session,
    )
    if mock_exchange_info is not None:
        # Pre-populate cache so tests don't hit the network
        from binance_futures_rest import SymbolInfo as SI

        client._exchange_info_cache = {
            raw["symbol"]: SI(raw) for raw in mock_exchange_info.get("symbols", [])
        }
    return client


# ---------------------------------------------------------------------------
# BinanceAPIError
# ---------------------------------------------------------------------------


class TestBinanceAPIError(unittest.TestCase):
    def test_attributes(self):
        err = BinanceAPIError(-1100, "Bad request", 400)
        self.assertEqual(err.code, -1100)
        self.assertEqual(err.msg, "Bad request")
        self.assertEqual(err.status_code, 400)

    def test_str_contains_code(self):
        err = BinanceAPIError(-1100, "illegal chars")
        self.assertIn("-1100", str(err))
        self.assertIn("illegal chars", str(err))

    def test_is_runtime_error(self):
        self.assertIsInstance(BinanceAPIError(-1, "x"), RuntimeError)


# ---------------------------------------------------------------------------
# SymbolInfo
# ---------------------------------------------------------------------------


class TestSymbolInfo(unittest.TestCase):
    def _eth(self, **kwargs) -> SymbolInfo:
        return SymbolInfo(_make_raw_symbol(**kwargs))

    def test_basic_fields(self):
        si = self._eth()
        self.assertEqual(si.symbol, "ETHUSDT")
        self.assertEqual(si.status, "TRADING")
        self.assertEqual(si.base_asset, "ETH")
        self.assertEqual(si.quote_asset, "USDT")

    def test_is_trading_true(self):
        self.assertTrue(self._eth(status="TRADING").is_trading())

    def test_is_trading_false(self):
        self.assertFalse(self._eth(status="BREAK").is_trading())

    def test_precision_derived_from_step_size(self):
        si = self._eth(step_size="0.001")
        self.assertEqual(si.qty_step, Decimal("0.001"))
        self.assertEqual(si.qty_precision, 3)

    def test_precision_integer_step(self):
        si = self._eth(step_size="1")
        self.assertEqual(si.qty_precision, 0)

    def test_price_precision(self):
        si = self._eth(tick_size="0.10")
        self.assertEqual(si.price_precision, 1)

    def test_round_qty_exact_multiple(self):
        si = self._eth(step_size="0.001")
        self.assertEqual(si.round_qty(1.234), Decimal("1.234"))

    def test_round_qty_truncates_down(self):
        si = self._eth(step_size="0.001")
        # 1.2349 should become 1.234, not 1.235
        self.assertEqual(si.round_qty(1.2349), Decimal("1.234"))

    def test_round_qty_large_step(self):
        si = self._eth(step_size="10")
        self.assertEqual(si.round_qty(35.9), Decimal("30"))

    def test_round_price(self):
        si = self._eth(tick_size="0.10")
        self.assertEqual(si.round_price(1842.76), Decimal("1842.7"))

    def test_validate_qty_valid(self):
        si = self._eth(step_size="0.001", min_qty="0.001")
        si.validate_qty(Decimal("0.001"))  # should not raise

    def test_validate_qty_zero_raises(self):
        si = self._eth(step_size="0.001", min_qty="0.001")
        with self.assertRaises(ValueError):
            si.validate_qty(Decimal("0"))

    def test_validate_qty_below_min_raises(self):
        si = self._eth(step_size="0.001", min_qty="0.010")
        with self.assertRaises(ValueError):
            si.validate_qty(Decimal("0.005"))

    def test_no_filters(self):
        # Symbol with no filters at all should use safe defaults
        raw = {"symbol": "XYZUSDT", "status": "TRADING", "baseAsset": "XYZ", "quoteAsset": "USDT", "filters": []}
        si = SymbolInfo(raw)
        self.assertEqual(si.qty_step, Decimal("0.001"))
        self.assertEqual(si.price_tick, Decimal("0.01"))


# ---------------------------------------------------------------------------
# BinanceFuturesClient — HMAC signing
# ---------------------------------------------------------------------------


class TestBinanceFuturesClientSign(unittest.TestCase):
    def test_sign_is_hex_sha256(self):
        client = _make_client(api_secret="mysecret")
        params = {"symbol": "ETHUSDT", "timestamp": 1700000000000}
        sig = client._sign(params)
        # Verify independently
        query = urllib.parse.urlencode(params)
        expected = hmac.new(b"mysecret", query.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(sig, expected)

    def test_sign_deterministic(self):
        client = _make_client(api_secret="secret123")
        p = {"a": 1, "b": "two"}
        self.assertEqual(client._sign(p), client._sign(p))

    def test_sign_changes_with_secret(self):
        c1 = _make_client(api_secret="secret_a")
        c2 = _make_client(api_secret="secret_b")
        p = {"x": 1}
        self.assertNotEqual(c1._sign(p), c2._sign(p))


# ---------------------------------------------------------------------------
# BinanceFuturesClient — endpoint selection
# ---------------------------------------------------------------------------


class TestBinanceFuturesClientEndpoints(unittest.TestCase):
    def test_prod_base_url(self):
        c = _make_client(env="prod")
        self.assertEqual(c.base_url, PROD_BASE_URL)

    def test_testnet_base_url(self):
        c = _make_client(env="testnet")
        self.assertEqual(c.base_url, TESTNET_BASE_URL)


# ---------------------------------------------------------------------------
# BinanceFuturesClient — _handle
# ---------------------------------------------------------------------------


def _mock_response(json_data: Any, status_code: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = (status_code < 400)
    r.json.return_value = json_data
    r.text = json.dumps(json_data)
    return r


class TestBinanceFuturesClientHandle(unittest.TestCase):
    def test_success(self):
        r = _mock_response({"orderId": 123})
        result = BinanceFuturesClient._handle(r)
        self.assertEqual(result["orderId"], 123)

    def test_binance_error_code(self):
        r = _mock_response({"code": -1100, "msg": "Illegal characters"}, status_code=400)
        with self.assertRaises(BinanceAPIError) as ctx:
            BinanceFuturesClient._handle(r)
        self.assertEqual(ctx.exception.code, -1100)

    def test_non_ok_without_error_code(self):
        r = _mock_response({"something": "unexpected"}, status_code=503)
        with self.assertRaises(BinanceAPIError):
            BinanceFuturesClient._handle(r)

    def test_invalid_json_raises(self):
        r = MagicMock()
        r.ok = False
        r.status_code = 500
        r.json.side_effect = ValueError("no json")
        r.text = "internal error"
        with self.assertRaises(BinanceAPIError):
            BinanceFuturesClient._handle(r)


# ---------------------------------------------------------------------------
# BinanceFuturesClient — normalize helpers (uses cached exchange info)
# ---------------------------------------------------------------------------


class TestBinanceFuturesClientNormalize(unittest.TestCase):
    def setUp(self):
        raw_eth = _make_raw_symbol(
            symbol="ETHUSDT",
            step_size="0.001",
            min_qty="0.001",
            tick_size="0.01",
        )
        self.client = _make_client(
            mock_exchange_info=_make_exchange_info([raw_eth])
        )

    def test_normalize_qty(self):
        qty = self.client.normalize_qty("ETHUSDT", 1.2349)
        self.assertEqual(qty, Decimal("1.234"))

    def test_normalize_qty_zero_raises(self):
        # price > notional, so qty becomes < stepSize
        with self.assertRaises(ValueError):
            # 0.000001 / 1.0 -> 0.000001; after rounding with step=0.001 -> 0.000
            self.client.normalize_qty("ETHUSDT", 0.0000001)

    def test_normalize_price(self):
        price = self.client.normalize_price("ETHUSDT", 1842.768)
        self.assertEqual(price, Decimal("1842.76"))

    def test_unknown_symbol_raises(self):
        with self.assertRaises(KeyError):
            self.client.normalize_qty("XYZUSDT", 1.0)


# ---------------------------------------------------------------------------
# BinanceFuturesClient — place_market_order / close_position_market (mocked)
# ---------------------------------------------------------------------------


class TestBinanceFuturesClientOrders(unittest.TestCase):
    def setUp(self):
        raw_eth = _make_raw_symbol(
            symbol="ETHUSDT",
            step_size="0.001",
            min_qty="0.001",
            tick_size="0.01",
        )
        self.client = _make_client(
            mock_exchange_info=_make_exchange_info([raw_eth])
        )
        # Patch _signed_post to return a fake order response
        self._post_patch = patch.object(
            self.client,
            "_signed_post",
            return_value={"orderId": 999, "status": "FILLED"},
        )
        self._post_mock = self._post_patch.start()

    def tearDown(self):
        self._post_patch.stop()

    def test_place_market_order_buy(self):
        resp = self.client.place_market_order(
            symbol="ETHUSDT",
            side="BUY",
            qty_usdt=3000.0,
            current_price=2000.0,
        )
        self.assertEqual(resp["orderId"], 999)
        # Check the signed_post was called with expected params
        call_kwargs = self._post_mock.call_args
        params = call_kwargs[0][1]  # second positional arg
        self.assertEqual(params["symbol"], "ETHUSDT")
        self.assertEqual(params["side"], "BUY")
        self.assertEqual(params["type"], "MARKET")
        # qty = 3000/2000 = 1.5, rounded to step=0.001 -> 1.500
        self.assertEqual(Decimal(params["quantity"]), Decimal("1.500"))

    def test_place_market_order_reduce_only(self):
        self.client.place_market_order(
            symbol="ETHUSDT",
            side="SELL",
            qty_usdt=3000.0,
            current_price=2000.0,
            reduce_only=True,
        )
        params = self._post_mock.call_args[0][1]
        self.assertEqual(params.get("reduceOnly"), "true")

    def test_place_market_order_zero_price_raises(self):
        with self.assertRaises(ValueError):
            self.client.place_market_order(
                symbol="ETHUSDT",
                side="BUY",
                qty_usdt=1000.0,
                current_price=0.0,
            )

    def test_close_position_long_sends_sell(self):
        self.client.close_position_market(
            symbol="ETHUSDT",
            position_side_str="LONG",
            qty_usdt=3000.0,
            current_price=2000.0,
        )
        params = self._post_mock.call_args[0][1]
        self.assertEqual(params["side"], "SELL")
        self.assertEqual(params.get("reduceOnly"), "true")

    def test_close_position_short_sends_buy(self):
        self.client.close_position_market(
            symbol="ETHUSDT",
            position_side_str="SHORT",
            qty_usdt=3000.0,
            current_price=2000.0,
        )
        params = self._post_mock.call_args[0][1]
        self.assertEqual(params["side"], "BUY")
        self.assertEqual(params.get("reduceOnly"), "true")


# ---------------------------------------------------------------------------
# Integration: EthPerpStrategyEngineBinance dry-run still works
# ---------------------------------------------------------------------------


class TestEngineBackwardCompat(unittest.TestCase):
    """
    Verify that the engine's DRY-RUN behavior is unchanged after adding
    the trading_mode / exchange_client parameters.
    """

    def setUp(self):
        import importlib
        import sys
        # Ensure scripts dir is on path
        if _SCRIPTS not in sys.path:
            sys.path.insert(0, _SCRIPTS)
        self.Engine = importlib.import_module("eth_perp_engine_binance")

    def test_dry_run_default(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side
        from datetime import datetime

        engine = EthPerpStrategyEngineBinance(
            strategy_funds_usdt=10000.0,
            symbol="ETHUSDT",
        )
        self.assertEqual(engine.trading_mode, "dry-run")
        self.assertIsNone(engine.exchange_client)

    def test_dry_run_open_returns_dryrun_id(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side
        from datetime import datetime

        engine = EthPerpStrategyEngineBinance(strategy_funds_usdt=10000.0)
        order_id = engine._exchange_open_position(Side.LONG, 1000.0, 2000.0)
        self.assertTrue(order_id.startswith("DRYRUN-"))

    def test_dry_run_close_returns_dryrun_id(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side

        engine = EthPerpStrategyEngineBinance(strategy_funds_usdt=10000.0)
        order_id = engine._exchange_close_position(Side.LONG)
        self.assertTrue(order_id.startswith("DRYRUN-CLOSE-"))

    def test_live_requires_client(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance

        with self.assertRaises(ValueError):
            EthPerpStrategyEngineBinance(
                strategy_funds_usdt=10000.0,
                trading_mode="live",
                exchange_client=None,
            )

    def test_live_open_calls_client(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side

        mock_client = MagicMock()
        mock_client.place_market_order.return_value = {"orderId": 42}

        engine = EthPerpStrategyEngineBinance(
            strategy_funds_usdt=10000.0,
            trading_mode="live",
            exchange_client=mock_client,
        )
        order_id = engine._exchange_open_position(Side.LONG, 3000.0, 2000.0)
        self.assertEqual(order_id, "42")
        mock_client.place_market_order.assert_called_once()

    def test_live_close_calls_client(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side

        mock_client = MagicMock()
        mock_client.close_position_market.return_value = {"orderId": 77}

        engine = EthPerpStrategyEngineBinance(
            strategy_funds_usdt=10000.0,
            trading_mode="live",
            exchange_client=mock_client,
        )
        order_id = engine._exchange_close_position(Side.LONG, current_price=2100.0, notional_usdt=3000.0)
        self.assertEqual(order_id, "77")
        mock_client.close_position_market.assert_called_once()

    def test_live_close_zero_price_raises(self):
        from eth_perp_engine_binance import EthPerpStrategyEngineBinance, Side

        mock_client = MagicMock()
        engine = EthPerpStrategyEngineBinance(
            strategy_funds_usdt=10000.0,
            trading_mode="live",
            exchange_client=mock_client,
        )
        with self.assertRaises(ValueError):
            engine._exchange_close_position(Side.LONG, current_price=0.0, notional_usdt=1000.0)


if __name__ == "__main__":
    unittest.main()
