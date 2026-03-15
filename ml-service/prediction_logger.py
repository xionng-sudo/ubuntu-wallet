import json
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

# 默认路径：项目根目录下 data/predictions_log.jsonl
_LOG_PATH = os.getenv(
    "PREDICTIONS_LOG_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.jsonl")),
)

_lock = Lock()


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    追加写入一条预测日志到 JSONL 文件。
    ts: 对应的 as_of_ts（或 feature_ts），建议用 feature_ts
    symbol: 交易对，例如 "BTCUSDT"（你现在可能是 None，后面可以慢慢接 Go 的 symbol）
    interval: "1h" / "4h" / "1d"（目前你主要用 1h）
    """
    rec: Dict[str, Any] = {
        "ts": _to_utc_iso(ts),
        "symbol": symbol,
        "interval": interval,
        "proba_long": proba_long,
        "proba_short": proba_short,
        "proba_flat": proba_flat,
        "signal": signal,
        "confidence": confidence,
        "model_version": model_version,
        "active_model": active_model,
    }
    if extra:
        rec.update(extra)

    line = json.dumps(rec, ensure_ascii=False)

    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    with _lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
