from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

logger = logging.getLogger(__name__)

SUPPORTED_INTERVALS = {"1h", "4h", "1d"}


# ---------------------------------------------------------------------------
# Exogenous features
# ---------------------------------------------------------------------------

def load_exog_features(
    data_dir: str,
    symbol: str = "ETHUSDT",
    as_of_ts: Optional[str] = None,
) -> Dict[str, float]:
    """Load latest exogenous features from data/raw/exog_{symbol}.jsonl.

    Returns a dict with keys exog_funding_rate, exog_open_interest,
    exog_taker_buy_ratio, all zero if the feature flag is off or the file
    does not exist.
    """
    zero: Dict[str, float] = {
        "exog_funding_rate": 0.0,
        "exog_open_interest": 0.0,
        "exog_taker_buy_ratio": 0.0,
    }

    if os.environ.get("ENABLE_EXOG_FEATURES", "false").strip().lower() != "true":
        return zero

    path = os.path.join(data_dir, "raw", f"exog_{symbol}.jsonl")
    if not os.path.exists(path):
        logger.warning("load_exog_features: file not found: %s", path)
        return zero

    rows: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("load_exog_features: failed to read %s: %s", path, exc)
        return zero

    if not rows:
        return zero

    if as_of_ts is not None:
        try:
            cutoff = _to_utc_dt(as_of_ts)
            filtered = []
            for r in rows:
                ts_raw = r.get("timestamp")
                if ts_raw is None:
                    continue
                try:
                    row_ts = _to_utc_dt(ts_raw)
                    if row_ts <= cutoff:
                        filtered.append(r)
                except Exception:
                    continue
            rows = filtered if filtered else rows
        except Exception:
            pass

    latest = rows[-1]
    return {
        "exog_funding_rate": float(latest.get("funding_rate", 0.0)),
        "exog_open_interest": float(latest.get("open_interest", 0.0)),
        "exog_taker_buy_ratio": float(latest.get("taker_buy_ratio", 0.0)),
    }


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

@dataclass
class SchemaValidationResult:
    """Result of a feature schema validation check."""
    is_valid: bool
    missing_columns: List[str]   # columns in schema but not in data
    extra_columns: List[str]     # columns in data but not in schema
    zero_fill_columns: List[str] # columns that were filled with 0.0 (were missing)
    n_expected: int
    n_actual: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "missing_columns": self.missing_columns,
            "extra_columns": self.extra_columns,
            "zero_fill_columns": self.zero_fill_columns,
            "n_expected": self.n_expected,
            "n_actual": self.n_actual,
        }


def validate_feature_schema(
    df: pd.DataFrame,
    expected_columns: List[str],
    warn_on_missing: bool = True,
) -> SchemaValidationResult:
    """
    Validate that df contains the expected feature columns.

    Logs warnings for missing or extra columns to help detect
    online/offline feature drift early.

    Args:
        df:               DataFrame with constructed features.
        expected_columns: Canonical list of feature column names from training.
        warn_on_missing:  If True, log a warning for each missing column.

    Returns:
        SchemaValidationResult with details of any mismatch.
    """
    actual_set = set(df.columns)
    expected_set = set(expected_columns)

    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)

    if warn_on_missing and missing:
        logger.warning(
            "Feature schema drift: %d columns missing from live data that exist in training schema. "
            "They will be zero-filled. Missing: %s",
            len(missing),
            missing[:10],  # show first 10
        )
    if extra:
        logger.debug(
            "Feature schema: %d extra columns in live data (not in training schema). "
            "They will be dropped. Extra: %s",
            len(extra),
            extra[:10],
        )

    return SchemaValidationResult(
        is_valid=len(missing) == 0,
        missing_columns=missing,
        extra_columns=extra,
        zero_fill_columns=missing,
        n_expected=len(expected_columns),
        n_actual=len(df.columns),
    )


@dataclass
class FeatureBuildResult:
    X_row: np.ndarray
    feature_columns: List[str]
    feature_ts: str
    schema_validation: Optional[SchemaValidationResult] = None

# Multi-timeframe column prefixes used by event_v3
_EXTRA_TF_PREFIXES = ("tf4h_", "tf1d_")


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
                    volume=float(r[5]) if len(r) > 5 else 0.0,
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
        "open",
        "high",
        "low",
        "close",
        "volume",
        "returns",
        "log_returns",
        "price_range",
        "body_size",
        "upper_shadow",
        "lower_shadow",
        "volatility_5",
        "volatility_20",
        "volatility_ratio",
        "hour",
        "day_of_week",
        "is_weekend",
        "trader_buy_ratio",
        "trader_sell_ratio",
        "trader_net_flow",
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

    required = [c for c in _base_required_columns() if c in df.columns]
    df.dropna(subset=required, inplace=True)
    return df


def get_feature_columns_like_trainer(df: pd.DataFrame) -> List[str]:
    """
    Return numeric feature columns from a dataframe using the same exclusion rules as trainer.

    NOTE: For event_v3 inference, prefer the canonical trainer schema from
    models/feature_columns_event_v3.json to avoid column drift.
    """
    exclude = ["open", "high", "low", "close", "volume", "signal", "exchange", "symbol", "interval"]
    exclude += [col for col in df.columns if col.startswith("target_")]
    exclude += [col for col in df.columns if col.startswith("signal")]
    exclude += [col for col in df.columns if "ichimoku_chikou" in col]

    features = [
        col
        for col in df.columns
        if col not in exclude and df[col].dtype in [np.float64, np.int64, np.float32, np.int32]
    ]
    return features


def _load_feature_columns_event_v3(model_dir: str) -> List[str]:
    p = os.path.join(model_dir, "feature_columns_event_v3.json")
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    with open(p, "r", encoding="utf-8") as f:
        cols = json.load(f)
    if not isinstance(cols, list) or not cols:
        raise ValueError("feature_columns_event_v3.json invalid/empty")
    return [str(c) for c in cols]

# === 新增/修改部分：明确拼接多周期基础字段 ===

def _prefix_main_fields(src: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """将某周期的关键K线原始字段加前缀命名返回（如 tf4h_close）。"""
    # 只选取主要原始K线字段，加前缀，如tf4h_close
    ohlcv_cols = ["open", "high", "low", "close", "volume"]
    keep = [c for c in ohlcv_cols if c in src.columns]
    mapping = {c: f"{prefix}{c}" for c in keep}
    df = src[keep].rename(columns=mapping)
    return df

def build_multi_tf_feature_df(
    data_dir: str,
    as_of_ts: Optional[str] = None,
    min_rows: int = 120,
) -> pd.DataFrame:
    """
    Build a full multi-timeframe feature DataFrame for event_v3.
    Returns a DataFrame indexed by 1h timestamps.
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

    df_1h_analyzed = add_technical_indicators_like_system(df_1h)
    df_1h_feat = prepare_features_like_trainer(df_1h_analyzed)
    if df_1h_feat.empty:
        raise ValueError("1h feature df empty after dropna")

    df_4h_analyzed = add_technical_indicators_like_system(df_4h_raw)
    df_4h_feat = _add_engineered_features(df_4h_analyzed)

    df_1d_analyzed = add_technical_indicators_like_system(df_1d_raw)
    df_1d_feat = _add_engineered_features(df_1d_analyzed)

    # 主要原始字段带前缀，后面主表合并
    tf4h_main = _prefix_main_fields(df_4h_feat, "tf4h_")
    tf1d_main = _prefix_main_fields(df_1d_feat, "tf1d_")

    # 其余衍生特征也带前缀
    def _prefix_df(src: pd.DataFrame, prefix: str) -> pd.DataFrame:
        # 排除基础字段（这些已经由 tf4h_main、tf1d_main 处理）
        base_ohlcv = {"open", "high", "low", "close", "volume"}
        cols = {c: f"{prefix}{c}" for c in src.columns if c not in base_ohlcv}
        renamed = src.rename(columns=cols)
        return renamed[[v for v in cols.values() if v in renamed.columns]]

    df_4h_pfx = _prefix_df(df_4h_feat, "tf4h_")
    df_1d_pfx = _prefix_df(df_1d_feat, "tf1d_")

    # 先合并主1h特征
    merged = df_1h_feat.copy()  # index: 1h ts

    # 先 merge tf4h/1d 的基础K线字段（如 tf4h_close），用merge_asof实现对齐
    merged = pd.merge_asof(
        merged.sort_index(),
        tf4h_main.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    merged = pd.merge_asof(
        merged.sort_index(),
        tf1d_main.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
    )

    # 再 merge 其余带前缀的多周期衍生特征
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

    tf_cols = [c for c in merged.columns if c.startswith(_EXTRA_TF_PREFIXES)]
    if tf_cols:
        merged[tf_cols] = merged[tf_cols].ffill()

    return merged


def build_event_v3_feature_row(
    *,
    data_dir: str,
    model_dir: Optional[str] = None,
    interval: str = "1h",
    expected_n_features: Optional[int] = None,
    as_of_ts: Optional[str] = None,
) -> FeatureBuildResult:
    """
    Build event_v3 feature row aligned to training schema (feature_columns_event_v3.json).

    Compatibility: app.py may call this without model_dir. In that case, default to
    <repo_root>/models.
    """
    if interval and interval.strip() not in SUPPORTED_INTERVALS:
        supported = ", ".join(sorted(SUPPORTED_INTERVALS))
        raise ValueError(f"unsupported interval={interval}, supported=[{supported}]")

    if not model_dir:
        model_dir = os.path.join(REPO_ROOT, "models")

    merged = build_multi_tf_feature_df(data_dir=data_dir, as_of_ts=as_of_ts)
    if merged is None or merged.empty:
        raise ValueError("event_v3 merged feature df empty")

    feature_columns = _load_feature_columns_event_v3(model_dir)

    # Inject exogenous features into the last row if the flag is enabled.
    # They are added as new columns so schema validation sees them as "extra"
    # unless the training schema already includes them.
    if os.environ.get("ENABLE_EXOG_FEATURES", "false").strip().lower() == "true":
        exog = load_exog_features(data_dir=data_dir, as_of_ts=as_of_ts)
        for col, val in exog.items():
            merged[col] = 0.0
            merged.iloc[-1, merged.columns.get_loc(col)] = val

    # Run schema validation BEFORE reindex to detect drift
    schema_result = validate_feature_schema(merged, feature_columns, warn_on_missing=True)

    # ONE-SHOT alignment: avoids DataFrame fragmentation warnings
    # Columns in schema but missing from data → filled with 0.0
    merged = merged.reindex(columns=feature_columns, fill_value=0.0)

    merged = merged.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    X = merged.values.astype(np.float32)
    if expected_n_features is not None and X.shape[1] != expected_n_features:
        raise ValueError(f"event_v3 engineered features={X.shape[1]} but model expects={expected_n_features}")

    latest_ts = (
        merged.index[-1]
        .to_pydatetime()
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    X_row = X[-1:].astype(np.float32)
    return FeatureBuildResult(
        X_row=X_row,
        feature_columns=feature_columns,
        feature_ts=latest_ts,
        schema_validation=schema_result,
    )


def build_latest_feature_row_from_klines(
    data_dir: str,
    interval: str = "1h",
    expected_n_features: Optional[int] = None,
    as_of_ts: Optional[str] = None,
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

    if as_of_ts:
        cutoff = _to_utc_dt(as_of_ts)
        df = df[df.index <= cutoff]
        if df.empty:
            raise ValueError(f"no klines <= as_of_ts={as_of_ts}")

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
