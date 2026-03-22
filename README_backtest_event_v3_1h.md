# event_v3 1h 策略回测与线上评估说明（2026-03）

> 模型版本：`event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6`  
> 数据区间：`2026-02-01 00:00:00Z` ~ `2026-03-10T23:00:00Z`，BTCUSDT 1h/4h/1d K 线  
> ml-service：本地 `http://127.0.0.1:9000/predict`

本文档记录当前 event_v3 1h 策略的**回测配置**、**最优参数**和**线上评估方式**，作为后续调参和模型迭代的基线。

> **前置条件**：运行回测前请确保 ml-service 已启动（`uvicorn app:app --host 127.0.0.1 --port 9000`）。详细的回测命令说明请参见 [README.md](README.md) 第 11 节。

当前版本已经集成：

- 单仓模式（一次只允许持有一笔仓位）
- 最大持仓 6 小时（`horizon-bars=6`）
- 多周期方向过滤（1h 信号 + 4h/1d 趋势约束）

---

## 1. 回测脚本与调用方式

脚本路径：

- `scripts/backtest_event_v3_http.py`

关键参数说明（1h 策略）：

- `--interval 1h`：使用 1 小时 K 线做信号。
- `--horizon-bars 6`：最大持仓 6 根 1h K 线（约 6 小时）。
- `--fee 0.0004`：单边手续费 0.04%。
- `--slippage 0.0`：暂不考虑额外滑点。
- `--objective avg_ret_mdd_daily`：目标函数为“平均收益 - 0.5 × 日度 MDD”。
- `--position-mode single`：单仓模式，一次只允许持有一笔仓位，在上一笔平仓之前忽略新的信号。
- 多周期过滤（内置在脚本中）：
  - 使用 `data/klines_4h.json` 和 `data/klines_1d.json` 计算 4h/1d 趋势 `UP/DOWN/NEUTRAL`。
  - 在 1h 信号基础上按方案 B 过滤（见第 3 节）。

### 1.1 回测命令示例（1h + 6 小时 horizon + 单仓 + 多周期过滤）

```bash
python scripts/backtest_event_v3_http.py \
  --data-dir data \
  --base-url http://127.0.0.1:9000 \
  --interval 1h \
  --since 2026-02-01T00:00:00Z \
  --until 2026-03-10T23:00:00Z \
  --thresholds 0.55:0.85:0.02 \
  --tp-grid 0.005:0.030:0.0025 \
  --sl-grid 0.003:0.020:0.001 \
  --horizon-bars 6 \
  --fee 0.0004 \
  --slippage 0.0 \
  --objective avg_ret_mdd_daily \
  --min-signals-per-week 1.0 \
  --position-mode single
```

---

## 2. 最优参数（单仓 + 6 小时 + 多周期过滤）

当前 grid search 的最佳组合：

```text
threshold=0.55
tp=1.75%
sl=0.90%
horizon=6
fee/side=0.0400%
slippage/side=0.0000%
timeout_exit=close
tie=SL
objective=avg_ret_mdd_daily
position_mode=single
```

### 2.1 性能指标（回测内，已包含 4h/1d 方向过滤）

```text
signals/week=1.66
n_trade=9 (long=4 short=5)
TP=8 SL=0 TO=1
win_rate=1.000
avg_ret=1.517%
profit_factor=inf

avg_ret_tp=1.670%
avg_ret_sl=0.000%
avg_ret_to=0.290%
timeout_win_rate=1.000

MDD(trade_seq)=0.00%
MDD(hourly)=0.00%
MDD(daily)=0.00%
max_consec_losses=0
bars_to_exit(min/median/p90/max)=1/2.0/4.2/5
```

### 2.2 策略特征总结

- **信号风格**：极低频，高置信度，约 `1.66` 笔交易/周。
- **方向**：在这段样本中出现了多空信号，但都经过 4h/1d 过滤：
  - `n_trade=9`，其中 `long=4`，`short=5`。
  - 所有 9 笔交易都是盈利（`TP=8`, `TO=1`, `SL=0`）。
- **结果**：
  - 平均每笔收益 `~1.517%`，profit factor=∞（无亏损笔）。
  - 在 trade-seq / hourly / daily 尺度上均未出现回撤（MDD≈0）。
  - `max_consec_losses=0`，没有连续亏损。
- **持仓时间**：
  - 最短 1 根 bar（1 小时）退出；
  - 中位持仓时间 2 小时；
  - 90 分位 ~4.2 小时；
  - 最大 5 小时，均小于设定的 6 小时上限。

> 结论（回测内）：  
> `threshold=0.55, tp=1.75%, sl=0.90%, horizon=6h`，在多周期过滤约束下，形成了一个**单仓、超低频、极高胜率、几乎无回撤**的 1h 策略版本，可作为当前的“保守基线配置”。

---

## 3. 多周期方向过滤规则（方案 B）

多周期过滤逻辑在 `backtest_event_v3_http.py` 内部实现，核心是：

1. 基于 4h/1d K 线定义趋势状态：
   - 对每根 4h/1d bar 计算：
     - `MA_fast = SMA(close, 5)`
     - `MA_slow = SMA(close, 20)`
   - 若 `MA_fast > MA_slow * (1 + eps)` → `TREND=UP`
   - 若 `MA_fast < MA_slow * (1 - eps)` → `TREND=DOWN`
   - 否则 `TREND=NEUTRAL`  
   （当前实现中 `eps=0.001`，即 0.1% 容忍区间）

2. 对于任意 1h 信号时间 `ts`，取：
   - `trend_4h_at(ts)`：ts 之前最近一根 4h bar 的趋势；
   - `trend_1d_at(ts)`：ts 之前最近一根 1d bar 的趋势���

3. 决策规则（方案 B，当前使用版本）：

以 LONG 为例（SHORT 可对称设计，当前重点在 LONG）：

- 首先用 1h 模型输出：
  - `p_long, p_short` → 通过 `decide_side(p_long, p_short, threshold)` 得到候选 `side`。
- 若 `side == LONG`，则应用多周期过滤：
  - 若 `trend_4h(ts) != UP` → 强制设为 `FLAT`（不交易）。
  - 否则若 `trend_1d(ts) == DOWN` → 强制设为 `FLAT`（不交易）。
  - 否则（4h=UP 且 1d∈{UP, NEUTRAL}）→ 保留为 LONG，允许进场。

直观理解：

- **4h 必须是 UP**：中周期趋势向上，否则不做多。
- **1d 不能是 DOWN**：日线如果在明显下跌，就不抄底做多。
- 对于 1d 横盘或略涨的情况，不强行否决，只要 4h 看涨即可。

> 这更接近现实中一个保守交易者的行为：  
> - 不在 4 小时下跌趋势里做 1 小时的反向多单；  
> - 不在日线明显下跌的背景中去抄短期反弹。

---

## 4. 线上预测日志与评估脚本

### 4.1 预测日志格式

ml-service 在 `/predict` 中将每次预测写入：

- 路径：`data/predictions_log.jsonl`
- 格式：一行一个 JSON，例如：

```json
{
  "ts": "2026-03-11T19:00:00Z",
  "symbol": "BTCUSDT",
  "interval": "1h",
  "proba_long": 0.73,
  "proba_short": 0.12,
  "proba_flat": 0.15,
  "signal": "LONG",
  "confidence": 0.73,
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "active_model": "event_v3",
  "as_of_ts": "2026-03-11T19:00:00Z"
}
```

说明：

- `ts`：特征对应的 bar 时间（对齐 `klines_1h.json`）。
- `proba_long/short/flat`：模型对 LONG/SHORT/FLAT 的概率估计。
- `signal/confidence`：当前服务端直接输出的信号与置信度。
- `active_model`：用于筛选不同模型版本或 A/B 测试。

### 4.2 线上��估脚本（当前版本：只基于 1h）

脚本路径：

- `scripts/evaluate_from_logs.py`

逻辑（当前不含多周期过滤，后续可对齐）：

1. 从 `data/predictions_log.jsonl` 中读取指定时间窗口和 interval 的预测。
2. 与 `data/klines_1h.json` 按 `ts` 对齐。
3. 使用与回测相同的三重障碍逻辑：
   - 在 1h 信号 bar 之后、`horizon-bars` 根内进行 TP / SL / TIMEOUT 判定。
4. 传入固定的 `(threshold, tp, sl, horizon)`，评估真实日志上的胜率 / 收益 / MDD 等指标。

线上评估命令示例（使用当前 BEST 参数）：

```bash
python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl \
  --data-dir data \
  --interval 1h \
  --active-model event_v3 \
  --since 2026-03-09T00:00:00Z \
  --until 2026-03-14T00:00:00Z \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.009 \
  --fee 0.0004 \
  --slippage 0.0 \
  --horizon-bars 6
```

> 注意：  
> 当前 `evaluate_from_logs.py` 只对 1h 概率 + 阈值做决策，还**没有引入 4h/1d 过滤**。  
> 当实盘策略中真正接入多周期过滤后，可以再对该脚本做相同的修改，使评估行为和实盘一致。

---

## 5. 后续工作方向（建议）

1. **实盘策略接入多周期过滤**
   - 在实盘策略引擎中维护 4h/1d K 线与趋势；
   - 按照方案 B 在 1h 候选信号上做过滤；
   - 确保“单仓、不叠仓”的执行逻辑与回测一致。

2. **线上评估脚本对齐多周期逻辑**
   - 将 `backtest_event_v3_http.py` 中的 `trend_4h_at/ trend_1d_at` 逻辑移植到 `evaluate_from_logs.py`；
   - 在从日志中还原决策时，应用同样的 4h/1d 过滤规则；
   - 这样，离线回测 / 实盘执行 / 日志评估三者行为完全一致。

3. **长期监控与版本对比**
   - 定期运行线上评估（例如每周），记录：
     - signals/week
     - win_rate
     - avg_ret
     - profit_factor
     - 各种 MDD
   - 未来如有新模型版本或新特征（例如更多 multi-timeframe features），可以重复上述流程、对比版本表现。
