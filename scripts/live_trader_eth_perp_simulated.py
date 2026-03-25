#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_trader_eth_perp_simulated.py  (ETHUSDT wrapper — deprecated)
==================================================================
Backward-compatible wrapper for ETHUSDT that delegates to the generic
``live_trader_perp_simulated.py`` script with ``--symbol ETHUSDT``.

For new runs, prefer the generic script directly:

    python scripts/live_trader_perp_simulated.py --symbol ETHUSDT [options]
    python scripts/live_trader_perp_simulated.py --all-symbols

CLI flags accepted here map to the generic script equivalents:
    --data-dir  →  --data-base-dir (the per-symbol sub-directory is appended automatically)
    all other flags are forwarded as-is

.. deprecated::
    Use ``live_trader_perp_simulated.py --symbol ETHUSDT`` instead.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from live_trader_perp_simulated import (  # type: ignore
    build_parser,
    run_for_symbol,
    run_simulation,
)


# ---------------------------------------------------------------------------
# Backward-compatible entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import warnings

    warnings.warn(
        "live_trader_eth_perp_simulated.py is deprecated. "
        "Use live_trader_perp_simulated.py --symbol ETHUSDT instead.",
        DeprecationWarning,
        stacklevel=1,
    )

    # Build parser that matches the generic script so old CLI flags still work.
    # Add --data-dir alias for users who pass it explicitly (maps to --data-base-dir).
    ap = build_parser()
    # --data-dir is the old flag name; add it for backward compat
    ap.add_argument(
        "--data-dir",
        default=None,
        dest="data_dir_compat",
        help=(
            "[DEPRECATED] Old per-root data dir.  Use --data-base-dir instead. "
            "If supplied, sets --data-base-dir to this value."
        ),
    )
    args = ap.parse_args()

    # Map legacy --data-dir to data_base_dir if user passed it
    if args.data_dir_compat:
        args.data_base_dir = args.data_dir_compat

    # Force symbol to ETHUSDT
    args.symbol = "ETHUSDT"
    args.all_symbols = False

    run_for_symbol("ETHUSDT", args)
