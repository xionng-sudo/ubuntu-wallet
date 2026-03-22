from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.market.dex.base import Quote
from app.costs.calculator import (
    CostBreakdown,
    calculate_cex_fee,
    calculate_dex_fee,
    estimate_gas_cost_usd,
    estimate_slippage,
)


@dataclass
class ArbitrageOpportunity:
    symbol: str
    direction: str          # "BUY_CEX_SELL_DEX" or "BUY_DEX_SELL_CEX"
    cex_exchange: str
    dex_exchange: str
    cex_price: float        # price used on CEX leg
    dex_price: float        # price used on DEX leg
    trade_amount_usd: float
    gross_profit_usd: float
    gross_profit_pct: float
    cex_fee_usd: float
    dex_fee_usd: float
    gas_cost_usd: float
    slippage_usd: float
    total_cost_usd: float
    net_profit_usd: float
    net_profit_pct: float
    liquidity_usd: float
    status: str             # "PASS" or "BLOCKED_*"
    status_reason: str = ""


class ArbitrageEngine:
    """
    Compares CEX and DEX quotes for the same symbol and computes
    profit/loss for both arbitrage directions.
    """

    def __init__(
        self,
        gas_gwei: Optional[float] = None,
        gas_units: Optional[int] = None,
        eth_price_usd: Optional[float] = None,
    ) -> None:
        self._gas_gwei = gas_gwei
        self._gas_units = gas_units
        self._eth_price_usd = eth_price_usd

    # ------------------------------------------------------------------
    def evaluate(
        self,
        cex_quote: Quote,
        dex_quote: Quote,
        trade_amount_usd: float,
    ) -> list[ArbitrageOpportunity]:
        """Return up to two ArbitrageOpportunity objects (one per direction)."""
        opportunities: list[ArbitrageOpportunity] = []

        gas_usd = estimate_gas_cost_usd(
            self._gas_gwei, self._gas_units, self._eth_price_usd
        )
        slippage_frac = estimate_slippage(trade_amount_usd, dex_quote.liquidity_usd)
        slippage_usd = trade_amount_usd * slippage_frac

        for direction in ("BUY_CEX_SELL_DEX", "BUY_DEX_SELL_CEX"):
            if direction == "BUY_CEX_SELL_DEX":
                buy_price = cex_quote.ask
                sell_price = dex_quote.bid
            else:
                buy_price = dex_quote.ask
                sell_price = cex_quote.bid

            if buy_price <= 0:
                continue

            # Gross profit in USD: (sell - buy) / buy * trade_amount
            gross_profit_usd = (sell_price - buy_price) / buy_price * trade_amount_usd
            gross_profit_pct = (sell_price - buy_price) / buy_price * 100.0

            cex_fee = calculate_cex_fee(trade_amount_usd)
            dex_fee = calculate_dex_fee(trade_amount_usd)
            total_cost = cex_fee + dex_fee + gas_usd + slippage_usd

            net_profit_usd = gross_profit_usd - total_cost
            net_profit_pct = net_profit_usd / trade_amount_usd * 100.0

            opp = ArbitrageOpportunity(
                symbol=cex_quote.symbol,
                direction=direction,
                cex_exchange=cex_quote.exchange,
                dex_exchange=dex_quote.exchange,
                cex_price=cex_quote.ask if direction == "BUY_CEX_SELL_DEX" else cex_quote.bid,
                dex_price=dex_quote.bid if direction == "BUY_CEX_SELL_DEX" else dex_quote.ask,
                trade_amount_usd=trade_amount_usd,
                gross_profit_usd=round(gross_profit_usd, 4),
                gross_profit_pct=round(gross_profit_pct, 4),
                cex_fee_usd=round(cex_fee, 4),
                dex_fee_usd=round(dex_fee, 4),
                gas_cost_usd=round(gas_usd, 4),
                slippage_usd=round(slippage_usd, 4),
                total_cost_usd=round(total_cost, 4),
                net_profit_usd=round(net_profit_usd, 4),
                net_profit_pct=round(net_profit_pct, 4),
                liquidity_usd=dex_quote.liquidity_usd,
                status="PASS",
            )
            opportunities.append(opp)

        return opportunities

    # ------------------------------------------------------------------
    def evaluate_all(
        self,
        cex_quotes: list[Quote],
        dex_quotes: list[Quote],
        trade_amount_usd: float,
    ) -> list[ArbitrageOpportunity]:
        """Match CEX and DEX quotes by symbol and return all opportunities."""
        dex_by_symbol = {q.symbol: q for q in dex_quotes}
        results: list[ArbitrageOpportunity] = []
        for cq in cex_quotes:
            dq = dex_by_symbol.get(cq.symbol)
            if dq is None:
                continue
            results.extend(self.evaluate(cq, dq, trade_amount_usd))
        return results
