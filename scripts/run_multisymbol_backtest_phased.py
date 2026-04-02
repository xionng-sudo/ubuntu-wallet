#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "data" / "reports"
REPORT_ROOT.mkdir(parents=True, exist_ok=True)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]

# phase1 grid
PHASE1_THRESHOLDS = [0.50, 0.54, 0.57, 0.60]
PHASE1_MT_MODES = ["daily_guard", "off"]
PHASE1_TP = [0.0150, 0.0170, 0.0200]
PHASE1_SL = [0.0070, 0.0090, 0.0110]
PHASE1_HORIZON = [6, 8]

# phase2 refinement grid (tp/sl/h) but threshold+mt_mode come from phase1_best.json
PHASE2_TPS = [0.0150, 0.0170, 0.0200, 0.0225]
PHASE2_SLS = [0.0070, 0.0090, 0.0100, 0.0110]
PHASE2_HORIZONS = [6, 8, 12]

USE_TWO_STAGE_TP = False
TP1_RATIO = 0.70
TP1_SIZE = 0.60
BE_OFFSET = 0.002


@dataclass
class ParsedBest:
    status: str
    symbol: str
    mt_filter_mode: str
    threshold: float
    tp: float
    sl: float
    horizon: int
    stdout_path: str

    n_trade: int = 0
    n_long: int = 0
    n_short: int = 0
    signals_per_week: float = 0.0
    win_rate: float = 0.0
    avg_ret_pct: float = 0.0
    profit_factor: Optional[float] = None
    mdd_trade_seq_pct: float = 0.0
    mdd_hourly_pct: float = 0.0
    mdd_daily_pct: float = 0.0
    max_consec_losses: int = 0
    reject_reasons: str = ""
    stderr: str = ""


def _run(cmd: List[str], cwd: Path) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _parse_best_from_stdout(
    *,
    symbol: str,
    mt_mode: str,
    stdout: str,
    stderr: str,
    stdout_path: Path,
    returncode: int,
) -> ParsedBest:
    row = ParsedBest(
        status="ok" if returncode == 0 else "error",
        symbol=symbol,
        mt_filter_mode=mt_mode,
        threshold=float("nan"),
        tp=float("nan"),
        sl=float("nan"),
        horizon=0,
        stdout_path=str(stdout_path),
        stderr=stderr.strip(),
    )
    if returncode != 0:
        return row

    # parse BEST CONFIG line
    # example: threshold=0.50 tp=1.50% sl=0.70% fee/side=...
    m_best = re.search(r"threshold=([0-9.]+)\s+tp=([0-9.]+)%\s+sl=([0-9.]+)%.*?horizon=(\d+)", stdout)
    if not m_best:
        row.status = "error"
        row.stderr = (row.stderr + "\n" if row.stderr else "") + "parse_error: BEST CONFIG not found"
        return row

    row.threshold = float(m_best.group(1))
    row.tp = float(m_best.group(2)) / 100.0
    row.sl = float(m_best.group(3)) / 100.0
    row.horizon = int(m_best.group(4))

    # metrics line
    m_metrics = re.search(
        r"metrics:\s+signals/week=([0-9.]+)\s+n_trade=(\d+)\s+\(long=(\d+)\s+short=(\d+)\)\s+"
        r"TP=(\d+)\s+SL=(\d+)\s+TO=(\d+)\s+win_rate=([0-9.]+)\s+avg_ret=([+-]?[0-9.]+)%\s+profit_factor=([A-Za-z0-9.+-]+)",
        stdout,
    )
    if m_metrics:
        row.signals_per_week = float(m_metrics.group(1))
        row.n_trade = int(m_metrics.group(2))
        row.n_long = int(m_metrics.group(3))
        row.n_short = int(m_metrics.group(4))
        row.win_rate = float(m_metrics.group(8))
        row.avg_ret_pct = float(m_metrics.group(9))
        pf_raw = m_metrics.group(10)
        row.profit_factor = None if pf_raw.lower() == "inf" else float(pf_raw)

    m_risk = re.search(
        r"risk/realism:\s+MDD\(trade_seq\)=([0-9.]+)%\s+MDD\(hourly\)=([0-9.]+)%\s+MDD\(daily\)=([0-9.]+)%\s+max_consec_losses=(\d+)",
        stdout,
    )
    if m_risk:
        row.mdd_trade_seq_pct = float(m_risk.group(1))
        row.mdd_hourly_pct = float(m_risk.group(2))
        row.mdd_daily_pct = float(m_risk.group(3))
        row.max_consec_losses = int(m_risk.group(4))

    m_reject = re.search(r"mt_reject_reasons=(\{.*?\})", stdout)
    if m_reject:
        row.reject_reasons = m_reject.group(1)

    return row


def _format_range(vals: List[float]) -> str:
    # expect monotonic list with fixed step
    if not vals:
        raise ValueError("empty grid")
    if len(vals) == 1:
        a = b = vals[0]
        step = 0.01
    else:
        a = vals[0]
        b = vals[-1]
        step = round(vals[1] - vals[0], 10)
    return f"{a:.4f}:{b:.4f}:{step:.4f}"


def _format_threshold_range(vals: List[float]) -> str:
    if not vals:
        raise ValueError("empty thresholds")
    if len(vals) == 1:
        a = b = vals[0]
        step = 0.01
    else:
        a = vals[0]
        b = vals[-1]
        step = round(vals[1] - vals[0], 10)
    return f"{a:.2f}:{b:.2f}:{step:.2f}"


def _aggregate(rows: List[ParsedBest]) -> Dict[str, Dict]:
    grouped: Dict[str, List[ParsedBest]] = {}
    for r in rows:
        if r.status != "ok":
            continue
        grouped.setdefault(r.mt_filter_mode, []).append(r)

    summary = {}
    for mode, xs in grouped.items():
        summary[mode] = {
            "symbols": len(xs),
            "total_n_trade": sum(x.n_trade for x in xs),
            "avg_win_rate": round(sum(x.win_rate for x in xs) / len(xs), 6) if xs else 0.0,
            "avg_avg_ret_pct": round(sum(x.avg_ret_pct for x in xs) / len(xs), 6) if xs else 0.0,
            "avg_mdd_daily_pct": round(sum(x.mdd_daily_pct for x in xs) / len(xs), 6) if xs else 0.0,
        }
    return summary


def _write_md_best(rows: List[ParsedBest], symbols: List[str], path: Path) -> None:
    # One line per symbol (best only)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Multi-Symbol Backtest (Best per Symbol)\n\n")
        f.write("| symbol | mt_mode | thr | tp | sl | horizon | n_trade | win_rate | avg_ret_pct | mdd_trade_seq_pct | max_consec_losses |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        by_sym = {r.symbol: r for r in rows if r.status == "ok"}
        for s in symbols:
            r = by_sym.get(s)
            if not r:
                continue
            f.write(
                f"| {r.symbol} | {r.mt_filter_mode} | {r.threshold:.2f} | {r.tp:.4f} | {r.sl:.4f} | {r.horizon} "
                f"| {r.n_trade} | {r.win_rate:.3f} | {r.avg_ret_pct:.3f} | {r.mdd_trade_seq_pct:.2f} | {r.max_consec_losses} |\n"
            )


def _run_symbol_best(
    *,
    symbol: str,
    since: str,
    until: str,
    mt_mode: str,
    thresholds: List[float],
    tp_grid: List[float],
    sl_grid: List[float],
    horizon: int,
    sleep_ms: int,
    position_mode: str,
    objective: str,
    out_dir: Path,
    pred_cache: str,
    pred_cache_dir: str,
    pred_cache_format: str,
    pred_cache_key: str,
    retries: int,
    retry_backoff_s: float,
) -> ParsedBest:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / f"{symbol}__phasegrid__mt-{mt_mode}__h{horizon}.txt"

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "backtest_event_v3_http.py"),
        "--data-dir", str(ROOT / "data" / symbol),
        "--interval", "1h",
        "--since", since,
        "--until", until,
        "--thresholds", _format_threshold_range(thresholds),
        "--tp-grid", _format_range(tp_grid),
        "--sl-grid", _format_range(sl_grid),
        "--horizon-bars", str(horizon),
        "--timeout-exit", "close",
        "--position-mode", position_mode,
        "--objective", objective,
        "--side-source", "probs",
        "--mt-filter-mode", mt_mode,
        "--min-signals-per-week", "0",
        "--sleep-ms", str(sleep_ms),
        "--debug-best",
        "--pred-cache", pred_cache,
        "--pred-cache-dir", pred_cache_dir,
        "--pred-cache-format", pred_cache_format,
        "--pred-cache-key", pred_cache_key,
    ]

    if USE_TWO_STAGE_TP:
        cmd.extend(
            [
                "--use-two-stage-tp",
                "--tp1-ratio", f"{TP1_RATIO:.2f}",
                "--tp1-size", f"{TP1_SIZE:.2f}",
                "--be-offset", f"{BE_OFFSET:.4f}",
            ]
        )

    attempt_total = max(1, int(retries) + 1)
    last_rc, last_stdout, last_stderr = 1, "", ""
    for attempt in range(1, attempt_total + 1):
        rc, stdout, stderr = _run(cmd, cwd=ROOT)
        last_rc, last_stdout, last_stderr = rc, stdout, stderr
        stdout_path.write_text(stdout, encoding="utf-8")

        if rc == 0:
            return _parse_best_from_stdout(
                symbol=symbol, mt_mode=mt_mode, stdout=stdout, stderr=stderr, stdout_path=stdout_path, returncode=rc
            )

        combined = (stdout + "\n" + stderr).lower()
        transient = any(
            x in combined
            for x in [
                "precomputing predictions",
                "requests.exceptions",
                "connection refused",
                "connection reset",
                "read timed out",
                "timeout",
                "temporarily unavailable",
                "502",
                "503",
                "504",
            ]
        )
        if (attempt < attempt_total) and transient:
            sleep_s = float(retry_backoff_s) * (2 ** (attempt - 1))
            print(f"[retry] {symbol} mt={mt_mode} h={horizon} attempt={attempt}/{attempt_total} rc={rc} sleep={sleep_s:.1f}s", flush=True)
            time.sleep(sleep_s)
            continue
        break

    return _parse_best_from_stdout(
        symbol=symbol,
        mt_mode=mt_mode,
        stdout=last_stdout,
        stderr=last_stderr,
        stdout_path=stdout_path,
        returncode=last_rc,
    )


def run_phase1_by_symbol(args) -> int:
    out_dir = REPORT_ROOT / "backtest_phase1_stdout"
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols: List[str] = args.symbols

    # For phase1 we need choose best between mt modes; we run 2 jobs per symbol (daily_guard/off), each with full grid.
    # Horizons: because horizon isn't a grid input in backtest script (it's a single arg),
    # we run separate jobs per horizon too; still small: symbols * mt_modes * horizons = 7*2*2=28 jobs.
    tasks = []
    for symbol in symbols:
        for mt_mode in PHASE1_MT_MODES:
            for h in PHASE1_HORIZON:
                tasks.append((symbol, mt_mode, h))

    total = len(tasks)
    print(f"[phase1] by_symbol jobs={total}, workers={args.workers}", flush=True)

    rows: List[ParsedBest] = []

    def _one(job):
        symbol, mt_mode, h = job
        return _run_symbol_best(
            symbol=symbol,
            since=args.since,
            until=args.until,
            mt_mode=mt_mode,
            thresholds=PHASE1_THRESHOLDS,
            tp_grid=PHASE1_TP,
            sl_grid=PHASE1_SL,
            horizon=h,
            sleep_ms=args.sleep_ms,
            position_mode=args.position_mode,
            objective=args.objective,
            out_dir=out_dir,
            pred_cache=args.pred_cache,
            pred_cache_dir=args.pred_cache_dir,
            pred_cache_format=args.pred_cache_format,
            pred_cache_key=args.pred_cache_key,
            retries=args.retries,
            retry_backoff_s=args.retry_backoff_s,
        )

    if args.workers <= 1:
        for idx, job in enumerate(tasks, 1):
            symbol, mt_mode, h = job
            print(f"[phase1] {idx}/{total} {symbol} mt={mt_mode} h={h}", flush=True)
            rows.append(_one(job))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            fut_map = {}
            for idx, job in enumerate(tasks, 1):
                symbol, mt_mode, h = job
                print(f"[phase1-submit] {idx}/{total} {symbol} mt={mt_mode} h={h}", flush=True)
                fut_map[ex.submit(_one, job)] = job

            done = 0
            for fut in as_completed(fut_map):
                done += 1
                symbol, mt_mode, h = fut_map[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    r = ParsedBest(
                        status="error",
                        symbol=symbol,
                        mt_filter_mode=mt_mode,
                        threshold=float("nan"),
                        tp=float("nan"),
                        sl=float("nan"),
                        horizon=h,
                        stdout_path="",
                        stderr=f"executor_exception: {e}",
                    )
                print(
                    f"[phase1-done] {done}/{total} {symbol} mt={mt_mode} h={h} "
                    f"status={r.status} n_trade={r.n_trade} win_rate={r.win_rate:.3f} avg_ret_pct={r.avg_ret_pct:.3f}",
                    flush=True,
                )
                rows.append(r)

    # pick best per symbol across (mt_mode, horizon)
    best_by_symbol: Dict[str, ParsedBest] = {}
    for s in symbols:
        ok = [r for r in rows if r.symbol == s and r.status == "ok"]
        if not ok:
            continue
        # use objective score from backtest script? not printed; use a simple proxy:
        # maximize avg_ret_pct - 0.5*mdd_daily_pct, then win_rate, then n_trade
        ok.sort(key=lambda r: (r.avg_ret_pct - 0.5 * r.mdd_daily_pct, r.win_rate, r.n_trade), reverse=True)
        best_by_symbol[s] = ok[0]

    best_path = REPORT_ROOT / "backtest_phase1_best.json"
    with open(best_path, "w", encoding="utf-8") as f:
        json.dump({k: asdict(v) for k, v in best_by_symbol.items()}, f, indent=2)

    agg_path = REPORT_ROOT / "backtest_phase1_aggregate.json"
    with open(agg_path, "w", encoding="utf-8") as f:
        json.dump(_aggregate(list(best_by_symbol.values())), f, indent=2)

    md_path = REPORT_ROOT / "backtest_phase1_top.md"
    _write_md_best(list(best_by_symbol.values()), symbols, md_path)

    print(f"[done] wrote {best_path}")
    print(f"[done] wrote {agg_path}")
    print(f"[done] wrote {md_path}")
    return 0


def run_phase2_by_symbol(args) -> int:
    best_path = REPORT_ROOT / "backtest_phase1_best.json"
    if not best_path.exists():
        print(f"ERROR: {best_path} not found. Run --phase phase1 first.", flush=True)
        return 1

    phase1_best = json.loads(best_path.read_text(encoding="utf-8"))
    out_dir = REPORT_ROOT / "backtest_phase2_stdout"
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols: List[str] = args.symbols
    tasks = []
    for s in symbols:
        if s not in phase1_best:
            continue
        tasks.append(s)

    total = len(tasks)
    print(f"[phase2] by_symbol jobs={total}, workers={args.workers}", flush=True)

    rows: List[ParsedBest] = []

    def _one(symbol: str) -> ParsedBest:
        thr = float(phase1_best[symbol]["threshold"])
        mt_mode = str(phase1_best[symbol]["mt_filter_mode"])

        # phase2 still has horizon as a grid; run separate job per horizon and take best
        best_local: Optional[ParsedBest] = None
        for h in PHASE2_HORIZONS:
            r = _run_symbol_best(
                symbol=symbol,
                since=args.since,
                until=args.until,
                mt_mode=mt_mode,
                thresholds=[thr],
                tp_grid=PHASE2_TPS,
                sl_grid=PHASE2_SLS,
                horizon=h,
                sleep_ms=args.sleep_ms,
                position_mode=args.position_mode,
                objective=args.objective,
                out_dir=out_dir,
                pred_cache=args.pred_cache,
                pred_cache_dir=args.pred_cache_dir,
                pred_cache_format=args.pred_cache_format,
                pred_cache_key=args.pred_cache_key,
                retries=args.retries,
                retry_backoff_s=args.retry_backoff_s,
            )
            if r.status != "ok":
                continue
            if best_local is None:
                best_local = r
            else:
                cur = (r.avg_ret_pct - 0.5 * r.mdd_daily_pct, r.win_rate, r.n_trade)
                prv = (best_local.avg_ret_pct - 0.5 * best_local.mdd_daily_pct, best_local.win_rate, best_local.n_trade)
                if cur > prv:
                    best_local = r

        return best_local or ParsedBest(
            status="error",
            symbol=symbol,
            mt_filter_mode=mt_mode,
            threshold=thr,
            tp=float("nan"),
            sl=float("nan"),
            horizon=0,
            stdout_path="",
            stderr="phase2_all_horizons_failed",
        )

    if args.workers <= 1:
        for idx, s in enumerate(tasks, 1):
            print(f"[phase2] {idx}/{total} {s}", flush=True)
            rows.append(_one(s))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            fut_map = {}
            for idx, s in enumerate(tasks, 1):
                print(f"[phase2-submit] {idx}/{total} {s}", flush=True)
                fut_map[ex.submit(_one, s)] = s
            done = 0
            for fut in as_completed(fut_map):
                done += 1
                s = fut_map[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    r = ParsedBest(
                        status="error",
                        symbol=s,
                        mt_filter_mode="",
                        threshold=float("nan"),
                        tp=float("nan"),
                        sl=float("nan"),
                        horizon=0,
                        stdout_path="",
                        stderr=f"executor_exception: {e}",
                    )
                print(
                    f"[phase2-done] {done}/{total} {s} status={r.status} n_trade={r.n_trade} win_rate={r.win_rate:.3f} avg_ret_pct={r.avg_ret_pct:.3f}",
                    flush=True,
                )
                rows.append(r)

    best_by_symbol = {r.symbol: r for r in rows if r.status == "ok"}

    out_best = REPORT_ROOT / "backtest_phase2_best.json"
    with open(out_best, "w", encoding="utf-8") as f:
        json.dump({k: asdict(v) for k, v in best_by_symbol.items()}, f, indent=2)

    agg_path = REPORT_ROOT / "backtest_phase2_aggregate.json"
    with open(agg_path, "w", encoding="utf-8") as f:
        json.dump(_aggregate(list(best_by_symbol.values())), f, indent=2)

    md_path = REPORT_ROOT / "backtest_phase2_top.md"
    _write_md_best(list(best_by_symbol.values()), symbols, md_path)

    print(f"[done] wrote {out_best}")
    print(f"[done] wrote {agg_path}")
    print(f"[done] wrote {md_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phased multi-symbol backtest sweep (fast by-symbol runner)")
    ap.add_argument("--phase", choices=["phase1", "phase2"], required=True)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols")
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--sleep-ms", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--position-mode", choices=["stack", "single"], default="stack")
    ap.add_argument("--objective", choices=["pf", "avg_ret", "avg_ret_mdd_daily", "avg_ret_mdd_hourly"], default="avg_ret_mdd_daily")

    ap.add_argument("--pred-cache", choices=["on", "off"], default="on")
    ap.add_argument("--pred-cache-dir", default=str(ROOT / "data" / "pred_cache"))
    ap.add_argument("--pred-cache-format", choices=["jsonl"], default="jsonl")
    ap.add_argument("--pred-cache-key", choices=["interval_window", "model_interval_window"], default="model_interval_window")

    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--retry-backoff-s", type=float, default=2.0)

    args = ap.parse_args()
    args.symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    args.workers = max(1, int(args.workers))

    if args.phase == "phase1":
        return run_phase1_by_symbol(args)
    return run_phase2_by_symbol(args)


if __name__ == "__main__":
    raise SystemExit(main())
