# ubuntu-wallet 第二阶段任务拆分（可直接创建 GitHub Issue）

> 本文档用于将当前系统从"准生产研究系统"推进为"可持续维护的生产候选系统"。
> 每条任务都可直接拆成 GitHub Issue。
>
> **状态说明**（2026-03 审计基于仓库现状）：
> - ✅ 已实现 — 相关脚本/模块存在且覆盖了验收标准
> - 🟡 部分实现 — 核心框架已有，但存在缺口
> - ❌ 未开始 — 尚无对应实现

---

# 一、P0：必须优先完成

## Issue 1：将 4h / 1d 多周期特征正式并入训练特征
### 标题建议
`feat: add 4h and 1d multi-timeframe features into training and online inference`

### 实现状态
✅ **已实现**

### 证据
- `ml-service/feature_builder.py`：`build_multi_tf_feature_df()`、`build_event_v3_feature_row()`、`get_feature_columns_like_trainer()` 均已包含 `tf4h_*` / `tf1d_*` 特征列。
- `scripts/mt_trend_utils.py`：多周期趋势辅助工具。
- `tests/test_p0_pointer_and_schema.py`（`test_training_schema_includes_formal_4h_and_1d_features`）：明确断言 `tf4h_` 和 `tf1d_` 列存在于训练 schema。
- 对应 Feature Flag：`ENABLE_MTF_FEATURES`（S0 及以上默认开启）。

### 剩余缺口
无（训练侧与推理侧 schema 已对齐）。

---

## Issue 2：引入 model registry 与当前生产模型指针
### 标题建议
`feat: add model registry, production model pointer, and rollback support`

### 实现状态
🟡 **部分实现**

### 证据（已有）
- `ml-service/model_loader.py`：`get_prod_registry_entry()`、`find_registry_path()`、`load_model()` 支持从 `models/current/`（目录指针）加载，并可交叉校验 `registry.json`。
- `scripts/rollback_model.py`：完整的回滚脚本，操作 `models/registry.json` + `models/current/` 目录，支持 `--dry-run`。
- `ml-service/app.py`：`/healthz` 回显 `registry_path`、`model_version`、`status`。
- 对应 Feature Flag：`ENABLE_MODEL_REGISTRY`（S1 及以上默认开启）。

### 剩余缺口
- ❌ **`scripts/promote_model.py` 不存在**：晋升候选模型到生产需要手工操作，尚无自动化脚本。
- 🟡 `registry.json` 的初始化由 `python-analyzer/train_event_stack_v3.py` 完成，但若首次部署或手工训练，需手动创建 `registry.json`（文档中尚无一键初始化说明）。

---

## Issue 3：增加阈值网格报告工具
### 标题建议
`feat: add threshold grid report for precision, coverage, and pnl trade-off`

### 实现状态
✅ **已实现**

### 证据
- `scripts/report_threshold_grid.py`（466 行）：完整的阈值网格分析工具，支持 CSV/JSON 输出，覆盖 precision、coverage、avg return、max drawdown、LONG/SHORT 分别统计。
- 对应 Feature Flag：`ENABLE_THRESHOLD_GRID_REPORT`（S0 及以上默认开启）。

### 剩余缺口
无。

---

## Issue 4：增加每日评估报告自动生成
### 标题建议
`feat: generate daily markdown/json evaluation reports from prediction logs`

### 实现状态
✅ **已实现**

### 证据
- `scripts/generate_daily_report.py`（555 行）：从 prediction log 生成 `daily_eval_YYYY-MM-DD.json` 与 `.md`，包含 model_version、total predictions、coverage、precision、win rate、avg return、max drawdown、TP/SL/TIMEOUT 分布、LONG/SHORT 方向结果。
- `systemd/daily-report.service` + `systemd/daily-report.timer`：每日 01:05 UTC 自动触发，日志写入 `logs/daily_report.log`。
- 对应 Feature Flag：`ENABLE_DAILY_EVAL_REPORT`（S1 及以上默认开启）。

### 剩余缺口
无。

---

# 二、P1：高价值增强

## Issue 5：接入 funding rate / open interest / taker ratio 等外生因子
### 标题建议
`feat: add exogenous market features such as funding rate, open interest, and taker imbalance`

### 实现状态
❌ **未开始**

### 证据（基础设施已有）
- `go-collector/collector/`：已有 `binance.go`、`okx.go`、`coinbase.go` 数据采集框架。
- `go-collector/features/`：`compute.go`、`indicators.go`、`storage.go` 等特征计算工具。
- 但目前尚无 funding rate / OI / taker ratio 等外生因子的采集、存储或特征构造逻辑。
- 对应 Feature Flag：`ENABLE_EXOGENOUS_FEATURES`（**所有 Stage 默认关闭**，需手动覆盖）。

### 剩余缺口
- 扩展 `go-collector/` 采集 funding/OI/taker imbalance。
- 训练侧特征构造对齐。
- 推理侧 `feature_builder.py` 读取新因子。
- 更新 `feature_schema.json`。

---

## Issue 6：增加 feature drift / data drift 监控
### 标题建议
`feat: add feature drift monitoring and schema-health reporting`

### 实现状态
❌ **未开始**

### 证据（当前无对应实现）
- 未找到 drift 检查脚本。
- `scripts/evaluate_from_logs.py` 提供历史评估，但不做特征分布漂移检测。
- 对应 Feature Flag：`ENABLE_DRIFT_MONITOR`（S2 及以上默认开启）。

### 剩余缺口
- 新增 drift 检查脚本（建议路径：`scripts/check_feature_drift.py`）。
- 对比训练分布与当前分布（missing rate、mean/std drift、PSI）。
- 输出日报或告警日志。

---

## Issue 7：增加校准质量报告
### 标题建议
`feat: add calibration quality report with reliability curve and bin statistics`

### 实现状态
🟡 **部分实现**

### 证据（已有）
- `ml-service/calibration.py`：Isotonic / Sigmoid 校准器封装，支持在推理时应用校准结果，`/predict` 响应中已回显 `calibrated_confidence` 与 `calibration_method`。
- `ml-service/prediction_logger.py`：`predictions_log.jsonl` 结构包含 `cal_proba_long`、`cal_proba_short`、`calibration_method` 字段，为离线校准分析提供了数据基础。
- 对应 Feature Flag：`ENABLE_CALIBRATION_REPORT`（S2 及以上默认开启）。

### 剩余缺口
- ❌ `python-analyzer/calibration_report.py` **不存在**：无 reliability curve、bin expected vs observed hit rate、Brier 分解等离线质量报告。

---

# 三、P2：生产可维护性增强

## Issue 8：补充中文部署文档
### 标题建议
`docs: add detailed Chinese deployment guide for full ubuntu-wallet stack`

### 实现状态
✅ **已实现**

### 证据
- `docs/DEPLOY_CN.md`（24502 字节）：详细的中文部署指南，覆盖系统要求、环境安装、仓库克隆、venv 配置、data 目录准备、`.env` 配置、systemd 部署、启动与验证。

---

## Issue 9：补充中文运维手册
### 标题建议
`docs: add Chinese operations runbook for daily maintenance and troubleshooting`

### 实现状态
✅ **已实现**

### 证据
- `docs/RUNBOOK_CN.md`（14328 字节）：中文运维手册，覆盖日常检查、常用命令、日志查看、模型切换、回滚、常见错误排查。

---

## Issue 10：增加模型生命周期文档
### 标题建议
`docs: add model lifecycle documentation for training, validation, deployment, evaluation, and retirement`

### 实现状态
✅ **已实现**

### 证据
- `docs/MODEL_LIFECYCLE_CN.md`（55637 字节）：极详细的模型生命周期文档，覆盖数据准备、训练、CV、calibration、candidate、production、evaluation、rollback、retirement 全流程。

---

## Issue 11：增加失败模式与灾难恢复文档
### 标题建议
`docs: add failure modes and recovery guide for collector, ml-service, and evaluation loop`

### 实现状态
✅ **已实现**

### 证据
- `docs/FAILURE_MODES_CN.md`（16184 字节）：覆盖 collector 停止更新、ml-service 启动失败、`/predict` 报错、prediction log 不写、calibration artifact 缺失、systemd timer 不触发、数据时间错位等场景及恢复路径。

---

# 四、P3：实盘前增强

## Issue 12：增强 ETH 永续风险引擎
### 标题建议
`feat: strengthen perp risk engine with daily loss cap, streak breaker, and market anomaly guards`

### 实现状态
🟡 **部分实现**

### 证据（已有）
- `docs/ETH_perp_risk_rules.md`（4718 字节）：详细的风控规则文档。
- `scripts/eth_perp_engine_binance.py`：ETH 永续引擎骨架。
- `scripts/live_trader_eth_perp_binance.py`（5638 字节）：实盘交易脚本，包含基础风控逻辑。
- `scripts/live_trader_eth_perp_simulated.py`（19789 字节）：模拟交易脚本（更完整的风控测试版本）。
- 对应 Feature Flag：`ENABLE_PERP_RISK_GUARDS`（S3 及以上默认开启）。

### 剩余缺口
- 需核查：当日最大亏损停机、最大连续亏损停机、异常波动停机、数据延迟停机、重复开仓保护等高级守卫是否已在 `live_trader_eth_perp_binance.py` 中完整实现。
- 建议添加可独立运行的单元测试，验证各守卫逻辑。

---

## Issue 13：增加自动重训与候选模型比较
### 标题建议
`feat: add scheduled retraining and candidate-vs-production comparison pipeline`

### 实现状态
❌ **未开始**

### 证据（当前无对应实现）
- 未找到定时重训脚本或 systemd retrain timer。
- `python-analyzer/train_event_stack_v3.py` 支持手工训练，但无自动化调度。
- 对应 Feature Flag：`ENABLE_SCHEDULED_RETRAIN`（S4 默认开启）。

### 剩余缺口
- 新增定时重训脚本与对应 systemd timer。
- 训练完成后自动运行 walk-forward 评估。
- 与生产模型对比，生成候选评估报告。

---

## Issue 14：增加模型晋升与回滚自动化
### 标题建议
`feat: automate promote and rollback workflow for production models`

### 实现状态
🟡 **部分实现**

### 证据（已有）
- `scripts/rollback_model.py`（6825 字节）：完整的回滚脚本，支持 `--dry-run`，操作 `models/registry.json` + `models/current/` 目录，更新 registry 状态。
- 对应 Feature Flag：`ENABLE_PROMOTE_ROLLBACK_AUTOMATION`（S4 默认开启）。

### 剩余缺口
- ❌ **`scripts/promote_model.py` 不存在**：将候选模型晋升为生产需要手工操作，无自动化脚本。
- 🟡 `scripts/rollback_model.py` 中已有 `_promote_to_current()` 函数，可作为 `promote_model.py` 的基础。
- ml-service 热加载/重启自动化：回滚后需手动 `sudo systemctl restart ml-service`，尚无自动触发机制。

---

# 五、推荐 Issue 创建顺序（基于当前实现状态）

> 原有顺序保留，并基于现状补充建议。

## 优先补齐 P0/P1 缺口（建议立即处理）

1. **Issue 2（补全晋升脚本）**：`rollback_model.py` 已有，补充 `promote_model.py` 代价低，
   但对 registry 闭环至关重要。建议首先完成。
2. **Issue 7（校准质量报告）**：推理侧校准已有，离线报告脚本缺失。
   补充 `python-analyzer/calibration_report.py` 可验证当前校准器是否可信。
3. **Issue 6（drift 监控）**：
   预测日志已记录校准概率分布，drift 脚本可直接复用。
   建议在 S2 上线前完成。

## P2 文档类（已全部完成，无需新建 Issue）

4~7：Issues 8、9、10、11 已全部实现，无需额外工作。

## P3 实盘前（需按顺序推进）

8. **Issue 12（风控增强核查）**：确认 live_trader 实现中的高级守卫是否完整，
   补充单元测试。
9. **Issue 5（外生因子）**：数据源和特征对齐成熟后开启，
   不建议在 S2 之前启动。
10. **Issue 13（自动重训）**：S4 前完成。
11. **Issue 14（自动晋升完善）**：补充 `promote_model.py` + 服务重启自动化。

---

# 六、建议的里程碑（基于当前状态更新）

## Milestone 1：稳定高 precision 基线（**已基本完成**）
- Issue 1 ✅ 已实现
- Issue 2 🟡 补充 `promote_model.py`
- Issue 3 ✅ 已实现
- Issue 4 ✅ 已实现

## Milestone 2：提高泛化与抗漂移能力（**部分完成，需补齐**）
- Issue 5 ❌ 未开始（外生因子）
- Issue 6 ❌ 未开始（drift 监控）
- Issue 7 🟡 需补充校准报告脚本

## Milestone 3：补齐生产运维（**已全部完成**）
- Issue 8 ✅ 已实现
- Issue 9 ✅ 已实现
- Issue 10 ✅ 已实现
- Issue 11 ✅ 已实现

## Milestone 4：实盘前准备（**未开始，建议 S3 达成后推进**）
- Issue 12 🟡 需核查高级守卫完整性
- Issue 13 ❌ 未开始
- Issue 14 🟡 需补充晋升脚本
