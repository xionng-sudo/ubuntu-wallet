from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Fee constants
# ---------------------------------------------------------------------------
CEX_FEE_RATE: float = 0.001   # 0.1 % Binance taker fee
DEX_FEE_RATE: float = 0.003   # 0.3 % Uniswap V3 standard pool

# ---------------------------------------------------------------------------
# Gas defaults
# ---------------------------------------------------------------------------
DEFAULT_GAS_GWEI: float = 30.0
DEFAULT_GAS_UNITS: int = 150_000   # typical Uniswap V3 swap
ETH_PRICE_USD: float = 3000.0      # fallback; override via argument


# ---------------------------------------------------------------------------
@dataclass
class CostBreakdown:
    cex_fee_usd: float
    dex_fee_usd: float
    gas_cost_usd: float
    slippage_usd: float
    total_cost_usd: float


# ---------------------------------------------------------------------------
def estimate_gas_cost_usd(
    gas_gwei: float | None = None,
    gas_units: int | None = None,
    eth_price_usd: float | None = None,
) -> float:
    """Return estimated gas cost in USD for a single Uniswap swap."""
    gwei = gas_gwei if gas_gwei is not None else DEFAULT_GAS_GWEI
    units = gas_units if gas_units is not None else DEFAULT_GAS_UNITS
    eth_px = eth_price_usd if eth_price_usd is not None else ETH_PRICE_USD
    gas_eth = (gwei * 1e-9) * units
    return gas_eth * eth_px


def estimate_slippage(trade_amount_usd: float, liquidity_usd: float) -> float:
    """
    Approximate price impact using a simplified sqrt-AMM formula.
    slippage_pct = (trade / liquidity) * 0.5, capped at 5 %.
    Returns a fraction (e.g. 0.01 = 1 %).
    """
    if liquidity_usd <= 0:
        return 0.05
    raw = (trade_amount_usd / liquidity_usd) * 0.5
    return min(raw, 0.05)


def calculate_cex_fee(
    trade_amount_usd: float, fee_rate: float = CEX_FEE_RATE
) -> float:
    return trade_amount_usd * fee_rate


def calculate_dex_fee(
    trade_amount_usd: float, fee_rate: float = DEX_FEE_RATE
) -> float:
    return trade_amount_usd * fee_rate
