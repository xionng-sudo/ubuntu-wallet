from __future__ import annotations

from app.market.dex.base import BaseDEXQuote, Quote


class UniswapV3Quote(BaseDEXQuote):
    """
    Placeholder implementation for Uniswap V3 on-chain quote fetching.

    A real implementation would need:
      - The Graph API (https://thegraph.com/hosted-service/subgraph/uniswap/uniswap-v3)
        to query pool addresses, liquidity, and tick data.
      - OR direct on-chain calls via web3.py to the Uniswap V3 Quoter contract
        (0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6 on Ethereum mainnet).
      - Pool fee tiers: 0.05 %, 0.30 %, 1.00 %
      - Sqrt price math to convert sqrtPriceX96 → human-readable price.
      - An Ethereum RPC endpoint (e.g. Infura, Alchemy) stored in env var
        ETHEREUM_RPC_URL.
    """

    @property
    def name(self) -> str:
        return "uniswap_v3"

    def fetch_quotes(
        self, symbols: list[str], trade_amount_usd: float
    ) -> list[Quote]:
        raise NotImplementedError(
            "UniswapV3Quote is not yet implemented. "
            "Use --dex mock for end-to-end testing, or implement "
            "on-chain / Graph API calls and set ETHEREUM_RPC_URL in .env."
        )
