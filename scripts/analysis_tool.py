#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis_tool.py — 多功能评估/诊断工具（合并 debug / visualize / sl-scan / eval-loop）

子命令：
  - debug_inspect   : 逐笔打印 simulate_trade 的详细检查
  - visualize       : 导出 trades CSV 并生成可视化 PNG（outcomes, bars_to_exit, sample paths）
  - sl_scan_mt      : 在进程内批量扫描 sl（包含 multi-timeframe 4h/1d 过滤）
  - eval_loop       : 循环调用 scripts/evaluate_from_logs.py，解析输出并写 CSV
  - sl_scan_simple  : 简单批量扫描 sl（无 MT 过滤）

用法示例：
  python scripts/analysis_tool.py debug_inspect --preds /tmp/preds_valid.jsonl --data data --threshold 0.55 --tp 0.0175 --horizon 6 --max 30
  python scripts/analysis_tool.py visualize --preds /tmp/preds_valid.jsonl --data data --threshold 0.55 --tp 0.0175 --horizon 6 --sample 20
  python scripts/analysis_tool.py sl_scan_mt --preds /tmp/preds_valid.jsonl --data data --tp 0.0175 --threshold 0.55 --sl-range 0.001:0.02:0.001 --horizon 6 --out outputs/sl_sensitivity_mt.csv
  python scripts/analysis_tool.py eval_loop --sl-list 0.001 0.002 0.003 --log-path /tmp/preds_valid.jsonl --tp 0.0175 --threshold 0.55 --horizon 6 --out outputs/eval_sl_scan.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
from bisect import bisect_right
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# 依赖本仓库的回测函数
from backtest_event_v3_http import (
    load_klines_1h,
    simulate_trade,
    compute_metrics,
    decide_side,
    _trend_series,
)

# matplotlib 仅在 visualize 时需要
try:
    import matplotlib.pyplot as plt
    import numpy as np
except Exception:
    plt = None
    np = None


# -------------------------
# 共用工具
# -------------------------
def parse_range(spec: str) -> List[float]:
    a, b, step = [float(x) for x in spec.split(":")]
    out = []
    x = a
    while x <= b + 1e-12:
        out.append(round(x, 12))
        x += step
    return out


def load_preds(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def build_ts_index_map(klines: List[Dict[str, Any]]):
    d: Dict[str, int] = {}
    for i, k in enumerate(klines):
        ts = k["ts"]
        if isinstance(ts, str):
            key = ts
        else:
            key = ts.isoformat().replace("+00:00", "Z")
        d[key] = i
    return d


# -------------------------
# debug_inspect 子命令
# -------------------------
def cmd_debug_inspect(args):
    klines = load_klines_1h(f"{args.data.rstrip('/')}/klines_1h.json")
    klines_4h = load_klines_1h(f"{args.data.rstrip('/')}/klines_4h.json")
    klines_1d = load_klines_1h(f"{args.data.rstrip('/')}/klines_1d.json")
    preds = load_preds(args.preds)
    idx_map = build_ts_index_map(klines)

    trend_4h_list = _trend_series(klines_4h, fast=5, slow=20, eps=0.001)
    ts_4h = [k["ts"] for k in klines_4h]
    trend_1d_list = _trend_series(klines_1d, fast=5, slow=20, eps=0.001)
    ts_1d = [k["ts"] for k in klines_1d]

    def trend_4h_at(ts):
        idx = bisect_right(ts_4h, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_4h_list[idx]

    def trend_1d_at(ts):
        idx = bisect_right(ts_1d, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_1d_list[idx]

    inspected = 0
    stats = Counter()
    for p in preds:
        if inspected >= args.max:
            break
        ts = p.get("ts")
        if not ts:
            continue
        i = idx_map.get(ts)
        if i is None:
            continue
        p_long = p.get("proba_long")
        p_short = p.get("proba_short")
        cal_p_long = p.get("cal_proba_long")
        cal_p_short = p.get("cal_proba_short")
        if cal_p_long is not None and cal_p_short is not None:
            eff_p_long = float(cal_p_long)
            eff_p_short = float(cal_p_short)
        else:
            eff_p_long = float(p_long) if p_long is not None else 0.0
            eff_p_short = float(p_short) if p_short is not None else 0.0

        side = decide_side(eff_p_long, eff_p_short, args.threshold)

        # MT 过滤（Scheme B）
        if side == "LONG":
            t4 = trend_4h_at(klines[i]["ts"])
            t1d = trend_1d_at(klines[i]["ts"])
            if t4 != "UP" or t1d == "DOWN":
                side = "FLAT"
        elif side == "SHORT":
            t4 = trend_4h_at(klines[i]["ts"])
            t1d = trend_1d_at(klines[i]["ts"])
            if t4 != "DOWN" or t1d == "UP":
                side = "FLAT"

        if side == "FLAT":
            continue

        tr = simulate_trade(
            klines=klines,
            i=i,
            side=side,
            tp_pct=args.tp,
            sl_pct=args.sl,
            fee_per_side=args.fee,
            slippage_per_side=args.slippage,
            horizon_bars=args.horizon,
            tie_breaker="SL",
            timeout_exit=args.timeout_exit,
        )
        if tr.outcome == "NO_TRADE":
            stats["NO_TRADE"] += 1
            continue
        stats[tr.outcome] += 1
        print("===")
        print(f"PRED TS: {ts}  eff_p_long={eff_p_long} eff_p_short={eff_p_short} => side={side}")
        print(f"  outcome={tr.outcome} side={tr.side} entry={tr.entry:.6f} entry_exec={tr.entry_exec:.6f} exit={tr.exit_price:.6f} exit_exec={tr.exit_exec:.6f} ret_net={tr.ret_net:.6f} bars_held={tr.bars_held}")
        entry_bar_idx = i + 1
        last_idx = min(i + args.horizon, len(klines) - 1)
        print("  Bars checked (idx offset from entry):")
        for j in range(entry_bar_idx, last_idx + 1):
            b = klines[j]
            rel = j - entry_bar_idx
            print(f"    [{rel}] ts={b['ts']} open={b.get('open')} high={b.get('high')} low={b.get('low')} close={b.get('close')}")
        inspected += 1
    print("=== SUMMARY ===")
    print(dict(stats))


# -------------------------
# visualize 子命令
# -------------------------
def cmd_visualize(args):
    if plt is None or np is None:
        print("visualize 需要 matplotlib 和 numpy，请先安装：pip install matplotlib numpy", file=sys.stderr)
        return
    klines = load_klines_1h(f"{args.data.rstrip('/')}/klines_1h.json")
    klines_4h = load_klines_1h(f"{args.data.rstrip('/')}/klines_4h.json")
    klines_1d = load_klines_1h(f"{args.data.rstrip('/')}/klines_1d.json")
    preds = load_preds(args.preds)
    idx_map = build_ts_index_map(klines)

    ts_4h = [k["ts"] for k in klines_4h]
    ts_1d = [k["ts"] for k in klines_1d]
    trend_4h_list = _trend_series(klines_4h, fast=5, slow=20, eps=0.001)
    trend_1d_list = _trend_series(klines_1d, fast=5, slow=20, eps=0.001)

    def trend_4h_at(ts):
        idx = bisect_right(ts_4h, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_4h_list[idx]

    def trend_1d_at(ts):
        idx = bisect_right(ts_1d, ts) - 1
        if idx < 0:
            return "NEUTRAL"
        return trend_1d_list[idx]

    trades_info = []
    outcomes = Counter()
    bars_to_exit = []

    for p in preds:
        ts_raw = p.get("ts")
        if not ts_raw:
            continue
        i = idx_map.get(ts_raw)
        if i is None or i + args.horizon >= len(klines):
            continue
        p_long = p.get("proba_long")
        p_short = p.get("proba_short")
        cal_p_long = p.get("cal_proba_long")
        cal_p_short = p.get("cal_proba_short")
        if cal_p_long is not None and cal_p_short is not None:
            eff_p_long = float(cal_p_long)
            eff_p_short = float(cal_p_short)
        else:
            eff_p_long = float(p_long) if p_long is not None else 0.0
            eff_p_short = float(p_short) if p_short is not None else 0.0
        side = decide_side(eff_p_long, eff_p_short, args.threshold)

        # MT filter
        if side == "LONG":
            t4 = trend_4h_at(klines[i]["ts"]); t1d = trend_1d_at(klines[i]["ts"])
            if t4 != "UP" or t1d == "DOWN": side = "FLAT"
        elif side == "SHORT":
            t4 = trend_4h_at(klines[i]["ts"]); t1d = trend_1d_at(klines[i]["ts"])
            if t4 != "DOWN" or t1d == "UP": side = "FLAT"

        if side == "FLAT":
            continue

        tr = simulate_trade(
            klines=klines,
            i=i,
            side=side,
            tp_pct=args.tp,
            sl_pct=args.sl,
            fee_per_side=args.fee,
            slippage_per_side=args.slippage,
            horizon_bars=args.horizon,
            tie_breaker="SL",
            timeout_exit=args.timeout_exit,
        )
        if tr.outcome == "NO_TRADE":
            continue
        outcomes[tr.outcome] += 1
        bars_to_exit.append(tr.bars_held)
        trades_info.append({
            "ts": ts_raw, "side": tr.side, "outcome": tr.outcome, "entry": tr.entry, "entry_exec": tr.entry_exec,
            "exit_price": tr.exit_price, "exit_exec": tr.exit_exec, "ret_net": tr.ret_net, "bars_held": tr.bars_held, "entry_idx": i+1
        })

    outdir = args.outdir or "outputs"
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "trades_details.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ts","side","outcome","entry","entry_exec","exit_price","exit_exec","ret_net","bars_held","entry_idx"])
        writer.writeheader()
        for r in trades_info:
            writer.writerow(r)
    print(f"Wrote CSV: {csv_path}")

    # outcomes plot
    fig1, ax1 = plt.subplots(figsize=(6,4))
    labels = list(outcomes.keys())
    vals = [outcomes[k] for k in labels]
    colors = [ "tab:green" if l=="TP" else ("tab:red" if l=="SL" else "tab:orange") for l in labels]
    ax1.bar(labels, vals, color=colors)
    ax1.set_title("Trade outcomes")
    fig1.tight_layout(); fig1_path = os.path.join(outdir, "fig_outcomes.png"); fig1.savefig(fig1_path); print(f"Wrote {fig1_path}")

    # bars_to_exit hist
    fig2, ax2 = plt.subplots(figsize=(6,4))
    ax2.hist(bars_to_exit, bins=range(0, max(bars_to_exit)+2 if bars_to_exit else 2), color="tab:blue", edgecolor="k")
    ax2.set_title("Bars held distribution")
    fig2.tight_layout(); fig2_path = os.path.join(outdir, "fig_bars_to_exit.png"); fig2.savefig(fig2_path); print(f"Wrote {fig2_path}")

    # sample paths
    sample_n = min(args.sample, len(trades_info))
    if sample_n > 0:
        rng = np.linspace(0, len(trades_info)-1, sample_n, dtype=int)
        fig3, axes = plt.subplots(sample_n, 1, figsize=(10, 2.5*sample_n))
        if sample_n == 1: axes = [axes]
        for ax, idx in zip(axes, rng):
            t = trades_info[idx]; eidx = t["entry_idx"]; last_idx = min(eidx + args.horizon, len(klines)-1)
            xs=[]; highs=[]; lows=[]; closes=[]
            for j in range(eidx, last_idx+1):
                b = klines[j]; xs.append(j-eidx); highs.append(float(b["high"])); lows.append(float(b["low"])); closes.append(float(b["close"]))
            entry = t["entry"]
            highs_pct = [(h/entry - 1.0)*100.0 for h in highs]; lows_pct = [(l/entry - 1.0)*100.0 for l in lows]; closes_pct = [(c/entry - 1.0)*100.0 for c in closes]
            ax.plot(xs, highs_pct, color="gray", linestyle="--", label="high"); ax.plot(xs, lows_pct, color="gray", linestyle=":", label="low"); ax.plot(xs, closes_pct, color="blue", label="close")
            if t["side"] == "LONG":
                tp_pct = (t["exit_price"]/entry - 1.0)*100.0; sl_pct = ((t["entry"]*(1.0-args.sl))/entry - 1.0)*100.0
            else:
                tp_pct = (t["exit_price"]/entry - 1.0)*100.0; sl_pct = ((t["entry"]*(1.0+args.sl))/entry - 1.0)*100.0
            ax.axhline(tp_pct, color="tab:green", linestyle="--", label="TP"); ax.axhline(sl_pct, color="tab:red", linestyle="--", label="SL")
            ax.set_title(f"{t['ts']} {t['side']} outcome={t['outcome']} ret={t['ret_net']*100:.2f}% bars={t['bars_held']}")
            ax.set_ylabel("% vs entry"); ax.legend(loc="upper right", fontsize="small")
        fig3.tight_layout(); fig3_path = os.path.join(outdir, "fig_sample_paths.png"); fig3.savefig(fig3_path); print(f"Wrote {fig3_path}")
    else:
        print("No trades to plot sample paths.")


# -------------------------
# sl_scan_mt 子命令
# -------------------------
def simulate_for_sl_with_mt(preds, klines, klines_4h, klines_1d, idx_map, sl, tp, threshold, horizon, slippage):
    ts_4h = [k["ts"] for k in klines_4h]
    ts_1d = [k["ts"] for k in klines_1d]
    trend_4h_list = _trend_series(klines_4h, fast=5, slow=20, eps=0.001)
    trend_1d_list = _trend_series(klines_1d, fast=5, slow=20, eps=0.001)

    def trend_4h_at(ts):
        idx = bisect_right(ts_4h, ts) - 1
        if idx < 0: return "NEUTRAL"
        return trend_4h_list[idx]
    def trend_1d_at(ts):
        idx = bisect_right(ts_1d, ts) - 1
        if idx < 0: return "NEUTRAL"
        return trend_1d_list[idx]

    trades=[]
    for p in preds:
        ts = p.get("ts"); 
        if not ts: continue
        i = idx_map.get(ts)
        if i is None or i + horizon >= len(klines): continue
        p_long = p.get("proba_long"); p_short = p.get("proba_short")
        cal_p_long = p.get("cal_proba_long"); cal_p_short = p.get("cal_proba_short")
        if cal_p_long is not None and cal_p_short is not None:
            eff_p_long = float(cal_p_long); eff_p_short = float(cal_p_short)
        else:
            eff_p_long = float(p_long) if p_long is not None else 0.0
            eff_p_short = float(p_short) if p_short is not None else 0.0
        side = decide_side(eff_p_long, eff_p_short, threshold)
        # MT filter
        if side == "LONG":
            t4 = trend_4h_at(klines[i]["ts"]); t1d = trend_1d_at(klines[i]["ts"])
            if t4 != "UP": side = "FLAT"
            elif t1d == "DOWN": side = "FLAT"
        elif side == "SHORT":
            t4 = trend_4h_at(klines[i]["ts"]); t1d = trend_1d_at(klines[i]["ts"])
            if t4 != "DOWN": side = "FLAT"
            elif t1d == "UP": side = "FLAT"
        if side == "FLAT": continue
        tr = simulate_trade(klines=klines, i=i, side=side, tp_pct=tp, sl_pct=sl, fee_per_side=0.0004, slippage_per_side=slippage, horizon_bars=horizon, tie_breaker="SL", timeout_exit="close")
        if tr.outcome == "NO_TRADE": continue
        trades.append(tr)
    if not trades: return None
    m = compute_metrics(trades, total_bars=len(klines))
    return m


def cmd_sl_scan_mt(args):
    if args.sl_range:
        sl_values = parse_range(args.sl_range)
    elif args.sl_list:
        sl_values = sorted(set([round(float(x), 12) for x in args.sl_list]))
    else:
        raise SystemExit("需要指定 --sl-range 或 --sl-list")
    preds = load_preds(args.preds)
    klines = load_klines_1h(f"{args.data.rstrip('/')}/klines_1h.json")
    klines_4h = load_klines_1h(f"{args.data.rstrip('/')}/klines_4h.json")
    klines_1d = load_klines_1h(f"{args.data.rstrip('/')}/klines_1d.json")
    idx_map = build_ts_index_map(klines)
    rows=[]
    for slv in sl_values:
        print(f"[mt_scan] sl={slv} ...", flush=True)
        m = simulate_for_sl_with_mt(preds, klines, klines_4h, klines_1d, idx_map, slv, args.tp, args.threshold, args.horizon, args.slippage)
        if m is None:
            rows.append({"sl":slv,"n_trade":0,"n_long":0,"n_short":0,"tp":0,"sl_count":0,"timeout":0,"avg_ret":0.0,"profit_factor":0.0,"win_rate":0.0,"avg_ret_tp":0.0,"avg_ret_sl":0.0,"avg_ret_to":0.0,"timeout_win_rate":0.0,"bars_to_exit_median":None})
        else:
            rows.append({"sl":slv,"n_trade":m.n_trade,"n_long":m.n_long,"n_short":m.n_short,"tp":m.tp,"sl_count":m.sl,"timeout":m.timeout,"avg_ret":m.avg_ret,"profit_factor":m.profit_factor if math.isfinite(m.profit_factor) else float("inf"),"win_rate":m.win_rate,"avg_ret_tp":m.avg_ret_tp,"avg_ret_sl":m.avg_ret_sl,"avg_ret_to":m.avg_ret_to,"timeout_win_rate":m.timeout_win_rate,"bars_to_exit_median":m.bars_to_exit_median})
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fieldnames = ["sl","n_trade","n_long","n_short","tp","sl_count","timeout","avg_ret","profit_factor","win_rate","avg_ret_tp","avg_ret_sl","avg_ret_to","timeout_win_rate","bars_to_exit_median"]
    with open(args.out,"w",newline="",encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows: writer.writerow(r)
    print(f"[mt_scan] 写入 {args.out}")


# -------------------------
# eval_loop 子命令（调用 evaluate_from_logs.py）
# -------------------------
def parse_metrics_from_eval_output(output: str) -> dict:
    d: Dict[str, Optional[float]] = {}
    m = re.search(r"Total predictions loaded\s*:\s*(\d+)", output)
    d["n_predictions_loaded"] = int(m.group(1)) if m else None
    m = re.search(r"Skipped \(no kline match\)\s*:\s*(\d+)", output)
    d["skipped_no_kline"] = int(m.group(1)) if m else None
    m = re.search(r"Filtered \(MT / threshold\)\s*:\s*(\d+)", output)
    d["filtered_mt"] = int(m.group(1)) if m else None
    m = re.search(r"Trades triggered\s*:\s*(\d+)", output)
    d["trades_triggered"] = int(m.group(1)) if m else None
    m = re.search(r"Coverage \(trades/preds\)\s*:\s*([0-9.]+)", output)
    d["coverage"] = float(m.group(1)) if m else None
    m = re.search(r"Avg confidence @ trigger\s*:\s*([0-9.]+)", output)
    d["avg_confidence"] = float(m.group(1)) if m else None
    m = re.search(r"TP=(\d+)\s+SL=(\d+)\s+TIMEOUT=(\d+)", output)
    if m:
        d["tp"] = int(m.group(1)); d["sl_count"] = int(m.group(2)); d["timeout"] = int(m.group(3))
    else:
        d["tp"] = d["sl_count"] = d["timeout"] = None
    m = re.search(r"Win rate\s*:\s*([0-9.]+)", output)
    d["win_rate"] = float(m.group(1)) if m else None
    m = re.search(r"Avg return\s*:\s*([0-9.]+)%", output)
    d["avg_return_pct"] = float(m.group(1)) if m else None
    m = re.search(r"Profit factor\s*:\s*([0-9.]+)", output)
    d["profit_factor"] = float(m.group(1)) if m else None
    return d


def cmd_eval_loop(args):
    if not args.sl_list:
        raise SystemExit("--sl-list 在 eval_loop 模式下是必需的")
    python_exec = sys.executable
    common_args = ["--log-path", args.log_path, "--data-dir", args.data_dir, "--threshold", str(args.threshold), "--tp", str(args.tp), "--horizon-bars", str(args.horizon)]
    rows=[]
    for sl in args.sl_list:
        print(f"[eval_loop] running evaluate_from_logs.py for sl={sl} ...", flush=True)
        cmd = [python_exec, "scripts/evaluate_from_logs.py"] + common_args + ["--sl", str(sl)]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
            out = proc.stdout
        except subprocess.CalledProcessError as e:
            out = e.stdout or ""
        parsed = parse_metrics_from_eval_output(out)
        parsed["sl"] = sl
        parsed["raw_head"] = "\n".join(out.splitlines()[:40])
        rows.append(parsed)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fieldnames = ["sl","n_predictions_loaded","skipped_no_kline","filtered_mt","trades_triggered","coverage","avg_confidence","tp","sl_count","timeout","win_rate","avg_return_pct","profit_factor"]
    with open(args.out,"w",newline="",encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k,"") for k in fieldnames})
    print(f"[eval_loop] 写入 {args.out}")


# -------------------------
# sl_scan_simple 子命令（简单扫描，无 MT）
# -------------------------
def cmd_sl_scan_simple(args):
    if args.sl_range:
        sl_values = parse_range(args.sl_range)
    elif args.sl_list:
        sl_values = sorted(set([round(float(x),12) for x in args.sl_list]))
    else:
        raise SystemExit("需要 --sl-range 或 --sl-list")
    preds = load_preds(args.preds)
    klines = load_klines_1h(f"{args.data.rstrip('/')}/klines_1h.json")
    idx_map = build_ts_index_map(klines)
    rows=[]
    for slv in sl_values:
        trades=[]
        for p in preds:
            ts_raw = p.get("ts")
            if not ts_raw: continue
            i = idx_map.get(ts_raw)
            if i is None or i + args.horizon >= len(klines): continue
            p_long = p.get("proba_long"); p_short = p.get("proba_short")
            cal_p_long = p.get("cal_proba_long"); cal_p_short = p.get("cal_proba_short")
            if cal_p_long is not None and cal_p_short is not None:
                eff_p_long = float(cal_p_long); eff_p_short = float(cal_p_short)
            else:
                eff_p_long = float(p_long) if p_long is not None else 0.0; eff_p_short = float(p_short) if p_short is not None else 0.0
            side = decide_side(eff_p_long, eff_p_short, args.threshold)
            if side == "FLAT": continue
            tr = simulate_trade(klines=klines, i=i, side=side, tp_pct=args.tp, sl_pct=slv, fee_per_side=args.fee, slippage_per_side=args.slippage, horizon_bars=args.horizon, tie_breaker="SL", timeout_exit="close")
            if tr.outcome == "NO_TRADE": continue
            trades.append(tr)
        if not trades:
            rows.append({"sl":slv,"n_trade":0,"tp":0,"sl_count":0,"timeout":0,"avg_ret":0.0,"profit_factor":0.0})
        else:
            m = compute_metrics(trades, total_bars=len(klines))
            rows.append({"sl":slv,"n_trade":m.n_trade,"tp":m.tp,"sl_count":m.sl,"timeout":m.timeout,"avg_ret":m.avg_ret,"profit_factor":m.profit_factor if math.isfinite(m.profit_factor) else float("inf")})
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fieldnames=["sl","n_trade","tp","sl_count","timeout","avg_ret","profit_factor"]
    with open(args.out,"w",newline="",encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames); writer.writeheader(); [writer.writerow(r) for r in rows]
    print(f"Wrote {args.out}")


# -------------------------
# CLI 入口
# -------------------------
def main():
    ap = argparse.ArgumentParser(description="分析工具：debug/visualize/sl-scan/eval-loop 合并")
    sub = ap.add_subparsers(dest="cmd")

    # debug_inspect
    p = sub.add_parser("debug_inspect", help="逐笔打印 simulate_trade 明细")
    p.add_argument("--preds", required=True)
    p.add_argument("--data", default="data")
    p.add_argument("--threshold", type=float, default=0.55)
    p.add_argument("--tp", type=float, required=True)
    p.add_argument("--sl", type=float, required=True)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--timeout-exit", choices=["close","open_next"], default="close")
    p.add_argument("--max", type=int, default=20)
    p.set_defaults(func=cmd_debug_inspect)

    # visualize
    p = sub.add_parser("visualize", help="导出 CSV 并生成图片")
    p.add_argument("--preds", required=True)
    p.add_argument("--data", default="data")
    p.add_argument("--threshold", type=float, default=0.55)
    p.add_argument("--tp", type=float, required=True)
    p.add_argument("--sl", type=float, required=True)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--timeout-exit", choices=["close", "open_next"], default="close")
    p.add_argument("--sample", type=int, default=20)
    p.add_argument("--outdir", default="outputs")
    p.set_defaults(func=cmd_visualize)

    # sl_scan_mt
    p = sub.add_parser("sl_scan_mt", help="在进程内批量扫描 sl（包含 4h/1d MT 过滤）")
    p.add_argument("--preds", required=True)
    p.add_argument("--data", default="data")
    p.add_argument("--tp", type=float, required=True)
    p.add_argument("--threshold", type=float, default=0.55)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--sl-range")
    group.add_argument("--sl-list", nargs="+", type=float)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--out", default="outputs/sl_sensitivity_mt.csv")
    p.set_defaults(func=cmd_sl_scan_mt)

    # eval_loop
    p = sub.add_parser("eval_loop", help="循环调用 evaluate_from_logs.py 并解析输出")
    p.add_argument("--sl-list", nargs="+", type=float, required=True)
    p.add_argument("--log-path", default="/tmp/preds_valid.jsonl")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--threshold", type=float, default=0.55)
    p.add_argument("--tp", type=float, required=True)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--out", default="outputs/eval_sl_scan.csv")
    p.set_defaults(func=cmd_eval_loop)

    # sl_scan_simple
    p = sub.add_parser("sl_scan_simple", help="简单 sl 扫描（无 MT 过滤）")
    p.add_argument("--preds", required=True)
    p.add_argument("--data", default="data")
    p.add_argument("--tp", type=float, required=True)
    p.add_argument("--threshold", type=float, default=0.55)
    g2 = p.add_mutually_exclusive_group(required=True)
    g2.add_argument("--sl-range")
    g2.add_argument("--sl-list", nargs="+", type=float)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--out", default="outputs/sl_sensitivity_simple.csv")
    p.set_defaults(func=cmd_sl_scan_simple)

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help(); return
    args.func(args)


if __name__ == "__main__":
    main()
