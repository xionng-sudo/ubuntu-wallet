from __future__ import annotations

from dataclasses import dataclass

from app.arbitrage.engine import ArbitrageOpportunity


@dataclass
class RiskConfig:
    min_net_profit_usd: float = 1.0        # Minimum net profit in USD
    max_gas_cost_usd: float = 50.0         # Maximum gas cost in USD
    max_slippage_pct: float = 1.0          # Maximum slippage %
    min_liquidity_usd: float = 10_000.0    # Minimum DEX liquidity in USD
    min_gross_profit_pct: float = 0.1      # Minimum gross profit %


def apply_risk_filter(
    opp: ArbitrageOpportunity,
    config: RiskConfig | None = None,
) -> ArbitrageOpportunity:
    """
    Evaluate a single opportunity against risk thresholds.
    Updates opp.status and opp.status_reason in-place and returns it.
    First failing check wins.
    """
    cfg = config or RiskConfig()

    if opp.liquidity_usd < cfg.min_liquidity_usd:
        opp.status = "BLOCKED_LOW_LIQUIDITY"
        opp.status_reason = (
            f"liquidity ${opp.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}"
        )
        return opp

    slippage_pct = (
        opp.slippage_usd / opp.trade_amount_usd * 100.0
        if opp.trade_amount_usd > 0
        else 0.0
    )
    if slippage_pct > cfg.max_slippage_pct:
        opp.status = "BLOCKED_HIGH_SLIPPAGE"
        opp.status_reason = (
            f"slippage {slippage_pct:.2f}% > {cfg.max_slippage_pct:.2f}%"
        )
        return opp

    if opp.gas_cost_usd > cfg.max_gas_cost_usd:
        opp.status = "BLOCKED_HIGH_GAS"
        opp.status_reason = (
            f"gas ${opp.gas_cost_usd:.2f} > ${cfg.max_gas_cost_usd:.2f}"
        )
        return opp

    if opp.gross_profit_pct < cfg.min_gross_profit_pct:
        opp.status = "BLOCKED_LOW_PROFIT"
        opp.status_reason = (
            f"gross {opp.gross_profit_pct:.3f}% < {cfg.min_gross_profit_pct:.3f}%"
        )
        return opp

    if opp.net_profit_usd < cfg.min_net_profit_usd:
        opp.status = "BLOCKED_LOW_PROFIT"
        opp.status_reason = (
            f"net profit ${opp.net_profit_usd:.2f} < ${cfg.min_net_profit_usd:.2f}"
        )
        return opp

    opp.status = "PASS"
    opp.status_reason = ""
    return opp


def filter_opportunities(
    opportunities: list[ArbitrageOpportunity],
    config: RiskConfig | None = None,
) -> list[ArbitrageOpportunity]:
    """Apply risk filters to a list of opportunities."""
    return [apply_risk_filter(opp, config) for opp in opportunities]
