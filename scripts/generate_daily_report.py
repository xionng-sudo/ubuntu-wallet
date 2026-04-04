#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_daily_report.py
========================
Generate a structured daily evaluation report from prediction logs.

Per-symbol output files:
  data/<SYMBOL>/reports/daily_eval_YYYY-MM-DD.json
  data/<SYMBOL>/reports/daily_eval_YYYY-MM-DD.md

Primary-symbol compatibility fallback:
  data/reports/daily_eval_YYYY-MM-DD.json
  data/reports/daily_eval_YYYY-MM-DD.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from backtest_event_v3_http import (  # noqa: E402
    load_klines_1h,
    simulate_trade,
    compute_metrics,
    _trend_series,
)
from signal_logic import (  # noqa: E402
    normalize_log_prediction,
    select_effective_probs,
    decide_side,
    apply_mt_filter_common,
    normalize_mt_mode,
)


def _get_symbol_paths_module():
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)
    import symbol_paths  # type: ignore[import]
    return symbol_paths


def _to_utc_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_predictions_for_day(
    path: str,
    symbol: Optional[str],
    interval: str,
    active_model: Optional[str],
    day_start: datetime,
    day_end: datetime,
) -> List[Dict[str, Any]]:
    """Load predictions whose ts falls within [day_start, day_end)."""
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue

            if symbol is not None and j.get("symbol") != symbol:
                continue
            if j.get("interval") != interval:
                continue
            if active_model and j.get("active_model") != active_model:
                continue

            ts_raw = j.get("ts")
            if not ts_raw:
                continue
            try:
                ts = _to_utc_dt(str(ts_raw))
            except Exception:
                continue

            if ts < day_start or ts >= day_end:
                continue

            out.append(
                {
                    "ts": ts,
                    "raw": j,
                }
            )

    out.sort(key=lambda x: x["ts"])
    return out


def _pick_model_version(preds: List[Dict[str, Any]], override: Optional[str]) -> str:
    if override:
        return override
    for p in reversed(preds):
        raw = p.get("raw") or {}
        mv = raw.get("model_version")
        if mv:
            return str(mv)
    return "unknown"


def _build_report(
    *,
    date_str: str,
    preds: List[Dict[str, Any]],
    klines: List[Dict],
    idx_by_ts: Dict,
    ts_4h: List,
    trend_4h_list: List,
    ts_1d: List,
    trend_1d_list: List,
    threshold: float,
    tp_pct: float,
    sl_pct: float,
    fee: float,
    slippage: float,
    horizon: int,
    tie_breaker: str,
    timeout_exit: str,
    mt_filter: bool,
    mt_filter_mode: str,
    model_version_override: Optional[str],
) -> Dict[str, Any]:
    def trend_4h_at(ts) -> str:
        idx = bisect_right(ts_4h, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_4h_list[idx]

    def trend_1d_at(ts) -> str:
        idx = bisect_right(ts_1d, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_1d_list[idx]

    model_version = _pick_model_version(preds, model_version_override)

    n_total = len(preds)
    trades = []

    skipped_no_kline = 0
    skipped_flat_model = 0
    filtered_mt = 0
    passed_mt = 0

    initial_side_counts = Counter()
    final_side_counts = Counter()
    trend_4h_counts = Counter()
    trend_1d_counts = Counter()
    mt_reject_reasons = Counter()
    selected_prob_sources = Counter()

    for p in preds:
        ts = p["ts"]
        raw = p["raw"]

        i = idx_by_ts.get(ts)
        if i is None or i + horizon >= len(klines):
            skipped_no_kline += 1
            continue

        snap = normalize_log_prediction(raw)
        eff_p_long, eff_p_short, eff_p_flat, prob_source = select_effective_probs(snap)
        selected_prob_sources[prob_source] += 1

        side_initial = decide_side(eff_p_long, eff_p_short, threshold)
        initial_side_counts[side_initial] += 1

        if side_initial == "FLAT":
            skipped_flat_model += 1
            final_side_counts["FLAT"] += 1
            continue

        side_final = side_initial

        if mt_filter:
            t4 = trend_4h_at(ts)
            t1 = trend_1d_at(ts)
            trend_4h_counts[t4] += 1
            trend_1d_counts[t1] += 1

            side_final = apply_mt_filter_common(
                side=side_initial,
                t4=t4,
                t1d=t1,
                mode=mt_filter_mode,
                mt_reject_reasons=mt_reject_reasons,
            )

        final_side_counts[side_final] += 1

        if side_final == "FLAT":
            if mt_filter:
                filtered_mt += 1
            continue

        passed_mt += 1

        tr = simulate_trade(
            klines=klines,
            i=i,
            side=side_final,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            fee_per_side=fee,
            slippage_per_side=slippage,
            horizon_bars=horizon,
            tie_breaker=tie_breaker,
            timeout_exit=timeout_exit,
        )
        if tr.outcome == "NO_TRADE":
            continue
        trades.append(tr)

    n_trades = len(trades)
    coverage = n_trades / n_total if n_total > 0 else 0.0

    report: Dict[str, Any] = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model_version": model_version,
        "params": {
            "threshold": threshold,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "fee_per_side": fee,
            "slippage_per_side": slippage,
            "horizon_bars": horizon,
            "mt_filter": mt_filter,
            "mt_filter_mode": (normalize_mt_mode(mt_filter_mode) if mt_filter else "off"),
        },
        "signal_stats": {
            "total_predictions": n_total,
            "skipped_no_kline": skipped_no_kline,
            "skipped_flat_model": skipped_flat_model,
            "filtered_mt": filtered_mt,
            "passed_mt": passed_mt,
            "n_trades": n_trades,
            "coverage": round(coverage, 4),
        },
        "debug_mt": {
            "initial_side_counts": dict(initial_side_counts),
            "final_side_counts": dict(final_side_counts),
            "trend_4h_counts": dict(trend_4h_counts),
            "trend_1d_counts": dict(trend_1d_counts),
            "mt_reject_reasons": dict(mt_reject_reasons),
            "selected_prob_sources": dict(selected_prob_sources),
        },
    }

    if not trades:
        report["strategy_metrics"] = None
        report["direction_breakdown"] = {"long": None, "short": None}
        return report

    m = compute_metrics(trades, total_bars=len(klines))

    long_trades = [t for t in trades if t.side == "LONG"]
    short_trades = [t for t in trades if t.side == "SHORT"]

    def _dir_stats(ts_list: List) -> Optional[Dict[str, Any]]:
        if not ts_list:
            return None
        wins = sum(1 for t in ts_list if t.ret_net > 0)
        tp_c = sum(1 for t in ts_list if t.outcome == "TP")
        sl_c = sum(1 for t in ts_list if t.outcome == "SL")
        to_c = sum(1 for t in ts_list if t.outcome == "TIMEOUT")
        rets = [t.ret_net for t in ts_list]
        return {
            "n": len(ts_list),
            "win_rate": round(wins / len(ts_list), 4),
            "avg_return_pct": round((sum(rets) / len(rets)) * 100, 4),
            "tp": tp_c,
            "sl": sl_c,
            "timeout": to_c,
        }

    report["strategy_metrics"] = {
        "n_trade": m.n_trade,
        "n_long": m.n_long,
        "n_short": m.n_short,
        "precision": round(m.win_rate, 4),
        "win_rate": round(m.win_rate, 4),
        "avg_return_pct": round(m.avg_ret * 100, 4),
        "mdd_trade_seq_pct": round(m.mdd_trade_seq * 100, 4),
        "mdd_hourly_pct": round(m.mdd_hourly * 100, 4),
        "mdd_daily_pct": round(m.mdd_daily * 100, 4),
        "profit_factor": round(m.profit_factor, 4) if m.profit_factor != float("inf") else None,
        "tp": m.tp,
        "sl": m.sl,
        "timeout": m.timeout,
        "avg_ret_tp_pct": round(m.avg_ret_tp * 100, 4),
        "avg_ret_sl_pct": round(m.avg_ret_sl * 100, 4),
        "avg_ret_timeout_pct": round(m.avg_ret_to * 100, 4),
        "max_consec_losses": m.max_consec_losses,
    }

    report["direction_breakdown"] = {
        "long": _dir_stats(long_trades),
        "short": _dir_stats(short_trades),
    }

    return report


def _render_markdown(report: Dict[str, Any]) -> str:
    date = report["date"]
    gen = report["generated_at"]
    mv = report["model_version"]
    ss = report["signal_stats"]
    params = report["params"]
    sm = report.get("strategy_metrics")
    db = report.get("direction_breakdown", {})

    lines = [
        f"# Daily Evaluation Report: {date}",
        "",
        f"_Generated at: {gen}_",
        "",
        "## Model",
        "",
        f"- **model_version**: `{mv}`",
        "",
        "## Parameters",
        "",
        f"- threshold: `{params['threshold']}`",
        f"- tp: `{params['tp_pct']*100:.2f}%`",
        f"- sl: `{params['sl_pct']*100:.2f}%`",
        f"- fee/side: `{params['fee_per_side']*100:.4f}%`",
        f"- horizon_bars: `{params['horizon_bars']}`",
        f"- mt_filter: `{params['mt_filter']}`",
        f"- mt_filter_mode: `{params.get('mt_filter_mode', 'unknown')}`",
        "",
        "## Signal Stats",
        "",
        f"| Metric | Value |",
        f"| --- | --- |",
        f"| total_predictions | {ss['total_predictions']} |",
        f"| skipped_no_kline  | {ss['skipped_no_kline']} |",
        f"| skipped_flat_model | {ss['skipped_flat_model']} |",
        f"| filtered_mt        | {ss['filtered_mt']} |",
        f"| passed_mt          | {ss['passed_mt']} |",
        f"| n_trades          | {ss['n_trades']} |",
        f"| coverage          | {ss['coverage']:.4f} |",
        "",
    ]

    if sm is None:
        lines += ["## Strategy Metrics", "", "_No trades generated for this period._", ""]
    else:
        lines += [
            "## Strategy Metrics",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| n_trade | {sm['n_trade']} |",
            f"| n_long | {sm['n_long']} |",
            f"| n_short | {sm['n_short']} |",
            f"| precision / win_rate | {sm['precision']:.4f} |",
            f"| avg_return | {sm['avg_return_pct']:+.4f}% |",
            f"| mdd_trade_seq | {sm['mdd_trade_seq_pct']:.4f}% |",
            f"| mdd_hourly | {sm['mdd_hourly_pct']:.4f}% |",
            f"| mdd_daily | {sm['mdd_daily_pct']:.4f}% |",
            f"| profit_factor | {sm['profit_factor'] if sm['profit_factor'] is not None else '∞'} |",
            f"| max_consec_losses | {sm['max_consec_losses']} |",
            "",
            "### TP / SL / TIMEOUT Distribution",
            "",
            f"| Outcome | Count | Avg Return |",
            f"| --- | --- | --- |",
            f"| TP      | {sm['tp']} | {sm['avg_ret_tp_pct']:+.4f}% |",
            f"| SL      | {sm['sl']} | {sm['avg_ret_sl_pct']:+.4f}% |",
            f"| TIMEOUT | {sm['timeout']} | {sm['avg_ret_timeout_pct']:+.4f}% |",
            "",
        ]

    lines += ["## Direction Breakdown", ""]
    for direction in ("long", "short"):
        ds = (db or {}).get(direction)
        lines.append(f"### {direction.upper()}")
        lines.append("")
        if ds is None:
            lines.append("_No trades._")
        else:
            lines += [
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| n | {ds['n']} |",
                f"| win_rate | {ds['win_rate']:.4f} |",
                f"| avg_return | {ds['avg_return_pct']:+.4f}% |",
                f"| TP | {ds['tp']} |",
                f"| SL | {ds['sl']} |",
                f"| TIMEOUT | {ds['timeout']} |",
            ]
        lines.append("")

    dbg = report.get("debug_mt")
    if dbg:
        lines += [
            "## Debug (MT Filter)",
            "",
            f"- initial_side_counts: `{dbg.get('initial_side_counts')}`",
            f"- final_side_counts: `{dbg.get('final_side_counts')}`",
            f"- trend_4h_counts: `{dbg.get('trend_4h_counts')}`",
            f"- trend_1d_counts: `{dbg.get('trend_1d_counts')}`",
            f"- mt_reject_reasons: `{dbg.get('mt_reject_reasons')}`",
            f"- selected_prob_sources: `{dbg.get('selected_prob_sources')}`",
            "",
        ]

    return "\n".join(lines)


def _resolve_symbol_paths(
    *,
    symbol: str,
    log_path: Optional[str],
    data_dir: Optional[str],
    report_dir: Optional[str],
) -> Tuple[str, str, str]:
    base_data = data_dir or "data"

    per_symbol_log = os.path.join(base_data, symbol, "predictions_log.jsonl")
    per_symbol_data_dir = os.path.join(base_data, symbol)
    per_symbol_report_dir = os.path.join(base_data, symbol, "reports")

    root_log = os.path.join(base_data, "predictions_log.jsonl")
    root_data_dir = base_data
    root_report_dir = os.path.join(base_data, "reports")

    final_log = log_path or (per_symbol_log if os.path.exists(per_symbol_log) else root_log)
    final_data_dir = data_dir or (per_symbol_data_dir if os.path.exists(per_symbol_data_dir) else root_data_dir)
    final_report_dir = report_dir or (per_symbol_report_dir if os.path.exists(per_symbol_data_dir) else root_report_dir)

    return final_log, final_data_dir, final_report_dir


def _run_one_symbol(args: argparse.Namespace, symbol: Optional[str]) -> int:
    if args.date:
        try:
            report_date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: invalid --date format '{args.date}', expected YYYY-MM-DD", flush=True)
            return 1
    else:
        report_date = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    date_str = report_date.strftime("%Y-%m-%d")
    day_start = report_date
    day_end = report_date + timedelta(days=1)

    effective_symbol = symbol if symbol is not None else args.symbol

    # ---------------------------------------------------------------------------
    # Resolve critical trading parameters
    # In symbol mode: CLI overrides YAML; missing in both → error (no built-in fallback)
    # In legacy mode: CLI required for threshold/tp/sl; built-in defaults for horizon/mt_filter_mode
    # ---------------------------------------------------------------------------
    config_sources: Optional[Dict[str, str]] = None

    if effective_symbol:
        # Symbol mode – strict YAML/CLI resolution
        try:
            sp = _get_symbol_paths_module()
        except ImportError as exc:
            print(f"ERROR: could not import symbol_paths: {exc}", flush=True)
            return 1

        raw_yaml: Dict[str, Any] = sp._load_symbols_config().get(effective_symbol, {})

        errors: List[str] = []
        config_sources = {}

        def _resolve_param(cli_val: Any, yaml_key: str, display_name: str) -> Any:
            yaml_val = raw_yaml.get(yaml_key)
            if cli_val is not None:
                config_sources[display_name] = "cli"
                return cli_val
            if yaml_val is not None:
                config_sources[display_name] = "yaml"
                return yaml_val
            errors.append(
                f"{display_name}: missing from both CLI (--{display_name.replace('_', '-')}) "
                f"and YAML config for symbol={effective_symbol}"
            )
            return None

        eff_threshold = _resolve_param(args.threshold, "threshold", "threshold")
        eff_tp = _resolve_param(args.tp, "tp", "tp")
        eff_sl = _resolve_param(args.sl, "sl", "sl")
        eff_horizon = _resolve_param(args.horizon_bars, "horizon", "horizon_bars")
        eff_mt_filter_mode = _resolve_param(args.mt_filter_mode, "mt_filter_mode", "mt_filter_mode")

        if errors:
            for err in errors:
                print(f"ERROR [{effective_symbol}] {err}", flush=True)
            return 1

        print(
            f"[config] {effective_symbol}: "
            f"threshold={eff_threshold} ({config_sources['threshold']}), "
            f"tp={eff_tp} ({config_sources['tp']}), "
            f"sl={eff_sl} ({config_sources['sl']}), "
            f"horizon_bars={eff_horizon} ({config_sources['horizon_bars']}), "
            f"mt_filter_mode={eff_mt_filter_mode} ({config_sources['mt_filter_mode']})",
            flush=True,
        )
    else:
        # Legacy explicit mode – threshold/tp/sl must be supplied via CLI
        missing = [
            name for name, val in [("--threshold", args.threshold), ("--tp", args.tp), ("--sl", args.sl)]
            if val is None
        ]
        if missing:
            print(
                f"ERROR: {', '.join(missing)} must be provided in explicit (no-symbol) mode",
                flush=True,
            )
            return 1

        eff_threshold = args.threshold
        eff_tp = args.tp
        eff_sl = args.sl
        eff_horizon = args.horizon_bars if args.horizon_bars is not None else 6
        eff_mt_filter_mode = args.mt_filter_mode if args.mt_filter_mode is not None else "layered"

    if effective_symbol:
        log_path, data_dir, report_dir = _resolve_symbol_paths(
            symbol=effective_symbol,
            log_path=args.log_path if args.log_path != "data/predictions_log.jsonl" else None,
            data_dir=args.data_dir if args.data_dir != "data" else None,
            report_dir=args.report_dir if args.report_dir != "data/reports" else None,
        )
    else:
        log_path = args.log_path
        data_dir = args.data_dir
        report_dir = args.report_dir

    print(
        f"Generating daily report for {date_str} symbol={effective_symbol or 'ALL_IN_LOG'} "
        f"({day_start.isoformat()} → {day_end.isoformat()})",
        flush=True,
    )

    klines_1h_path = os.path.join(data_dir, "klines_1h.json")
    if not os.path.exists(klines_1h_path):
        print(f"ERROR: {klines_1h_path} not found", flush=True)
        return 2

    klines = load_klines_1h(klines_1h_path)
    if not klines:
        print("ERROR: 1h klines empty", flush=True)
        return 2

    idx_by_ts = {k["ts"]: i for i, k in enumerate(klines)}
    print(f"Loaded {len(klines)} 1h klines from {klines_1h_path}", flush=True)

    mt_filter = not args.no_mt_filter
    ts_4h: List = []
    trend_4h_list: List = []
    ts_1d: List = []
    trend_1d_list: List = []

    if mt_filter:
        try:
            klines_4h = load_klines_1h(os.path.join(data_dir, "klines_4h.json"))
            klines_1d = load_klines_1h(os.path.join(data_dir, "klines_1d.json"))
            trend_4h_list = _trend_series(klines_4h, fast=5, slow=20, eps=0.001)
            trend_1d_list = _trend_series(klines_1d, fast=5, slow=20, eps=0.001)
            ts_4h = [k["ts"] for k in klines_4h]
            ts_1d = [k["ts"] for k in klines_1d]
        except Exception as e:
            print(f"WARNING: could not load 4h/1d klines ({e}), MT filter disabled", flush=True)
            mt_filter = False

    if not os.path.exists(log_path):
        print(f"ERROR: prediction log not found: {log_path}", flush=True)
        return 2

    preds = _load_predictions_for_day(
        path=log_path,
        symbol=effective_symbol,
        interval=args.interval,
        active_model=args.active_model,
        day_start=day_start,
        day_end=day_end,
    )

    print(f"Loaded {len(preds)} predictions from {log_path}", flush=True)

    report = _build_report(
        date_str=date_str,
        preds=preds,
        klines=klines,
        idx_by_ts=idx_by_ts,
        ts_4h=ts_4h,
        trend_4h_list=trend_4h_list,
        ts_1d=ts_1d,
        trend_1d_list=trend_1d_list,
        threshold=eff_threshold,
        tp_pct=eff_tp,
        sl_pct=eff_sl,
        fee=args.fee,
        slippage=args.slippage,
        horizon=eff_horizon,
        tie_breaker=args.tie_breaker,
        timeout_exit=args.timeout_exit,
        mt_filter=mt_filter,
        mt_filter_mode=eff_mt_filter_mode,
        model_version_override=args.model_version,
    )

    if config_sources:
        report["params"]["config_source"] = config_sources

    os.makedirs(report_dir, exist_ok=True)

    json_path = os.path.join(report_dir, f"daily_eval_{date_str}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"JSON report written to {json_path}", flush=True)

    md_path = os.path.join(report_dir, f"daily_eval_{date_str}.md")
    md_text = _render_markdown(report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    print(f"Markdown report written to {md_path}", flush=True)

    print("\n" + "=" * 60, flush=True)
    print(f"DAILY REPORT SUMMARY: {date_str} symbol={effective_symbol or 'ALL_IN_LOG'}", flush=True)
    print(f"  model_version : {report['model_version']}", flush=True)
    ss = report["signal_stats"]
    print(f"  predictions   : {ss['total_predictions']}", flush=True)
    print(f"  trades        : {ss['n_trades']}", flush=True)
    print(f"  coverage      : {ss['coverage']:.4f}", flush=True)
    print(f"  flat(model)   : {ss['skipped_flat_model']}", flush=True)
    print(f"  filtered_mt   : {ss['filtered_mt']}", flush=True)
    print(f"  passed_mt     : {ss['passed_mt']}", flush=True)
    sm = report.get("strategy_metrics")
    if sm:
        print(f"  win_rate      : {sm['win_rate']:.4f}", flush=True)
        print(f"  avg_return    : {sm['avg_return_pct']:+.4f}%", flush=True)
        print(f"  mdd_trade_seq : {sm['mdd_trade_seq_pct']:.4f}%", flush=True)
        print(f"  TP/SL/TIMEOUT : {sm['tp']}/{sm['sl']}/{sm['timeout']}", flush=True)
    print("=" * 60, flush=True)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate daily evaluation reports (JSON + Markdown) from prediction logs"
    )
    ap.add_argument("--symbol", default=None, help="Single symbol, e.g. BTCUSDT")
    ap.add_argument(
        "--all-symbols",
        action="store_true",
        default=False,
        help="Run daily evaluation for all enabled symbols from configs/symbols.yaml",
    )
    ap.add_argument(
        "--log-path",
        default="data/predictions_log.jsonl",
        help="Path to predictions_log.jsonl (legacy explicit mode)",
    )
    ap.add_argument(
        "--data-dir",
        default="data",
        help="Directory with klines json files (legacy explicit mode)",
    )
    ap.add_argument(
        "--date",
        default=None,
        help="Date to evaluate in YYYY-MM-DD format (UTC). Default: yesterday.",
    )
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--active-model", default="event_v3")
    ap.add_argument("--model-version", default=None, help="Override model_version in report")

    ap.add_argument(
        "--tp",
        type=float,
        default=None,
        help="Take-profit fraction, e.g. 0.0175 (required in explicit mode; overrides YAML in symbol mode)",
    )
    ap.add_argument(
        "--sl",
        type=float,
        default=None,
        help="Stop-loss fraction, e.g. 0.007 (required in explicit mode; overrides YAML in symbol mode)",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Probability threshold, e.g. 0.55 (required in explicit mode; overrides YAML in symbol mode)",
    )
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument(
        "--horizon-bars",
        type=int,
        default=None,
        help="Horizon bars (overrides YAML in symbol mode; defaults to 6 in explicit mode)",
    )
    ap.add_argument("--tie-breaker", choices=["SL", "TP"], default="SL")
    ap.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")

    ap.add_argument("--no-mt-filter", action="store_true", help="Disable 4h/1d multi-timeframe filter")
    ap.add_argument(
        "--mt-filter-mode",
        choices=["off", "long_only", "symmetric", "strict", "relaxed", "regime", "conflict", "layered"],
        default=None,
        help=(
            "Unified MT filter mode (overrides YAML in symbol mode; "
            "defaults to 'layered' in explicit mode)"
        ),
    )

    ap.add_argument(
        "--report-dir",
        default="data/reports",
        help="Directory to write report files (legacy explicit mode)",
    )

    args = ap.parse_args()

    if args.all_symbols:
        try:
            sp = _get_symbol_paths_module()
        except ImportError as exc:
            print(f"ERROR: could not import symbol_paths: {exc}", flush=True)
            return 1

        symbols = sp.list_enabled_symbols()
        if not symbols:
            print("WARNING: no enabled symbols found; nothing to do.", flush=True)
            return 0

        any_failed = False
        for sym in symbols:
            print(f"\n[eval] running for symbol={sym}", flush=True)
            rc = _run_one_symbol(args, sym)
            if rc != 0:
                any_failed = True
        return 1 if any_failed else 0

    return _run_one_symbol(args, args.symbol)


if __name__ == "__main__":
    raise SystemExit(main())
