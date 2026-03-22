from __future__ import annotations

import os
import time
import warnings
from typing import Optional

from app.market.dex.base import BaseDEXQuote, Quote

# ---------------------------------------------------------------------------
# Ethereum mainnet token registry: ticker → (checksum_address, decimals)
# ---------------------------------------------------------------------------
_TOKEN_REGISTRY: dict[str, tuple[str, int]] = {
    "ETH":  ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),  # WETH
    "WETH": ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
    "BTC":  ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  8),   # WBTC
    "WBTC": ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  8),
    "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7",  6),
    "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  6),
    "DAI":  ("0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
    "BNB":  ("0xB8c77482e45F1F44dE1745F52C74426C631bDD52", 18),
}

# Preferred Uniswap V3 fee tier (basis points) per symbol pair.
# Common tiers: 100 (0.01 %), 500 (0.05 %), 3000 (0.3 %), 10000 (1 %)
_DEFAULT_FEE_TIERS: dict[str, int] = {
    "ETH/USDT":  500,
    "ETH/USDC":  500,
    "WETH/USDT": 500,
    "WETH/USDC": 500,
    "BTC/USDT":  3000,
    "BTC/USDC":  3000,
    "WBTC/USDT": 3000,
    "WBTC/USDC": 3000,
    "BNB/USDT":  3000,
}
_FALLBACK_FEE_TIER = 3000

# ---------------------------------------------------------------------------
# Uniswap V3 QuoterV2 — Ethereum mainnet
# Address: https://docs.uniswap.org/contracts/v3/reference/deployments
# ---------------------------------------------------------------------------
_QUOTER_V2_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

_QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address",  "name": "tokenIn",          "type": "address"},
                    {"internalType": "address",  "name": "tokenOut",         "type": "address"},
                    {"internalType": "uint256",  "name": "amountIn",         "type": "uint256"},
                    {"internalType": "uint24",   "name": "fee",              "type": "uint24"},
                    {"internalType": "uint160",  "name": "sqrtPriceLimitX96","type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut",                  "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After",          "type": "uint160"},
            {"internalType": "uint32",  "name": "initializedTicksCrossed",    "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate",                "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# How old (in seconds) a quote may be before the execution layer should reject it.
QUOTE_TTL_SECONDS: int = 30


class UniswapV3Quote(BaseDEXQuote):
    """
    Real Uniswap V3 on-chain quote fetcher using QuoterV2.

    Prerequisites
    -------------
    * ``pip install web3>=6.0.0``
    * Set ``ETHEREUM_RPC_URL`` in your ``.env`` file (Infura / Alchemy endpoint).

    Supported chain
    ---------------
    Ethereum mainnet (chain ID 1).

    Supported symbols
    -----------------
    Any BASE/QUOTE pair where both tokens appear in the built-in token registry
    (ETH, WETH, BTC, WBTC, USDT, USDC, DAI, BNB).  Examples: ETH/USDT,
    BTC/USDT, ETH/USDC, WBTC/USDC.

    Quote TTL
    ---------
    Each returned Quote carries ``timestamp`` and ``raw["quote_ttl_seconds"]``.
    The execution layer (``app/execution/swap_executor.py``) rejects quotes
    older than ``QUOTE_TTL_SECONDS`` (default 30 s) to prevent stale-price
    execution.

    How prices are derived
    ----------------------
    Two ``quoteExactInputSingle`` calls are made per symbol:

    * **Bid** – sell ``trade_amount_usd / mid_price`` units of base token for
      quote token.  ``bid = quote_out / base_in`` (USD per base token).
    * **Ask** – buy ``trade_amount_usd`` units of quote token to receive base
      token.  ``ask = quote_in / base_out`` (USD per base token).

    Both calls use the actual ``trade_amount_usd`` so that price impact is
    baked into the quoted price, making gas and slippage estimates realistic.
    """

    def __init__(self, rpc_url: Optional[str] = None) -> None:
        self._rpc_url = rpc_url or os.environ.get("ETHEREUM_RPC_URL", "")

    @property
    def name(self) -> str:
        return "uniswap_v3"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _web3(self):
        """Return a connected Web3 instance; raises on import error or no RPC."""
        try:
            from web3 import Web3
        except ImportError as exc:
            raise ImportError(
                "The 'web3' package is required for UniswapV3Quote. "
                "Install with: pip install web3>=6.0.0"
            ) from exc

        if not self._rpc_url:
            raise ValueError(
                "ETHEREUM_RPC_URL is not set. "
                "Add it to your .env file: ETHEREUM_RPC_URL=https://mainnet.infura.io/v3/YOUR_KEY"
            )

        w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        if not w3.is_connected():
            raise ConnectionError(
                f"Cannot connect to Ethereum RPC: {self._rpc_url!r}. "
                "Verify ETHEREUM_RPC_URL and your network connection."
            )
        return w3

    def _parse_symbol(self, symbol: str) -> tuple[str, str]:
        """'ETH/USDT' → ('ETH', 'USDT').  Raises ValueError on bad format."""
        parts = symbol.upper().split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Unsupported symbol format: {symbol!r}. Expected 'BASE/QUOTE' (e.g. 'ETH/USDT')."
            )
        return parts[0], parts[1]

    def _token_info(self, ticker: str) -> tuple[str, int]:
        """Return (address, decimals) for a ticker; raises ValueError if unknown."""
        info = _TOKEN_REGISTRY.get(ticker.upper())
        if info is None:
            raise ValueError(
                f"Token {ticker!r} not in registry. "
                f"Supported tokens: {', '.join(sorted(_TOKEN_REGISTRY))}. "
                "Add a new entry to _TOKEN_REGISTRY in app/market/dex/uniswap_v3.py."
            )
        return info

    def _quote_exact_input(
        self,
        w3,
        token_in: str,
        token_out: str,
        decimals_in: int,
        decimals_out: int,
        amount_in_human: float,
        fee: int,
    ) -> tuple[float, int]:
        """
        Call QuoterV2.quoteExactInputSingle.

        Returns
        -------
        (amount_out_human, gas_estimate)
            amount_out_human: how many *out* tokens for the given *in* amount.
            gas_estimate:     estimated gas units for the swap.
        """
        from web3 import Web3

        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(_QUOTER_V2_ADDRESS),
            abi=_QUOTER_V2_ABI,
        )
        amount_in_wei = int(amount_in_human * (10 ** decimals_in))
        result = quoter.functions.quoteExactInputSingle(
            {
                "tokenIn":           Web3.to_checksum_address(token_in),
                "tokenOut":          Web3.to_checksum_address(token_out),
                "amountIn":          amount_in_wei,
                "fee":               fee,
                "sqrtPriceLimitX96": 0,
            }
        ).call()
        amount_out_wei, _, _, gas_estimate = result
        amount_out_human = amount_out_wei / (10 ** decimals_out)
        return amount_out_human, int(gas_estimate)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_quotes(
        self, symbols: list[str], trade_amount_usd: float
    ) -> list[Quote]:
        """
        Fetch on-chain quotes for all requested symbols.

        For each symbol two QuoterV2 calls are made:

        1. **Ask** – tokenIn=quote, tokenOut=base, amountIn=trade_amount_usd
           (cost to buy base with quote token)
        2. **Bid** – tokenIn=base, tokenOut=quote, amountIn=base_amount
           (proceeds from selling base for quote token)

        Each returned ``Quote`` includes ``raw`` with all data needed by
        the execution layer (token addresses, decimals, fee tier,
        quote_timestamp, quote_ttl_seconds, and amount_in_base).

        Failed symbols are skipped with a RuntimeWarning rather than raising.
        """
        w3 = None
        quotes: list[Quote] = []
        for symbol in symbols:
            try:
                if w3 is None:
                    w3 = self._web3()
                base_tok, quote_tok = self._parse_symbol(symbol)
                base_addr,  base_dec  = self._token_info(base_tok)
                quote_addr, quote_dec = self._token_info(quote_tok)
                fee = _DEFAULT_FEE_TIERS.get(symbol.upper(), _FALLBACK_FEE_TIER)

                # ---- Ask: buy base by spending trade_amount_usd of quote token ----
                # tokenIn=quote, amountIn=trade_amount_usd → amountOut=base_received
                base_received, _ = self._quote_exact_input(
                    w3,
                    token_in=quote_addr, token_out=base_addr,
                    decimals_in=quote_dec, decimals_out=base_dec,
                    amount_in_human=trade_amount_usd,
                    fee=fee,
                )
                # ask = USD spent / base received
                ask = trade_amount_usd / base_received if base_received > 0 else 0.0

                # ---- Bid: sell base_amount of base token for quote token ----
                # Use ask to derive a sensible base amount at this trade size.
                base_amount = trade_amount_usd / ask if ask > 0 else 1.0
                quote_received, gas_est = self._quote_exact_input(
                    w3,
                    token_in=base_addr, token_out=quote_addr,
                    decimals_in=base_dec, decimals_out=quote_dec,
                    amount_in_human=base_amount,
                    fee=fee,
                )
                # bid = USD received / base sold
                bid = quote_received / base_amount if base_amount > 0 else 0.0

                mid = (bid + ask) / 2.0
                ts  = time.time()

                # Rough liquidity proxy: 50× trade size (conservative; pool depth
                # requires tick math which is outside the scope of this MVP).
                liquidity_usd = trade_amount_usd * 50.0

                raw: dict = {
                    "base_token":        base_tok,
                    "quote_token":       quote_tok,
                    "base_addr":         base_addr,
                    "quote_addr":        quote_addr,
                    "base_decimals":     base_dec,
                    "quote_decimals":    quote_dec,
                    "fee_tier":          fee,
                    "amount_in_base":    base_amount,
                    "gas_estimate":      gas_est,
                    "quote_timestamp":   ts,
                    "quote_ttl_seconds": QUOTE_TTL_SECONDS,
                }

                quotes.append(Quote(
                    symbol=symbol,
                    exchange="uniswap_v3",
                    exchange_type="DEX",
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    timestamp=ts,
                    liquidity_usd=liquidity_usd,
                    raw=raw,
                ))

            except Exception as exc:
                warnings.warn(
                    f"UniswapV3Quote: failed to quote {symbol!r}: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        return quotes
