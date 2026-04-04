# MT_FILTER 统一多周期过滤与执行确认层

**版本**：v1.1（2026-04）

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
5. **v1.1 新增**：支持从 `configs/symbols.yaml` 按币种读取 `mt_filter_mode`，优先级为 CLI > YAML > 内置默认值。

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

`--mt-filter-mode` 优先级：**CLI > `configs/symbols.yaml` 中该币的 `mt_filter_mode` > 内置默认值（`daily_guard`）**

```bash
# 省略 --mt-filter-mode → 自动读取 configs/symbols.yaml 中 BTCUSDT 的 mt_filter_mode
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir data/BTCUSDT --symbol BTCUSDT \
  --base-url http://127.0.0.1:9000 \
  --since 2026-03-01T00:00:00Z --until 2026-03-10T00:00:00Z \
  --position-mode single

# 显式覆盖（CLI 优先级最高）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir data/BTCUSDT --symbol BTCUSDT \
  --base-url http://127.0.0.1:9000 \
  --since 2026-03-01T00:00:00Z --until 2026-03-10T00:00:00Z \
  --position-mode single --mt-filter-mode daily_guard
```

输出会打印：
- `[config:debug] loaded symbol config for BTCUSDT from …/scripts/symbol_paths.py`
- `[config] symbol=BTCUSDT … mt_filter_mode=off (YAML)` — 括号里说明来源

可选值：`off` | `long_only` | `symmetric` | `strict` | `relaxed` | `trend_guard` | `daily_guard` | `conflict` | `regime` | `layered`

**内置默认：`daily_guard`**（当 CLI 未传且 YAML 中无此字段时生效）

### 3.1a live_trader_perp_simulated.py（PR #29 新增，v1.1 更新）

`--mt-filter-mode` 优先级：**CLI > `configs/symbols.yaml` 中该币的 `mt_filter_mode` > 内置默认值（`daily_guard`）**

```bash
# 省略 --mt-filter-mode → 自动读取 YAML 中 BTCUSDT 的 mt_filter_mode
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_perp_simulated.py \
  --symbol BTCUSDT --side-source probs

# 显式指定（CLI 覆盖 YAML）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_perp_simulated.py \
  --symbol BTCUSDT --mt-filter-mode daily_guard --side-source probs

# --all-symbols：每个币自动从 YAML 读取其 mt_filter_mode
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_perp_simulated.py \
  --all-symbols --since 2026-03-01T00:00:00Z --until 2026-03-10T00:00:00Z

# 对齐验证：使用回测生成的 pred_cache 读取预测，确保输入完全一致
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_perp_simulated.py \
  --symbol BTCUSDT \
  --since 2026-03-01T00:00:00Z --until 2026-03-10T00:00:00Z \
  --mt-filter-mode daily_guard \
  --pred-cache-file data/pred_cache/pred_cache__<hash>.jsonl
```

每次运行会打印：`[sim:BTCUSDT] mt_filter_mode=off (YAML)` — 括号里说明来源。

可选值（与回测完全对齐）：  
`--mt-filter-mode`：同 backtest，默认从 YAML 读取  
`--side-source {signal,probs}`：默认 `probs`  
`--timeout-exit {close,open_next}`：默认 `close`  
`--tie-breaker {SL,TP}`：默认 `SL`  
`--position-mode {single,stack}`：默认 `single`

### 3.1b live_trader_perp_binance.py（v1.1 新增）

`--mt-filter-mode` 优先级：**CLI > `configs/symbols.yaml` 中该币的 `mt_filter_mode` > 内置默认值（`daily_guard`）**

过滤现在使用 `apply_mt_filter_with_context`（与回测/模拟语义完全一致），不再依赖旧的 (side, weight) 软过滤。

```bash
# 省略 --mt-filter-mode → 自动读取 YAML 中该币的 mt_filter_mode（DRY-RUN）
python scripts/live_trader_perp_binance.py --symbol BTCUSDT

# 显式指定 mt_filter_mode（CLI 优先级最高）
python scripts/live_trader_perp_binance.py --symbol BTCUSDT --mt-filter-mode off

# --all-symbols：每个币自动从 YAML 读取其 mt_filter_mode，启动时打印
python scripts/live_trader_perp_binance.py --all-symbols
# 输出类似：
#   [config] BTCUSDT mt_filter_mode=off (YAML)
#   [config] BNBUSDT mt_filter_mode=daily_guard (YAML)

# 多币指定，共享同一 mt_filter_mode（CLI 优先级最高）
python scripts/live_trader_perp_binance.py --symbols BTCUSDT,ETHUSDT --mt-filter-mode daily_guard
```

可选值：同 backtest，`off` | `long_only` | `symmetric` | `strict` | `relaxed` | `trend_guard` | `daily_guard` | `conflict` | `regime` | `layered`

> **向后兼容**：`--use-layered-gate` 和 `--use-15m-confirm` 参数保留。但当 `--mt-filter-mode` 被设置（CLI 或 YAML）时，过滤完全由 `apply_mt_filter_with_context` 接管，`--use-layered-gate` 不再影响过滤逻辑。

### 3.2 evaluate_from_logs.py

新增 `--mt-filter-mode` 参数（默认 `symmetric`，保持原有行为）：

```bash
# 原有行为（symmetric，默认）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --threshold 0.55 --tp 0.0175 --sl 0.007

# 使用新 layered gate
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --threshold 0.55 --tp 0.0175 --sl 0.007 \
  --mt-filter-mode layered
```

可选值：`symmetric` | `layered`

### 3.3 report_threshold_grid.py

新增 `--mt-filter-mode` 参数（默认 `symmetric`，保持原有行为）：

```bash
# 使用新 layered gate 进行阈值网格分析
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_threshold_grid.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --tp 0.0175 --sl 0.007 \
  --mt-filter-mode layered
```

可选值：`symmetric` | `layered`

### 3.4 generate_daily_report.py

新增 `layered` 作为 `--mt-filter-mode` 的可选值（默认仍为 `conflict`）：

```bash
# 使用 layered gate 生成日报
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/generate_daily_report.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --tp 0.0175 --sl 0.007 --threshold 0.55 \
  --mt-filter-mode layered
```

可选值：`strict` | `relaxed` | `regime` | `conflict`（默认） | `layered`（新增）

### 3.5 live_trader_eth_perp_binance.py

新增两个可选 CLI 参数（默认关闭，保持原有行为）：

```bash
# 默认（legacy 模式，原有行为）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_eth_perp_binance.py

# 使用新 layered gate
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_eth_perp_binance.py --use-layered-gate

# 同时启用 15m 执行确认（需要 data/klines_15m.json）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_eth_perp_binance.py --use-layered-gate --use-15m-confirm
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

| 脚本 | 默认 mt_filter_mode | 来源 | 是否有行为变化 |
|------|---------------------|------|--------------|
| `backtest_event_v3_http.py` | YAML 中该币 > `daily_guard` | CLI > YAML > default | ✅ v1.1 新增 YAML 读取 |
| `live_trader_perp_simulated.py` | YAML 中该币 > `daily_guard` | CLI > YAML > default | ✅ v1.1 新增 YAML 读取 |
| `live_trader_perp_binance.py` | YAML 中该币 > `daily_guard` | CLI > YAML > default | ✅ v1.1 新增，替换旧 (side,weight) 过滤 |
| `evaluate_from_logs.py` | `symmetric` | CLI | 无变化 |
| `report_threshold_grid.py` | `symmetric` | CLI | 无变化 |
| `generate_daily_report.py` | `conflict` | CLI | 无变化 |
| `live_trader_eth_perp_binance.py` | legacy | CLI flag | 无变化（`--use-layered-gate` 需显式开启） |

**新 `layered` 模式与 `symmetric` 模式的区别**：
- `layered` 比 `symmetric` 稍宽松：允许「4h 中性 + 1d 同向」的弱放行
- `symmetric` 要求 4h 必须同向，不允许 4h 中性

### 5.1 v1.1 变更：import 路径修正

v1.1 将三个脚本的 symbol_config 导入从顶层 `from symbol_config import ...` 改为包导入 `from scripts.symbol_config import ...`，以避免 `symbol_config` 顶层模块名冲突（旧方式在 sys.path 中只有 `scripts/` 时无法找到 `scripts.symbol_paths`，导致 `_get_symbol_config` 回退为 `None`，YAML 配置完全失效）。

---

## 7. 推荐使用方式（Recommended Usage Guidance）

### 7.1 当前阶段推荐配置

| 链路 | 推荐配置 | 说明 |
|------|---------|------|
| 回测 `backtest_event_v3_http.py` | 省略 `--mt-filter-mode`，由 YAML 自动读取 | 每币独立配置，保持精确的历史对比口径 |
| 模拟交易 `live_trader_perp_simulated.py` | 省略 `--mt-filter-mode`，由 YAML 自动读取 | 与回测完全对齐 |
| 实盘 DRY-RUN `live_trader_perp_binance.py` | 省略 `--mt-filter-mode`，由 YAML 自动读取 | 与回测/模拟口径一致 |
| 日志评估 `evaluate_from_logs.py` | `--mt-filter-mode symmetric`（默认，不变） | 与回测 Scheme B 口径对齐 |
| 阈值网格 `report_threshold_grid.py` | `--mt-filter-mode symmetric`（默认，不变） | 与回测 Scheme B 口径对齐 |
| 日报 `generate_daily_report.py` | `--mt-filter-mode conflict`（默认，不变） | 生产稳定配置 |
| 实盘真实开仓 | 暂不推荐直接 PROD | 需完成 Dry-Run 统计验证 |

**一句话原则**：

> 生产脚本的 mt_filter_mode 由 `configs/symbols.yaml` 集中管理；只有在调试或临时对比时才在 CLI 显式覆盖。

---

### 7.2 ⚠️ 15m 执行确认层的接入范围（重要）

**当前版本（v1.1）15m 执行确认层（`exec_confirm_15m`）仅接入了 `live_trader_eth_perp_binance.py` 和 `live_trader_perp_binance.py`**。

| 脚本 | 是否支持 15m confirm |
|------|-------------------|
| `live_trader_eth_perp_binance.py` | ✅ 可选，`--use-15m-confirm` |
| `live_trader_perp_binance.py` | ✅ 可选，`--use-15m-confirm` |
| `backtest_event_v3_http.py` | ❌ 尚未接入 |
| `evaluate_from_logs.py` | ❌ 尚未接入 |
| `report_threshold_grid.py` | ❌ 尚未接入 |
| `generate_daily_report.py` | ❌ 尚未接入 |

**后果**：若在实盘中开启 `--use-15m-confirm`，回测与实盘将存在**口径差异**——回测不会过滤 15m 逆向入场，实盘会。这会导致信号覆盖率和效果归因的解读存在偏差。

**建议**：
1. v1.1 阶段：仅在 Dry-Run 实盘中灰度开启 `--use-15m-confirm`，观察信号覆盖变化。
2. 后续版本：在 `backtest_event_v3_http.py` 和 `evaluate_from_logs.py` 中补充 15m confirm 回测支持（通过加载 `data/klines_15m.json`），以关闭回测/实盘口径差异。

---

### 7.3 layered 模式的验证建议

开启 `--mt-filter-mode layered` 后，建议与 `symmetric` 模式进行对比，重点关注：

| 指标 | 关注方向 |
|------|---------|
| coverage（交易数/预测数） | layered 应略高于 symmetric（因放宽 4h=NEUTRAL 情况） |
| win rate（胜率） | 若 win rate 明显下降，说明新放行的信号质量较差 |
| avg return（平均收益） | 综合收益是否提升 |
| max drawdown（最大回撤） | 不应因放宽过滤而显著恶化 |
| timeout ratio（超时比例） | 较高 timeout 说明新放行信号方向感不足 |
| ALLOW_WEAK vs ALLOW_STRONG 分布 | 弱放行信号的 win rate 是否低于强放行 |

**核心判断**：`layered` 放宽的「4h 中性 + 1d 同向」信号，究竟是在**提纯**（保留高质量信号）还是**放宽过度**（引入更多噪声）？

建议先通过 `report_threshold_grid.py --mt-filter-mode layered` 与 `--mt-filter-mode symmetric` 对比运行，输出两份报表做横向比较，再决定是否在生产中推广。

---

## 8. 扩展建议

### 8.1 利用 ALLOW_STRONG / ALLOW_WEAK 做差异化仓位
```python
from mt_filter import mt_gate, ALLOW_STRONG, ALLOW_WEAK

gate = mt_gate(side, t4, t1d)
if gate == ALLOW_STRONG:
    position_size = normal_size
elif gate == ALLOW_WEAK:
    position_size = normal_size * 0.7  # 弱放行用更小仓位
```

### 8.2 未来扩展点
- 为 `mt_gate` 增加趋势强度参数（基于 ADX 或 EMA spread）
- 为 `exec_confirm_15m` 增加 higher-low / pullback 确认
- 将 4h/1d/15m 趋势特征正式并入模型训练
- 在 `backtest_event_v3_http.py` 等脚本中补充 15m confirm 回测支持，消除回测/实盘口径差异

