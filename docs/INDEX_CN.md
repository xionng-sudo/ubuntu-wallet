# ubuntu-wallet 中文文档索引

> 本文档是所有中文文档的导航入口。
>
> 仓库地址：`https://github.com/xionng-sudo/ubuntu-wallet`

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
② docs/RUNBOOK_CN.md（第 3 节：每日检查清单）
     ↓ 需要训练模型时
③ docs/MODEL_LIFECYCLE_CN.md（第 2-7 节）
```

### 路径 2：运维接手

```
① docs/RUNBOOK_CN.md（完整阅读）
② docs/FAILURE_MODES_CN.md（了解常见故障）
③ docs/MODEL_LIFECYCLE_CN.md（了解模型切换/回滚）
```

### 路径 3：模型迭代

```
① docs/MODEL_LIFECYCLE_CN.md（完整阅读）
② docs/RUNBOOK_CN.md（第 8 节：模型运维）
③ docs/DEPLOY_CN.md（第 16-17 节：升级/回滚流程）
```

### 路径 4：故障处理

```
① docs/FAILURE_MODES_CN.md（根据故障类型查阅对应章节）
② docs/RUNBOOK_CN.md（第 13 节：异常场景处置）
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
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# go-collector 健康检查
curl -s http://127.0.0.1:8080/api/healthz | python3 -m json.tool
```

### 重启服务

```bash
sudo systemctl restart go-collector
sudo systemctl restart ml-service
```

### 手动运行评估

```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --data-dir ~/ubuntu-wallet/data \
  --interval 1h \
  --active-model event_v3 \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --horizon-bars 6
deactivate
```

---

## 关键文件位置速查

| 文件                                          | 说明                          |
|-----------------------------------------------|-------------------------------|
| `~/ubuntu-wallet/data/predictions_log.jsonl`  | 预测日志（核心证据）          |
| `~/ubuntu-wallet/data/raw/klines_1h.json`     | 1h K 线数据                   |
| `~/ubuntu-wallet/data/raw/klines_4h.json`     | 4h K 线数据                   |
| `~/ubuntu-wallet/data/raw/klines_1d.json`     | 日线 K 线数据                 |
| `~/ubuntu-wallet/data/models/current/`        | 当前生产模型                  |
| `~/ubuntu-wallet/data/models/archive/`        | 历史归档模型                  |
| `~/ubuntu-wallet/logs/evaluate_predictions.log` | 评估任务日志                |
| `~/ubuntu-wallet/check-go-collector.log`      | go-collector 健康检查日志     |
| `/etc/ubuntu-wallet/collector.env`            | 交易所 API Key（不进 Git）    |
| `/etc/ubuntu-wallet/telegram.env`             | Telegram 通知配置（不进 Git） |
| `~/ubuntu-wallet/bin/go-collector`            | go-collector 编译产物         |
| `~/ubuntu-wallet/ml-service/.venv/`           | ml-service Python 虚拟环境    |

---

## 常见问题快速导航

| 问题                              | 查看文档                               |
|-----------------------------------|----------------------------------------|
| 如何从零部署？                    | `DEPLOY_CN.md`                         |
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
3. **部署用户**：服务以 `ubuntu` 用户运行，路径基于 `/home/ubuntu/ubuntu-wallet/`
4. **venv 路径**：
   - ml-service 推理 venv：`ml-service/.venv/`
   - 训练/分析 venv：`venv-analyzer/`（如有）
5. **真仓前必须先完成 2 周以上 DRY-RUN**
