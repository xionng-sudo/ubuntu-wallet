# ubuntu-wallet 中文文档索引

> 本文档是所有中文文档的导航入口。
>
> 仓库地址：`https://github.com/xionng-sudo/ubuntu-wallet`
>
> **新用户入口**：先阅读根目录 [README.md](../README.md)，它提供了完整的项目概述、快速开始、所有关键命令和故障排查。本文档作为更深入的文档导航索引。

---

## 项目简介

`ubuntu-wallet` 是一套用于 ETH 永续合约的机器学习量化交易系统。

### 核心能力

- **数据采集**：通过 `go-collector` 从 Binance / OKX / Coinbase 采集实时 K 线数据
- **机器学习推理**：通过 `ml-service`（FastAPI）提供在线推理服务
- **三分类预测**：输出 LONG（做多）/ FLAT（观望）/ SHORT（做空）信号
- **多周期特征**：1h 主周期 + 4h/1d 过滤，提升信号质量
- **概率校准**：Isotonic/Sigmoid 校准，使置信度更可靠
- **自动评估**：每 6 小时自动运行评估脚本，追踪模型表现

### 技术栈

| 组件              | 技术                                         |
|-------------------|----------------------------------------------|
| 数据采集          | Go 1.21+                                     |
| 在线推理服务      | Python 3.10+, FastAPI, uvicorn               |
| 机器学习          | LightGBM + XGBoost + LogisticRegression (堆叠) |
| 自动化调度        | systemd services + timers                    |
| 配置管理          | `.env` + `/etc/ubuntu-wallet/*.env`          |

---

## 仓库目录结构

> **说明**：本节只列出当前仓库中已存在并已纳入版本控制的目录/文件。部署后服务器上的运行目录、日志文件、模型产物、虚拟环境等不在本节中展示。
>
> **职责描述约定**：下方每一行的职责说明都按当前仓库中文件名、目录名和入口代码做保守摘要，只用于仓库导航，不表示该目录承担了系统中的全部行为。

```
ubuntu-wallet/
├── .env.example                  # 环境变量模板（复制为 .env 并填写）
├── README.md                     # 英文项目说明
│
├── docs/                         # 文档目录（本文件所在位置）
│   ├── INDEX_CN.md               # 本文件：中文文档索引
│   ├── ROADMAP_ISSUES_CN.md      # 第二阶段任务路线图
│   ├── ARCHITECTURE.md           # 英文架构文档
│   ├── ARCHITECTURE_CN.md        # 中文架构文档
│   ├── DEPLOY_CN.md              # 中文部署手册 ← 新手首选
│   ├── RUNBOOK_CN.md             # 中文日常运维手册
│   ├── MODEL_LIFECYCLE_CN.md     # 模型生命周期文档
│   ├── FAILURE_MODES_CN.md       # 故障排查与恢复手册
│   └── ETH_perp_risk_rules.md    # ETH 永续合约风控规则
│
├── go-collector/                 # Go 数据采集服务
│   ├── main.go                   # 程序入口
│   ├── collector/                # 交易所 API 客户端（Binance/OKX/Coinbase）
│   ├── features/                 # 特征计算（技术指标）
│   ├── market/                   # K 线写入
│   ├── models/                   # 数据模型定义
│   ├── signal/                   # 信号生成，调用 ml-service /predict
│   ├── go.mod                    # Go 依赖配置
│   └── OPS-NOTES.md              # 运维笔记
│
├── ml-service/                   # Python 在线推理服务（FastAPI）
│   ├── app.py                    # FastAPI 应用主文件（/healthz, /predict 端点）
│   ├── feature_builder.py        # 特征构建（从 K 线数据生成特征向量）
│   ├── model_loader.py           # 模型加载
│   ├── calibration.py            # 概率校准
│   ├── prediction_logger.py      # 预测日志写入
│   ├── requirements.txt          # Python 依赖（FastAPI, uvicorn, pydantic）
│   └── README.md                 # 快速启动说明
│
├── python-analyzer/              # 训练、分析、回测脚本
│   ├── train_event_stack_v3.py   # 主训练脚本（LightGBM + XGBoost + LR 堆叠）
│   ├── walkforward_cv.py         # Walk-Forward 时序交叉验证
│   ├── backtest_multi_tf.py      # 多周期回测
│   ├── data_collector.py         # 历史数据采集（研究用）
│   ├── technical_analysis.py     # 技术分析指标
│   ├── labeling.py               # 标签生成（triple barrier 等）
│   ├── ml_predictor.py           # 离线推理
│   ├── config.py                 # 配置
│   ├── alerts.py                 # 告警
│   ├── visualization.py          # 可视化
│   └── requirements.txt          # Python 依赖（scikit-learn, lightgbm, xgboost 等）
│
├── scripts/                      # 运维与操作脚本
│   ├── evaluate_from_logs.py     # 从 prediction log 评估模型表现
│   ├── live_trader_eth_perp_simulated.py  # 模拟交易（历史回放）
│   ├── live_trader_eth_perp_binance.py    # DRY-RUN / 真仓交易执行
│   ├── backtest_event_v3_http.py # HTTP 回测（调用 ml-service）
│   ├── report_drift.py           # 特征漂移监控（支持 --symbol / --all-symbols / --models-base-dir）
│   ├── symbol_paths.py           # 多币种路径解析工具（供其他脚本导入）
│   ├── train_symbol.sh           # 单币种训练便捷包装
│   ├── train_all_symbols.sh      # 批量训练所有启用币种（失败隔离）
│   ├── eth_perp_engine_binance.py# ETH 永续合约风控引擎
│   ├── analysis_tool.py          # 分析工具
│   ├── run.sh                    # 快速启动脚本
│   ├── run_live_eval_1h.sh       # 1h 实时评估启动脚本
│   ├── install.sh                # 安装脚本
│   ├── install/
│   │   └── bootstrap-new-server.sh  # 新服务器初始化脚本
│   └── ops/
│       ├── check-go-collector.sh    # go-collector 健康检查与自愈
│       └── notify-telegram.sh       # Telegram 告警通知
│
├── systemd/                      # systemd 服务文件
│   ├── go-collector.service      # go-collector 服务定义
│   ├── ml-service.service        # ml-service 服务定义
│   ├── evaluate-predictions.service  # 评估任务服务
│   ├── evaluate-predictions.timer    # 评估任务定时器（每 6 小时）
│   ├── drift-monitor.service     # 特征漂移监控服务（oneshot，调用 report_drift.py --all-symbols）
│   ├── drift-monitor.timer       # 漂移监控定时器（每 6 小时，以本机时区为准）
│   ├── daily-report.service      # 每日报告服务
│   ├── daily-report.timer        # 每日报告定时器（01:05 本机时区）
│   ├── calibration-report.service# 校准报告服务
│   ├── calibration-report.timer  # 校准报告定时器（周一 02:00 本机时区）
│   ├── check-go-collector.service    # go-collector 健康检查服务
│   ├── check-go-collector.timer      # 健康检查定时器（每 1 分钟）
│   ├── env/
│   │   ├── collector.env.example     # 采集器环境变量模板
│   │   └── telegram.env.example      # Telegram 通知环境变量模板
│   ├── DEPLOY-NEW-SERVER.md          # 新服务器部署速查
│   └── UPGRADE.md                    # 升级说明
│
└── README_backtest_event_v3_1h.md    # 回测结果说明
```

---

## 文档阅读路径推荐

### 路径 1：第一次部署（从零到一）

```
① docs/DEPLOY_CN.md
     ↓ 完成部署后
② docs/RUNBOOK_CN.md（第 18 节：快速入门）
     ↓ 需要训练模型时
③ docs/RUNBOOK_CN.md（第 19 节：完整 Ops + ML 工作流程）
     ↓ 需要管理多币种时
④ docs/MODEL_LIFECYCLE_CN.md（第 2-7 节）
```

### 路径 2：运维接手

```
① docs/RUNBOOK_CN.md（完整阅读，重点第 18-22 节）
② docs/FAILURE_MODES_CN.md（了解常见故障）
③ docs/MODEL_LIFECYCLE_CN.md（了解模型切换/回滚）
```

### 路径 3：模型迭代

```
① docs/MODEL_LIFECYCLE_CN.md（完整阅读）
② docs/RUNBOOK_CN.md（第 19 节：Ops + ML 工作流程）
③ docs/DEPLOY_CN.md（第 16-17 节：升级/回滚流程）
```

### 路径 4：故障处理

```
① docs/RUNBOOK_CN.md（第 22 节：故障排查手册）
② docs/FAILURE_MODES_CN.md（根据故障类型查阅对应章节）
③ docs/RUNBOOK_CN.md（第 13 节：异常场景处置）
```

### 路径 5：漂移监控运维

```
① docs/RUNBOOK_CN.md（第 20 节：漂移监控完整参考）
② docs/RUNBOOK_CN.md（第 22.1-22.4 节：常见 drift 故障排查）
```

### 路径 6：新增币种

```
① docs/RUNBOOK_CN.md（第 21 节：新增币种与阈值调试指南）
② docs/RUNBOOK_CN.md（第 19.3 节：何时需要重新训练）
```

---

## 服务端口速查

| 服务             | 端口  | 协议  | 用途                                         |
|------------------|-------|-------|----------------------------------------------|
| ml-service       | 9000  | HTTP  | `/healthz`、`/predict`                       |
| go-collector     | 8080  | HTTP  | `/api/healthz`（健康检查，供监控脚本使用）    |

---

## 关键命令速查

### 查看服务状态

```bash
systemctl status go-collector
systemctl status ml-service
systemctl status evaluate-predictions.timer
systemctl status check-go-collector.timer
```

### 查看服务日志

```bash
journalctl -u go-collector -n 200 --no-pager
journalctl -u ml-service -n 200 --no-pager
journalctl -u evaluate-predictions.service -n 100 --no-pager
```

### 健康检查

```bash
# ml-service 健康检查
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool

# go-collector 健康检查
curl -fsS http://127.0.0.1:8080/api/healthz | python3 -m json.tool
```

### 重启服务

```bash
sudo systemctl restart go-collector
sudo systemctl restart ml-service
```

### 手动运行评估

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --data-dir ~/ubuntu-wallet/data \
  --interval 1h \
  --active-model event_v3 \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --horizon-bars 6
```

---

## 关键文件位置速查

### 仓库内已存在的关键文件/目录

| 路径                                          | 说明                                   |
|-----------------------------------------------|----------------------------------------|
| `.env.example`                                | 环境变量模板                           |
| `configs/symbols.yaml`                        | 多币种配置（threshold/tp/sl/horizon/calibration） |
| `docs/DEPLOY_CN.md`                           | 中文部署手册                           |
| `docs/RUNBOOK_CN.md`                          | 中文运维手册（含 Quick Start、ML 工作流、Drift 监控、新增币种、故障排查） |
| `docs/MODEL_LIFECYCLE_CN.md`                  | 模型生命周期文档                       |
| `docs/FAILURE_MODES_CN.md`                    | 故障排查与恢复手册                     |
| `go-collector/main.go`                        | go-collector 程序入口                  |
| `ml-service/app.py`                           | ml-service FastAPI 入口                |
| `python-analyzer/train_event_stack_v3.py`     | 主训练脚本                             |
| `python-analyzer/walkforward_cv.py`           | Walk-Forward 验证脚本                  |
| `scripts/evaluate_from_logs.py`               | prediction log 评估脚本                |
| `scripts/backtest_event_v3_http.py`           | 调用 ml-service 的 HTTP 回测脚本       |
| `scripts/report_drift.py`                     | 特征漂移监控脚本（支持 --all-symbols、--models-base-dir） |
| `scripts/symbol_paths.py`                     | 多币种路径解析工具                     |
| `scripts/train_symbol.sh`                     | 单币种训练便捷包装                     |
| `scripts/train_all_symbols.sh`                | 批量训练所有启用币种                   |
| `systemd/ml-service.service`                  | ml-service systemd 服务文件            |
| `systemd/go-collector.service`                | go-collector systemd 服务文件          |
| `systemd/evaluate-predictions.service`        | 自动评估任务服务文件                   |
| `systemd/drift-monitor.service`               | 漂移监控 systemd 服务文件              |
| `systemd/drift-monitor.timer`                 | 漂移监控定时器（每 6 小时）            |
| `systemd/env/collector.env.example`           | collector 环境变量模板                 |
| `systemd/env/telegram.env.example`            | Telegram 通知环境变量模板              |

### 部署后服务器上的常见运行路径（非仓库内容）

> **注意**：以下路径用于部署/运维说明，通常只会在服务器运行环境中出现，**不是 Git 仓库内已有文件**。

| 路径                                          | 说明                                   |
|-----------------------------------------------|----------------------------------------|
| `~/ubuntu-wallet/data/predictions_log.jsonl`  | 预测日志（运行后生成）                 |
| `~/ubuntu-wallet/data/<SYM>/predictions_log.jsonl` | 多币种预测日志（运行后生成）      |
| `~/ubuntu-wallet/data/<SYM>/reports/drift_YYYY-MM-DD.json` | Drift 监控 JSON 报告       |
| `~/ubuntu-wallet/data/<SYM>/reports/drift_YYYY-MM-DD.md`   | Drift 监控 Markdown 摘要   |
| `~/ubuntu-wallet/data/klines_1h.json`     | 1h K 线数据（采集后生成）              |
| `~/ubuntu-wallet/data/klines_4h.json`     | 4h K 线数据（采集后生成）              |
| `~/ubuntu-wallet/data/klines_1d.json`     | 日线 K 线数据（采集后生成）            |
| `~/ubuntu-wallet/models/`                     | 模型文件目录（训练默认输出，服务加载源）|
| `~/ubuntu-wallet/models/<SYM>/current/train_feature_stats.json` | Drift 监控必需的训练统计文件 |
| `~/ubuntu-wallet/models_backup/`              | 模型备份目录（建议人工维护）           |
| `~/ubuntu-wallet/data/logs/drift_monitor.log` | drift-monitor.service 日志（systemd 定时触发） |
| `~/ubuntu-wallet/data/logs/evaluate_predictions.log` | 评估任务日志（部署后写入）       |
| `~/ubuntu-wallet/data/logs/check-go-collector.log`   | 健康检查日志（部署后写入）       |
| `/etc/ubuntu-wallet/ml-service.env`           | ml-service 环境变量（含 ENABLE_DRIFT_MONITOR、APP_ROOT 等） |
| `/etc/ubuntu-wallet/collector.env`            | 交易所 API Key 配置（服务器本地）      |
| `/etc/ubuntu-wallet/telegram.env`             | Telegram 通知配置（服务器本地）        |
| `~/ubuntu-wallet/bin/go-collector`            | go-collector 编译产物（构建后生成）    |
| `~/ubuntu-wallet/ml-service/.venv/`           | ml-service Python 虚拟环境（部署后创建）|

---

## 常见问题快速导航

| 问题                              | 查看文档                               |
|-----------------------------------|----------------------------------------|
| 如何从零部署？                    | `DEPLOY_CN.md`                         |
| 系统快速入门（首次上线）          | `RUNBOOK_CN.md` 第 18 节              |
| 完整 ML + Ops 工作流程            | `RUNBOOK_CN.md` 第 19 节              |
| 如何训练 / 回测 / 判断是否重训    | `RUNBOOK_CN.md` 第 19.2-19.4 节       |
| Drift 监控完整参考                | `RUNBOOK_CN.md` 第 20 节              |
| systemd drift-monitor 部署        | `RUNBOOK_CN.md` 第 20.6 节            |
| 新增币种步骤                      | `RUNBOOK_CN.md` 第 21 节              |
| 阈值调参方法                      | `RUNBOOK_CN.md` 第 21.2 节            |
| 故障排查（ENABLE_DRIFT_MONITOR / MODEL_DIR / systemd） | `RUNBOOK_CN.md` 第 22 节 |
| 所有脚本参数速查（`--help` 摘要）  | `RUNBOOK_CN.md` 第 23 节 |
| ml-service 起不来怎么办？         | `FAILURE_MODES_CN.md` 第 6.1 节        |
| go-collector 没有数据怎么办？     | `FAILURE_MODES_CN.md` 第 3 节          |
| 如何切换/回滚模型？               | `MODEL_LIFECYCLE_CN.md` 第 8/10 节     |
| 评估 timer 不触发怎么办？         | `FAILURE_MODES_CN.md` 第 7.3 节        |
| 日常应该检查什么？                | `RUNBOOK_CN.md` 第 3 节               |
| 如何训练新模型？                  | `MODEL_LIFECYCLE_CN.md` 第 4 节        |
| prediction log 不写入怎么办？     | `FAILURE_MODES_CN.md` 第 6.3 节        |
| /predict 返回错误怎么办？         | `FAILURE_MODES_CN.md` 第 6.2 节        |

---

## 注意事项

1. **API Key 安全**：`/etc/ubuntu-wallet/*.env` 只存在于服务器本地，**绝对不要提交到 Git**
2. **端口确认**：ml-service 使用端口 **9000**（不是 8000）
3. **部署路径**：文档里的 `~/ubuntu-wallet/` 表示部署服务器上的典型 checkout 路径，不是仓库内固定路径
4. **venv 路径**：
   - ml-service 推理 venv：`~/ubuntu-wallet/ml-service/.venv/`（部署后创建，systemd 服务硬编码使用此路径）
   - 训练/分析 venv：统一复用 `ml-service/.venv`（安装 `python-analyzer/requirements.txt`），不再单独创建 `venv-analyzer/`
5. **真仓前必须先完成 2 周以上 DRY-RUN**
