#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
labeling.py
===========
Unified market-oriented label generation for ML training.

Supports:
  - Ternary labels:       UP (2) / FLAT (1) / DOWN (0)
    Based on forward return over `horizon` bars vs configurable thresholds.

  - Triple-barrier labels: UP (2) / FLAT (1) / DOWN (0)
    Exit rule mirrors live strategy:
      * Price hits TP (+tp_pct) first  → label = direction of barrier
      * Price hits SL (-sl_pct) first  → label = opposite
      * Neither hit within `horizon` bars → label = FLAT (timeout)

Label encoding always:
    SHORT / DOWN = 0
    FLAT        = 1
    LONG / UP   = 2

Usage:
    from labeling import make_ternary_labels, make_triple_barrier_labels

    y_ternary = make_ternary_labels(df, horizon=12, up_thresh=0.015, down_thresh=0.015)
    y_barrier = make_triple_barrier_labels(df, horizon=6, tp_pct=0.0175, sl_pct=0.009)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Label configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class LabelConfig:
    """Serialisable label configuration stored in model metadata."""
    method: str            # "ternary" | "triple_barrier"
    horizon: int
    up_thresh: float       # used by ternary
    down_thresh: float     # used by ternary
    tp_pct: float          # used by triple_barrier
    sl_pct: float          # used by triple_barrier

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "horizon": self.horizon,
            "up_thresh": self.up_thresh,
            "down_thresh": self.down_thresh,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
        }

    @staticmethod
    def from_dict(d: dict) -> "LabelConfig":
        return LabelConfig(
            method=d.get("method", "ternary"),
            horizon=int(d.get("horizon", 12)),
            up_thresh=float(d.get("up_thresh", 0.015)),
            down_thresh=float(d.get("down_thresh", 0.015)),
            tp_pct=float(d.get("tp_pct", 0.0175)),
            sl_pct=float(d.get("sl_pct", 0.009)),
        )


# ---------------------------------------------------------------------------
# Ternary labels
# ---------------------------------------------------------------------------

def make_ternary_labels(
    df: pd.DataFrame,
    horizon: int = 12,
    up_thresh: float = 0.015,
    down_thresh: float = 0.015,
) -> pd.Series:
    """
    Assign 3-class labels based on forward return over `horizon` bars.

      forward_return = close[t + horizon] / close[t] - 1

      LONG  (2): forward_return >= +up_thresh
      SHORT (0): forward_return <= -down_thresh
      FLAT  (1): otherwise (or NaN when forward bar doesn't exist)

    Args:
        df:          DataFrame with a 'close' column, sorted by time.
        horizon:     Number of future bars to look ahead.
        up_thresh:   Fractional return threshold for LONG label (e.g. 0.015 = 1.5%).
        down_thresh: Fractional return threshold for SHORT label.

    Returns:
        pd.Series[int] aligned to df.index, with NaN for the last `horizon` rows.
    """
    fwd_ret = df["close"].shift(-horizon) / df["close"] - 1
    labels = pd.Series(1, index=df.index, dtype=float)  # default FLAT
    labels[fwd_ret >= up_thresh] = 2                     # LONG
    labels[fwd_ret <= -down_thresh] = 0                  # SHORT
    # last `horizon` rows have no valid forward bar
    labels.iloc[-horizon:] = np.nan
    return labels


# ---------------------------------------------------------------------------
# Triple-barrier labels
# ---------------------------------------------------------------------------

def make_triple_barrier_labels(
    df: pd.DataFrame,
    horizon: int = 6,
    tp_pct: float = 0.0175,
    sl_pct: float = 0.009,
    direction: str = "both",
) -> pd.Series:
    """
    Triple-barrier labeling aligned to live strategy exit logic.

    For each bar t (entry assumed at close[t]):
      - Check bars t+1 … t+horizon for TP / SL breach using high/low.
      - First barrier hit determines the label.
      - If neither barrier is hit → FLAT (timeout).

    direction parameter controls which barriers apply per bar:
      "both"  : TP and SL both active (default, symmetric).
      "long"  : Only LONG-side barriers (TP = +tp_pct, SL = -sl_pct).
      "short" : Only SHORT-side barriers.

    Labels:
      UP / LONG  (2): TP first (or timeout with positive return > tp_pct/2).
      DOWN/SHORT (0): SL first.
      FLAT       (1): Timeout – neither barrier reached.

    Args:
        df:       DataFrame with columns: open, high, low, close. Sorted by time.
        horizon:  Max bars to hold.
        tp_pct:   Take-profit as fraction of entry price (e.g. 0.0175 = 1.75%).
        sl_pct:   Stop-loss as fraction of entry price (e.g. 0.009 = 0.9%).
        direction: "both", "long", or "short".

    Returns:
        pd.Series[float] aligned to df.index with NaN for rows where there
        aren't enough future bars.
    """
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    labels = np.full(n, np.nan)

    for i in range(n - horizon):
        entry = closes[i]
        tp_price = entry * (1.0 + tp_pct)
        sl_price = entry * (1.0 - sl_pct)

        outcome = 1  # default: FLAT (timeout)
        for j in range(i + 1, i + 1 + horizon):
            h = highs[j]
            lo = lows[j]

            hit_tp = h >= tp_price
            hit_sl = lo <= sl_price

            if hit_tp and hit_sl:
                # tie-break: SL (conservative)
                outcome = 0  # SHORT / DOWN
                break
            if hit_tp:
                outcome = 2  # LONG / UP
                break
            if hit_sl:
                outcome = 0  # SHORT / DOWN
                break

        labels[i] = outcome

    return pd.Series(labels, index=df.index, dtype=float)


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def make_labels(df: pd.DataFrame, cfg: LabelConfig) -> pd.Series:
    """
    Dispatch to the correct labeling function based on cfg.method.

    Args:
        df:  DataFrame with OHLCV columns and time index.
        cfg: LabelConfig instance describing how labels should be constructed.

    Returns:
        pd.Series[float] with labels (0/1/2) and NaN where invalid.
    """
    if cfg.method == "triple_barrier":
        return make_triple_barrier_labels(
            df,
            horizon=cfg.horizon,
            tp_pct=cfg.tp_pct,
            sl_pct=cfg.sl_pct,
        )
    # default: ternary
    return make_ternary_labels(
        df,
        horizon=cfg.horizon,
        up_thresh=cfg.up_thresh,
        down_thresh=cfg.down_thresh,
    )
