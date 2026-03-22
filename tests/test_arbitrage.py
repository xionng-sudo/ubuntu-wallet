from __future__ import annotations

import sys
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.market.dex.base import Quote
from app.market.dex.mock_dex import MockDEXQuote
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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
if __name__ == "__main__":
    unittest.main()
