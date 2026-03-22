#!/usr/bin/env python3
"""
DEX/CEX Arbitrage Scanner CLI
Usage:
    python scripts/scan_arbitrage.py --help
    python scripts/scan_arbitrage.py --symbols ETH/USDT,BTC/USDT --amount 10000
    python scripts/scan_arbitrage.py --cex mock --dex mock --output json --show-all

    # Real on-chain quote (requires ETHEREUM_RPC_URL in .env)
    python scripts/scan_arbitrage.py --dex uniswap_v3 --show-all

    # Execute DEX leg only (requires ETHEREUM_RPC_URL + WALLET_PRIVATE_KEY in .env)
    python scripts/scan_arbitrage.py --dex uniswap_v3 --execute

    # Execute BOTH legs — full DEX/CEX dual-sided closed loop
    # (requires ETHEREUM_RPC_URL + WALLET_PRIVATE_KEY + BINANCE_API_KEY + BINANCE_API_SECRET)
    python scripts/scan_arbitrage.py --dex uniswap_v3 --execute-both
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Ensure repo root is on the path so `app.*` imports work
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Load .env from the repo root so ETHEREUM_RPC_URL, WALLET_PRIVATE_KEY, etc.
# are available without requiring the caller to manually `export` them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv not installed; fall back to environment variables

from app.market.cex.binance import BinanceCEXQuote
from app.market.cex.mock_cex import MockCEXQuote
from app.market.dex.mock_dex import MockDEXQuote
from app.market.dex.uniswap_v3 import UniswapV3Quote
from app.arbitrage.engine import ArbitrageEngine, ArbitrageOpportunity
from app.risk.filters import RiskConfig, filter_opportunities


# ---------------------------------------------------------------------------
# Table output
# ---------------------------------------------------------------------------
_TABLE_COLS = [
    ("Symbol",      10),
    ("Direction",   20),
    ("CEX px",       12),
    ("DEX px",       12),
    ("Gross%",        8),
    ("Net $",         9),
    ("Net%",          7),
    ("Status",       24),
]


def _row(opp: ArbitrageOpportunity) -> list[str]:
    return [
        opp.symbol,
        opp.direction,
        f"{opp.cex_price:,.4f}",
        f"{opp.dex_price:,.4f}",
        f"{opp.gross_profit_pct:+.3f}%",
        f"${opp.net_profit_usd:+.2f}",
        f"{opp.net_profit_pct:+.3f}%",
        opp.status if opp.status == "PASS" else f"{opp.status}",
    ]


def print_table(opps: list[ArbitrageOpportunity]) -> None:
    header = "  ".join(f"{name:<{w}}" for name, w in _TABLE_COLS)
    sep = "  ".join("-" * w for _, w in _TABLE_COLS)
    print(header)
    print(sep)
    for opp in opps:
        cells = _row(opp)
        line = "  ".join(f"{v:<{w}}" for v, (_, w) in zip(cells, _TABLE_COLS))
        print(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scan DEX/CEX arbitrage opportunities",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbols", default="ETH/USDT,BTC/USDT",
                   help="Comma-separated trading pairs (BNB/USDT is not supported with --dex uniswap_v3)")
    p.add_argument("--amount", type=float, default=10_000.0,
                   help="Trade amount in USD")
    p.add_argument("--cex", choices=["binance", "mock"], default="binance",
                   help="CEX source to use (use 'mock' for offline demo)")
    p.add_argument("--dex", choices=["mock", "uniswap_v3"], default="mock",
                   help="DEX source to use")
    p.add_argument("--output", choices=["table", "json"], default="table",
                   help="Output format")
    p.add_argument("--min-profit", type=float, default=1.0,
                   help="Minimum net profit USD")
    p.add_argument("--max-gas", type=float, default=50.0,
                   help="Maximum gas cost USD")
    p.add_argument("--max-slippage", type=float, default=1.0,
                   help="Maximum slippage %%")
    p.add_argument("--min-liquidity", type=float, default=10_000.0,
                   help="Minimum liquidity USD")
    p.add_argument("--show-all", action="store_true", default=False,
                   help="Show blocked opportunities too")
    p.add_argument("--demo", action="store_true", default=False,
                   help="Use demo mode with exaggerated mock spreads to show PASS results")
    p.add_argument(
        "--execute", action="store_true", default=False,
        help=(
            "Execute the DEX leg only of PASS opportunities on-chain via Uniswap V3. "
            "Requires --dex uniswap_v3, ETHEREUM_RPC_URL and WALLET_PRIVATE_KEY in .env. "
            "WARNING: this sends real transactions and spends real funds."
        ),
    )
    p.add_argument(
        "--execute-both", action="store_true", default=False,
        help=(
            "Execute BOTH the DEX and CEX legs — full dual-sided closed-loop arbitrage. "
            "Requires --dex uniswap_v3, ETHEREUM_RPC_URL, WALLET_PRIVATE_KEY, "
            "BINANCE_API_KEY and BINANCE_API_SECRET in .env. "
            "WARNING: this sends real transactions and real orders, spending real funds. "
            "A partial-fill (one leg succeeds, other fails) leaves an open position."
        ),
    )
    p.add_argument(
        "--slippage-tolerance", type=float, default=0.5,
        help="Max slippage %% accepted during on-chain execution (default: 0.5%%)",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: no symbols provided", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Validate --execute / --execute-both preconditions
    # ------------------------------------------------------------------
    if args.execute and args.execute_both:
        print("ERROR: use either --execute or --execute-both, not both", file=sys.stderr)
        return 1

    if args.execute or args.execute_both:
        if args.dex != "uniswap_v3":
            print("ERROR: --execute/--execute-both requires --dex uniswap_v3", file=sys.stderr)
            return 1
        if args.cex == "mock":
            print("ERROR: --execute/--execute-both requires real CEX data (--cex binance)", file=sys.stderr)
            return 1

    if args.execute_both:
        api_key    = os.environ.get("BINANCE_API_KEY",    "")
        api_secret = os.environ.get("BINANCE_API_SECRET", "")
        if not api_key or not api_secret:
            print(
                "ERROR: --execute-both requires BINANCE_API_KEY and BINANCE_API_SECRET in .env.",
                file=sys.stderr,
            )
            return 1

    # ------------------------------------------------------------------
    # Fetch CEX quotes
    # ------------------------------------------------------------------
    if args.cex == "mock":
        print("NOTE: using mock CEX (offline demo mode)", file=sys.stderr)
        cex_fetcher = MockCEXQuote()
        cex_quotes = cex_fetcher.fetch_quotes(symbols, args.amount)
    else:
        print(f"Fetching Binance quotes for: {', '.join(symbols)} …", file=sys.stderr)
        cex_fetcher = BinanceCEXQuote()
        try:
            cex_quotes = cex_fetcher.fetch_quotes(symbols, args.amount)
        except Exception as exc:
            print(f"ERROR: failed to fetch CEX quotes: {exc}", file=sys.stderr)
            return 1

        if not cex_quotes:
            print(
                "ERROR: no CEX quotes returned — check symbols or network.\n"
                "TIP: run with --cex mock for offline demo mode.",
                file=sys.stderr,
            )
            return 1

    # ------------------------------------------------------------------
    # Fetch DEX quotes
    # ------------------------------------------------------------------
    if args.dex == "mock":
        ref_prices = {q.symbol: q.mid for q in cex_quotes}
        if args.demo:
            # Demo mode: apply a positive bias so DEX prices are ~1.5% above CEX.
            # BUY_CEX_SELL_DEX becomes profitable, illustrating a PASS result.
            dex_fetcher = MockDEXQuote(
                reference_prices=ref_prices,
                spread_range=(0.001, 0.002),
                noise_pct=0.002,
                bias_pct=0.015,  # DEX is ~1.5% higher → buy CEX, sell DEX
                seed=42,
            )
        else:
            dex_fetcher = MockDEXQuote(reference_prices=ref_prices)
    else:
        dex_fetcher = UniswapV3Quote()  # type: ignore[assignment]

    print(f"Fetching {args.dex} DEX quotes …", file=sys.stderr)
    try:
        dex_quotes = dex_fetcher.fetch_quotes(symbols, args.amount)
    except Exception as exc:
        print(f"ERROR: failed to fetch DEX quotes: {exc}", file=sys.stderr)
        return 1

    # Index DEX quotes by symbol for later lookup during execution
    dex_quotes_by_symbol = {q.symbol: q for q in dex_quotes}

    # ------------------------------------------------------------------
    # Arbitrage engine
    # ------------------------------------------------------------------
    engine = ArbitrageEngine()
    opportunities = engine.evaluate_all(cex_quotes, dex_quotes, args.amount)

    # ------------------------------------------------------------------
    # Risk filters
    # ------------------------------------------------------------------
    risk_cfg = RiskConfig(
        min_net_profit_usd=args.min_profit,
        max_gas_cost_usd=args.max_gas,
        max_slippage_pct=args.max_slippage,
        min_liquidity_usd=args.min_liquidity,
    )
    opportunities = filter_opportunities(opportunities, risk_cfg)

    if not args.show_all:
        opportunities = [o for o in opportunities if o.status == "PASS"]

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    if args.output == "json":
        import dataclasses
        print(json.dumps([dataclasses.asdict(o) for o in opportunities], indent=2))
    else:
        if not opportunities:
            print("No opportunities found (use --show-all to see blocked ones).")
        else:
            print_table(opportunities)

    passing = sum(1 for o in opportunities if o.status == "PASS")
    print(
        f"\nScanned {len(symbols)} symbol(s) · "
        f"{len(opportunities)} result(s) · "
        f"{passing} passing filters",
        file=sys.stderr,
    )

    # ------------------------------------------------------------------
    # Optional on-chain execution (--execute)
    # ------------------------------------------------------------------
    if args.execute and passing > 0:
        return _run_execution(args, opportunities, dex_quotes_by_symbol)

    # ------------------------------------------------------------------
    # Optional dual-sided execution (--execute-both)
    # ------------------------------------------------------------------
    if args.execute_both and passing > 0:
        return _run_dual_execution(args, opportunities, dex_quotes_by_symbol)

    return 0


def _run_execution(args, opportunities, dex_quotes_by_symbol: dict) -> int:
    """Execute the DEX leg of PASS opportunities via Uniswap V3."""
    from app.risk.chain_risk import assess_chain_risks
    from app.execution.wallet import get_account
    from app.execution.swap_executor import UniswapV3SwapExecutor

    try:
        from web3 import Web3
    except ImportError:
        print(
            "ERROR: web3 is not installed. Run: pip install web3>=6.0.0",
            file=sys.stderr,
        )
        return 1

    rpc_url = os.environ.get("ETHEREUM_RPC_URL", "")
    if not rpc_url:
        print(
            "ERROR: ETHEREUM_RPC_URL is not set. Add it to your .env file.",
            file=sys.stderr,
        )
        return 1

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print(
                f"ERROR: Cannot connect to Ethereum RPC at {rpc_url!r}",
                file=sys.stderr,
            )
            return 1
        account = get_account(w3)
    except Exception as exc:
        print(f"ERROR: wallet/RPC setup failed: {exc}", file=sys.stderr)
        return 1

    executor = UniswapV3SwapExecutor(
        slippage_tolerance=args.slippage_tolerance / 100.0,
    )

    print(
        f"\n⚠️  EXECUTING DEX LEG for {sum(1 for o in opportunities if o.status == 'PASS')} "
        f"PASS opportunity(ies) — REAL TRANSACTIONS, REAL FUNDS",
        file=sys.stderr,
    )

    for opp in opportunities:
        if opp.status != "PASS":
            continue
        dex_quote = dex_quotes_by_symbol.get(opp.symbol)
        if dex_quote is None:
            print(f"  [{opp.symbol}] SKIP — no DEX quote found", file=sys.stderr)
            continue

        # Chain risk assessment
        risk = assess_chain_risks(opp, dex_quote)
        for warn in risk.warnings:
            print(f"  [{opp.symbol}] WARN: {warn}", file=sys.stderr)
        if not risk.is_safe:
            print(f"  [{opp.symbol}] BLOCKED by chain risk — skipping", file=sys.stderr)
            continue

        try:
            result = executor.execute_dex_leg(w3, account, opp, dex_quote)
            status = "✅ SUCCESS" if result.success else "❌ REVERTED"
            print(
                f"  [{opp.symbol}] {opp.direction} {status} "
                f"tx={result.tx_hash} gas={result.gas_used}",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"  [{opp.symbol}] ERROR: {exc}", file=sys.stderr)

    return 0


def _run_dual_execution(args, opportunities, dex_quotes_by_symbol: dict) -> int:
    """
    Execute BOTH legs (DEX + CEX) for each PASS opportunity.

    Execution order:
      BUY_CEX_SELL_DEX → CEX buy first, then DEX sell.
      BUY_DEX_SELL_CEX → DEX buy first, then CEX sell.

    Results are written to data/arb_results.jsonl.
    Partial fills (one leg succeeded, other failed) emit an alert to stderr.
    """
    from app.execution.wallet import get_account
    from app.execution.swap_executor import UniswapV3SwapExecutor
    from app.execution.cex_executor import BinanceCEXExecutor
    from app.execution.arbitrage_executor import ArbitrageExecutor

    try:
        from web3 import Web3
    except ImportError:
        print("ERROR: web3 is not installed. Run: pip install web3>=6.0.0", file=sys.stderr)
        return 1

    rpc_url = os.environ.get("ETHEREUM_RPC_URL", "")
    if not rpc_url:
        print("ERROR: ETHEREUM_RPC_URL is not set.", file=sys.stderr)
        return 1

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print(f"ERROR: Cannot connect to Ethereum RPC at {rpc_url!r}", file=sys.stderr)
            return 1
        account = get_account(w3)
    except Exception as exc:
        print(f"ERROR: wallet/RPC setup failed: {exc}", file=sys.stderr)
        return 1

    api_key    = os.environ.get("BINANCE_API_KEY",    "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    orchestrator = ArbitrageExecutor(
        dex_executor=UniswapV3SwapExecutor(
            slippage_tolerance=args.slippage_tolerance / 100.0,
        ),
        cex_executor=BinanceCEXExecutor(),
    )

    n_pass = sum(1 for o in opportunities if o.status == "PASS")
    print(
        f"\n⚠️  DUAL-SIDED EXECUTION for {n_pass} PASS opportunity(ies) "
        "— REAL TRANSACTIONS + REAL CEX ORDERS, REAL FUNDS",
        file=sys.stderr,
    )

    for opp in opportunities:
        if opp.status != "PASS":
            continue
        dex_quote = dex_quotes_by_symbol.get(opp.symbol)
        if dex_quote is None:
            print(f"  [{opp.symbol}] SKIP — no DEX quote found", file=sys.stderr)
            continue

        result = orchestrator.execute(opp, dex_quote, w3, account, api_key, api_secret)

        for warn in result.warnings:
            print(f"  [{opp.symbol}] WARN: {warn}", file=sys.stderr)

        if result.error:
            print(f"  [{opp.symbol}] ERROR: {result.error}", file=sys.stderr)

        if result.success:
            print(f"  [{opp.symbol}] {opp.direction} ✅ BOTH LEGS SUCCESS"
                  f" ({result.elapsed_seconds:.1f}s)", file=sys.stderr)
        elif result.partial:
            buy_status  = "✅" if result.buy_leg  and result.buy_leg.success  else "❌"
            sell_status = "✅" if result.sell_leg and result.sell_leg.success else "❌"
            print(
                f"  [{opp.symbol}] {opp.direction} ⚠️  PARTIAL "
                f"buy={buy_status} sell={sell_status} — POSITION OPEN, manual action needed",
                file=sys.stderr,
            )
        else:
            print(f"  [{opp.symbol}] {opp.direction} ❌ ABORTED (no exposure)", file=sys.stderr)

    print("\nResults appended to data/arb_results.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
