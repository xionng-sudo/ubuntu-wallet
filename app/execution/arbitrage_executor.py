from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.arbitrage.engine import ArbitrageOpportunity
from app.execution.cex_executor import BinanceCEXExecutor, CEXOrderResult
from app.execution.swap_executor import SwapResult, UniswapV3SwapExecutor
from app.market.dex.base import Quote
from app.risk.chain_risk import assess_chain_risks

# ---------------------------------------------------------------------------
# Execution order policy
# ---------------------------------------------------------------------------
# For BUY_CEX_SELL_DEX: buy on CEX first, then sell on DEX.
#   Rationale: CEX market order fills almost instantly; we know our exact
#   acquisition cost before committing to the on-chain swap.
#
# For BUY_DEX_SELL_CEX: buy on DEX first, then sell on CEX.
#   Rationale: the on-chain swap is atomic; once it settles we hold the base
#   token and can immediately sell on CEX.
#
# In both cases the "buy" leg executes before the "sell" leg.
# If the buy leg fails → abort immediately, no exposure.
# If the buy leg succeeds but the sell leg fails → alert; position is open.

_RESULT_LOG_PATH: str = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "arb_results.jsonl"
)


@dataclass
class LegResult:
    """Outcome of one leg (CEX or DEX) of a dual-sided execution."""
    leg:     str        # "CEX" or "DEX"
    success: bool
    detail:  dict       # serialisable dict from CEXOrderResult / SwapResult
    error:   Optional[str] = None


@dataclass
class ArbitrageExecutionResult:
    """
    Complete outcome of a dual-sided arbitrage execution attempt.

    Attributes
    ----------
    opportunity_symbol:
        Trading pair.
    direction:
        ``"BUY_CEX_SELL_DEX"`` or ``"BUY_DEX_SELL_CEX"``.
    buy_leg:
        Result of the buy leg (executed first).
    sell_leg:
        Result of the sell leg (executed second; ``None`` if buy leg failed).
    success:
        ``True`` only when *both* legs reported success.
    partial:
        ``True`` when one leg succeeded and the other failed.  The caller
        should alert and manually unwind the open position.
    elapsed_seconds:
        Wall-clock time from start to completion.
    timestamp:
        Unix timestamp at start of execution.
    warnings:
        Chain-risk advisory messages gathered before execution.
    error:
        Top-level error message if execution was aborted before sending any
        orders.
    """
    opportunity_symbol: str
    direction:          str
    buy_leg:            Optional[LegResult]
    sell_leg:           Optional[LegResult]
    success:            bool
    partial:            bool
    elapsed_seconds:    float
    timestamp:          float
    warnings:           list[str] = field(default_factory=list)
    error:              Optional[str] = None


class ArbitrageExecutor:
    """
    Orchestrates the complete DEX/CEX dual-sided arbitrage execution flow
    for a single PASS opportunity.

    Execution order
    ---------------
    * **BUY_CEX_SELL_DEX**: CEX buy first → on-chain DEX sell.
    * **BUY_DEX_SELL_CEX**: on-chain DEX buy first → CEX sell.

    Failure handling
    ----------------
    * If the **buy leg fails** → abort; no orders have been sent; no
      exposure.
    * If the buy leg succeeds but the **sell leg fails** → ``partial=True``
      in the result; an alert is emitted to stderr; the position remains open
      and must be manually unwound.

    Result persistence
    ------------------
    Each execution result is appended as a JSON line to
    ``data/arb_results.jsonl`` (created if absent).  This log is the
    primary audit trail.

    Usage
    -----
    See ``scripts/scan_arbitrage.py`` for CLI integration.
    """

    def __init__(
        self,
        dex_executor:  Optional[UniswapV3SwapExecutor] = None,
        cex_executor:  Optional[BinanceCEXExecutor]    = None,
        result_log:    Optional[str]                    = None,
    ) -> None:
        self._dex = dex_executor or UniswapV3SwapExecutor()
        self._cex = cex_executor or BinanceCEXExecutor()
        self._log_path = result_log or _RESULT_LOG_PATH

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_result(self, result: ArbitrageExecutionResult) -> None:
        """Append result to JSONL log; silently ignore write errors."""
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(result)) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _wrap_dex(self, swap_result: SwapResult) -> LegResult:
        return LegResult(
            leg="DEX",
            success=swap_result.success,
            detail=asdict(swap_result),
            error=swap_result.error if not swap_result.success else None,
        )

    def _wrap_cex(self, order_result: CEXOrderResult) -> LegResult:
        return LegResult(
            leg="CEX",
            success=order_result.success,
            detail=asdict(order_result),
            error=order_result.error if not order_result.success else None,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def execute(
        self,
        opportunity: ArbitrageOpportunity,
        dex_quote:   Quote,
        w3,
        account,
        api_key:     str,
        api_secret:  str,
    ) -> ArbitrageExecutionResult:
        """
        Execute both legs for a single PASS opportunity.

        Parameters
        ----------
        opportunity:
            A ``PASS``-status ``ArbitrageOpportunity`` from the engine.
        dex_quote:
            The original ``UniswapV3Quote``-sourced ``Quote`` (must have
            on-chain metadata in ``raw``).
        w3:
            Connected ``Web3`` instance.
        account:
            ``LocalAccount`` loaded via ``app.execution.wallet.get_account``.
        api_key:
            Binance API key (Spot trading permission).
        api_secret:
            Binance API secret.

        Returns
        -------
        ArbitrageExecutionResult
        """
        start_ts  = time.time()
        direction = opportunity.direction
        symbol    = opportunity.symbol

        # --- Chain risk pre-check ---
        chain_risk = assess_chain_risks(opportunity, dex_quote)
        warnings   = list(chain_risk.warnings)
        if not chain_risk.is_safe:
            result = ArbitrageExecutionResult(
                opportunity_symbol=symbol,
                direction=direction,
                buy_leg=None, sell_leg=None,
                success=False, partial=False,
                elapsed_seconds=time.time() - start_ts,
                timestamp=start_ts,
                warnings=warnings,
                error=f"Chain risk check blocked execution: {'; '.join(warnings)}",
            )
            self._log_result(result)
            return result

        # --- Dispatch by direction ---
        if direction == "BUY_CEX_SELL_DEX":
            return self._execute_buy_cex_sell_dex(
                opportunity, dex_quote, w3, account, api_key, api_secret,
                start_ts, warnings,
            )
        elif direction == "BUY_DEX_SELL_CEX":
            return self._execute_buy_dex_sell_cex(
                opportunity, dex_quote, w3, account, api_key, api_secret,
                start_ts, warnings,
            )
        else:
            result = ArbitrageExecutionResult(
                opportunity_symbol=symbol, direction=direction,
                buy_leg=None, sell_leg=None,
                success=False, partial=False,
                elapsed_seconds=time.time() - start_ts,
                timestamp=start_ts, warnings=warnings,
                error=f"Unknown direction: {direction!r}",
            )
            self._log_result(result)
            return result

    # ------------------------------------------------------------------
    def _execute_buy_cex_sell_dex(
        self,
        opp:        ArbitrageOpportunity,
        dex_quote:  Quote,
        w3, account,
        api_key: str, api_secret: str,
        start_ts: float,
        warnings: list[str],
    ) -> ArbitrageExecutionResult:
        """
        BUY_CEX_SELL_DEX: buy on CEX first, then sell on DEX.

        Step 1 — CEX buy (market order on Binance).
        Step 2 — DEX sell (Uniswap V3 exactInputSingle).
        """
        # Step 1: CEX buy
        cex_result = self._cex.execute_cex_leg(opp, api_key, api_secret)
        buy_leg    = self._wrap_cex(cex_result)

        if not cex_result.success:
            result = ArbitrageExecutionResult(
                opportunity_symbol=opp.symbol, direction=opp.direction,
                buy_leg=buy_leg, sell_leg=None,
                success=False, partial=False,
                elapsed_seconds=time.time() - start_ts,
                timestamp=start_ts, warnings=warnings,
                error=f"CEX buy leg failed: {cex_result.error}",
            )
            self._log_result(result)
            return result

        # Step 2: DEX sell
        try:
            swap_result = self._dex.execute_dex_leg(w3, account, opp, dex_quote)
        except Exception as exc:  # noqa: BLE001
            swap_result_dict = {
                "tx_hash": None, "amount_in_wei": 0, "amount_out_wei": 0,
                "gas_used": 0, "success": False, "error": str(exc),
            }
            sell_leg = LegResult(leg="DEX", success=False,
                                  detail=swap_result_dict, error=str(exc))
            result = ArbitrageExecutionResult(
                opportunity_symbol=opp.symbol, direction=opp.direction,
                buy_leg=buy_leg, sell_leg=sell_leg,
                success=False, partial=True,
                elapsed_seconds=time.time() - start_ts,
                timestamp=start_ts, warnings=warnings,
                error=(
                    f"⚠️  PARTIAL FILL — CEX buy succeeded but DEX sell failed: {exc}\n"
                    "Position is OPEN. Manually sell the base token to close exposure."
                ),
            )
            self._log_result(result)
            return result

        sell_leg = self._wrap_dex(swap_result)
        both_ok  = cex_result.success and swap_result.success
        partial  = cex_result.success != swap_result.success

        error_msg: Optional[str] = None
        if partial:
            error_msg = (
                "⚠️  PARTIAL FILL — CEX buy succeeded but DEX sell failed. "
                "Position is OPEN. Manually close the position to avoid losses."
            )

        result = ArbitrageExecutionResult(
            opportunity_symbol=opp.symbol, direction=opp.direction,
            buy_leg=buy_leg, sell_leg=sell_leg,
            success=both_ok, partial=partial,
            elapsed_seconds=time.time() - start_ts,
            timestamp=start_ts, warnings=warnings,
            error=error_msg,
        )
        self._log_result(result)
        return result

    # ------------------------------------------------------------------
    def _execute_buy_dex_sell_cex(
        self,
        opp:        ArbitrageOpportunity,
        dex_quote:  Quote,
        w3, account,
        api_key: str, api_secret: str,
        start_ts: float,
        warnings: list[str],
    ) -> ArbitrageExecutionResult:
        """
        BUY_DEX_SELL_CEX: buy on DEX first (atomic on-chain), then sell on CEX.

        Step 1 — DEX buy (Uniswap V3 exactInputSingle).
        Step 2 — CEX sell (market order on Binance).
        """
        # Step 1: DEX buy
        try:
            swap_result = self._dex.execute_dex_leg(w3, account, opp, dex_quote)
        except Exception as exc:  # noqa: BLE001
            swap_result_dict = {
                "tx_hash": None, "amount_in_wei": 0, "amount_out_wei": 0,
                "gas_used": 0, "success": False, "error": str(exc),
            }
            buy_leg = LegResult(leg="DEX", success=False,
                                 detail=swap_result_dict, error=str(exc))
            result = ArbitrageExecutionResult(
                opportunity_symbol=opp.symbol, direction=opp.direction,
                buy_leg=buy_leg, sell_leg=None,
                success=False, partial=False,
                elapsed_seconds=time.time() - start_ts,
                timestamp=start_ts, warnings=warnings,
                error=f"DEX buy leg failed: {exc}",
            )
            self._log_result(result)
            return result

        buy_leg = self._wrap_dex(swap_result)
        if not swap_result.success:
            result = ArbitrageExecutionResult(
                opportunity_symbol=opp.symbol, direction=opp.direction,
                buy_leg=buy_leg, sell_leg=None,
                success=False, partial=False,
                elapsed_seconds=time.time() - start_ts,
                timestamp=start_ts, warnings=warnings,
                error=f"DEX buy leg reverted: {swap_result.error}",
            )
            self._log_result(result)
            return result

        # Step 2: CEX sell
        cex_result = self._cex.execute_cex_leg(opp, api_key, api_secret)
        sell_leg   = self._wrap_cex(cex_result)
        both_ok    = swap_result.success and cex_result.success
        partial    = swap_result.success != cex_result.success

        error_msg: Optional[str] = None
        if partial:
            error_msg = (
                "⚠️  PARTIAL FILL — DEX buy succeeded but CEX sell failed. "
                "Position is OPEN. Manually sell the base token to close exposure."
            )

        result = ArbitrageExecutionResult(
            opportunity_symbol=opp.symbol, direction=opp.direction,
            buy_leg=buy_leg, sell_leg=sell_leg,
            success=both_ok, partial=partial,
            elapsed_seconds=time.time() - start_ts,
            timestamp=start_ts, warnings=warnings,
            error=error_msg,
        )
        self._log_result(result)
        return result
