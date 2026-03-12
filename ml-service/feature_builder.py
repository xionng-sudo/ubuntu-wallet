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

# Multi-timeframe column prefixes used by event_v3
_EXTRA_TF_PREFIXES = ("tf4h_", "tf1d_")


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


def _add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add engineered features WITHOUT final dropna. Used by both single-tf and multi-tf paths."""
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

    return df


def _base_required_columns() -> List[str]:
    """Return the minimal set of 1h base columns that must not be NaN."""
    required = [
        "open", "high", "low", "close", "volume",
        "returns", "log_returns", "price_range", "body_size",
        "upper_shadow", "lower_shadow",
        "volatility_5", "volatility_20", "volatility_ratio",
        "hour", "day_of_week", "is_weekend",
        "trader_buy_ratio", "trader_sell_ratio", "trader_net_flow",
    ]
    for lag in [1, 2, 3, 5, 10, 20]:
        required.append(f"return_lag_{lag}")
        required.append(f"volume_lag_{lag}")
    for window in [5, 10, 20, 50]:
        required.append(f"rolling_mean_{window}")
        required.append(f"rolling_std_{window}")
        required.append(f"rolling_vol_mean_{window}")
        required.append(f"price_to_ma_{window}")
    return required


def prepare_features_like_trainer(df: pd.DataFrame) -> pd.DataFrame:
    """Add engineered features and drop rows with NaN in essential 1h base columns only."""
    df = _add_engineered_features(df)
    if df is None or df.empty:
        return df

    # Only drop rows where the minimal required 1h base columns are NaN.
    # This avoids dropping recent rows due to NaN in non-essential columns
    # (e.g. ichimoku_chikou which is close.shift(-kijun) and is NaN at the tail).
    required = [c for c in _base_required_columns() if c in df.columns]
    df.dropna(subset=required, inplace=True)
    return df


def get_feature_columns_like_trainer(df: pd.DataFrame) -> List[str]:
    exclude = ["open", "high", "low", "close", "volume", "signal", "exchange", "symbol", "interval"]
    exclude += [col for col in df.columns if col.startswith("target_")]
    exclude += [col for col in df.columns if col.startswith("signal")]
    # Exclude ichimoku_chikou from ALL timeframes (it's close.shift(-kijun) = data leakage at tail)
    exclude += [col for col in df.columns if "ichimoku_chikou" in col]

    features = [
        col
        for col in df.columns
        if col not in exclude and df[col].dtype in [np.float64, np.int64, np.float32, np.int32]
    ]
    return features


def build_multi_tf_feature_df(
    data_dir: str,
    as_of_ts: Optional[str] = None,
    min_rows: int = 120,
) -> pd.DataFrame:
    """
    Build a full multi-timeframe feature DataFrame for event_v3.

    Loads 1h (base) + 4h + 1d klines, computes TA indicators and engineered
    features for each, merges onto the 1h index via backward asof join, then:
      - forward-fills non-essential multi-tf columns (handles ichimoku_chikou
        NaN at the tail and other burn-in gaps after merge)
      - drops NaN only for the feature columns (not globally), so recent 1h
        rows are preserved even when tf4h/tf1d chikou columns are still NaN.

    Returns a DataFrame indexed by 1h timestamps ready for model inference or
    training label assignment.
    """
    df_1h = load_klines_json(os.path.join(data_dir, "klines_1h.json"))
    df_4h_raw = load_klines_json(os.path.join(data_dir, "klines_4h.json"))
    df_1d_raw = load_klines_json(os.path.join(data_dir, "klines_1d.json"))

    if as_of_ts:
        cutoff = _to_utc_dt(as_of_ts)
        df_1h = df_1h[df_1h.index <= cutoff]
        df_4h_raw = df_4h_raw[df_4h_raw.index <= cutoff]
        df_1d_raw = df_1d_raw[df_1d_raw.index <= cutoff]

    if len(df_1h) < min_rows:
        raise ValueError(f"not enough 1h klines rows: {len(df_1h)} (need >= {min_rows})")

    # 1h: full TA + engineered features; dropna only on essential 1h columns
    df_1h_analyzed = add_technical_indicators_like_system(df_1h)
    df_1h_feat = prepare_features_like_trainer(df_1h_analyzed)
    if df_1h_feat.empty:
        raise ValueError("1h feature df empty after dropna")

    # 4h/1d: TA + engineered features WITHOUT final dropna (merge handles alignment)
    df_4h_analyzed = add_technical_indicators_like_system(df_4h_raw)
    df_4h_feat = _add_engineered_features(df_4h_analyzed)

    df_1d_analyzed = add_technical_indicators_like_system(df_1d_raw)
    df_1d_feat = _add_engineered_features(df_1d_analyzed)

    # Prefix multi-tf columns (exclude raw OHLCV to avoid name clashes)
    _base_ohlcv = {"open", "high", "low", "close", "volume"}

    def _prefix_df(src: pd.DataFrame, prefix: str) -> pd.DataFrame:
        cols = {c: f"{prefix}{c}" for c in src.columns if c not in _base_ohlcv}
        renamed = src.rename(columns=cols)
        return renamed[[v for v in cols.values() if v in renamed.columns]]

    df_4h_pfx = _prefix_df(df_4h_feat, "tf4h_")
    df_1d_pfx = _prefix_df(df_1d_feat, "tf1d_")

    # Merge onto 1h index using last-known value (backward asof join)
    merged = df_1h_feat.copy()
    merged = pd.merge_asof(
        merged.sort_index(),
        df_4h_pfx.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    merged = pd.merge_asof(
        merged.sort_index(),
        df_1d_pfx.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
    )

    # Forward-fill multi-tf columns to handle:
    #   - ichimoku_chikou NaN at the tail (close.shift(-kijun) is NaN for recent rows)
    #   - any NaN gaps from the merge for early rows without a prior 4h/1d bar
    tf_cols = [c for c in merged.columns if c.startswith(_EXTRA_TF_PREFIXES)]
    if tf_cols:
        merged[tf_cols] = merged[tf_cols].ffill()

    # Drop NaN only on feature columns (NOT globally).
    # ichimoku_chikou columns are excluded from features so they never trigger a drop.
    feature_cols = get_feature_columns_like_trainer(merged)
    merged = merged.dropna(subset=feature_cols)

    if merged.empty:
        raise ValueError("event_v3 feature df empty after targeted dropna on feature columns")

    return merged


def build_event_v3_feature_row(
    data_dir: str,
    expected_n_features: Optional[int] = None,
    as_of_ts: Optional[str] = None,
) -> FeatureBuildResult:
    """
    Build a single-row feature vector for the event_v3 multi-timeframe stacking model.
    Uses build_multi_tf_feature_df internally and returns the latest (most recent) row.
    """
    merged = build_multi_tf_feature_df(data_dir, as_of_ts=as_of_ts)
    feature_cols = get_feature_columns_like_trainer(merged)
    X = merged[feature_cols].values

    if expected_n_features is not None and X.shape[1] != expected_n_features:
        raise ValueError(
            f"event_v3 engineered features={X.shape[1]} but model expects={expected_n_features}"
        )

    latest_ts = (
        merged.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    X_row = X[-1:].astype(np.float32)
    return FeatureBuildResult(X_row=X_row, feature_columns=feature_cols, feature_ts=latest_ts)


def build_latest_feature_row_from_klines(
    data_dir: str,
    interval: str = "1h",
    expected_n_features: Optional[int] = None,
    as_of_ts: Optional[str] = None,  # ISO8601 like 2026-03-05T12:00:00Z
) -> FeatureBuildResult:
    """Build single-timeframe feature row (backward-compat / non-event_v3 models)."""
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

    # Filter history up to as_of_ts (inclusive)
    if as_of_ts:
        cutoff = _to_utc_dt(as_of_ts)
        df = df[df.index <= cutoff]
        if df.empty:
            raise ValueError(f"no klines <= as_of_ts={as_of_ts}")

    # Need enough history for TA indicators + rolling(50) + lag(20).
    if len(df) < 120:
        raise ValueError(f"not enough klines rows: {len(df)} (need >= 120)")

    df_analyzed = add_technical_indicators_like_system(df)
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
