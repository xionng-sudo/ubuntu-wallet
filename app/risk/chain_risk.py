from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.arbitrage.engine import ArbitrageOpportunity
from app.market.dex.base import Quote

# Net profit threshold below which the trade is exposed to front-running / MEV
_MEV_RISK_THRESHOLD_PCT: float = 0.3


@dataclass
class ChainRiskResult:
    """
    Result of a chain-level risk assessment for a single opportunity.

    Attributes
    ----------
    is_safe:
        ``True`` only when all blocking checks pass and the opportunity can be
        safely submitted on-chain.  A ``False`` value means the caller should
        *not* proceed with execution.
    warnings:
        Human-readable list of all issues found (blocking and advisory).
        Advisory warnings (e.g. MEV risk) set ``is_safe=True``; they inform
        but do not block.
    """
    is_safe:  bool
    warnings: list[str] = field(default_factory=list)


def check_quote_ttl(dex_quote: Quote, ttl_seconds: int = 30) -> bool:
    """
    Return ``True`` if *dex_quote* is still fresh enough for execution.

    A quote with ``timestamp <= 0`` is always treated as stale (e.g. mock
    quotes that don't set a real timestamp).

    Parameters
    ----------
    dex_quote:
        Quote whose ``timestamp`` field is checked.
    ttl_seconds:
        Maximum allowed age in seconds (default: 30).
    """
    if dex_quote.timestamp <= 0:
        return False
    age = time.time() - dex_quote.timestamp
    return age <= ttl_seconds


def assess_chain_risks(
    opportunity: ArbitrageOpportunity,
    dex_quote: Quote,
    quote_ttl_seconds: int = 30,
) -> ChainRiskResult:
    """
    Assess chain-level execution risks for a PASS opportunity.

    Checks performed
    ----------------
    1. **Quote TTL** (blocking) — is the on-chain price still fresh?
       Stale quotes risk executing at an unfavourable price.
    2. **Mock DEX** (blocking) — mock quotes cannot be executed on-chain.
    3. **Route validity** (blocking) — Uniswap V3 quotes must carry on-chain
       metadata (token addresses, fee tier, etc.) in ``dex_quote.raw``.
    4. **MEV / front-run risk** (advisory) — very thin profit margins are
       vulnerable to front-running by MEV bots.  The warning is informational;
       it does not block execution (consider using Flashbots Protect).

    Parameters
    ----------
    opportunity:
        The arbitrage opportunity to assess (ideally status == "PASS").
    dex_quote:
        The DEX ``Quote`` used to build the opportunity.
    quote_ttl_seconds:
        Maximum quote age for the TTL check (default: 30 s).

    Returns
    -------
    ChainRiskResult
        ``is_safe=True`` only when all blocking checks pass.
    """
    warnings: list[str] = []
    is_safe = True

    # 1. Mock DEX cannot be executed
    if dex_quote.exchange == "mock_dex":
        warnings.append(
            "MOCK_DEX: execution attempted with a mock DEX quote — "
            "no real transaction will be sent. Use --dex uniswap_v3 for live execution."
        )
        is_safe = False

    # 2. Quote TTL (only meaningful for real on-chain quotes)
    if dex_quote.exchange == "uniswap_v3" and not check_quote_ttl(dex_quote, ttl_seconds=quote_ttl_seconds):
        warnings.append(
            f"STALE_QUOTE: DEX quote is older than {quote_ttl_seconds}s TTL. "
            "Re-fetch quotes before executing to avoid stale-price execution."
        )
        is_safe = False

    # 3. Route metadata (Uniswap V3 only)
    if dex_quote.exchange == "uniswap_v3":
        required_keys = {"base_addr", "quote_addr", "fee_tier", "quote_timestamp",
                         "base_decimals", "quote_decimals", "amount_in_base"}
        missing = required_keys - set(dex_quote.raw.keys())
        if missing:
            warnings.append(
                f"INVALID_ROUTE: on-chain metadata missing: {sorted(missing)}. "
                "Re-fetch the DEX quote; the quote object may be corrupt."
            )
            is_safe = False

    # 4. MEV / front-run risk (advisory — does not block)
    if 0 < opportunity.net_profit_pct < _MEV_RISK_THRESHOLD_PCT:
        warnings.append(
            f"MEV_RISK: net profit {opportunity.net_profit_pct:.3f}% is below "
            f"{_MEV_RISK_THRESHOLD_PCT:.2f}% — this trade may be front-run by MEV bots. "
            "Consider routing through Flashbots Protect: https://protect.flashbots.net"
        )
        # Advisory only — is_safe remains unchanged

    return ChainRiskResult(is_safe=is_safe, warnings=warnings)
