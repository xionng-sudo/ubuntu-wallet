#!/usr/bin/env python3
"""
DEX/CEX Arbitrage Scanner CLI
Usage:
    python scripts/scan_arbitrage.py --help
    python scripts/scan_arbitrage.py --symbols ETH/USDT,BTC/USDT --amount 10000
    python scripts/scan_arbitrage.py --dex mock --output json --show-all
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

from app.market.cex.binance import BinanceCEXQuote
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
    p.add_argument("--symbols", default="ETH/USDT,BTC/USDT,BNB/USDT",
                   help="Comma-separated trading pairs")
    p.add_argument("--amount", type=float, default=10_000.0,
                   help="Trade amount in USD")
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
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: no symbols provided", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Fetch CEX quotes
    # ------------------------------------------------------------------
    print(f"Fetching Binance quotes for: {', '.join(symbols)} …", file=sys.stderr)
    cex_fetcher = BinanceCEXQuote()
    try:
        cex_quotes = cex_fetcher.fetch_quotes(symbols, args.amount)
    except Exception as exc:
        print(f"ERROR: failed to fetch CEX quotes: {exc}", file=sys.stderr)
        return 1

    if not cex_quotes:
        print("ERROR: no CEX quotes returned — check symbols or network", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Fetch DEX quotes
    # ------------------------------------------------------------------
    if args.dex == "mock":
        ref_prices = {q.symbol: q.mid for q in cex_quotes}
        dex_fetcher = MockDEXQuote(reference_prices=ref_prices)
    else:
        dex_fetcher = UniswapV3Quote()  # type: ignore[assignment]

    print(f"Fetching {args.dex} DEX quotes …", file=sys.stderr)
    try:
        dex_quotes = dex_fetcher.fetch_quotes(symbols, args.amount)
    except NotImplementedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: failed to fetch DEX quotes: {exc}", file=sys.stderr)
        return 1

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
