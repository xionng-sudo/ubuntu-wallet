#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETHUSDT 永续合约实盘脚本（wrapper，向后兼容）

此脚本已被通用脚本替代：
    scripts/live_trader_perp_binance.py --symbol ETHUSDT [options]

保留此文件仅为了兼容旧命令/旧入口。
"""

from __future__ import annotations

import os
import sys
import warnings

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from live_trader_perp_binance import main as _generic_main  # type: ignore  # noqa: E402


def main() -> None:
    warnings.warn(
        "live_trader_eth_perp_binance.py 已弃用。"
        "请改用：live_trader_perp_binance.py --symbol ETHUSDT",
        DeprecationWarning,
        stacklevel=2,
    )

    # Forward all args, but force symbol=ETHUSDT.
    argv = sys.argv[1:]

    # If user provided symbol selection flags, keep them (but we will ensure ETHUSDT)
    # To be strict and simple: always inject --symbol ETHUSDT and remove --all-symbols/--symbols/--symbol if present.
    cleaned: list[str] = []
    skip_next = False
    for i, a in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if a in ("--symbol", "--symbols"):
            skip_next = True
            continue
        if a == "--all-symbols":
            continue
        cleaned.append(a)

    cleaned = ["--symbol", "ETHUSDT", *cleaned]
    _generic_main(cleaned)


if __name__ == "__main__":
    main()
