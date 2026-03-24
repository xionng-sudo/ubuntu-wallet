# -*- coding: utf-8 -*-
"""
prediction_logger.py
- 在进程内对相同预测（按 effective_as_of_used 或 ts, symbol, interval, model_version）做去重，避免重复写入 predictions_log.jsonl
- 配置项（通过 systemd env 或环境变量设置）：
    PREDICTIONS_LOG_PATH: 根级兜底日志路径（默认 repo/data/predictions_log.jsonl）
    PREDICTIONS_LOG_DEDUPE: "1" 启用，"0"/"false" 关闭（默认启用）
    PREDICTIONS_LOG_DEDUPE_CACHE_SIZE: LRU 缓存大小，默认 4096
    PREDICTIONS_LOG_ALSO_ROOT: "1" 在写入 per-symbol 路径的同时，也额外追加写入根级路径（迁移期兼容；默认关闭）

多币种行为：
    - 若 symbol 非空，日志写入 <DATA_DIR>/<SYMBOL>/predictions_log.jsonl
    - 若 symbol 为 None，退回到根级路径 PREDICTIONS_LOG_PATH
    - PREDICTIONS_LOG_ALSO_ROOT=1 可同时写根级路径（用于迁移过渡期）
"""
import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

# 根级兜底日志路径（可由环境覆盖，用于 symbol=None 时或 PREDICTIONS_LOG_ALSO_ROOT=1 时）
_LOG_PATH = os.getenv(
    "PREDICTIONS_LOG_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.jsonl")),
)

# 去重开关：默认开启（设置 PREDICTIONS_LOG_DEDUPE=0 可关闭）
_DEDUPE_ENABLED = os.getenv("PREDICTIONS_LOG_DEDUPE", "1") not in ("0", "false", "False")

# 去重缓存大小（LRU），默认 4096
_DEDUPE_CACHE_SIZE = int(os.getenv("PREDICTIONS_LOG_DEDUPE_CACHE_SIZE", "4096"))

# 兼容双写开关：写 per-symbol 路径时是否同时写根级路径（迁移过渡期使用）
_ALSO_ROOT = os.getenv("PREDICTIONS_LOG_ALSO_ROOT", "0") not in ("0", "false", "False")

_lock = Lock()

# LRU 缓存：key -> last_seen_unix_ts。OrderedDict 保证顺序，最旧在最前面
_dedupe_cache = OrderedDict()


def _get_per_symbol_log_path(symbol: str) -> str:
    """Return per-symbol log path: <DATA_DIR>/<SYMBOL>/predictions_log.jsonl.

    Uses the DATA_DIR environment variable (or the default data/ directory next
    to the repository root) as the base directory.
    """
    base_data_dir = os.getenv(
        "DATA_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data")),
    )
    return os.path.join(base_data_dir, symbol, "predictions_log.jsonl")


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_key_from_parts(ts_iso: str, symbol: Optional[str], interval: Optional[str], model_version: Optional[str], effective_as_of: Optional[str]) -> str:
    """
    构建去重 key：
      - 优先使用 effective_as_of（如果 caller 通过 extra 或其它字段传入）
      - 否则使用 ts_iso
    key 格式: symbol|interval|effective_or_ts|model_version
    """
    use_time = effective_as_of if effective_as_of else ts_iso
    return f"{symbol or ''}|{interval or ''}|{use_time}|{model_version or ''}"


def _cache_check_and_add(key: str) -> bool:
    """
    在 _dedupe_cache 中检查 key：
      - 如果已存在，返回 True（表示已经写过，应跳过）
      - 否则添加并返回 False
    注意：调用方必须持有 _lock
    """
    if not _DEDUPE_ENABLED:
        return False

    if key in _dedupe_cache:
        # 标记为最近使用
        _dedupe_cache.move_to_end(key)
        return True

    _dedupe_cache[key] = int(time.time())
    # 裁剪超出容量的最旧项
    if len(_dedupe_cache) > _DEDUPE_CACHE_SIZE:
        _dedupe_cache.popitem(last=False)
    return False


def log_prediction(
    *,
    ts: datetime,
    symbol: Optional[str],
    interval: Optional[str],
    proba_long: Optional[float],
    proba_short: Optional[float],
    proba_flat: Optional[float],
    signal: str,
    confidence: float,
    model_version: str,
    active_model: str,
    # Calibration fields
    cal_proba_long: Optional[float] = None,
    cal_proba_short: Optional[float] = None,
    cal_proba_flat: Optional[float] = None,
    calibrated_confidence: Optional[float] = None,
    calibration_method: Optional[str] = None,
    # Decision thresholds used
    threshold_long: Optional[float] = None,
    threshold_short: Optional[float] = None,
    # Multi-timeframe context
    trend_4h: Optional[str] = None,
    trend_1d: Optional[str] = None,
    # 额外字段（可能包含 effective_as_of_used）
    extra: Optional[Dict[str, Any]] = None,
    # 显式指定日志路径（可选）；未指定时按 symbol 自动推断
    log_path: Optional[str] = None,
) -> None:
    """
    将一次预测追加到 JSONL 日志文件（进程内去重）。

    日志路径解析优先级：
    1. 若 log_path 显式指定，使用该路径。
    2. 若 symbol 非空，写入 <DATA_DIR>/<SYMBOL>/predictions_log.jsonl（per-symbol）。
    3. 否则退回到根级路径 _LOG_PATH（由 PREDICTIONS_LOG_PATH 环境变量控制）。

    若 PREDICTIONS_LOG_ALSO_ROOT=1，per-symbol 写入后还额外追加写入根级路径。

    去重 key 优先用 extra 中的 "effective_as_of_used"（如果存在），否则用 ts。
    """
    ts_iso = _to_utc_iso(ts)
    rec: Dict[str, Any] = {
        "ts": ts_iso,
        "symbol": symbol,
        "interval": interval,
        "proba_long": round(proba_long, 6) if proba_long is not None else None,
        "proba_short": round(proba_short, 6) if proba_short is not None else None,
        "proba_flat": round(proba_flat, 6) if proba_flat is not None else None,
        "signal": signal,
        "confidence": round(confidence, 6),
        "model_version": model_version,
        "active_model": active_model,
    }

    # 校准字段（有则写）
    if cal_proba_long is not None:
        rec["cal_proba_long"] = round(cal_proba_long, 6)
    if cal_proba_short is not None:
        rec["cal_proba_short"] = round(cal_proba_short, 6)
    if cal_proba_flat is not None:
        rec["cal_proba_flat"] = round(cal_proba_flat, 6)
    if calibrated_confidence is not None:
        rec["calibrated_confidence"] = round(calibrated_confidence, 6)
    if calibration_method is not None:
        rec["calibration_method"] = calibration_method

    # 阈值上下文
    if threshold_long is not None:
        rec["threshold_long"] = threshold_long
    if threshold_short is not None:
        rec["threshold_short"] = threshold_short

    # 多周期上下文
    if trend_4h is not None:
        rec["trend_4h"] = trend_4h
    if trend_1d is not None:
        rec["trend_1d"] = trend_1d

    # 合并 extra
    effective_as_of = None
    if extra:
        rec.update(extra)
        # 如果 extra 里有 effective_as_of_used（或 effective_as_of），优先提取它
        if "effective_as_of_used" in extra and extra["effective_as_of_used"]:
            effective_as_of = extra["effective_as_of_used"]
        elif "effective_as_of" in extra and extra["effective_as_of"]:
            effective_as_of = extra["effective_as_of"]

    line = json.dumps(rec, ensure_ascii=False)

    # 解析目标日志路径
    if log_path is not None:
        effective_path = log_path
    elif symbol:
        effective_path = _get_per_symbol_log_path(symbol)
    else:
        effective_path = _LOG_PATH

    # 构建去重 key（优先 effective_as_of）
    key = _make_key_from_parts(ts_iso, symbol, interval, model_version, effective_as_of)

    with _lock:
        # 已存在则跳过写入
        if _cache_check_and_add(key):
            return

        # 追加写入目标路径（per-symbol 或根级）
        os.makedirs(os.path.dirname(effective_path), exist_ok=True)
        with open(effective_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        # 兼容双写：PREDICTIONS_LOG_ALSO_ROOT=1 时额外追加写入根级路径
        if _ALSO_ROOT and effective_path != _LOG_PATH:
            os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
