# MT_FILTER 统一多周期过滤与执行确认层

**版本**：v1.0（2026-03）

---

## 1. 背景与目标

本系统以 **1h** 为主决策周期，配合 **4h / 1d** 多周期趋势过滤。历史上各脚本各自维护了一套独立的 4h/1d 过滤逻辑，导致口径不一致：

| 脚本 | 原有过滤方式 |
|------|------------|
| `backtest_event_v3_http.py` | Scheme B / symmetric 风格 |
| `evaluate_from_logs.py` | 硬编码 symmetric 逻辑 |
| `report_threshold_grid.py` | 硬编码 symmetric 逻辑 |
| `generate_daily_report.py` | strict / relaxed / regime / conflict 四模式 |
| `live_trader_eth_perp_binance.py` | (side, weight) 软过滤 |

**目标**：
1. 统一一套 `mt_gate` 函数作为 4h/1d 过滤的公共来源。
2. 引入 15m 执行确认层（不改 1h 主模型）。
3. 关键脚本均可通过 `--mt-filter-mode layered` 切换到新统一逻辑。
4. 默认行为保持兼容，渐进引入。

---

## 2. 统一 Gate 模块：`scripts/mt_filter.py`

### 2.1 mt_gate — 4h/1d 分层过滤

```python
from mt_filter import mt_gate, gate_allows, gate_is_strong

result = mt_gate(side, t4, t1d)
# result: "ALLOW_STRONG" | "ALLOW_WEAK" | "REJECT"
```

**LONG 规则**：

| 4h 趋势 | 1d 趋势 | 结果 |
|---------|---------|------|
| UP      | UP      | ALLOW_STRONG |
| UP      | NEUTRAL | ALLOW_WEAK |
| NEUTRAL | UP      | ALLOW_WEAK |
| UP      | DOWN    | REJECT（1d 反向） |
| DOWN    | 任意    | REJECT（4h 反向） |
| 任意    | DOWN    | REJECT（1d 反向） |
| NEUTRAL | NEUTRAL | REJECT |
| NEUTRAL | DOWN    | REJECT |

**SHORT 规则**（与 LONG 对称）：

| 4h 趋势 | 1d 趋势 | 结果 |
|---------|---------|------|
| DOWN    | DOWN    | ALLOW_STRONG |
| DOWN    | NEUTRAL | ALLOW_WEAK |
| NEUTRAL | DOWN    | ALLOW_WEAK |
| DOWN    | UP      | REJECT（1d 反向） |
| UP      | 任意    | REJECT（4h 反向） |
| 任意    | UP      | REJECT（1d 反向） |
| NEUTRAL | NEUTRAL | REJECT |
| NEUTRAL | UP      | REJECT |

**与原 symmetric 模式的区别**：

新增允许的情况：
- LONG：4h==NEUTRAL and 1d==UP → ALLOW_WEAK（原来被拒绝）
- SHORT：4h==NEUTRAL and 1d==DOWN → ALLOW_WEAK（原来被拒绝）

其他情况与 symmetric 模式等价。

### 2.2 exec_confirm_15m — 15m 执行确认

```python
from mt_filter import exec_confirm_15m, ENTER, WAIT, CANCEL

result = exec_confirm_15m(side, klines_15m, enabled=True)
# result: "ENTER" | "WAIT" | "CANCEL"
```

**用途**：
- 不改变 1h 主模型信号方向。
- 在入场前做轻量技术确认，过滤明显逆向的入场时机。

**LONG 评分规则**：

| 条件 | 满足得分 |
|------|---------|
| 15m close > EMA(20) | +1 |
| RSI(14) > 50 | +1 |
| 最新一根收盘 > 前一根收盘 | +1 |

- score ≥ 2 → **ENTER**
- score == 0 → **CANCEL**（明显逆向）
- 其他 → **WAIT**

**SHORT 规则**（对称）：close < EMA20, RSI < 50, 最新 close < 前一根 close。

**降级容错**：
- 若 `enabled=False`，直接返回 `ENTER`
- 若 `klines_15m` 长度不足（< 2 根），返回 `WAIT`（保守放行）

---

## 3. 各脚本接入方式

### 3.1 backtest_event_v3_http.py

新增 `--mt-filter-mode layered` 选项：

```bash
# 使用原有 symmetric 模式（默认保持不变）
python scripts/backtest_event_v3_http.py --data-dir data --mt-filter-mode symmetric

# 使用新 layered gate（稍宽松：允许 4h NEUTRAL + 1d 同向）
python scripts/backtest_event_v3_http.py --data-dir data --mt-filter-mode layered
```

可选值：`off` | `long_only` | `symmetric` | `layered`（新增）

默认：`long_only`（不变）

### 3.2 evaluate_from_logs.py

新增 `--mt-filter-mode` 参数（默认 `symmetric`，保持原有行为）：

```bash
# 原有行为（symmetric，默认）
python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --threshold 0.55 --tp 0.0175 --sl 0.007

# 使用新 layered gate
python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --threshold 0.55 --tp 0.0175 --sl 0.007 \
  --mt-filter-mode layered
```

可选值：`symmetric` | `layered`

### 3.3 report_threshold_grid.py

新增 `--mt-filter-mode` 参数（默认 `symmetric`，保持原有行为）：

```bash
# 使用新 layered gate 进行阈值网格分析
python scripts/report_threshold_grid.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --tp 0.0175 --sl 0.007 \
  --mt-filter-mode layered
```

可选值：`symmetric` | `layered`

### 3.4 generate_daily_report.py

新增 `layered` 作为 `--mt-filter-mode` 的可选值（默认仍为 `conflict`）：

```bash
# 使用 layered gate 生成日报
python scripts/generate_daily_report.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --tp 0.0175 --sl 0.007 --threshold 0.55 \
  --mt-filter-mode layered
```

可选值：`strict` | `relaxed` | `regime` | `conflict`（默认） | `layered`（新增）

### 3.5 live_trader_eth_perp_binance.py

新增两个可选 CLI 参数（默认关闭，保持原有行为）：

```bash
# 默认（legacy 模式，原有行为）
python scripts/live_trader_eth_perp_binance.py

# 使用新 layered gate
python scripts/live_trader_eth_perp_binance.py --use-layered-gate

# 同时启用 15m 执行确认（需要 data/klines_15m.json）
python scripts/live_trader_eth_perp_binance.py --use-layered-gate --use-15m-confirm
```

---

## 4. 与 MTTrendContext 的关系

`mt_filter.py` 中的 `mt_gate` 只接受已计算好的趋势标签（`'UP'` / `'DOWN'` / `'NEUTRAL'`），不依赖特定趋势计算方式。

**推荐用法**（配合 `mt_trend_utils.MTTrendContext`）：

```python
from mt_trend_utils import MTTrendContext
from mt_filter import mt_gate, gate_allows

mt_ctx = MTTrendContext(klines_4h=klines_4h, klines_1d=klines_1d)

t4 = mt_ctx.trend_4h_at(signal_ts)
t1d = mt_ctx.trend_1d_at(signal_ts)

gate_result = mt_gate(side, t4, t1d)
if gate_allows(gate_result):
    # 继续入场逻辑
    pass
```

---

## 5. 兼容性说明

| 脚本 | 默认模式 | 是否有行为变化 |
|------|---------|--------------|
| `backtest_event_v3_http.py` | `long_only` | 无变化 |
| `evaluate_from_logs.py` | `symmetric` | 无变化 |
| `report_threshold_grid.py` | `symmetric` | 无变化 |
| `generate_daily_report.py` | `conflict` | 无变化 |
| `live_trader_eth_perp_binance.py` | legacy | 无变化（`--use-layered-gate` 需显式开启） |

**新 `layered` 模式与 `symmetric` 模式的区别**：
- `layered` 比 `symmetric` 稍宽松：允许「4h 中性 + 1d 同向」的弱放行
- `symmetric` 要求 4h 必须同向，不允许 4h 中性

---

## 6. 扩展建议

### 6.1 利用 ALLOW_STRONG / ALLOW_WEAK 做差异化仓位
```python
from mt_filter import mt_gate, ALLOW_STRONG, ALLOW_WEAK

gate = mt_gate(side, t4, t1d)
if gate == ALLOW_STRONG:
    position_size = normal_size
elif gate == ALLOW_WEAK:
    position_size = normal_size * 0.7  # 弱放行用更小仓位
```

### 6.2 未来扩展点
- 为 `mt_gate` 增加趋势强度参数（基于 ADX 或 EMA spread）
- 为 `exec_confirm_15m` 增加 higher-low / pullback 确认
- 将 4h/1d/15m 趋势特征正式并入模型训练
