# ubuntu-wallet 第二阶段任务拆分（可直接创建 GitHub Issue）

> 本文档用于将当前系统从“准生产研究系统”推进为“可持续维护的生产候选系统”。
> 每条任务都可直接拆成 GitHub Issue。

---

# 一、P0：必须优先完成

## Issue 1：将 4h / 1d 多周期特征正式并入训练特征
### 标题建议
`feat: add 4h and 1d multi-timeframe features into training and online inference`

### 目标
将当前主要作为“过滤器”的多周期逻辑，升级为训练阶段和推理阶段共享的正式特征。

### 任务内容
- 在 `python-analyzer/` 中新增或扩展多周期特征构建模块
- 将 4h / 1d 趋势特征并入训练数据集
- 在 `ml-service/feature_builder.py` 中同步支持相同 schema
- 输出并保存新版 `feature_schema.json`
- 更新文档，说明多周期特征来源、对齐方式和使用方法

### 验收标准
- 训练侧与线上推理侧 feature schema 一致
- walk-forward CV 可正常跑
- `/predict` 可正常返回结果
- 文档说明清晰

---

## Issue 2：引入 model registry 与当前生产模型指针
### 标题建议
`feat: add model registry, production model pointer, and rollback support`

### 目标
建立模型注册与切换机制，避免后期无法追踪当前生产模型。

### 任务内容
- 新增 `data/models/registry.json`
- 记录：
  - model_version
  - train period
  - label config
  - threshold config
  - calibration method
  - summary metrics
  - created_at
  - status（candidate/prod/archived）
- 增加生产模型指针（如 `current.json` 或 `current/` 软链接）
- 为 `ml-service/model_loader.py` 增加从 registry/current 读取模型的逻辑
- 增加回滚脚本

### 验收标准
- 可明确知道当前线上模型是谁
- 可回滚到上一个稳定版本
- 切换后 `/healthz` 能正确反映 model_version

---

## Issue 3：增加阈值网格报告工具
### 标题建议
`feat: add threshold grid report for precision, coverage, and pnl trade-off`

### 目标
用统一工具分析不同 threshold 对策略质量的影响。

### 任务内容
- 新增脚本，例如 `scripts/report_threshold_grid.py`
- 输入 prediction log 和市场数据
- 对 threshold 网格（如 0.55~0.75）输出：
  - precision
  - coverage
  - avg return
  - max drawdown
  - LONG / SHORT 分别表现
- 支持 CSV / JSON 输出

### 验收标准
- 可一键跑出 threshold 报告
- 结果可用于指导正式阈值选择

---

## Issue 4：增加每日评估报告自动生成
### 标题建议
`feat: generate daily markdown/json evaluation reports from prediction logs`

### 目标
让评估结果不仅停留在终端输出，而是生成结构化日报。

### 任务内容
- 新增脚本，例如 `scripts/generate_daily_report.py`
- 输出：
  - `daily_eval_YYYY-MM-DD.json`
  - `daily_eval_YYYY-MM-DD.md`
- 报告包含：
  - model_version
  - total predictions
  - coverage
  - precision
  - win rate
  - avg return
  - max drawdown
  - TP/SL/TIMEOUT 分布
  - LONG / SHORT 分方向结果
- 增加 systemd service/timer

### 验收标准
- 每日自动生成报告
- 报告文件可被后续监控或人工查看

---

# 二、P1：高价值增强

## Issue 5：接入 funding rate / open interest / taker ratio 等外生因子
### 标题建议
`feat: add exogenous market features such as funding rate, open interest, and taker imbalance`

### 目标
提升高置信度信号质量，减少单纯 K 线特征的局限。

### 任务内容
- 扩展 `go-collector/` 数据采集
- 保存 funding / OI / taker imbalance 等数据
- 在训练和推理侧增加特征构造
- 更新 schema 和文档

### 验收标准
- 新因子可进入训练
- 在线推理可读取相同因子
- schema 一致

---

## Issue 6：增加 feature drift / data drift 监控
### 标题建议
`feat: add feature drift monitoring and schema-health reporting`

### 目标
防止模型在生产环境中悄然失效。

### 任务内容
- 新增 drift 检查脚本
- 对比训练分布与当前分布
- 统计：
  - missing rate
  - mean/std drift
  - PSI（如果实现）
  - confidence distribution drift
- 输出日报或告警日志

### 验收标准
- 能自动发现线上��征分布异常
- 可定位哪些特征漂移严重

---

## Issue 7：增加校准质量报告
### 标题建议
`feat: add calibration quality report with reliability curve and bin statistics`

### 目标
让概率校准不只“存在”，而是可被验证。

### 任务内容
- 新增 `python-analyzer/calibration_report.py`
- 输出：
  - reliability curve
  - binned expected vs observed hit rate
  - Brier 分解（如可行）
- 支持 png/csv/json 报告

### 验收标准
- 能清晰看到 0.6 / 0.7 / 0.8 置信度区间是否可靠

---

# 三、P2：生产可维护性增强

## Issue 8：补充中文部署文档
### 标题建议
`docs: add detailed Chinese deployment guide for full ubuntu-wallet stack`

### 目标
让新维护者能在一台新服务器上完整部署系统。

### 任务内容
- 新增 `docs/DEPLOY_CN.md`
- 内容包括：
  - 系统要求
  - Python/Go 环境安装
  - 仓库克隆
  - venv 配置
  - data 目录准备
  - `.env` 配置
  - systemd 部署
  - 启动与验证

### 验收标准
- 按文档操作可完成从 0 到 1 部署

---

## Issue 9：补充中文运维手册
### 标题建议
`docs: add Chinese operations runbook for daily maintenance and troubleshooting`

### 目标
让系统具备交接能力和长期维护能力。

### 任务内容
- 新增 `docs/RUNBOOK_CN.md`
- 内容包括：
  - 日常检查
  - 常用命令
  - 日志查看
  - 模型切换
  - 回滚
  - 常见错误排查

### 验收标准
- 运维人员可按文档完成基础维护

---

## Issue 10：增加模型生命周期文档
### 标题建议
`docs: add model lifecycle documentation for training, validation, deployment, evaluation, and retirement`

### 目标
明确模型从训练到退役的整个流程。

### 任务内容
- 新增 `docs/MODEL_LIFECYCLE_CN.md`
- 说明：
  - 数据准备
  - 训练
  - CV
  - calibration
  - candidate
  - production
  - evaluation
  - rollback
  - retirement

### 验收标准
- 模型上线流程清晰、可执行、可复盘

---

## Issue 11：增加失败模式与灾难恢复文档
### 标题建议
`docs: add failure modes and recovery guide for collector, ml-service, and evaluation loop`

### 目标
降低线上故障恢复成本。

### 任务内容
- 新增 `docs/FAILURE_MODES_CN.md`
- 覆盖：
  - collector 停止更新
  - ml-service 启动失败
  - `/predict` 报错
  - prediction log 不写
  - calibration artifact 缺失
  - systemd timer 不触发
  - 数据时间错位

### 验收标准
- 常见故障有明确恢复路径

---

# 四、P3：实盘前增强

## Issue 12：增强 ETH 永续风险引擎
### 标题建议
`feat: strengthen perp risk engine with daily loss cap, streak breaker, and market anomaly guards`

### 目标
在真正上实盘前，把风险壳从基础版升级到更稳健版本。

### 任务内容
- 增加：
  - 当日最大亏损停机
  - 最大连续亏损停机
  - 异常波动停机
  - 数据延迟停机
  - 重复开仓保护
- 更新风控文档

### 验收标准
- 风控逻辑可单元测试或模拟测试
- 在异常条件下不会继续开仓

---

## Issue 13：增加自动重训与候选模型比较
### 标题建议
`feat: add scheduled retraining and candidate-vs-production comparison pipeline`

### 目标
让系统进入半自动优化模式。

### 任务内容
- 增加定时重训脚本
- 新模型训练完成后自动跑 walk-forward 和基础评估
- 与生产模型比较
- 生成候选评估报告

### 验收标准
- 不需要手工重复执行完整训练链路
- 候选模型有可比较的报告

---

## Issue 14：增加模型晋升与回滚自动化
### 标题建议
`feat: automate promote and rollback workflow for production models`

### 目标
让上线/回滚不依赖手工改文件路径。

### 任务内容
- `scripts/promote_model.py`
- `scripts/rollback_model.py`
- 更新 registry 状态
- 更新 current pointer
- 重启/热加载 ml-service（视现有架构而定）

### 验收标准
- 一条命令即可晋升
- 一条命令即可回滚

---

# 五、推荐的 Issue 创建顺序

推荐顺序如下：

1. 多周期特征进训练
2. model registry
3. threshold grid report
4. daily report
5. 外生因子
6. drift monitor
7. calibration report
8. 中文部署文档
9. 中文运维手册
10. 模型生命周期文档
11. 失败模式文档
12. 风控壳增强
13. 自动重训
14. 自动晋升/回滚

---

# 六、建议的里程碑

## Milestone 1：稳定高 precision 基线
- Issue 1
- Issue 2
- Issue 3
- Issue 4

## Milestone 2：提高泛化与抗漂移能力
- Issue 5
- Issue 6
- Issue 7

## Milestone 3：补齐生产运维
- Issue 8
- Issue 9
- Issue 10
- Issue 11

## Milestone 4：实盘前准备
- Issue 12
- Issue 13
- Issue 14
