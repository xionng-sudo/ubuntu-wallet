from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Make sure we can import python-analyzer modules when running from repo root
THIS_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)
if os.path.join(REPO_ROOT, "python-analyzer") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "python-analyzer"))

from technical_analysis import TechnicalAnalyzer  # type: ignore

try:
    import requests  # type: ignore
except Exception:
    requests = None


# ----------------------------
# Utils
# ----------------------------
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


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_klines_json(path: str) -> pd.DataFrame:
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

    raise ValueError(f"Unsupported klines json format in {path}")


def ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(timezone.utc)
    else:
        df.index = df.index.tz_convert(timezone.utc)
    return df


# ----------------------------
# ML-service client (historical via as_of_ts)
# ----------------------------
@dataclass
class MLSig:
    signal: str  # LONG|SHORT|FLAT
    confidence: float
    feature_ts: str
    reasons: List[str]


class MLServiceClient:
    def __init__(self, base_url: str, timeout_s: float = 10.0):
        if requests is None:
            raise RuntimeError("requests not installed. Run: python3 -m pip install -U requests")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._cache: Dict[Tuple[str, str], MLSig] = {}  # (interval, as_of_ts) -> MLSig

    def predict(self, interval: str, as_of_ts: str) -> MLSig:
        key = (interval, as_of_ts)
        if key in self._cache:
            return self._cache[key]

        url = f"{self.base_url}/predict"
        resp = requests.post(url, json={"interval": interval, "as_of_ts": as_of_ts}, timeout=self.timeout_s)
        resp.raise_for_status()
        j = resp.json()

        reasons = list(j.get("reasons", []) or [])
        feature_ts = ""
        for r in reasons:
            if isinstance(r, str) and r.startswith("feature_ts="):
                feature_ts = r.split("=", 1)[1].strip()
                break

        out = MLSig(
            signal=str(j.get("signal", "FLAT")),
            confidence=float(j.get("confidence", 0.0)),
            feature_ts=feature_ts,
            reasons=reasons,
        )
        self._cache[key] = out
        return out


# ----------------------------
# Params / Trade
# ----------------------------
@dataclass
class Params:
    # ML thresholds (high precision)
    conf_1d: float = 0.70
    conf_4h: float = 0.70
    conf_1h: float = 0.75

    # Trend filters from 1h TA
    adx_1h_min: float = 20.0
    rsi_1h_long_max: float = 65.0
    rsi_1h_short_min: float = 35.0

    # 15m entry indicators
    ema_len_15m: int = 20
    rsi_15m_long_max: float = 55.0
    rsi_15m_short_min: float = 45.0
    atr_mult_sl: float = 1.5

    # exits (R-multiples)
    tp1_r: float = 1.0
    tp2_r: float = 2.0


@dataclass
class Trade:
    side: str  # LONG|SHORT
    entry_ts: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    exit_ts: str
    exit_price: float
    exit_reason: str
    pnl: float
    r_multiple: float
    context: str


# ----------------------------
# Indicators for 15m execution
# ----------------------------
def add_exec_indicators_15m(df15: pd.DataFrame, p: Params) -> pd.DataFrame:
    df = df15.copy()
    df["ema20"] = df["close"].ewm(span=p.ema_len_15m, adjust=False).mean()

    # RSI(14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    # ATR(14)
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    return df


def compute_ta_1h(df1h: pd.DataFrame) -> pd.DataFrame:
    df1h = ensure_utc_index(df1h)
    ta = TechnicalAnalyzer()
    return ta.analyze(df1h)


def find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


# ----------------------------
# Trend modes
# ----------------------------
def trend_mode_strict(
    ml_1d: MLSig,
    ml_4h: MLSig,
    ml_1h: MLSig,
    adx_1h: float,
    rsi_1h: float,
    p: Params,
) -> str:
    """
    Strict high-precision mode:
      - 1d,4h,1h must all align
    Returns ONLY_LONG | ONLY_SHORT | FLAT
    """
    if (
        ml_1d.signal == "LONG"
        and ml_4h.signal == "LONG"
        and ml_1h.signal == "LONG"
        and ml_1d.confidence >= p.conf_1d
        and ml_4h.confidence >= p.conf_4h
        and ml_1h.confidence >= p.conf_1h
        and adx_1h >= p.adx_1h_min
        and rsi_1h <= p.rsi_1h_long_max
    ):
        return "ONLY_LONG"

    if (
        ml_1d.signal == "SHORT"
        and ml_4h.signal == "SHORT"
        and ml_1h.signal == "SHORT"
        and ml_1d.confidence >= p.conf_1d
        and ml_4h.confidence >= p.conf_4h
        and ml_1h.confidence >= p.conf_1h
        and adx_1h >= p.adx_1h_min
        and rsi_1h >= p.rsi_1h_short_min
    ):
        return "ONLY_SHORT"

    return "FLAT"


def trend_mode_balanced(
    ml_1d: MLSig,
    ml_4h: MLSig,
    ml_1h: MLSig,
    adx_1h: float,
    rsi_1h: float,
    p: Params,
) -> str:
    """
    Balanced (high win-rate) mode with a guard:
      - 1d is the anchor trend
      - (4h OR 1h) confirms
      - BUT if 1h is strongly opposite (>= conf_1h), block entries.
    Returns ONLY_LONG | ONLY_SHORT | FLAT
    """
    strong_1h_short = (ml_1h.signal == "SHORT" and ml_1h.confidence >= p.conf_1h)
    strong_1h_long = (ml_1h.signal == "LONG" and ml_1h.confidence >= p.conf_1h)

    if ml_1d.signal == "LONG" and ml_1d.confidence >= p.conf_1d:
        if strong_1h_short:
            return "FLAT"
        ok_confirm = (
            (ml_4h.signal == "LONG" and ml_4h.confidence >= p.conf_4h)
            or (ml_1h.signal == "LONG" and ml_1h.confidence >= p.conf_1h)
        )
        if ok_confirm and adx_1h >= p.adx_1h_min and rsi_1h <= p.rsi_1h_long_max:
            return "ONLY_LONG"

    if ml_1d.signal == "SHORT" and ml_1d.confidence >= p.conf_1d:
        if strong_1h_long:
            return "FLAT"
        ok_confirm = (
            (ml_4h.signal == "SHORT" and ml_4h.confidence >= p.conf_4h)
            or (ml_1h.signal == "SHORT" and ml_1h.confidence >= p.conf_1h)
        )
        if ok_confirm and adx_1h >= p.adx_1h_min and rsi_1h >= p.rsi_1h_short_min:
            return "ONLY_SHORT"

    return "FLAT"


# ----------------------------
# Backtest (true historical)
# ----------------------------
def run_backtest(
    data_dir: str,
    start: str,
    end: str,
    ml_url: str,
    out_csv: str,
    p: Params,
    trend_mode: str,
) -> None:
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    df15 = ensure_utc_index(load_klines_json(os.path.join(data_dir, "klines_15m.json")))
    df1h = ensure_utc_index(load_klines_json(os.path.join(data_dir, "klines_1h.json")))
    if df15.empty or df1h.empty:
        raise RuntimeError("klines_15m.json or klines_1h.json is empty")

    df1h_ta = compute_ta_1h(df1h)
    adx_col = find_col(df1h_ta, ["adx", "ADX", "adx_14", "adx14"])
    rsi_col = find_col(df1h_ta, ["rsi", "RSI", "rsi_14", "rsi14"])
    if not adx_col or not rsi_col:
        raise RuntimeError(
            f"Cannot find ADX/RSI columns in technical_analysis output. "
            f"adx_col={adx_col} rsi_col={rsi_col} sample_cols={list(df1h_ta.columns)[:60]}"
        )

    df15_ind = add_exec_indicators_15m(df15, p)
    df15_ind.dropna(inplace=True)

    ml = MLServiceClient(ml_url)
    trades: List[Trade] = []

    pos: Optional[str] = None
    entry_price = 0.0
    entry_ts = ""
    sl = tp1 = tp2 = 0.0
    risk = 0.0
    tp1_taken = False
    context = ""

    hour_index = df1h.index[(df1h.index >= start_dt) & (df1h.index <= end_dt)]
    if len(hour_index) == 0:
        raise RuntimeError("No 1h bars in selected date range. Check --start/--end vs data timestamps.")

    allow_long_hours = 0
    allow_short_hours = 0

    for hour_ts in hour_index:
        as_of = iso_z(hour_ts.to_pydatetime())

        ml_1d = ml.predict("1d", as_of)
        ml_4h = ml.predict("4h", as_of)
        ml_1h = ml.predict("1h", as_of)

        sub1h = df1h_ta.loc[:hour_ts]
        if sub1h.empty:
            continue
        adx_1h = float(sub1h[adx_col].iloc[-1])
        rsi_1h = float(sub1h[rsi_col].iloc[-1])

        if trend_mode == "balanced":
            mode = trend_mode_balanced(ml_1d, ml_4h, ml_1h, adx_1h, rsi_1h, p)
        else:
            mode = trend_mode_strict(ml_1d, ml_4h, ml_1h, adx_1h, rsi_1h, p)

        if mode == "ONLY_LONG":
            allow_long_hours += 1
        elif mode == "ONLY_SHORT":
            allow_short_hours += 1

        h_start = hour_ts
        h_end = hour_ts + pd.Timedelta(minutes=59)
        hour_15m = df15_ind.loc[(df15_ind.index >= h_start) & (df15_ind.index <= h_end)]
        if hour_15m.empty:
            continue

        for ts, row in hour_15m.iterrows():
            close = float(row["close"])
            ema = float(row["ema20"])
            rsi15 = float(row["rsi14"])
            atr15 = float(row["atr14"])

            # Exit
            if pos == "LONG":
                if close <= sl:
                    exit_ts = iso_z(ts.to_pydatetime())
                    exit_price = close
                    pnl = exit_price - entry_price
                    r_mult = pnl / risk if risk > 0 else 0.0
                    trades.append(Trade("LONG", entry_ts, entry_price, sl, tp1, tp2, exit_ts, exit_price, "SL", pnl, r_mult, context))
                    pos = None
                    continue

                if (not tp1_taken) and close >= tp1:
                    tp1_taken = True
                    sl = entry_price

                if close >= tp2:
                    exit_ts = iso_z(ts.to_pydatetime())
                    exit_price = close
                    pnl = exit_price - entry_price
                    r_mult = pnl / risk if risk > 0 else 0.0
                    trades.append(Trade("LONG", entry_ts, entry_price, sl, tp1, tp2, exit_ts, exit_price, "TP2", pnl, r_mult, context))
                    pos = None
                    continue

            elif pos == "SHORT":
                if close >= sl:
                    exit_ts = iso_z(ts.to_pydatetime())
                    exit_price = close
                    pnl = entry_price - exit_price
                    r_mult = pnl / risk if risk > 0 else 0.0
                    trades.append(Trade("SHORT", entry_ts, entry_price, sl, tp1, tp2, exit_ts, exit_price, "SL", pnl, r_mult, context))
                    pos = None
                    continue

                if (not tp1_taken) and close <= tp1:
                    tp1_taken = True
                    sl = entry_price

                if close <= tp2:
                    exit_ts = iso_z(ts.to_pydatetime())
                    exit_price = close
                    pnl = entry_price - exit_price
                    r_mult = pnl / risk if risk > 0 else 0.0
                    trades.append(Trade("SHORT", entry_ts, entry_price, sl, tp1, tp2, exit_ts, exit_price, "TP2", pnl, r_mult, context))
                    pos = None
                    continue

            # Entry
            if pos is not None:
                continue

            hist = df15_ind.loc[:ts]
            if len(hist) < 2:
                continue
            rsi_prev = float(hist.iloc[-2]["rsi14"])

            if mode == "ONLY_LONG":
                near_ema = close <= ema * 1.002
                rsi_turn_up = rsi15 > rsi_prev
                rsi_ok = rsi15 < p.rsi_15m_long_max

                if near_ema and rsi_turn_up and rsi_ok:
                    pos = "LONG"
                    entry_price = close
                    entry_ts = iso_z(ts.to_pydatetime())
                    sl = entry_price - p.atr_mult_sl * atr15
                    risk = entry_price - sl
                    tp1 = entry_price + p.tp1_r * risk
                    tp2 = entry_price + p.tp2_r * risk
                    tp1_taken = False
                    context = (
                        f"as_of={as_of};trend_mode={trend_mode};mode={mode};"
                        f"1d={ml_1d.signal}:{ml_1d.confidence:.3f};"
                        f"4h={ml_4h.signal}:{ml_4h.confidence:.3f};"
                        f"1h={ml_1h.signal}:{ml_1h.confidence:.3f};"
                        f"adx={adx_1h:.2f};rsi1h={rsi_1h:.2f}"
                    )
                    continue

            if mode == "ONLY_SHORT":
                near_ema = close >= ema * 0.998
                rsi_turn_dn = rsi15 < rsi_prev
                rsi_ok = rsi15 > p.rsi_15m_short_min

                if near_ema and rsi_turn_dn and rsi_ok:
                    pos = "SHORT"
                    entry_price = close
                    entry_ts = iso_z(ts.to_pydatetime())
                    sl = entry_price + p.atr_mult_sl * atr15
                    risk = sl - entry_price
                    tp1 = entry_price - p.tp1_r * risk
                    tp2 = entry_price - p.tp2_r * risk
                    tp1_taken = False
                    context = (
                        f"as_of={as_of};trend_mode={trend_mode};mode={mode};"
                        f"1d={ml_1d.signal}:{ml_1d.confidence:.3f};"
                        f"4h={ml_4h.signal}:{ml_4h.confidence:.3f};"
                        f"1h={ml_1h.signal}:{ml_1h.confidence:.3f};"
                        f"adx={adx_1h:.2f};rsi1h={rsi_1h:.2f}"
                    )
                    continue

    # Mark-to-market if position still open at end
    if pos is not None and len(df15_ind) > 0:
        last_ts = df15_ind.index[-1]
        last_close = float(df15_ind["close"].iloc[-1])
        exit_ts = iso_z(last_ts.to_pydatetime())
        exit_price = last_close
        pnl = (exit_price - entry_price) if pos == "LONG" else (entry_price - exit_price)
        r_mult = pnl / risk if risk > 0 else 0.0
        trades.append(Trade(pos, entry_ts, entry_price, sl, tp1, tp2, exit_ts, exit_price, "EOD", pnl, r_mult, context))

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    df_out = pd.DataFrame([t.__dict__ for t in trades])
    df_out.to_csv(out_csv, index=False)

    print(f"Trend mode setting: {trend_mode}")
    print(f"Allowed hours: ONLY_LONG={allow_long_hours}, ONLY_SHORT={allow_short_hours}, total_hours={len(hour_index)}")
    print(f"Trades CSV: {out_csv}")

    if len(trades) == 0:
        print("No trades generated in the selected period.")
        return

    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    avg_r = float(np.mean([t.r_multiple for t in trades]))
    total_r = float(np.sum([t.r_multiple for t in trades]))

    print(f"Trades: {len(trades)} | WinRate: {win_rate:.2%} | AvgR: {avg_r:.3f} | TotalR: {total_r:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-timeframe TRUE historical backtest (ETHUSDT snapshot)")
    parser.add_argument("--data-dir", type=str, default=os.getenv("DATA_DIR", "./data"))
    parser.add_argument("--start", type=str, required=True, help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--ml-url", type=str, default=os.getenv("ML_URL", "http://127.0.0.1:9000"))
    parser.add_argument("--trend-mode", type=str, default="balanced", choices=["strict", "balanced"])
    parser.add_argument(
        "--out",
        type=str,
        default=os.path.join(os.getenv("DATA_DIR", "./data"), "backtest_trades_ethusdt.csv"),
        help="Output CSV path",
    )

    args = parser.parse_args()
    params = Params()
    run_backtest(args.data_dir, args.start, args.end, args.ml_url, args.out, params, args.trend_mode)


if __name__ == "__main__":
    main()
