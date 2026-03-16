# ubuntu-wallet 中文完整架构说明与部署运维手册

> 仓库：`xionng-sudo/ubuntu-wallet`
> 
> 目标：将本仓库说明为一套可持续维护的加密交易机器学习系统，包括：
> - 数据采集
> - 特征构建
> - 标签生成
> - 模型训练
> - 时间序列验证
> - 概率校准
> - 在线推理
> - 预测日志闭环
> - 策略评估
> - 模拟交易 / DRY-RUN
> - systemd 部署与日常运维
>
> 本文档面向以下人群：
> - 仓库维护者
> - 服务器部署者
> - 后续接手开发者
> - 想理解系统全貌的研究/策略人员

---

# 目录

1. [仓库总体定位](#1-仓库总体定位)
2. [系统总体架构](#2-系统总体架构)
3. [目录结构说明](#3-目录结构说明)
4. [核心数据流](#4-核心数据流)
5. [模块级详细说明](#5-模块级详细说明)
6. [模型与策略逻辑说明](#6-模型与策略逻辑说明)
7. [环境准备与依赖安装](#7-环境准备与依赖安装)
8. [完整部署流程](#8-完整部署流程)
9. [训练流程](#9-训练流程)
10. [在线推理服务使用方法](#10-在线推理服务使用方法)
11. [回测与评估流程](#11-回测与评估流程)
12. [模拟交易与 DRY-RUN 流程](#12-模拟交易与-dry-run-流程)
13. [systemd 部署与调度](#13-systemd-部署与调度)
14. [日常运维手册](#14-日常运维手册)
15. [常见故障排查](#15-常见故障排查)
16. [数据与模型维护规范](#16-数据与模型维护规范)
17. [安全与风控注意事项](#17-安全与风控注意事项)
18. [后续优化建议](#18-后续优化建议)
19. [推荐的生产目录规范](#19-推荐的生产目录规范)
20. [上线前检查清单](#20-上线前检查清单)

---

# 1. 仓库总体定位

`ubuntu-wallet` 不是单纯的“预测涨跌”脚本，而是一套逐步工程化的量化/交易机器学习系统。

它的核心思路是：

1. 通过数据采集模块持续获取市场数据；
2. 通过训练模块构建特征与标签，训练模型；
3. 用时间序列验证与校准确保模型输出更接近真实概率；
4. 在线推理服务按统一 schema 输出概率与交易信号；
5. 通过预测日志记录每次信号；
6. 通过后验评估脚本持续验证模型是否真的有效；
7. 通过模拟交易或 DRY-RUN 验证执行链路与风控；
8. 最终为真实交易或准实盘系统提供高质量交易信号。

本系统当前更适合的风格是：

- **高 precision（高正确率）**
- **低 coverage（少而精的信号）**
- 使用 `LONG / SHORT / FLAT` 三态决策
- 使用 TP / SL / horizon 风格管理持仓

---

# 2. 系统总体架构

整个系统可以分为六层：

## 2.1 数据采集层
目录：`go-collector/`

职责：

- 对接市场/交易所数据接口
- 获取 K 线或其他市场数据
- 为训练和推理提供基础数据输入

## 2.2 离线分析训练层
目录：`python-analyzer/`

职责：

- 数据清洗与特征构建
- 标签生成
- 模型训练
- Walk-forward 时间序列验证
- 概率校准
- 导出模型产物与元数据

## 2.3 在线推理服务层
目录：`ml-service/`

职责：

- 加载生产模型
- 构建在线推理特征
- 执行 schema 校验
- 输出 raw probability 与 calibrated probability
- 生成 LONG / SHORT / FLAT
- 落盘 prediction log

## 2.4 策略评估与模拟执行层
目录：`scripts/`

职责：

- 回测
- 从 prediction log 做后验评估
- 模拟历史顺序交易
- 运行 DRY-RUN 实时策略

## 2.5 文档与规则层
目录：`docs/`

职责：

- 解释系统架构与规则
- 记录风险规则
- 支撑交接与维护

## 2.6 部署与调度层
目录：`systemd/`

职责：

- 常驻服务
- 定时任务
- 新机器部署
- 升级与恢复

---

# 3. 目录结构说明

以下是仓库中的主要目录与用途。

## 3.1 根目录
- `README.md`
  - 项目总入口说明，适合写概览、快速开始、常用命令
- `README_backtest_event_v3_1h.md`
  - 某个回测/事件模型相关说明
- `.env.example`
  - 环境变量模板
- `.gitignore`
  - Git 忽略规则

## 3.2 `go-collector/`
- `main.go`
  - Go 采集主入口
- `collector/`
  - 采集逻辑相关代码
- `market/`
  - 市场数据定义或接口逻辑
- `features/`
  - 与特征预处理相关的 Go 侧逻辑
- `models/`
  - Go 侧的数据结构模型
- `signal/`
  - 信号相关逻辑
- `OPS-NOTES.md`
  - 运行与运维相关说明

## 3.3 `python-analyzer/`
- `train_event_stack_v3.py`
  - 当前主训练脚本
- `walkforward_cv.py`
  - 时间序列滚动验证脚本
- `labeling.py`
  - 标签定义与生成模块
- `backtest_multi_tf.py`
  - 多周期回测相关逻辑
- `ml_predictor.py`
  - 预测器相关逻辑
- `technical_analysis.py`
  - 技术指标构建
- `data_collector.py`
  - Python 侧数据读取/整合
- `visualization.py`
  - 可视化相关功能
- `config.py`
  - Python 侧配置
- `alerts.py`
  - 告警或相关分析逻辑
- `main.py`
  - 可能用于总体运行入口或试验入口

## 3.4 `ml-service/`
- `app.py`
  - 在线推理服务主入口
- `feature_builder.py`
  - 在线推理特征构建
- `model_loader.py`
  - 模型加载、模型元数据处理、校准器加载
- `prediction_logger.py`
  - 预测结果日志记录
- `calibration.py`
  - 概率校准逻辑
- `requirements.txt`
  - 服务依赖
- `README.md`
  - ml-service 局部说明

## 3.5 `scripts/`
- `backtest_event_v3_http.py`
  - 用 HTTP 调在线模型做回测
- `evaluate_from_logs.py`
  - 从 prediction log 评估后验表现
- `mt_trend_utils.py`
  - 多周期趋势工具
- `report_threshold_grid.py` *(P0-3 新增)*
  - 阈值网格分析工具：对 prediction log 的多个 threshold 值批量输出 precision / coverage / avg_return / MDD / LONG/SHORT 分项统计
  - 支持 JSON / CSV 双格式输出
- `generate_daily_report.py` *(P0-4 新增)*
  - 每日评估报告生成器：输出 `daily_eval_YYYY-MM-DD.json` 和 `daily_eval_YYYY-MM-DD.md`
  - 包含 model_version / precision / coverage / TP/SL/TIMEOUT 分布 / LONG/SHORT 分方向结果
- `export_feature_schema.py` *(P0-1 新增)*
  - 从训练模型目录导出 / 验证 `feature_columns_event_v3.json` 特征 schema
  - 支持 `--rebuild`（训练 / walk-forward 路径重建）与 `--validate-inference-row`（在线推理单行特征契约检查）
- `rollback_model.py` *(P0-2 新增)*
  - 基于 `models/registry.json` + `models/current.json` 的一键式模型回滚脚本，支持 `--dry-run` 预览
- `eth_perp_engine_binance.py`
  - ETH 永续风险与执行引擎外壳
- `live_trader_eth_perp_binance.py`
  - ETH 实时/准实时交易执行脚本
- `live_trader_eth_perp_simulated.py`
  - 历史顺序回放模拟交易脚本
- `install.sh`
  - 安装辅助脚本
- `run.sh`
  - 运行辅助脚本
- `install/`
  - 安装相关子脚本
- `ops/`
  - 运维辅助脚本

## 3.6 `docs/`
- `ARCHITECTURE.md`
  - 英文或原始架构说明
- `ETH_perp_risk_rules.md`
  - ETH 永续风险规则说明
- 建议新增：
  - `ARCHITECTURE_CN.md`
  - `DEPLOY_CN.md`
  - `RUNBOOK_CN.md`
  - `MODEL_LIFECYCLE_CN.md`

## 3.7 `systemd/`
- `ml-service.service`
  - 推理服务 systemd 配置
- `go-collector.service`
  - 采集服务 systemd 配置
- `check-go-collector.service`
  - 采集检查任务
- `check-go-collector.timer`
  - 采集检查定时器
- `evaluate-predictions.service`
  - 评估任务服务（使用 `ml-service/.venv`，需安装 `ml-service/requirements.txt`）
- `evaluate-predictions.timer`
  - 评估任务定时器（每 6 小时运行一次 evaluate_from_logs.py）
- `daily-report.service` *(P0-4 新增)*
  - 每日报告生成任务服务（使用 `ml-service/.venv`，需安装 `ml-service/requirements.txt`）
- `daily-report.timer` *(P0-4 新增)*
  - 每日报告定时器（UTC 01:05 每天运行一次 generate_daily_report.py）
- `DEPLOY-NEW-SERVER.md`
  - 新服务器部署说明
- `UPGRADE.md`
  - 升级说明
- `env/`
  - systemd 环境文件目录

---

# 4. 核心数据流

本系统运行的本质是一个“数据闭环”。

## 4.1 数据采集
Go 采集器周期性拉取市场数据，并写入本地数据目录或项目约定的数据文件。

典型数据包括：

- 1h K 线
- 4h K 线
- 1d K 线

这些数据会被：

- Python 训练模块读取
- ml-service 推理模块读取
- 评估/模拟脚本读取

## 4.2 离线训练
训练流程大致为：

1. 读取历史 K 线；
2. 生成技术特征；
3. 生成标签；
4. 划分训练/验证；
5. 做 walk-forward CV；
6. 训练最终模型；
7. 进行概率校准；
8. 导出模型与元数据。

## 4.3 在线推理
在线调用 `/predict` 时：

1. 加载当前生产模型；
2. 使用 `feature_builder.py` 构造特征；
3. 做 schema 校验；
4. 用模型输出 raw probability；
5. 如果存在 calibration，则输出 calibrated probability；
6. 根据阈值生成 LONG / SHORT / FLAT；
7. 写入 prediction log。

## 4.4 后验评估
评估脚本读取 `predictions_log.jsonl`：

1. 找到每条预测对应的后续 K 线；
2. 根据 TP / SL / horizon 逻辑判断该信号结果；
3. 聚合输出：
   - 胜率
   - 覆盖率
   - 平均收益
   - 最大回撤
   - LONG / SHORT 表现
   - TP / SL / TIMEOUT 分布

## 4.5 模拟交易
模拟交易脚本按历史 bar 顺序逐条回放：

1. 每到新 bar 调一次 `/predict`；
2. 多周期过滤；
3. 决定是否开仓；
4. 用 TP / SL / horizon 决定平仓；
5. 更新资金曲线；
6. 输出交易与权益日志。

---

# 5. 模块级详细说明

# 5.1 go-collector 模块

## 作用
Go 采集层是整个系统的数据入口。如果采集失败，后续所有流程都会受到影响。

## 主要职责
- 获取行情数据
- 维护 K 线文件
- 可扩展采集更多外生因子（future work）

## 部署建议
Go collector 应作为常驻 systemd 服务运行。

## 运维重点
- 确认采集进程存活
- 确认输出文件持续更新
- 确认时间戳连续
- 确认没有长时间空洞

## 常见问题
- API 请求失败
- 写文件失败
- 数据断档
- 时间对齐错误

---

# 5.2 labeling.py

## 作用
统一标签体系。

## 为什么必须有
交易模型最容易出问题的不是模型本身，而是标签定义。标签定义不合理，模型再复杂也没意义。

## 当前支持
### ternary
将未来收益分成：
- UP
- DOWN
- FLAT

适合你的 `LONG / SHORT / FLAT` 目标。

### triple_barrier
以：
- 止盈 TP
- 止损 SL
- 最大持有时间 horizon

共同定义标签。

这比“下一根涨跌”更接近真实交易逻辑。

## 使用建议
如果你最终执行逻辑是 TP/SL/horizon，训练时优先使用 triple-barrier。

---

# 5.3 walkforward_cv.py

## 作用
时间序列滚动验证。

## 为什么不能用普通 train_test_split
金融时间序列有顺序性。随机切分会把未来信息泄漏到过去，导致结果虚高。

## 它解决的问题
- 时间泄漏
- 单次切分偶然性
- 阈值在不同阶段表现不一致

## 输出指标
- AUC
- F1
- Precision
- Recall
- Brier Score
- precision@confidence_threshold
- coverage

## 生产建议
每次准备上线新模型前，必须先跑 walk-forward CV。

---

# 5.4 train_event_stack_v3.py

## 作用
训练主入口。

## 新版职责
- 读取数据
- 调用 labeling 模块
- 训练模型
- 调用 calibration
- 写 metadata

## 关键参数
- `--label-method`
- `--tp-pct`
- `--sl-pct`
- `--calibration`

## 训练产物建议
训练完成后，建议输出目录内至少包含：
- `model.*`
- `calibration.*`
- `model_meta.json`
- `feature_schema.json`
- `cv_report.csv`

---

# 5.5 calibration.py

## 作用
让概率更可信。

## 两种方法
- Isotonic
- Sigmoid / Platt

## 使用建议
### Isotonic
适合样本量较大、想追求更灵活映射时。

### Sigmoid
更稳健，样本较少时也可用。

## 注意
校准只会提升概率可信度，不保证收益必然变好。  
但对你这种阈值决策型系统非常重要。

---

# 5.6 model_loader.py

## 作用
在线加载模型与校准器。

## 当前设计意义
- 模型存在就正常预测
- 校准器存在则启用校准
- 校准器缺失则降级使用 raw probability

## 风险点
- 生产目录切换模型时，模型与校准器版本必须一致
- 如果 model 和 calibration 对应不上，会导致推理逻辑异常

---

# 5.7 feature_builder.py

## 作用
构造在线推理使用的特征。

## 当前重点
- 特征构造
- 缺失值处理
- schema 校验

## 为什么这里最危险
线上与线下不一致是最常见的失效来源之一。

例如：
- 训练时用了 58 个特征
- 线上只构造了 56 个
- 或者顺序变了
- 或者某列 fillna 逻辑不同

## 当前建议
- 严格维护 feature schema
- 每次训练新模型时导出 schema
- 每次线上推理时校验 schema

---

# 5.8 app.py

## 作用
在线推理 API 服务。

## 应包含
- `/predict`
- `/healthz`

## `/predict` 负责
- 接收输入
- 构造特征
- 调模型
- 应用 calibration
- 生成 LONG / SHORT / FLAT
- 写日志

## `/healthz` 负责
- 返回服务存活状态
- 返回当前模型版本
- 返回 calibration 是否可用

---

# 5.9 prediction_logger.py

## 作用
记录每次预测。

## 为什么它极其重要
没有 prediction log，你就无法知道模型在真实时间里到底是否有效。

## 推荐日志字段
- 请求时间
- 特征时间
- symbol / interval
- model_version
- raw probability
- calibrated probability
- signal
- threshold_long
- threshold_short
- trend_4h
- trend_1d

## 注意
日志必须版本化，后期追溯才有意义。

---

# 5.10 evaluate_from_logs.py

## 作用
读取 prediction log，后验评估真实效果。

## 它回答的问题
- 最近几天模型是否真的有效？
- 当前阈值是否合适？
- LONG 与 SHORT 哪个更强？
- 哪个市场阶段失效？

## 当前输出建议关注
- win rate
- avg return
- max drawdown
- coverage
- TP / SL / TIMEOUT 比例

---

# 5.11 live_trader_eth_perp_simulated.py

## 作用
做历史逐 bar 回放模拟。

## 与普通回测的差别
不是一次性事后看，而是按顺序模拟“每一刻你当时能知道什么”。

## 它适合做
- 验证多周期过滤
- 验证 TP/SL/horizon 逻辑
- 验证风控引擎
- 看资金曲线是否合理

---

# 5.12 eth_perp_engine_binance.py

## 作用
ETH 永续交易外壳与风险引擎。

## 重点
模型只是发信号，真正能不能执行、仓位多大、何时停机，都应由风险引擎把关。

## 实盘前必须核查
- 最大仓位
- 最大单日亏损
- 连续亏损停机
- 熔断逻辑

---

# 6. 模型与策略逻辑说明

本系统建议的主要思路是：

## 6.1 不是追求高频，而是追求高质量信号
你当前更适合：
- 少做单
- 只在高置信度时出手
- 低 coverage 高 precision

## 6.2 三态信号体系
模型输出不是单纯“看多 / 看空”，而是：
- LONG
- SHORT
- FLAT

FLAT 很重要，因为它让系统在不确定时不交易。

## 6.3 多周期逻辑
推荐逻辑：
- `1h`：触发器
- `4h`：次趋势过滤
- `1d`：大趋势过滤

即：
- 只有大方向和中方向都不反对时，1h 信号才放行

## 6.4 TP / SL / horizon
持仓逻辑建议固定化、版本化，常见如：
- TP = 1.75%
- SL = 0.7%
- horizon = 6 bars

这样训练、回测、评估、模拟交易能统一语言。

---

# 7. 环境准备与依赖安装

# 7.1 系统环境建议
推荐：
- Ubuntu 22.04 或接近版本
- Python 3.10+
- Go 1.21+（或项目实际要求版本）
- systemd 可用

## 7.2 克隆仓库
```bash
cd ~
git clone https://github.com/xionng-sudo/ubuntu-wallet.git
cd ubuntu-wallet
```

## 7.3 Python 虚拟环境建议
建议分两个环境：

### 推理服务环境
```bash
python3 -m venv venv-ml-service
source venv-ml-service/bin/activate
pip install -r ml-service/requirements.txt
deactivate
```

### 训练分析环境
```bash
python3 -m venv venv-analyzer
source venv-analyzer/bin/activate
pip install -r python-analyzer/requirements.txt
deactivate
```

## 7.4 Go 依赖安装
```bash
cd go-collector
go mod download
go build ./...
```

---

# 8. 完整部署流程

下面按“新服务器部署”的视角说明。

## 8.1 部署前准备
准备：
- Git
- Python
- Go
- systemd
- 运行目录权限
- 环境变量文件

## 8.2 克隆代码
```bash
cd /opt
git clone https://github.com/xionng-sudo/ubuntu-wallet.git
cd ubuntu-wallet
```

## 8.3 准备数据目录
建议：
```bash
mkdir -p data/raw
mkdir -p data/logs
mkdir -p data/models
mkdir -p data/reports
mkdir -p data/derived
```

## 8.4 配置环境变量
复制 `.env.example`：
```bash
cp .env.example .env
```

然后根据实际情况填写：
- API Key
- API Secret
- 数据目录
- 模型目录
- 服务端口
- 运行模式

## 8.5 部署 Go collector
```bash
cd go-collector
go build -o ../bin/go-collector main.go
```

## 8.6 部署 ml-service
```bash
source ~/ubuntu-wallet/venv-ml-service/bin/activate
cd ~/ubuntu-wallet/ml-service
python app.py
```

## 8.7 配置 systemd
复制 service 文件到系统目录：
```bash
sudo cp systemd/go-collector.service /etc/systemd/system/
sudo cp systemd/ml-service.service /etc/systemd/system/
sudo cp systemd/evaluate-predictions.service /etc/systemd/system/
sudo cp systemd/evaluate-predictions.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## 8.8 启动服务
```bash
sudo systemctl enable go-collector
sudo systemctl start go-collector

sudo systemctl enable ml-service
sudo systemctl start ml-service

sudo systemctl enable evaluate-predictions.timer
sudo systemctl start evaluate-predictions.timer
```

## 8.9 验证服务
```bash
systemctl status go-collector
systemctl status ml-service
systemctl status evaluate-predictions.timer
```

---

# 9. 训练流程

# 9.1 训练前检查
先确认：
- 数据完整
- 时间戳连续
- 特征构造正常
- 标签参数明确
- 没有未来数据污染

## 9.2 先跑 walk-forward CV
```bash
source ~/ubuntu-wallet/venv-analyzer/bin/activate
cd ~/ubuntu-wallet

python python-analyzer/walkforward_cv.py \
  --data-dir data \
  --n-splits 5 \
  --gap-bars 12 \
  --label-method ternary \
  --confidence-threshold 0.65 \
  --output-csv /tmp/cv_report.csv
```

## 9.2.1 再做训练 / 推理 schema 一致性检查
```bash
source ~/ubuntu-wallet/venv-analyzer/bin/activate
cd ~/ubuntu-wallet

python scripts/export_feature_schema.py \
  --model-dir models \
  --data-dir data \
  --rebuild \
  --validate-inference-row
```

> 说明：
> - `--rebuild` 使用与训练 / walk-forward 相同的 `build_multi_tf_feature_df()` + `get_feature_columns_like_trainer()` 路径重建 schema；
> - `--validate-inference-row` 使用 `build_event_v3_feature_row()` 验证 `/predict` 在线推理单行特征是否与保存的 schema 对齐。

## 9.3 正式训练
```bash
python python-analyzer/train_event_stack_v3.py \
  --label-method triple_barrier \
  --tp-pct 0.0175 \
  --sl-pct 0.009 \
  --calibration isotonic
```

## 9.4 训练后检查
检查是否生成：
- 模型文件
- calibration artifact
- model_meta.json
- `feature_columns_event_v3.json`
- `registry.json`
- `current.json`
- metrics 输出

## 9.5 上线前验证
上线前至少做：
- walk-forward 结果检查
- threshold 合理性检查
- 模拟交易回放
- 日志评估脚本 dry-run

---

# 10. 在线推理服务使用方法

# 10.1 启动
```bash
source ~/ubuntu-wallet/venv-ml-service/bin/activate
cd ~/ubuntu-wallet/ml-service
python app.py
```

## 10.2 健康检查
```bash
curl http://127.0.0.1:8000/healthz
```

应关注：
- 服务是否存活
- 当前模型版本
- calibration 是否启用

## 10.3 预测请求
调用 `/predict`，传入当前 bar 和上下文数据。

具体请求格式取决于 `app.py` 约定。

## 10.4 输出应包含
- raw probability
- calibrated probability
- confidence
- signal
- model_version
- calibration_method

---

# 11. 回测与评估流程

# 11.1 HTTP 回测
使用：
```bash
python scripts/backtest_event_v3_http.py
```

作用：
- 通过 HTTP 调真实线上模型接口回测
- 确保“回测与线上服务更一致”

## 11.2 从日志评估
```bash
python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl \
  --data-dir data \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --horizon-bars 6
```

## 11.3 推荐看的指标
- coverage
- precision
- win rate
- avg return
- max drawdown
- LONG / SHORT 分方向表现
- TP / SL / TIMEOUT 比例

---

# 12. 模拟交易与 DRY-RUN 流程

# 12.1 历史顺序模拟
```bash
python scripts/live_trader_eth_perp_simulated.py
```

作用：
- 逐根 bar 顺序执行
- 更接近真实时间流程
- 看资金曲线和执行逻辑

## 12.2 DRY-RUN
```bash
python scripts/live_trader_eth_perp_binance.py --mode dry-run
```

作用：
- 使用真实最新市场环境
- 但不实际下单

## 12.3 真仓前要求
满足以下条件再考虑：
- 连续 2 周以上 DRY-RUN
- 样本足够多
- 不同市场环境下表现稳定
- 风控验证通过
- 接口异常处理完整

---

# 13. systemd 部署与调度

# 13.1 核心服务
- `go-collector.service`
- `ml-service.service`

## 13.2 核心定时任务
- `evaluate-predictions.timer`
- `check-go-collector.timer`

## 13.3 常用命令
查看状态：
```bash
systemctl status ml-service
systemctl status go-collector
systemctl status evaluate-predictions.timer
```

重启服务：
```bash
sudo systemctl restart ml-service
sudo systemctl restart go-collector
```

查看日志：
```bash
journalctl -u ml-service -n 200 --no-pager
journalctl -u go-collector -n 200 --no-pager
```

---

# 14. 日常运维手册

每天至少检查以下内容：

## 14.1 服务状态
- ml-service 是否在线
- go-collector 是否在线
- 定时评估是否按期执行

## 14.2 数据状态
- K 线是否更新
- 多周期文件是否同步
- 是否有缺失 bar

## 14.3 日志状态
- prediction log 是否持续追加
- 是否出现异常字段缺失
- 是否出现大量 schema warning

## 14.4 模型状态
- 当前 model_version 是否正确
- calibration 是否启用
- 最近 7 天 precision 是否下降

## 14.5 策略状态
- coverage 是否异常变化
- LONG/SHORT 分布是否异常
- MDD 是否明显抬升

---

# 15. 常见故障排查

# 15.1 ml-service 启动失败
## 可能原因
- Python 环境未安装依赖
- 模型文件路径错误
- calibration artifact 损坏
- 端口占用

## 排查步骤
```bash
journalctl -u ml-service -n 200 --no-pager
```
检查：
- ImportError
- FileNotFoundError
- PermissionError
- Address already in use

---

# 15.2 /predict 报错
## 可能原因
- 输入数据缺字段
- schema 不一致
- 特征构造失败
- 模型加载失败

## 排查方向
- 检查请求 payload
- 检查 feature_builder warning
- 检查 model_loader 日志

---

# 15.3 prediction log 不写入
## 可能原因
- 日志路径权限不足
- 磁盘满了
- logger 异常被吞掉

## 排查
- 检查 `data/logs/`
- 检查文件权限
- 检查 journalctl

---

# 15.4 evaluate_from_logs 没有 trade 或结果异常少
## 可能原因
- 阈值太高
- 覆盖率太低
- prediction log 样本太少
- 时间对齐失败

## 排查
- 降低 threshold 做对照测试
- 检查 feature_ts 与 K 线 ts 是否对齐
- 检查 horizon/TP/SL 参数是否与训练一致

---

# 15.5 walk-forward 结果很好，上线却变差
## 可能原因
- 线上线下特征不一致
- 数据分布漂移
- 训练标签和执行逻辑不一致
- 阈值在当前市场不适配

## 排查
- 检查 feature schema
- 检查 calibration
- 检查近期数据分布是否变了
- 跑 threshold grid 对比

---

# 15.6 Go collector 正常但数据不更新
## 可能原因
- API 限流
- 写文件路径错误
- 逻辑卡住

## 排查
- 查看 go-collector 日志
- 检查输出目录修改时间
- 检查磁盘权限

---

# 16. 数据与模型维护规范

# 16.1 数据规范
建议保留：
- 原始 K 线
- 衍生特征
- prediction log
- trade/equity log
- daily reports

## 16.2 模型规范
每个模型目录建议包含：
- `model.*`
- `calibration.*`
- `model_meta.json`
- `feature_schema.json`
- `cv_report.csv`

## 16.3 版本规范
建议使用：
- 训练日期
- 训练区间
- 标签配置
- 校准方式
- 阈值版本

例如：
- `event_v3_lightgbm_2026-03-15_tb_h6_tp175_sl70_iso`

---

# 17. 安全与风控注意事项

# 17.1 不要直接把回测最优参数无脑上真仓
回测可能过拟合。

## 17.2 必须保留 FLAT 状态
不要强迫模型每个 bar 都给信号。

## 17.3 真仓前必须 DRY-RUN
至少 2 周以上。

## 17.4 API Key 必须隔离
- 不要写进代码
- 用 `.env`
- 最小权限原则

## 17.5 风控必须独立于模型
模型可以出错，风控壳不能省。

---

# 18. 后续优化建议

推荐优先级：

## P0
- 多周期特征真正进训练
- model registry
- 阈值报告
- 每日报告

## P1
- 外生因子（funding / OI / taker）
- drift monitor
- calibration report

## P2
- 自动重训
- 自动晋升/回滚
- 更强风控壳

---

# 19. 推荐的生产目录规范

建议最终整理成：

```text
ubuntu-wallet/
├── bin/
├── configs/
├── data/
│   ├── raw/
│   ├── derived/
│   ├── logs/
│   ├── reports/
│   └── models/
├── docs/
├── go-collector/
├── ml-service/
├── python-analyzer/
├── scripts/
└── systemd/
```

推荐的关键文件：
- `data/logs/predictions_log.jsonl`
- `data/logs/trades_log.jsonl`
- `data/reports/daily_eval_YYYY-MM-DD.json`
- `data/models/current/`
- `data/models/archive/`

---

# 20. 上线前检查清单

## 数据
- [ ] 1h / 4h / 1d K 线连续
- [ ] 时间对齐正确
- [ ] 无大量缺失值

## 训练
- [ ] walk-forward 跑过
- [ ] triple-barrier 参数明确
- [ ] calibration 正常

## 推理
- [ ] `/healthz` 正常
- [ ] schema 校验无严重 warning
- [ ] 预测日志写入正常

## 评估
- [ ] evaluate_from_logs 可正常执行
- [ ] 输出覆盖率与 precision 可解释

## 模拟交易
- [ ] simulated trader 正常
- [ ] 风控壳正常
- [ ] 资金曲线可解释

## 部署
- [ ] systemd 服务正常
- [ ] timer 正常触发
- [ ] journalctl 无持续报错

## 风控
- [ ] DRY-RUN 足够久
- [ ] 未出现异常连续亏损
- [ ] 参数版本已记录

---

# 附录：推荐的日常命令

## 查看服务状态
```bash
systemctl status ml-service
systemctl status go-collector
systemctl status evaluate-predictions.timer
```

## 查看最近日志
```bash
journalctl -u ml-service -n 200 --no-pager
journalctl -u go-collector -n 200 --no-pager
```

## 启动评估
```bash
source ~/ubuntu-wallet/venv-analyzer/bin/activate
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --data-dir ~/ubuntu-wallet/data \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --horizon-bars 6
```

## 跑模拟交易
```bash
source ~/ubuntu-wallet/venv-analyzer/bin/activate
python ~/ubuntu-wallet/scripts/live_trader_eth_perp_simulated.py
```

## 训练新模型
```bash
source ~/ubuntu-wallet/venv-analyzer/bin/activate
python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --label-method triple_barrier \
  --tp-pct 0.0175 \
  --sl-pct 0.009 \
  --calibration isotonic
```
