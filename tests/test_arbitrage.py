from __future__ import annotations

import json
import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.market.dex.base import Quote
from app.market.dex.mock_dex import MockDEXQuote
from app.market.dex.uniswap_v3 import UniswapV3Quote, _TOKEN_REGISTRY, _DEFAULT_FEE_TIERS
from app.costs.calculator import (
    CostBreakdown,
    calculate_cex_fee,
    calculate_dex_fee,
    estimate_gas_cost_usd,
    estimate_slippage,
    CEX_FEE_RATE,
    DEX_FEE_RATE,
    DEFAULT_GAS_GWEI,
    DEFAULT_GAS_UNITS,
    ETH_PRICE_USD,
)
from app.arbitrage.engine import ArbitrageEngine, ArbitrageOpportunity
from app.risk.filters import RiskConfig, apply_risk_filter, filter_opportunities
from app.risk.chain_risk import ChainRiskResult, assess_chain_risks, check_quote_ttl
from app.execution.wallet import load_private_key
from app.execution.swap_executor import (
    UniswapV3SwapExecutor,
    DEFAULT_SLIPPAGE_TOLERANCE,
    DEFAULT_QUOTE_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_mock_web3():
    """Return a minimal mock of the web3 module for tests that need it."""
    mock_Web3_cls = MagicMock()
    mock_Web3_cls.to_checksum_address.side_effect = lambda x: x
    mock_Web3_cls.to_wei.side_effect = lambda val, unit: int(val * 10**9)
    mock_web3_mod = MagicMock()
    mock_web3_mod.Web3 = mock_Web3_cls
    return mock_web3_mod


def _make_cex_quote(
    symbol: str = "ETH/USDT",
    bid: float = 3000.0,
    ask: float = 3001.0,
) -> Quote:
    mid = (bid + ask) / 2.0
    return Quote(
        symbol=symbol,
        exchange="binance",
        exchange_type="CEX",
        bid=bid,
        ask=ask,
        mid=mid,
        timestamp=0.0,
        liquidity_usd=1_000_000.0,
    )


def _make_dex_quote(
    symbol: str = "ETH/USDT",
    bid: float = 3015.0,
    ask: float = 3020.0,
    liquidity: float = 500_000.0,
) -> Quote:
    mid = (bid + ask) / 2.0
    return Quote(
        symbol=symbol,
        exchange="mock_dex",
        exchange_type="DEX",
        bid=bid,
        ask=ask,
        mid=mid,
        timestamp=0.0,
        liquidity_usd=liquidity,
    )


# ---------------------------------------------------------------------------
class TestQuoteDataclass(unittest.TestCase):
    def test_creation(self):
        q = Quote(
            symbol="BTC/USDT",
            exchange="binance",
            exchange_type="CEX",
            bid=65000.0,
            ask=65010.0,
            mid=65005.0,
            timestamp=1_700_000_000.0,
            liquidity_usd=2_000_000.0,
        )
        self.assertEqual(q.symbol, "BTC/USDT")
        self.assertEqual(q.exchange_type, "CEX")
        self.assertAlmostEqual(q.mid, 65005.0)
        self.assertEqual(q.raw, {})

    def test_raw_default_is_empty_dict(self):
        q = _make_cex_quote()
        self.assertIsInstance(q.raw, dict)
        self.assertEqual(len(q.raw), 0)


# ---------------------------------------------------------------------------
class TestCostCalculator(unittest.TestCase):
    def test_gas_cost_defaults(self):
        cost = estimate_gas_cost_usd()
        expected = (DEFAULT_GAS_GWEI * 1e-9) * DEFAULT_GAS_UNITS * ETH_PRICE_USD
        self.assertAlmostEqual(cost, expected, places=6)

    def test_gas_cost_custom(self):
        cost = estimate_gas_cost_usd(gas_gwei=50.0, gas_units=200_000, eth_price_usd=2000.0)
        expected = (50.0 * 1e-9) * 200_000 * 2000.0
        self.assertAlmostEqual(cost, expected, places=6)

    def test_slippage_small_trade(self):
        # 1000 USD into 1M USD pool → (1000/1000000)*0.5 = 0.0005
        s = estimate_slippage(1_000.0, 1_000_000.0)
        self.assertAlmostEqual(s, 0.0005, places=6)

    def test_slippage_capped_at_5pct(self):
        s = estimate_slippage(1_000_000.0, 1_000.0)
        self.assertAlmostEqual(s, 0.05)

    def test_slippage_zero_liquidity(self):
        s = estimate_slippage(1_000.0, 0.0)
        self.assertAlmostEqual(s, 0.05)

    def test_cex_fee(self):
        fee = calculate_cex_fee(10_000.0)
        self.assertAlmostEqual(fee, 10_000.0 * CEX_FEE_RATE)

    def test_dex_fee(self):
        fee = calculate_dex_fee(10_000.0)
        self.assertAlmostEqual(fee, 10_000.0 * DEX_FEE_RATE)

    def test_custom_fee_rate(self):
        fee = calculate_cex_fee(10_000.0, fee_rate=0.002)
        self.assertAlmostEqual(fee, 20.0)


# ---------------------------------------------------------------------------
class TestArbitrageEngine(unittest.TestCase):
    def _engine(self):
        # Fixed gas for deterministic tests
        return ArbitrageEngine(gas_gwei=30.0, gas_units=150_000, eth_price_usd=3000.0)

    def test_returns_two_directions(self):
        engine = self._engine()
        cex = _make_cex_quote(bid=3000.0, ask=3001.0)
        dex = _make_dex_quote(bid=3015.0, ask=3020.0)
        opps = engine.evaluate(cex, dex, 10_000.0)
        self.assertEqual(len(opps), 2)
        directions = {o.direction for o in opps}
        self.assertIn("BUY_CEX_SELL_DEX", directions)
        self.assertIn("BUY_DEX_SELL_CEX", directions)

    def test_buy_cex_sell_dex_positive(self):
        """DEX bid > CEX ask → profitable before costs."""
        engine = self._engine()
        cex = _make_cex_quote(bid=3000.0, ask=3001.0)
        dex = _make_dex_quote(bid=3020.0, ask=3025.0)
        opps = engine.evaluate(cex, dex, 10_000.0)
        bcd = next(o for o in opps if o.direction == "BUY_CEX_SELL_DEX")
        self.assertGreater(bcd.gross_profit_usd, 0)

    def test_evaluate_all_matches_by_symbol(self):
        engine = self._engine()
        cex_quotes = [
            _make_cex_quote("ETH/USDT", 3000.0, 3001.0),
            _make_cex_quote("BTC/USDT", 65000.0, 65010.0),
        ]
        dex_quotes = [
            _make_dex_quote("ETH/USDT", 3015.0, 3020.0),
            # No BTC/USDT DEX quote intentionally omitted
        ]
        opps = engine.evaluate_all(cex_quotes, dex_quotes, 10_000.0)
        # Only ETH/USDT matched → 2 directions
        self.assertEqual(len(opps), 2)
        self.assertTrue(all(o.symbol == "ETH/USDT" for o in opps))

    def test_net_profit_calculation(self):
        engine = self._engine()
        cex = _make_cex_quote(bid=3000.0, ask=3000.0)
        dex = _make_dex_quote(bid=3000.0, ask=3000.0, liquidity=10_000_000.0)
        opps = engine.evaluate(cex, dex, 10_000.0)
        for opp in opps:
            # With equal prices gross profit = 0, net profit must be negative
            self.assertEqual(opp.gross_profit_usd, 0.0)
            self.assertLess(opp.net_profit_usd, 0)


# ---------------------------------------------------------------------------
class TestRiskFilters(unittest.TestCase):
    def _passing_opp(self) -> ArbitrageOpportunity:
        return ArbitrageOpportunity(
            symbol="ETH/USDT",
            direction="BUY_CEX_SELL_DEX",
            cex_exchange="binance",
            dex_exchange="mock_dex",
            cex_price=3001.0,
            dex_price=3020.0,
            trade_amount_usd=10_000.0,
            gross_profit_usd=60.0,
            gross_profit_pct=0.6,
            cex_fee_usd=10.0,
            dex_fee_usd=30.0,
            gas_cost_usd=13.5,
            slippage_usd=0.5,
            total_cost_usd=54.0,
            net_profit_usd=6.0,
            net_profit_pct=0.06,
            liquidity_usd=500_000.0,
            status="PASS",
        )

    def test_pass(self):
        opp = self._passing_opp()
        result = apply_risk_filter(opp, RiskConfig())
        self.assertEqual(result.status, "PASS")

    def test_blocked_low_profit_usd(self):
        opp = self._passing_opp()
        opp.net_profit_usd = 0.5
        result = apply_risk_filter(opp, RiskConfig(min_net_profit_usd=1.0))
        self.assertEqual(result.status, "BLOCKED_LOW_PROFIT")

    def test_blocked_high_gas(self):
        opp = self._passing_opp()
        opp.gas_cost_usd = 100.0
        result = apply_risk_filter(opp, RiskConfig(max_gas_cost_usd=50.0))
        self.assertEqual(result.status, "BLOCKED_HIGH_GAS")

    def test_blocked_high_slippage(self):
        opp = self._passing_opp()
        opp.slippage_usd = 200.0  # 2 % of 10_000
        result = apply_risk_filter(opp, RiskConfig(max_slippage_pct=1.0))
        self.assertEqual(result.status, "BLOCKED_HIGH_SLIPPAGE")

    def test_blocked_low_liquidity(self):
        opp = self._passing_opp()
        opp.liquidity_usd = 5_000.0
        result = apply_risk_filter(opp, RiskConfig(min_liquidity_usd=10_000.0))
        self.assertEqual(result.status, "BLOCKED_LOW_LIQUIDITY")

    def test_low_liquidity_checked_first(self):
        """Low liquidity should take precedence over low profit."""
        opp = self._passing_opp()
        opp.liquidity_usd = 1.0
        opp.net_profit_usd = -500.0
        result = apply_risk_filter(opp)
        self.assertEqual(result.status, "BLOCKED_LOW_LIQUIDITY")

    def test_filter_opportunities_list(self):
        opps = [self._passing_opp() for _ in range(3)]
        opps[1].net_profit_usd = -5.0
        results = filter_opportunities(opps)
        statuses = [r.status for r in results]
        self.assertEqual(statuses[0], "PASS")
        self.assertEqual(statuses[1], "BLOCKED_LOW_PROFIT")
        self.assertEqual(statuses[2], "PASS")


# ---------------------------------------------------------------------------
class TestMockDEX(unittest.TestCase):
    def test_returns_quotes_for_known_symbols(self):
        dex = MockDEXQuote(seed=42)
        quotes = dex.fetch_quotes(["ETH/USDT", "BTC/USDT"], 10_000.0)
        self.assertEqual(len(quotes), 2)

    def test_quote_fields_valid(self):
        dex = MockDEXQuote(seed=0)
        q = dex.fetch_quotes(["ETH/USDT"], 10_000.0)[0]
        self.assertEqual(q.exchange_type, "DEX")
        self.assertEqual(q.exchange, "mock_dex")
        self.assertGreater(q.bid, 0)
        self.assertGreater(q.ask, 0)
        self.assertGreater(q.ask, q.bid)
        self.assertGreater(q.liquidity_usd, 0)

    def test_prices_close_to_reference(self):
        ref = {"ETH/USDT": 3000.0}
        dex = MockDEXQuote(reference_prices=ref, seed=1)
        q = dex.fetch_quotes(["ETH/USDT"], 10_000.0)[0]
        # Mid should be within ±2 % of reference
        self.assertAlmostEqual(q.mid, 3000.0, delta=3000.0 * 0.02)

    def test_name_property(self):
        dex = MockDEXQuote()
        self.assertEqual(dex.name, "mock_dex")

    def test_unknown_symbol_uses_fallback_price(self):
        dex = MockDEXQuote(seed=7)
        quotes = dex.fetch_quotes(["UNKNOWN/USDT"], 1000.0)
        self.assertEqual(len(quotes), 1)
        self.assertGreater(quotes[0].mid, 0)


# ---------------------------------------------------------------------------
class TestUniswapV3Quote(unittest.TestCase):
    """Tests for UniswapV3Quote — no real RPC calls are made."""

    def test_name_property(self):
        self.assertEqual(UniswapV3Quote().name, "uniswap_v3")

    def test_parse_symbol_valid(self):
        q = UniswapV3Quote()
        self.assertEqual(q._parse_symbol("ETH/USDT"), ("ETH", "USDT"))
        self.assertEqual(q._parse_symbol("eth/usdt"), ("ETH", "USDT"))

    def test_parse_symbol_invalid_no_slash(self):
        q = UniswapV3Quote()
        with self.assertRaises(ValueError):
            q._parse_symbol("ETHUSDT")

    def test_parse_symbol_invalid_empty_part(self):
        q = UniswapV3Quote()
        with self.assertRaises(ValueError):
            q._parse_symbol("/USDT")

    def test_token_info_known_tokens(self):
        q = UniswapV3Quote()
        addr, dec = q._token_info("ETH")
        self.assertTrue(addr.startswith("0x"))
        self.assertEqual(len(addr), 42)
        self.assertEqual(dec, 18)

        addr_wbtc, dec_wbtc = q._token_info("WBTC")
        self.assertEqual(dec_wbtc, 8)

        addr_usdt, dec_usdt = q._token_info("USDT")
        self.assertEqual(dec_usdt, 6)

    def test_token_info_case_insensitive(self):
        q = UniswapV3Quote()
        addr_upper, _ = q._token_info("ETH")
        addr_lower, _ = q._token_info("eth")
        self.assertEqual(addr_upper, addr_lower)

    def test_token_info_unknown_raises(self):
        q = UniswapV3Quote()
        with self.assertRaises(ValueError):
            q._token_info("FAKECOIN")

    def test_no_rpc_url_raises_value_error(self):
        q = UniswapV3Quote(rpc_url="")
        with self.assertRaises((ValueError, ImportError)):
            q._web3()

    def test_token_registry_has_required_tokens(self):
        for tok in ("ETH", "WETH", "BTC", "WBTC", "USDT", "USDC"):
            self.assertIn(tok, _TOKEN_REGISTRY, f"{tok} missing from token registry")

    def test_bnb_not_in_token_registry(self):
        """BNB ERC-20 must be absent: it has no real Uniswap V3 pool on mainnet."""
        self.assertNotIn("BNB", _TOKEN_REGISTRY,
                         "BNB should not be in the Uniswap V3 registry — "
                         "BNB ERC-20 has no meaningful mainnet pool and returns garbage quotes.")

    def test_bnb_usdt_skipped_with_warning(self):
        """BNB/USDT fetch_quotes emits RuntimeWarning (BNB not in registry)."""
        q = UniswapV3Quote(rpc_url="http://mock")
        mock_w3 = MagicMock()
        with patch.object(q, "_web3", return_value=mock_w3):
            with self.assertWarns(RuntimeWarning):
                results = q.fetch_quotes(["BNB/USDT"], 10_000.0)
        self.assertEqual(results, [], "BNB/USDT should produce no quotes")

    def test_bnb_usdt_absent_from_default_fee_tiers(self):
        """BNB/USDT must not have a fee tier entry (no valid pool)."""
        self.assertNotIn("BNB/USDT", _DEFAULT_FEE_TIERS)

    def test_default_fee_tiers_known(self):
        self.assertIn("ETH/USDT", _DEFAULT_FEE_TIERS)
        self.assertIn("BTC/USDT", _DEFAULT_FEE_TIERS)
        self.assertEqual(_DEFAULT_FEE_TIERS["ETH/USDT"], 500)

    def test_fetch_quotes_skips_failed_symbol_with_warning(self):
        """fetch_quotes emits RuntimeWarning and returns [] when all quotes fail."""
        q = UniswapV3Quote(rpc_url="http://localhost:8545")
        # Patch _web3 to raise inside the per-symbol try/except
        with patch.object(q, "_web3", side_effect=ConnectionError("no rpc")):
            with self.assertWarns(RuntimeWarning):
                results = q.fetch_quotes(["ETH/USDT"], 10_000.0)
        self.assertEqual(results, [])

    def test_fetch_quotes_with_mocked_rpc(self):
        """fetch_quotes returns Quote objects when the RPC responds correctly."""
        q = UniswapV3Quote(rpc_url="http://mock")

        # Mock web3 and QuoterV2 contract call
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True

        # quoteExactInputSingle returns (amountOut, sqrtPriceX96After, ticksCrossed, gasEst)
        # For ask: 10_000 USDT in → 3.333 WETH out  → ask = 10000/3.333 ≈ 3000
        # For bid: 3.333 WETH in  → 10_000 USDT out  → bid = 10000/3.333 ≈ 3000
        ask_amount_out_wei = int(3.333 * 10**18)   # WETH out for 10_000 USDT in
        bid_amount_out_wei = int(10_000 * 10**6)   # USDT out for 3.333 WETH in

        mock_quoter = MagicMock()
        mock_quoter.functions.quoteExactInputSingle.return_value.call.side_effect = [
            (ask_amount_out_wei, 0, 0, 150_000),  # ask call
            (bid_amount_out_wei, 0, 0, 150_000),  # bid call
        ]
        mock_w3.eth.contract.return_value = mock_quoter

        # Mock the web3 module so to_checksum_address works without installation
        mock_Web3_cls = MagicMock()
        mock_Web3_cls.to_checksum_address.side_effect = lambda x: x
        mock_web3_module = MagicMock()
        mock_web3_module.Web3 = mock_Web3_cls

        with patch.object(q, "_web3", return_value=mock_w3), \
             patch.dict("sys.modules", {"web3": mock_web3_module}):
            results = q.fetch_quotes(["ETH/USDT"], 10_000.0)

        self.assertEqual(len(results), 1)
        quote = results[0]
        self.assertEqual(quote.symbol, "ETH/USDT")
        self.assertEqual(quote.exchange, "uniswap_v3")
        self.assertEqual(quote.exchange_type, "DEX")
        self.assertGreater(quote.bid, 0)
        self.assertGreater(quote.ask, 0)
        self.assertGreater(quote.timestamp, 0)
        # Check on-chain metadata in raw
        for key in ("base_addr", "quote_addr", "fee_tier", "quote_timestamp",
                    "base_decimals", "quote_decimals", "amount_in_base"):
            self.assertIn(key, quote.raw, f"raw missing key: {key}")


# ---------------------------------------------------------------------------
class TestWallet(unittest.TestCase):
    """Tests for app.execution.wallet.load_private_key (no network required)."""

    _VALID_HEX = "a" * 64

    def test_load_with_0x_prefix(self):
        key = load_private_key("0x" + self._VALID_HEX)
        self.assertEqual(key, "0x" + self._VALID_HEX)

    def test_load_without_prefix(self):
        key = load_private_key(self._VALID_HEX)
        self.assertEqual(key, "0x" + self._VALID_HEX)

    def test_uppercase_normalised_to_lowercase(self):
        key = load_private_key("A" * 64)
        self.assertEqual(key, "0x" + "a" * 64)

    def test_empty_raises_value_error(self):
        with self.assertRaises(ValueError):
            load_private_key("")

    def test_too_short_raises_value_error(self):
        with self.assertRaises(ValueError):
            load_private_key("a" * 32)

    def test_too_long_raises_value_error(self):
        with self.assertRaises(ValueError):
            load_private_key("a" * 65)

    def test_non_hex_raises_value_error(self):
        with self.assertRaises(ValueError):
            load_private_key("z" * 64)

    def test_env_var_fallback(self):
        with patch.dict(os.environ, {"WALLET_PRIVATE_KEY": self._VALID_HEX}):
            key = load_private_key()
            self.assertEqual(key, "0x" + self._VALID_HEX)

    def test_explicit_arg_overrides_env(self):
        env_key  = "b" * 64
        arg_key  = "c" * 64
        with patch.dict(os.environ, {"WALLET_PRIVATE_KEY": env_key}):
            key = load_private_key(arg_key)
        self.assertEqual(key, "0x" + arg_key)


# ---------------------------------------------------------------------------
class TestChainRisk(unittest.TestCase):
    """Tests for app.risk.chain_risk (no network required)."""

    def _make_pass_opp(self, net_pct: float = 1.0) -> ArbitrageOpportunity:
        return ArbitrageOpportunity(
            symbol="ETH/USDT",
            direction="BUY_CEX_SELL_DEX",
            cex_exchange="binance",
            dex_exchange="uniswap_v3",
            cex_price=3001.0,
            dex_price=3060.0,
            trade_amount_usd=10_000.0,
            gross_profit_usd=200.0,
            gross_profit_pct=2.0,
            cex_fee_usd=10.0,
            dex_fee_usd=30.0,
            gas_cost_usd=13.5,
            slippage_usd=5.0,
            total_cost_usd=58.5,
            net_profit_usd=net_pct * 100,
            net_profit_pct=net_pct,
            liquidity_usd=500_000.0,
            status="PASS",
        )

    def _make_uniswap_quote(self, fresh: bool = True) -> Quote:
        ts = time.time() if fresh else time.time() - 120
        return Quote(
            symbol="ETH/USDT",
            exchange="uniswap_v3",
            exchange_type="DEX",
            bid=3050.0,
            ask=3060.0,
            mid=3055.0,
            timestamp=ts,
            liquidity_usd=500_000.0,
            raw={
                "base_addr":       "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "quote_addr":      "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "base_decimals":   18,
                "quote_decimals":  6,
                "fee_tier":        500,
                "amount_in_base":  3.277,
                "gas_estimate":    150_000,
                "quote_timestamp": ts,
                "quote_ttl_seconds": 30,
            },
        )

    def _make_mock_dex_quote(self) -> Quote:
        return Quote(
            symbol="ETH/USDT", exchange="mock_dex", exchange_type="DEX",
            bid=3050.0, ask=3060.0, mid=3055.0, timestamp=time.time(),
            liquidity_usd=500_000.0, raw={"ref_price": 3000.0},
        )

    # --- check_quote_ttl ---

    def test_fresh_quote_passes_ttl(self):
        q = self._make_uniswap_quote(fresh=True)
        self.assertTrue(check_quote_ttl(q, ttl_seconds=30))

    def test_stale_quote_fails_ttl(self):
        q = self._make_uniswap_quote(fresh=False)
        self.assertFalse(check_quote_ttl(q, ttl_seconds=30))

    def test_zero_timestamp_fails_ttl(self):
        q = self._make_uniswap_quote()
        q.timestamp = 0.0
        self.assertFalse(check_quote_ttl(q))

    # --- assess_chain_risks ---

    def test_fresh_uniswap_quote_is_safe(self):
        opp   = self._make_pass_opp()
        quote = self._make_uniswap_quote(fresh=True)
        result = assess_chain_risks(opp, quote)
        self.assertTrue(result.is_safe)
        self.assertEqual(result.warnings, [])

    def test_stale_uniswap_quote_blocks(self):
        opp   = self._make_pass_opp()
        quote = self._make_uniswap_quote(fresh=False)
        result = assess_chain_risks(opp, quote, quote_ttl_seconds=30)
        self.assertFalse(result.is_safe)
        self.assertTrue(any("STALE_QUOTE" in w for w in result.warnings))

    def test_mock_dex_blocks_execution(self):
        opp   = self._make_pass_opp()
        quote = self._make_mock_dex_quote()
        result = assess_chain_risks(opp, quote)
        self.assertFalse(result.is_safe)
        self.assertTrue(any("MOCK_DEX" in w for w in result.warnings))

    def test_mev_risk_advisory_does_not_block(self):
        opp   = self._make_pass_opp(net_pct=0.1)   # below 0.3 % threshold
        quote = self._make_uniswap_quote(fresh=True)
        result = assess_chain_risks(opp, quote)
        self.assertTrue(result.is_safe)   # advisory only
        self.assertTrue(any("MEV_RISK" in w for w in result.warnings))

    def test_high_profit_has_no_mev_warning(self):
        opp   = self._make_pass_opp(net_pct=1.0)
        quote = self._make_uniswap_quote(fresh=True)
        result = assess_chain_risks(opp, quote)
        self.assertTrue(result.is_safe)
        self.assertFalse(any("MEV_RISK" in w for w in result.warnings))

    def test_missing_route_metadata_blocks(self):
        opp   = self._make_pass_opp()
        quote = self._make_uniswap_quote(fresh=True)
        del quote.raw["base_addr"]   # simulate corrupt quote
        result = assess_chain_risks(opp, quote)
        self.assertFalse(result.is_safe)
        self.assertTrue(any("INVALID_ROUTE" in w for w in result.warnings))


# ---------------------------------------------------------------------------
class TestSwapExecutor(unittest.TestCase):
    """Tests for UniswapV3SwapExecutor (no real transactions)."""

    def _executor(self) -> UniswapV3SwapExecutor:
        return UniswapV3SwapExecutor(
            slippage_tolerance=DEFAULT_SLIPPAGE_TOLERANCE,
            quote_ttl_seconds=DEFAULT_QUOTE_TTL_SECONDS,
        )

    def _make_pass_opp(self) -> ArbitrageOpportunity:
        return ArbitrageOpportunity(
            symbol="ETH/USDT",
            direction="BUY_CEX_SELL_DEX",
            cex_exchange="binance",
            dex_exchange="uniswap_v3",
            cex_price=3001.0,
            dex_price=3060.0,
            trade_amount_usd=10_000.0,
            gross_profit_usd=200.0,
            gross_profit_pct=2.0,
            cex_fee_usd=10.0,
            dex_fee_usd=30.0,
            gas_cost_usd=13.5,
            slippage_usd=5.0,
            total_cost_usd=58.5,
            net_profit_usd=141.5,
            net_profit_pct=1.415,
            liquidity_usd=500_000.0,
            status="PASS",
        )

    def _make_uniswap_quote(self) -> Quote:
        ts = time.time()
        return Quote(
            symbol="ETH/USDT",
            exchange="uniswap_v3",
            exchange_type="DEX",
            bid=3050.0,
            ask=3060.0,
            mid=3055.0,
            timestamp=ts,
            liquidity_usd=500_000.0,
            raw={
                "base_addr":       "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "quote_addr":      "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "base_decimals":   18,
                "quote_decimals":  6,
                "fee_tier":        500,
                "amount_in_base":  3.277,
                "gas_estimate":    150_000,
                "quote_timestamp": ts,
                "quote_ttl_seconds": 30,
            },
        )

    def test_ttl_raises_on_stale_quote(self):
        executor = self._executor()
        stale_ts = time.time() - 60
        with self.assertRaises(ValueError, msg="Should raise on stale quote"):
            executor._check_quote_ttl(stale_ts)

    def test_ttl_passes_for_fresh_quote(self):
        executor = self._executor()
        # Should not raise
        executor._check_quote_ttl(time.time())

    def test_non_pass_opportunity_raises(self):
        executor  = self._executor()
        opp       = self._make_pass_opp()
        opp.status = "BLOCKED_LOW_PROFIT"
        dex_quote = self._make_uniswap_quote()
        w3        = MagicMock()
        account   = MagicMock()
        with self.assertRaises(ValueError):
            executor.execute_dex_leg(w3, account, opp, dex_quote)

    def test_mock_dex_quote_raises(self):
        executor  = self._executor()
        opp       = self._make_pass_opp()
        mock_quote = Quote(
            symbol="ETH/USDT", exchange="mock_dex", exchange_type="DEX",
            bid=3050.0, ask=3060.0, mid=3055.0, timestamp=time.time(),
            liquidity_usd=500_000.0, raw={},
        )
        w3      = MagicMock()
        account = MagicMock()
        with self.assertRaises(ValueError):
            executor.execute_dex_leg(w3, account, opp, mock_quote)

    def test_missing_raw_metadata_raises(self):
        executor  = self._executor()
        opp       = self._make_pass_opp()
        dex_quote = self._make_uniswap_quote()
        dex_quote.raw.pop("base_addr")
        w3      = MagicMock()
        account = MagicMock()
        with self.assertRaises(ValueError):
            executor.execute_dex_leg(w3, account, opp, dex_quote)

    def test_insufficient_balance_raises(self):
        executor  = self._executor()
        opp       = self._make_pass_opp()
        dex_quote = self._make_uniswap_quote()
        w3        = MagicMock()
        account   = MagicMock()
        account.address = "0x" + "a" * 40

        # Patch at source module; balance is 0 (insufficient)
        with patch("app.execution.erc20.get_token_balance", return_value=0), \
             patch("app.execution.erc20.ensure_allowance", return_value=None), \
             patch.dict("sys.modules", {"web3": _make_mock_web3()}):
            with self.assertRaises(ValueError, msg="Should raise on insufficient balance"):
                executor.execute_dex_leg(w3, account, opp, dex_quote)

    def test_successful_swap(self):
        executor  = self._executor()
        opp       = self._make_pass_opp()
        dex_quote = self._make_uniswap_quote()
        w3        = MagicMock()
        account   = MagicMock()
        account.address = "0x" + "a" * 40

        tx_hash_bytes = b"\xde\xad\xbe\xef" + b"\x00" * 28
        signed_mock   = MagicMock()
        signed_mock.raw_transaction = b"\x00" * 100

        mock_receipt = {"status": 1, "gasUsed": 120_000, "logs": []}
        w3.eth.gas_price = 30 * 10**9
        w3.eth.get_transaction_count.return_value = 1
        w3.eth.estimate_gas.return_value = 150_000
        w3.eth.send_raw_transaction.return_value = tx_hash_bytes
        w3.eth.wait_for_transaction_receipt.return_value = mock_receipt
        account.sign_transaction.return_value = signed_mock

        router_contract_mock = MagicMock()
        router_contract_mock.functions.exactInputSingle.return_value.build_transaction.return_value = {
            "from": account.address,
            "gas": 150_000,
            "gasPrice": 30 * 10**9,
            "nonce": 1,
            "data": "0x",
            "to": "0x" + "b" * 40,
            "value": 0,
        }
        w3.eth.contract.return_value = router_contract_mock

        large_balance = int(100 * 10**18)   # 100 ETH in wei — enough for any trade size

        with patch("app.execution.erc20.get_token_balance", return_value=large_balance), \
             patch("app.execution.erc20.ensure_allowance", return_value=None), \
             patch.dict("sys.modules", {"web3": _make_mock_web3()}):
            result = executor.execute_dex_leg(w3, account, opp, dex_quote)

        self.assertTrue(result.success)
        self.assertEqual(result.gas_used, 120_000)


# ---------------------------------------------------------------------------
class TestBinanceCEXExecutor(unittest.TestCase):
    """Tests for BinanceCEXExecutor — no real network calls."""

    def _make_pass_opp(
        self, direction: str = "BUY_CEX_SELL_DEX"
    ) -> ArbitrageOpportunity:
        return ArbitrageOpportunity(
            symbol="ETH/USDT",
            direction=direction,
            cex_exchange="binance",
            dex_exchange="uniswap_v3",
            cex_price=3001.0,
            dex_price=3060.0,
            trade_amount_usd=10_000.0,
            gross_profit_usd=200.0,
            gross_profit_pct=2.0,
            cex_fee_usd=10.0,
            dex_fee_usd=30.0,
            gas_cost_usd=13.5,
            slippage_usd=5.0,
            total_cost_usd=58.5,
            net_profit_usd=141.5,
            net_profit_pct=1.415,
            liquidity_usd=500_000.0,
            status="PASS",
        )

    def test_non_pass_opportunity_raises(self):
        from app.execution.cex_executor import BinanceCEXExecutor
        opp = self._make_pass_opp()
        opp.status = "BLOCKED_LOW_PROFIT"
        with self.assertRaises(ValueError):
            BinanceCEXExecutor().execute_cex_leg(opp, "key", "secret")

    def test_unknown_direction_raises(self):
        from app.execution.cex_executor import BinanceCEXExecutor
        opp = self._make_pass_opp()
        opp.direction = "UNKNOWN"
        with self.assertRaises(ValueError):
            BinanceCEXExecutor().execute_cex_leg(opp, "key", "secret")

    def test_missing_api_credentials_returns_error_result(self):
        """Empty API key/secret should return an error CEXOrderResult (no raise)."""
        from app.execution.cex_executor import BinanceCEXExecutor
        opp = self._make_pass_opp()
        # Missing keys → _ccxt_exchange raises ValueError which is caught
        result = BinanceCEXExecutor().execute_cex_leg(opp, "", "")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_buy_direction_maps_to_buy_side(self):
        """BUY_CEX_SELL_DEX should place a buy order."""
        from app.execution.cex_executor import BinanceCEXExecutor

        mock_exchange = MagicMock()
        mock_exchange.create_market_order.return_value = {"id": "order123"}
        mock_exchange.fetch_order.return_value = {
            "id": "order123", "status": "closed",
            "filled": 3.33, "cost": 10_000.0, "average": 3003.0,
        }

        executor = BinanceCEXExecutor()
        opp      = self._make_pass_opp("BUY_CEX_SELL_DEX")

        with patch.object(executor, "_ccxt_exchange", return_value=mock_exchange):
            result = executor.execute_cex_leg(opp, "key", "secret")

        self.assertTrue(result.success)
        self.assertEqual(result.side, "buy")
        call_args = mock_exchange.create_market_order.call_args
        self.assertEqual(call_args[0][0], "ETH/USDT")
        self.assertEqual(call_args[0][1], "buy")
        self.assertAlmostEqual(call_args[0][2], 10_000.0 / 3001.0, delta=0.01)

    def test_sell_direction_maps_to_sell_side(self):
        """BUY_DEX_SELL_CEX should place a sell order."""
        from app.execution.cex_executor import BinanceCEXExecutor

        mock_exchange = MagicMock()
        mock_exchange.create_market_order.return_value = {"id": "ord456"}
        mock_exchange.fetch_order.return_value = {
            "id": "ord456", "status": "closed",
            "filled": 3.33, "cost": 9_990.0, "average": 3000.0,
        }
        executor = BinanceCEXExecutor()
        opp      = self._make_pass_opp("BUY_DEX_SELL_CEX")
        with patch.object(executor, "_ccxt_exchange", return_value=mock_exchange):
            result = executor.execute_cex_leg(opp, "key", "secret")

        self.assertTrue(result.success)
        self.assertEqual(result.side, "sell")

    def test_failed_order_returns_unsuccessful_result(self):
        """A closed order with filled=0 should return success=False."""
        from app.execution.cex_executor import BinanceCEXExecutor

        mock_exchange = MagicMock()
        mock_exchange.create_market_order.return_value = {"id": "ord789"}
        mock_exchange.fetch_order.return_value = {
            "id": "ord789", "status": "canceled",
            "filled": 0.0, "cost": 0.0, "average": None,
        }
        executor = BinanceCEXExecutor()
        opp      = self._make_pass_opp()
        with patch.object(executor, "_ccxt_exchange", return_value=mock_exchange):
            result = executor.execute_cex_leg(opp, "key", "secret")

        self.assertFalse(result.success)

    def test_exchange_exception_returns_error_result(self):
        """Any exception during order placement should return error result, not raise."""
        from app.execution.cex_executor import BinanceCEXExecutor

        mock_exchange = MagicMock()
        mock_exchange.create_market_order.side_effect = RuntimeError("network error")
        executor = BinanceCEXExecutor()
        opp      = self._make_pass_opp()
        with patch.object(executor, "_ccxt_exchange", return_value=mock_exchange):
            result = executor.execute_cex_leg(opp, "key", "secret")

        self.assertFalse(result.success)
        self.assertIn("network error", result.error)


# ---------------------------------------------------------------------------
class TestArbitrageExecutor(unittest.TestCase):
    """Tests for the dual-sided ArbitrageExecutor — no real network calls."""

    def _make_pass_opp(self, direction: str = "BUY_CEX_SELL_DEX") -> ArbitrageOpportunity:
        return ArbitrageOpportunity(
            symbol="ETH/USDT", direction=direction,
            cex_exchange="binance", dex_exchange="uniswap_v3",
            cex_price=3001.0, dex_price=3060.0,
            trade_amount_usd=10_000.0,
            gross_profit_usd=200.0, gross_profit_pct=2.0,
            cex_fee_usd=10.0, dex_fee_usd=30.0,
            gas_cost_usd=13.5, slippage_usd=5.0, total_cost_usd=58.5,
            net_profit_usd=141.5, net_profit_pct=1.415,
            liquidity_usd=500_000.0, status="PASS",
        )

    def _make_uniswap_quote(self) -> Quote:
        ts = time.time()
        return Quote(
            symbol="ETH/USDT", exchange="uniswap_v3", exchange_type="DEX",
            bid=3050.0, ask=3060.0, mid=3055.0, timestamp=ts,
            liquidity_usd=500_000.0,
            raw={
                "base_addr": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "quote_addr": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "base_decimals": 18, "quote_decimals": 6,
                "fee_tier": 500, "amount_in_base": 3.277,
                "gas_estimate": 150_000, "quote_timestamp": ts,
                "quote_ttl_seconds": 30,
            },
        )

    def _make_swap_result(self, success: bool = True):
        from app.execution.swap_executor import SwapResult
        return SwapResult(
            tx_hash="0xdeadbeef", amount_in_wei=10**18,
            amount_out_wei=10**6 * 3000,
            gas_used=120_000, success=success,
            error=None if success else "reverted",
        )

    def _make_cex_result(self, success: bool = True):
        from app.execution.cex_executor import CEXOrderResult
        return CEXOrderResult(
            order_id="ord-1", symbol="ETH/USDT", side="buy",
            amount=3.33, filled=3.33 if success else 0.0,
            average_price=3001.0 if success else None,
            cost=9_993.33 if success else 0.0,
            status="closed" if success else "canceled",
            success=success,
            error=None if success else "canceled",
        )

    def test_chain_risk_blocks_execution(self):
        """Mock DEX quote should be blocked by chain risk."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp       = self._make_pass_opp()
        mock_quote = Quote(
            symbol="ETH/USDT", exchange="mock_dex", exchange_type="DEX",
            bid=3050.0, ask=3060.0, mid=3055.0, timestamp=time.time(),
            liquidity_usd=500_000.0, raw={},
        )
        executor = ArbitrageExecutor()
        result = executor.execute(
            opp, mock_quote, MagicMock(), MagicMock(), "key", "secret"
        )
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertIn("MOCK_DEX", " ".join(result.warnings))

    def test_buy_cex_sell_dex_both_succeed(self):
        """BUY_CEX_SELL_DEX: CEX buy + DEX sell both succeed → success=True."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp   = self._make_pass_opp("BUY_CEX_SELL_DEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_dex.execute_dex_leg.return_value = self._make_swap_result(True)
        mock_cex = MagicMock()
        mock_cex.execute_cex_leg.return_value = self._make_cex_result(True)

        executor = ArbitrageExecutor(dex_executor=mock_dex, cex_executor=mock_cex)
        result   = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

        self.assertTrue(result.success)
        self.assertFalse(result.partial)
        self.assertEqual(result.buy_leg.leg, "CEX")
        self.assertEqual(result.sell_leg.leg, "DEX")

    def test_buy_cex_sell_dex_cex_fails(self):
        """BUY_CEX_SELL_DEX: CEX buy fails → abort, no DEX leg executed."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp   = self._make_pass_opp("BUY_CEX_SELL_DEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_cex = MagicMock()
        mock_cex.execute_cex_leg.return_value = self._make_cex_result(False)

        executor = ArbitrageExecutor(dex_executor=mock_dex, cex_executor=mock_cex)
        result   = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

        self.assertFalse(result.success)
        self.assertFalse(result.partial)   # buy failed → no exposure
        self.assertIsNone(result.sell_leg)
        mock_dex.execute_dex_leg.assert_not_called()

    def test_buy_cex_sell_dex_dex_fails(self):
        """BUY_CEX_SELL_DEX: CEX buy succeeds but DEX sell fails → partial=True."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp   = self._make_pass_opp("BUY_CEX_SELL_DEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_dex.execute_dex_leg.side_effect = RuntimeError("liquidity error")
        mock_cex = MagicMock()
        mock_cex.execute_cex_leg.return_value = self._make_cex_result(True)

        executor = ArbitrageExecutor(dex_executor=mock_dex, cex_executor=mock_cex)
        result   = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertIsNotNone(result.error)
        self.assertIn("PARTIAL", result.error)

    def test_buy_dex_sell_cex_both_succeed(self):
        """BUY_DEX_SELL_CEX: DEX buy + CEX sell both succeed → success=True."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp   = self._make_pass_opp("BUY_DEX_SELL_CEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_dex.execute_dex_leg.return_value = self._make_swap_result(True)
        mock_cex = MagicMock()
        mock_cex.execute_cex_leg.return_value = self._make_cex_result(True)

        executor = ArbitrageExecutor(dex_executor=mock_dex, cex_executor=mock_cex)
        result   = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

        self.assertTrue(result.success)
        self.assertEqual(result.buy_leg.leg, "DEX")
        self.assertEqual(result.sell_leg.leg, "CEX")

    def test_buy_dex_sell_cex_dex_fails(self):
        """BUY_DEX_SELL_CEX: DEX buy fails → abort, no CEX leg executed."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp   = self._make_pass_opp("BUY_DEX_SELL_CEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_dex.execute_dex_leg.return_value = self._make_swap_result(False)
        mock_cex = MagicMock()

        executor = ArbitrageExecutor(dex_executor=mock_dex, cex_executor=mock_cex)
        result   = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

        self.assertFalse(result.success)
        self.assertFalse(result.partial)
        self.assertIsNone(result.sell_leg)
        mock_cex.execute_cex_leg.assert_not_called()

    def test_buy_dex_sell_cex_cex_fails(self):
        """BUY_DEX_SELL_CEX: DEX buy succeeds but CEX sell fails → partial=True."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp   = self._make_pass_opp("BUY_DEX_SELL_CEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_dex.execute_dex_leg.return_value = self._make_swap_result(True)
        mock_cex = MagicMock()
        mock_cex.execute_cex_leg.return_value = self._make_cex_result(False)

        executor = ArbitrageExecutor(dex_executor=mock_dex, cex_executor=mock_cex)
        result   = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertIn("PARTIAL", result.error)

    def test_result_is_logged(self):
        """execute() should write a JSON line to the result log."""
        from app.execution.arbitrage_executor import ArbitrageExecutor
        import tempfile, os as _os
        opp   = self._make_pass_opp("BUY_CEX_SELL_DEX")
        quote = self._make_uniswap_quote()

        mock_dex = MagicMock()
        mock_dex.execute_dex_leg.return_value = self._make_swap_result(True)
        mock_cex = MagicMock()
        mock_cex.execute_cex_leg.return_value = self._make_cex_result(True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
            log_path = tmp.name

        try:
            executor = ArbitrageExecutor(
                dex_executor=mock_dex, cex_executor=mock_cex, result_log=log_path
            )
            executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")

            with open(log_path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            logged = json.loads(lines[0])
            self.assertEqual(logged["opportunity_symbol"], "ETH/USDT")
            self.assertTrue(logged["success"])
        finally:
            _os.unlink(log_path)

    def test_unknown_direction_returns_error(self):
        from app.execution.arbitrage_executor import ArbitrageExecutor
        opp = self._make_pass_opp()
        opp.direction = "INVALID"
        quote = self._make_uniswap_quote()
        executor = ArbitrageExecutor()
        result = executor.execute(opp, quote, MagicMock(), MagicMock(), "k", "s")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    unittest.main()
