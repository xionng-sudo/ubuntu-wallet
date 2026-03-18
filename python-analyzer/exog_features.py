#!/usr/bin/env python3
"""Exogenous features loader for the ubuntu-wallet ML pipeline.

Loads funding rate, open interest, and taker buy ratio from the JSONL file
written by the Go collector (go-collector/exog/collector.go).

Usage:
  python python-analyzer/exog_features.py --help
  python python-analyzer/exog_features.py --path data/raw/exog_ETHUSDT.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    pd = None  # type: ignore
    _PANDAS_AVAILABLE = False


def load_exog_jsonl(path: str, as_of_ts: Optional[str] = None) -> pd.DataFrame:
    """Load exogenous features JSONL file into a DataFrame.

    Args:
        path:      Path to exog_SYMBOL.jsonl file.
        as_of_ts:  ISO8601 cutoff; rows with timestamp > cutoff are excluded.

    Returns:
        DataFrame with columns: symbol, funding_rate, open_interest,
        taker_buy_ratio, timestamp.  Empty DataFrame if file missing.
    """
    if not os.path.exists(path):
        return pd.DataFrame()

    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    if as_of_ts is not None and "timestamp" in df.columns:
        # pandas handles both 'Z' suffix and '+HH:MM' offset correctly
        cutoff = pd.Timestamp(as_of_ts).tz_localize("UTC") if pd.Timestamp(as_of_ts).tzinfo is None else pd.Timestamp(as_of_ts).tz_convert("UTC")
        df = df[df["timestamp"] <= cutoff].reset_index(drop=True)

    return df


def build_exog_feature_row(df: pd.DataFrame) -> dict:
    """Build a feature dict from the latest row of an exog DataFrame.

    Returns zeros for all keys if df is empty.

    Returns:
        dict with keys: exog_funding_rate, exog_open_interest, exog_taker_buy_ratio
    """
    zero = {"exog_funding_rate": 0.0, "exog_open_interest": 0.0, "exog_taker_buy_ratio": 0.0}
    if df is None or df.empty:
        return zero

    row = df.iloc[-1]
    return {
        "exog_funding_rate": float(row.get("funding_rate", 0.0)),
        "exog_open_interest": float(row.get("open_interest", 0.0)),
        "exog_taker_buy_ratio": float(row.get("taker_buy_ratio", 0.0)),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and display exogenous features from JSONL file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--path", default="data/raw/exog_ETHUSDT.jsonl", help="Path to exog JSONL file")
    parser.add_argument("--as-of-ts", default=None, help="ISO8601 cutoff timestamp (optional)")
    parser.add_argument("--tail", type=int, default=5, help="Number of tail rows to display")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    df = load_exog_jsonl(args.path, as_of_ts=args.as_of_ts)
    if df.empty:
        print(f"No data found in {args.path}", file=sys.stderr)
        sys.exit(0)

    print(f"Loaded {len(df)} rows from {args.path}")
    print(df.tail(args.tail).to_string(index=False))
    print("\nLatest feature row:")
    print(json.dumps(build_exog_feature_row(df), indent=2))


if __name__ == "__main__":
    main()
