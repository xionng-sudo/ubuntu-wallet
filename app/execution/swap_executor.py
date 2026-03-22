from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.arbitrage.engine import ArbitrageOpportunity
from app.market.dex.base import Quote

# ---------------------------------------------------------------------------
# Uniswap V3 SwapRouter02 — Ethereum mainnet
# https://docs.uniswap.org/contracts/v3/reference/deployments
# ---------------------------------------------------------------------------
_SWAP_ROUTER_ADDRESS = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"

_SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn",           "type": "address"},
                    {"internalType": "address", "name": "tokenOut",          "type": "address"},
                    {"internalType": "uint24",  "name": "fee",               "type": "uint24"},
                    {"internalType": "address", "name": "recipient",         "type": "address"},
                    {"internalType": "uint256", "name": "amountIn",          "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum",  "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"}
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]

# Default safety parameters
DEFAULT_SLIPPAGE_TOLERANCE: float = 0.005   # 0.5 % — minimum acceptable output ratio
DEFAULT_QUOTE_TTL_SECONDS:  int   = 30      # reject quotes older than this
DEFAULT_DEADLINE_BUFFER:    int   = 60      # transaction deadline = now + this (seconds)


@dataclass
class SwapResult:
    """Result of a single on-chain swap attempt."""
    tx_hash:       str
    amount_in_wei: int
    amount_out_wei: int
    gas_used:      int
    success:       bool
    error:         Optional[str] = None


class UniswapV3SwapExecutor:
    """
    Executes a single-hop ``exactInputSingle`` swap on Uniswap V3 SwapRouter02.

    Safety measures applied before sending any transaction
    -------------------------------------------------------
    1. **Quote TTL check** — rejects DEX quotes older than ``quote_ttl_seconds``
       to avoid executing at a stale price.
    2. **Pre-flight balance check** — verifies the wallet holds sufficient
       ``tokenIn`` before attempting the swap.
    3. **ERC-20 approval** — calls ``ensure_allowance`` so the router can spend
       ``tokenIn``; sends an ``approve`` transaction only when needed.
    4. **Gas estimation** — calls ``eth_estimateGas`` as a dry-run; a revert
       here (e.g. insufficient liquidity, invalid route) surfaces before any
       funds move.
    5. **Slippage protection** — sets ``amountOutMinimum`` =
       ``expected_out × (1 − slippage_tolerance)`` to cap worst-case loss.
    6. **Transaction deadline** — ``deadline = now + deadline_buffer_seconds``
       so the transaction automatically reverts if it stays pending too long.

    Supported direction mapping
    ---------------------------
    * ``BUY_DEX_SELL_CEX``  →  tokenIn = quote token, tokenOut = base token
      (buy base on DEX; CEX leg handled separately via CEX API / manual)
    * ``BUY_CEX_SELL_DEX``  →  tokenIn = base token, tokenOut = quote token
      (sell base on DEX after buying cheaper on CEX)

    Note
    ----
    CEX execution is out of scope for this module; only the DEX leg is executed
    here.  Integrate with your CEX API for the other leg.
    """

    def __init__(
        self,
        slippage_tolerance:    float = DEFAULT_SLIPPAGE_TOLERANCE,
        quote_ttl_seconds:     int   = DEFAULT_QUOTE_TTL_SECONDS,
        deadline_buffer_seconds: int = DEFAULT_DEADLINE_BUFFER,
    ) -> None:
        self.slippage_tolerance      = slippage_tolerance
        self.quote_ttl_seconds       = quote_ttl_seconds
        self.deadline_buffer_seconds = deadline_buffer_seconds

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    def _check_quote_ttl(self, quote_timestamp: float) -> None:
        """Raise ValueError if the quote is stale."""
        age = time.time() - quote_timestamp
        if age > self.quote_ttl_seconds:
            raise ValueError(
                f"Quote is stale (age={age:.1f}s > TTL={self.quote_ttl_seconds}s). "
                "Re-fetch DEX quotes before executing."
            )

    def _estimate_gas(self, w3, tx: dict) -> int:
        """Dry-run gas estimation; raises ContractLogicError if the swap would revert."""
        return w3.eth.estimate_gas(tx)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def execute_dex_leg(
        self,
        w3,
        account,
        opportunity: ArbitrageOpportunity,
        dex_quote: Quote,
        gas_price_gwei: Optional[float] = None,
    ) -> SwapResult:
        """
        Execute the DEX leg of an arbitrage opportunity via Uniswap V3.

        Parameters
        ----------
        w3:
            Connected Web3 instance (must have ``eth`` namespace).
        account:
            A ``web3.LocalAccount`` loaded with ``app.execution.wallet.get_account``.
        opportunity:
            A ``PASS``-status ``ArbitrageOpportunity`` from the arbitrage engine.
        dex_quote:
            The original ``Quote`` returned by ``UniswapV3Quote.fetch_quotes``.
            Must be ``exchange == "uniswap_v3"`` so that ``raw`` contains
            on-chain metadata (token addresses, fee tier, quote timestamp).
        gas_price_gwei:
            Override gas price; uses current on-chain base fee when ``None``.

        Returns
        -------
        SwapResult
            Contains tx_hash, amounts, gas used, and success flag.

        Raises
        ------
        ValueError
            If opportunity is not PASS, quote is stale, or DEX quote has no
            on-chain metadata (e.g. came from mock_dex).
        RuntimeError
            If gas estimation fails (likely revert) or approval fails.
        """
        # --- All guards run before any imports so they work without web3 installed ---

        # Guard: only execute PASS opportunities
        if opportunity.status != "PASS":
            raise ValueError(
                f"Opportunity status is {opportunity.status!r}. "
                "Only PASS opportunities should be executed."
            )

        # Guard: must be a real on-chain quote
        if dex_quote.exchange != "uniswap_v3":
            raise ValueError(
                f"dex_quote.exchange is {dex_quote.exchange!r}. "
                "Execution requires a quote from UniswapV3Quote, not mock_dex."
            )

        raw = dex_quote.raw
        required_raw_keys = {"base_addr", "quote_addr", "base_decimals", "quote_decimals",
                              "fee_tier", "amount_in_base", "quote_timestamp"}
        missing = required_raw_keys - set(raw.keys())
        if missing:
            raise ValueError(
                f"DEX quote is missing on-chain metadata: {sorted(missing)}. "
                "Re-fetch quotes via UniswapV3Quote."
            )

        # Guard: quote TTL
        self._check_quote_ttl(raw["quote_timestamp"])

        # --- Imports (after guards, so tests that exercise guards don't need web3) ---
        from web3 import Web3
        from app.execution.erc20 import ensure_allowance, get_token_balance

        base_addr:  str = raw["base_addr"]
        quote_addr: str = raw["quote_addr"]
        base_dec:   int = raw["base_decimals"]
        quote_dec:  int = raw["quote_decimals"]
        fee_tier:   int = raw["fee_tier"]
        amount_in_base: float = raw["amount_in_base"]

        direction = opportunity.direction
        if direction == "BUY_DEX_SELL_CEX":
            # Buy base token with quote token on DEX
            token_in_addr   = quote_addr
            token_out_addr  = base_addr
            decimals_in     = quote_dec
            decimals_out    = base_dec
            amount_in_human = opportunity.trade_amount_usd          # USD as quote tokens (1:1 for stables)
            expected_out_human = opportunity.trade_amount_usd / opportunity.dex_price
        else:
            # BUY_CEX_SELL_DEX: sell base token for quote token on DEX
            token_in_addr   = base_addr
            token_out_addr  = quote_addr
            decimals_in     = base_dec
            decimals_out    = quote_dec
            amount_in_human = amount_in_base
            expected_out_human = amount_in_base * opportunity.dex_price

        amount_in_wei      = int(amount_in_human * (10 ** decimals_in))
        expected_out_wei   = int(expected_out_human * (10 ** decimals_out))
        amount_out_minimum = int(expected_out_wei * (1.0 - self.slippage_tolerance))

        router_addr = Web3.to_checksum_address(_SWAP_ROUTER_ADDRESS)

        # --- Pre-flight: balance check ---
        balance_wei = get_token_balance(w3, token_in_addr, account.address)
        if balance_wei < amount_in_wei:
            raise ValueError(
                f"Insufficient tokenIn balance: "
                f"wallet has {balance_wei}, needs {amount_in_wei}. "
                "Top up your wallet before executing."
            )

        # --- Pre-flight: ERC-20 approval ---
        ensure_allowance(
            w3, account,
            token_address=token_in_addr,
            spender=router_addr,
            required_wei=amount_in_wei,
            gas_price_gwei=gas_price_gwei,
        )

        # --- Build swap transaction ---
        gas_price = (
            Web3.to_wei(gas_price_gwei, "gwei")
            if gas_price_gwei is not None
            else w3.eth.gas_price
        )
        deadline = int(time.time()) + self.deadline_buffer_seconds
        router = w3.eth.contract(address=router_addr, abi=_SWAP_ROUTER_ABI)

        tx = router.functions.exactInputSingle(
            {
                "tokenIn":           Web3.to_checksum_address(token_in_addr),
                "tokenOut":          Web3.to_checksum_address(token_out_addr),
                "fee":               fee_tier,
                "recipient":         account.address,
                "amountIn":          amount_in_wei,
                "amountOutMinimum":  amount_out_minimum,
                "sqrtPriceLimitX96": 0,
            }
        ).build_transaction(
            {
                "from":     account.address,
                "gasPrice": gas_price,
                "nonce":    w3.eth.get_transaction_count(account.address),
            }
        )

        # --- Gas estimation dry-run (catches reverts before any funds move) ---
        try:
            gas_limit = self._estimate_gas(w3, tx)
            tx["gas"] = int(gas_limit * 1.2)   # 20 % buffer
        except Exception as exc:
            raise RuntimeError(
                f"Gas estimation failed — swap would likely revert: {exc}\n"
                "Possible causes: insufficient on-chain liquidity, slippage too tight, "
                "invalid token pair, or stale quote."
            ) from exc

        # --- Sign, send, wait ---
        signed  = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        success = receipt["status"] == 1

        return SwapResult(
            tx_hash=tx_hash.hex(),
            amount_in_wei=amount_in_wei,
            # Exact amountOut requires parsing the Swap event log; use expected_out_wei
            # as a close approximation (actual may differ by slippage tolerance).
            amount_out_wei=amount_out_minimum if success else 0,
            gas_used=receipt["gasUsed"],
            success=success,
            error=None if success else "Transaction reverted on-chain",
        )
