from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

import numpy as np
import pandas as pd

# Allow importing python-analyzer modules without packaging
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY_ANALYZER_DIR = os.path.join(REPO_ROOT, "python-analyzer")
if PY_ANALYZER_DIR not in sys.path:
    sys.path.insert(0, PY_ANALYZER_DIR)

try:
    # python-analyzer/technical_analysis.py
    from technical_analysis import TechnicalAnalyzer  # type: ignore
except Exception as e:
    TechnicalAnalyzer = None  # type: ignore
    _TA_IMPORT_ERROR = e
else:
    _TA_IMPORT_ERROR = None


SUPPORTED_INTERVALS = {"1h", "4h", "1d"}


@dataclass
class FeatureBuildResult:
    X_row: np.ndarray
    feature_columns: List[str]
    feature_ts: str


def _to_utc_dt(ts: Any) -> datetime:
    if ts is None:
        raise ValueError("timestamp is None")

    if isinstance(ts, (int, float)):
        if ts > 10_000_000_000:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    s = str(ts)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_klines_json(path: str) -> pd.DataFrame:
    """
    Load klines json from data/klines_{interval}.json.

    Supported formats:
      - list[dict]: with keys timestamp/open/high/low/close/(volume)
      - list[list]: [ts, open, high, low, close, volume]
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        return pd.DataFrame()

    if isinstance(data, list) and isinstance(data[0], dict):
        rows = []
        for r in data:
            ts = r.get("timestamp") or r.get("open_time") or r.get("time") or r.get("t")
            dt = _to_utc_dt(ts)
            rows.append(
                dict(
                    ts=dt,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r.get("volume", 0.0)),
                )
            )
        return pd.DataFrame(rows).set_index("ts").sort_index()

    if isinstance(data, list) and isinstance(data[0], list):
        rows = []
        for r in data:
            dt = _to_utc_dt(r[0])
            rows.append(
                dict(
                    ts=dt,
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=float(r[5]),
                )
            )
        return pd.DataFrame(rows).set_index("ts").sort_index()

    raise ValueError("Unsupported klines json format")


def add_technical_indicators_like_system(df: pd.DataFrame) -> pd.DataFrame:
    """
    In training pipeline: df is first passed through TechnicalAnalyzer.analyze(df),
    then MLPredictor.prepare_features(df).
    We reuse the SAME TechnicalAnalyzer implementation to ensure the same columns exist.
    """
    if df is None or df.empty:
        return df

    if TechnicalAnalyzer is None:
        raise RuntimeError(f"cannot import TechnicalAnalyzer from python-analyzer: {_TA_IMPORT_ERROR}")

    analyzer = TechnicalAnalyzer()
    analyzed = analyzer.analyze(df)
    return analyzed


def prepare_features_like_trainer(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    df = df.copy()

    # price features
    df["returns"] = df["close"].pct_change()
    df["log_returns"] = np.log(df["close"] / df["close"].shift(1))
    df["price_range"] = (df["high"] - df["low"]) / df["close"]
    df["body_size"] = (df["close"] - df["open"]).abs() / df["close"]
    df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
    df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]

    # lag features
    for lag in [1, 2, 3, 5, 10, 20]:
        df[f"return_lag_{lag}"] = df["returns"].shift(lag)
        df[f"volume_lag_{lag}"] = df["volume"].shift(lag)

    # rolling features
    for window in [5, 10, 20, 50]:
        df[f"rolling_mean_{window}"] = df["close"].rolling(window).mean()
        df[f"rolling_std_{window}"] = df["close"].rolling(window).std()
        df[f"rolling_vol_mean_{window}"] = df["volume"].rolling(window).mean()
        df[f"price_to_ma_{window}"] = df["close"] / df[f"rolling_mean_{window}"]

    # volatility features
    df["volatility_5"] = df["returns"].rolling(5).std()
    df["volatility_20"] = df["returns"].rolling(20).std()
    df["volatility_ratio"] = df["volatility_5"] / df["volatility_20"].replace(0, np.nan)

    # time features
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["day_of_week"] = df.index.dayofweek
        df["is_weekend"] = (df.index.dayofweek >= 5).astype(int)

    # ensure trader columns exist
    for col in ["trader_buy_ratio", "trader_sell_ratio", "trader_net_flow"]:
        if col not in df.columns:
            df[col] = 0.0

    df.dropna(inplace=True)
    return df


def get_feature_columns_like_trainer(df: pd.DataFrame) -> List[str]:
    exclude = ["open", "high", "low", "close", "volume", "signal", "exchange", "symbol", "interval"]
    exclude += [col for col in df.columns if col.startswith("target_")]
    exclude += [col for col in df.columns if col.startswith("signal")]
    exclude += [col for col in df.columns if col.startswith("ichimoku_chikou")]

    features = [
        col
        for col in df.columns
        if col not in exclude and df[col].dtype in [np.float64, np.int64, np.float32, np.int32]
    ]
    return features


def build_latest_feature_row_from_klines(
    data_dir: str,
    interval: str = "1h",
    expected_n_features: Optional[int] = None,
) -> FeatureBuildResult:
    interval = (interval or "1h").strip()

    if interval not in SUPPORTED_INTERVALS:
        supported = ", ".join(sorted(SUPPORTED_INTERVALS))
        raise ValueError(f"unsupported interval={interval}, supported=[{supported}]")

    klines_path = os.path.join(data_dir, f"klines_{interval}.json")
    if not os.path.exists(klines_path):
        raise FileNotFoundError(klines_path)

    df = load_klines_json(klines_path)
    if df.empty:
        raise ValueError("klines empty")

    # Need enough history for TA indicators + rolling(50) + lag(20).
    if len(df) < 120:
        raise ValueError(f"not enough klines rows: {len(df)} (need >= 120)")

    # 1) add TA indicators (same as training pipeline)
    df_analyzed = add_technical_indicators_like_system(df)

    # 2) add ML feature engineering (same as trainer)
    df_feat = prepare_features_like_trainer(df_analyzed)
    if df_feat.empty:
        raise ValueError("feature df empty after dropna")

    feature_cols = get_feature_columns_like_trainer(df_feat)
    X = df_feat[feature_cols].values

    if expected_n_features is not None and X.shape[1] != expected_n_features:
        raise ValueError(f"engineered features={X.shape[1]} but model expects={expected_n_features}")

    latest_ts = df_feat.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    X_row = X[-1:].astype(np.float32)
    return FeatureBuildResult(X_row=X_row, feature_columns=feature_cols, feature_ts=latest_ts)
