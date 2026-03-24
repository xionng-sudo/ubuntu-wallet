# ETH 加密货币预测与机器学习交易系统

> **定位说明**：本项目是一套以 ETH 永续合约为核心研究对象的一体化系统，涵盖数据采集、特征工程、模型训练、在线推理、回测评估与 systemd 生产部署。  
> **适用场景**：研究学习 / 回测验证 / 论文实验 / 小规模策略评估。  
> **不适合**：直接作为全自动实盘系统使用——使用真实资金前请充分理解系统逻辑并完成独立验证。

---

## 目录

1. [项目能力说明](#1-项目能力说明)
2. [仓库结构说明](#2-仓库结构说明)
3. [系统架构](#3-系统架构)
4. [环境要求](#4-环境要求)
5. [快速开始](#5-快速开始)
6. [详细安装与配置](#6-详细安装与配置)
7. [配置说明（.env）](#7-配置说明env)
8. [数据采集（Go Collector）](#8-数据采集go-collector)
9. [模型训练（Python Analyzer）](#9-模型训练python-analyzer)
10. [**多币种架构（Multi-Symbol）**](#10-多币种架构multi-symbol)
11. [推理服务（ML Service）](#11-推理服务ml-service)
12. [回测说明](#12-回测说明)
13. [模拟运行与日志评估](#13-模拟运行与日志评估)
14. [生产部署（systemd）](#14-生产部署systemd)
15. [常用命令汇总](#15-常用命令汇总)
16. [故障排查](#16-故障排查)
17. [风险提示与免责声明](#17-风险提示与免责声明)

---

## 1. 项目能力说明

| 能力模块 | 说明 |
|---|---|
| **数据采集** | Go 编写的高性能采集器，支持从 Binance / OKX / Coinbase 拉取 1h/4h/1d K线数据，持久化为 JSON 文件 |
| **特征工程** | 多周期（1h/4h/1d）技术指标特征构造，20+ 种指标，包含趋势、动量、波动率、成交量 |
| **模型训练** | LightGBM + XGBoost 基学习器 + Logistic Regression 堆叠元模型，三分类（LONG/SHORT/FLAT）+ 概率校准 |
| **在线推理** | FastAPI 推理服务（端口 9000），提供 `/predict`、`/healthz` 等接口 |
| **回测** | 三重障碍法回测引擎（TP/SL/TIMEOUT），支持参数网格搜索，输出胜率/收益/MDD 等指标 |
| **模拟运行** | 基于历史 K 线回放的模拟交易，复现历史信号与出场逻辑 |
| **日志评估** | 对线上预测日志（JSONL）做事后评估，与回测逻辑对齐 |
| **systemd 运维** | 完整的 systemd 服务单元，支持自动重启、健康检查定时任务、Telegram 告警 |

---

## 2. 仓库结构说明

```
ubuntu-wallet/
├── .env.example                    # 环境变量模板（复制为 .env 并填入密钥）
├── .gitignore
├── README.md                       # 本文档
├── README_backtest_event_v3_1h.md  # event_v3 1h 策略回测详细记录
│
├── configs/                        # 多币种配置（新）
│   └── symbols.yaml                # 每币种 enabled/threshold/tp/sl/horizon/calibration
│
├── go-collector/                   # Go 数据采集服务
│   ├── main.go                     # HTTP API 服务主入口
│   ├── go.mod / go.sum             # Go 模块定义
│   ├── collector/                  # 各交易所采集器（Binance/OKX/Coinbase）
│   ├── market/                     # 行情数据结构
│   ├── models/                     # 数据模型
│   ├── signal/                     # 信号相关
│   ├── exog/                       # 外生特征
│   ├── features/                   # 特征计算
│   └── OPS-NOTES.md                # 运维操作笔记
│
├── python-analyzer/                # Python 训练/分析引擎
│   ├── train_event_stack_v3.py     # 主训练脚本（LightGBM+XGBoost+堆叠）
│   ├── labeling.py                 # 标签生成（三分类 / 三重障碍）
│   ├── walkforward_cv.py           # 时间序列交叉验证（无数据泄露）
│   ├── calibration_report.py       # 校准报告生成
│   ├── backtest_multi_tf.py        # 多周期回测工具
│   ├── technical_analysis.py       # 技术指标计算（20+ 种）
│   ├── config.py                   # 配置（SYMBOL 从 $SYMBOL 环境变量读取）
│   ├── requirements.txt            # Python 依赖
│   └── ...
│
├── ml-service/                     # FastAPI 推理服务（端口 9000）
│   ├── app.py                      # FastAPI 主入口（/predict, /healthz）
│   ├── model_loader.py             # 模型加载逻辑（从 models/current/ 目录加载）
│   ├── feature_builder.py          # 多周期特征构造 + schema 验证
│   ├── calibration.py              # 概率校准
│   ├── prediction_logger.py        # 预测日志（写入 data/predictions_log.jsonl）
│   ├── requirements.txt            # Python 依赖
│   └── README.md                   # 服务说明
│
├── scripts/                        # 运行、回测、评估脚本
│   ├── symbol_paths.py             # 每币种路径与配置辅助模块（新）
│   ├── train_symbol.sh             # 按币种训练便捷包装脚本（新）
│   ├── backtest_event_v3_http.py   # 三重障碍回测 + 参数网格搜索
│   ├── evaluate_from_logs.py       # 基于预测日志的事后评估（支持 --symbol）
│   ├── live_trader_eth_perp_simulated.py  # 模拟交易（历史回放）
│   ├── live_trader_eth_perp_binance.py    # Binance DRY-RUN / 真实交易
│   ├── mt_trend_utils.py           # 多周期趋势过滤工具
│   ├── rollback_model.py           # 模型版本回滚
│   ├── generate_daily_report.py    # 日报生成
│   ├── export_feature_schema.py    # 导出特征 schema
│   ├── report_drift.py             # 特征漂移报告（支持 --symbol）
│   ├── analysis_tool.py            # 分析工具
│   ├── install.sh                  # 一键安装脚本
│   ├── run.sh                      # 服务运行管理
│   └── ops/                        # 运维脚本（健康检查、Telegram 通知）
│
├── systemd/                        # systemd 服务单元与定时器
│   ├── go-collector.service        # Go 采集器服务
│   ├── ml-service.service          # ML 推理服务
│   ├── evaluate-predictions.service / .timer  # 定期评估
│   ├── check-go-collector.service / .timer    # 健康检查（每分钟）
│   ├── daily-report.service / .timer          # 日报定时
│   ├── drift-monitor.service / .timer         # 特征漂移监控
│   ├── calibration-report.service / .timer    # 校准报告
│   ├── env/                        # 环境变量模板（不含真实密钥）
│   ├── DEPLOY-NEW-SERVER.md        # 新服务器部署完整说明
│   └── UPGRADE.md                  # 升级说明
│
├── docs/                           # 详细文档
│   ├── INDEX_CN.md                 # 文档索引
│   ├── ARCHITECTURE.md / ARCHITECTURE_CN.md  # 架构说明（英/中）
│   ├── DEPLOY_CN.md                # 中文部署说明
│   ├── MODEL_LIFECYCLE_CN.md       # 模型生命周期管理
│   ├── RUNBOOK_CN.md               # 运维手册
│   ├── FAILURE_MODES_CN.md         # 故障模式与应对
│   ├── ETH_perp_risk_rules.md      # ETH 永续合约风控规则
│   └── ROADMAP_ISSUES_CN.md        # 路线图与已知问题
│
├── tests/                          # 测试文件
│   ├── test_multi_symbol.py        # 多币种路径与配置测试（新）
│   ├── test_p0_pointer_and_schema.py  # 模型指针与 schema 回归测试
│   └── verify_system.sh            # 系统验证脚本
│
├── tools/                          # 工具脚本
│
├── models/                         # 模型产物目录（运行时生成，不进 Git）
│   ├── BTCUSDT/                    # 每币种独立模型目录（多币种模式）
│   │   ├── current/                # 当前激活模型
│   │   ├── archive/                # 版本归档
│   │   └── registry.json           # 版本注册表
│   ├── ETHUSDT/                    # （其他币种同上）
│   └── current/                    # 单币种兼容路径（向后兼容保留）
│
└── data/                           # 数据目录（运行时生成，不进 Git）
    ├── BTCUSDT/                    # 每币种独立数据目录（多币种模式）
    │   ├── klines_1h.json
    │   ├── klines_4h.json
    │   ├── klines_1d.json
    │   ├── predictions_log.jsonl
    │   └── reports/
    ├── ETHUSDT/                    # （其他币种同上）
    └── predictions_log.jsonl       # 单币种兼容路径（向后兼容保留）
```


---

## 3. 系统架构

```
┌─────────────────────────────────────────────────┐
│              第一层：市场数据采集                   │
│                                                 │
│  Binance API ──┐                                │
│  OKX API     ──┼─► go-collector (端口 8080)     │
│  Coinbase API──┘      │                         │
│                       ▼                         │
│  data/BTCUSDT/klines_1h.json (等)               │
│  data/ETHUSDT/klines_1h.json (等)               │
│  data/SOLUSDT/ data/BNBUSDT/ ...               │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│            第二层：离线训练流水线                   │
│                                                 │
│  python-analyzer/train_event_stack_v3.py        │
│    ├── labeling.py（三重障碍标签）               │
│    ├── 多周期特征构造（1h/4h/1d）               │
│    ├── walkforward_cv.py（时序交叉验证）         │
│    └── 模型训练（LightGBM + XGBoost + 堆叠）    │
│              │                                  │
│              ▼                                  │
│         models/<版本目录>/                      │
│           lightgbm_event_v3.pkl                 │
│           xgboost_event_v3.json                 │
│           stacking_event_v3.pkl                 │
│           calibration_event_v3.pkl              │
│           feature_columns_event_v3.json         │
│           model_meta.json                       │
└─────────────────────────────────────────────────┘
         │（模型产物）
         ▼
┌─────────────────────────────────────────────────┐
│            第三层：在线推理服务                    │
│                                                 │
│  ml-service/app.py（FastAPI, 端口 9000）         │
│    ├── POST /predict                            │
│    ├── GET  /healthz                            │
│    ├── feature_builder.py（特征构造）            │
│    ├── model_loader.py（从 models/current/ 目录加载） │
│    └── prediction_logger.py（写预测日志）        │
│              │                                  │
│              ▼                                  │
│      data/predictions_log.jsonl                 │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│            第四层：策略执行 / 评估                  │
│                                                 │
│  回测：backtest_event_v3_http.py                │
│    └── 调用 /predict，三重障碍法网格搜索          │
│                                                 │
│  模拟交易：live_trader_eth_perp_simulated.py     │
│    └── 历史回放，输出模拟盈亏                    │
│                                                 │
│  日志评估：evaluate_from_logs.py                │
│    └── 读取 predictions_log.jsonl，             │
│        对照真实 K 线计算实际收益指标             │
└─────────────────────────────────────────────────┘
```

---

## 4. 环境要求

| 依赖项 | 推荐版本 | 说明 |
|---|---|---|
| 操作系统 | Ubuntu 22.04 LTS | 其他 Linux 发行版未经充分测试 |
| Python | 3.10 / 3.11 / 3.12 | 建议使用 venv 隔离环境 |
| Go | 1.21 及以上 | 用于编译 go-collector |
| `jq` | 任意最新版 | JSON 解析工具，健康检查脚本依赖 |
| `curl` | 任意最新版 | 服务健康检查 |
| `git` | 任意最新版 | 拉取仓库 |

**基础系统依赖安装：**

```bash
# 更新包列表
sudo apt update

# 安装基础工具
sudo apt install -y git curl jq python3 python3-venv python3-pip build-essential

# 安装 Go（使用官方安装包，推荐）
wget https://go.dev/dl/go1.22.4.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.22.4.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
source ~/.bashrc

# 验证 Go 安装
go version
```

---

## 5. 快速开始

以下是从零开始跑通整个流水线的最短路径。

```bash
# ── 第一步：克隆仓库 ──────────────────────────────────────
cd ~
git clone https://github.com/xionng-sudo/ubuntu-wallet.git
cd ubuntu-wallet

# ── 第二步：创建运行时目录 ────────────────────────────────
mkdir -p data models logs bin

# ── 第三步：复制并填写环境变量 ────────────────────────────
cp .env.example .env
# 用你的编辑器打开 .env，填入 Binance/OKX API Key 等信息
nano .env

# ── 第四步：安装 Python 依赖 ──────────────────────────────
cd ml-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
deactivate
cd ..

# ── 第五步：编译 Go Collector ─────────────────────────────
cd go-collector
go mod tidy
go build -o ../bin/go-collector .
cd ..

# ── 第六步：启动 Go Collector（后台运行） ─────────────────
nohup ./bin/go-collector > logs/go-collector.log 2>&1 &

# ── 第七步：验证采集器健康状态 ────────────────────────────
sleep 3
curl -s http://127.0.0.1:8080/api/healthz | jq .

# ── 第八步：启动推理服务（需要模型已训练完毕） ─────────────
cd ml-service
source .venv/bin/activate
nohup uvicorn app:app --host 127.0.0.1 --port 9000 \
  > ../logs/ml-service.log 2>&1 &

# ── 第九步：验证推理服务 ──────────────────────────────────
sleep 3
curl -s http://127.0.0.1:9000/healthz | jq .

# ── 第十步：运行快速回测（需要已训练好的模型） ─────────────
# 如果没有模型，请先参考第 9 节完成训练
cd ~/ubuntu-wallet
python scripts/backtest_event_v3_http.py \
  --data-dir data \
  --base-url http://127.0.0.1:9000 \
  --interval 1h \
  --threshold 0.65 \
  --tp-grid 0.0175:0.0175:0.001 \
  --sl-grid 0.007:0.007:0.001 \
  --horizon-bars 6
```

---

## 6. 详细安装与配置

### 6.1 克隆仓库

```bash
cd ~
git clone https://github.com/xionng-sudo/ubuntu-wallet.git
cd ubuntu-wallet

# 创建运行时所需目录（均不进 Git）
mkdir -p data models logs bin
```

### 6.2 系统依赖

```bash
sudo apt update
sudo apt install -y \
    git curl jq \
    python3 python3-venv python3-pip \
    build-essential \
    libgomp1          # LightGBM 多线程依赖
```

### 6.3 为 ml-service 创建 Python 虚拟环境

```bash
cd ~/ubuntu-wallet/ml-service
python3 -m venv .venv
source .venv/bin/activate

# 升级 pip 并安装推理服务依赖
pip install --upgrade pip
pip install -r requirements.txt

# 验证安装
python -c "import fastapi, uvicorn, lightgbm; print('依赖安装成功')"

deactivate
cd ~/ubuntu-wallet
```

> **说明**：ml-service 使用独立 `.venv`，路径固定为 `ml-service/.venv`。  
> systemd 服务单元硬编码使用 `ml-service/.venv/bin/python` 启动服务。

### 6.4 为 python-analyzer 安装训练依赖（仅需训练时安装）

```bash
cd ~/ubuntu-wallet

# 复用 ml-service 的 venv（在已激活的状态下继续安装）
source ml-service/.venv/bin/activate

pip install --upgrade pip
pip install -r python-analyzer/requirements.txt

# 注：python-analyzer/requirements.txt 包含 torch/tensorflow 等体积较大的包
# 如仅做推理，可跳过此步

deactivate
```

### 6.5 编译 Go Collector

```bash
cd ~/ubuntu-wallet/go-collector

# 下载 Go 模块依赖
go mod tidy

# 编译并输出到 bin/ 目录
go build -o ../bin/go-collector .

# 验证编译结果
ls -lh ../bin/go-collector
# 期望看到类似：-rwxr-xr-x 1 ubuntu ubuntu 12M ... bin/go-collector
```

### 6.6 配置 .env

```bash
cd ~/ubuntu-wallet
cp .env.example .env

# 用编辑器填入实际配置
nano .env
```

详细说明见第 7 节。

### 6.7 目录说明

| 目录 | 用途 | 是否进 Git |
|---|---|---|
| `data/` | K 线数据、预测日志、模型输入输出文件 | 不进 Git |
| `models/` | 模型训练产物（含 current/ 目录和 registry.json） | 不进 Git |
| `logs/` | 各服务运行日志（可选，也可通过 journalctl 查看） | 不进 Git |
| `bin/` | 编译好的 go-collector 二进制文件 | 不进 Git |

---

## 7. 配置说明（.env）

`.env.example` 包含以下关键变量：

```bash
# ── 交易所 API（读取权限即可，无需交易权限） ──────────────

BINANCE_API_KEY=your_binance_api_key_here
BINANCE_API_SECRET=your_binance_api_secret_here

OKX_API_KEY=your_okx_api_key_here
OKX_API_SECRET=your_okx_api_secret_here
OKX_PASSPHRASE=your_okx_passphrase_here

COINBASE_API_KEY=your_coinbase_api_key_here
COINBASE_API_SECRET=your_coinbase_api_secret_here

# ── Go Collector 配置 ──────────────────────────────────────

COLLECTOR_PORT=8080           # Go 采集器 HTTP 监听端口
COLLECTOR_API_URL=http://localhost:8080

# ── 路径配置 ──────────────────────────────────────────────

DATA_DIR=./data               # K 线数据、预测日志存储路径
# MODEL_DIR=./models/current  # ml-service 默认直接使用 models/current/ 目录（通常无需设置此变量）
```

**重要提示：**

- **绝对不要将真实 API Key 提交到 Git 仓库。** `.env` 文件已在 `.gitignore` 中排除。
- 生产环境建议将敏感配置放到 `/etc/ubuntu-wallet/collector.env`（`600` 权限），通过 systemd 的 `EnvironmentFile=` 注入，而不是放在项目目录下的 `.env`。
- API Key 只需要**读取行情**的权限，不需要交易权限（除非你要运行真实交易脚本）。
- `MODEL_DIR` 默认指向 `models/current`（目录），ml-service 启动时直接从该目录加载模型文件。训练脚本每次训练完成后会将模型产物复制到 `models/current/` 目录，并将版本信息写入 `models/registry.json`。
- 如果 API Key 已经泄露，请立即在交易所管理后台撤销并重新生成。

---

## 8. 数据采集（Go Collector）

### 8.1 职责说明

`go-collector` 是一个用 Go 编写的 HTTP 服务，负责：

- 从 Binance 拉取多个交易对的 1h、4h、1d K线数据（以及主交易对的 15m/1m/5m）
- 将数据持久化为按交易对分目录的 JSON 文件（`data/<SYMBOL>/klines_*.json`）
- 提供 `/api/healthz` 接口供监控检查

**支持的交易对**

| 阶段 | 交易对 | 默认启用 |
|------|--------|---------|
| Phase 1 | BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT | ✅ 默认启用 |
| Phase 2 | XRPUSDT, DOGEUSDT, ADAUSDT | ⚙️ 需显式启用 |

**文件写入路径**

```
data/
  BTCUSDT/  klines_1h.json  klines_4h.json  klines_1d.json  klines_15m.json  klines_1m.json  klines_5m.json  ← 第一个配置的交易对（默认）
  ETHUSDT/  klines_1h.json  klines_4h.json  klines_1d.json  klines_15m.json
  SOLUSDT/  klines_1h.json  klines_4h.json  klines_1d.json  klines_15m.json
  BNBUSDT/  ...
  XRPUSDT/  ...  (Phase 2)
  DOGEUSDT/ ...  (Phase 2)
  ADAUSDT/  ...  (Phase 2)
```

> **向下兼容**：默认同时将**主交易对**（`PRIMARY_SYMBOL`，默认 ETHUSDT）的数据写到 `data/klines_*.json` 根路径，保持旧消费者读取 ETHUSDT 数据的语义不变。迁移完成后在 `collector.env` 中设置 `LEGACY_ETHUSDT_COMPAT=false` 关闭该行为。

### 8.2 手动启动

```bash
cd ~/ubuntu-wallet

# 前台运行（方便查看日志，Ctrl+C 停止）
./bin/go-collector

# 后台运行（nohup，日志重定向到文件）
nohup ./bin/go-collector > logs/go-collector.log 2>&1 &
echo "go-collector 已启动，PID: $!"

# 使用 tmux（推荐，方便随时查看日志）
tmux new -s go-collector
./bin/go-collector
# 按 Ctrl+B 然后按 D 分离会话
# 重新连接：tmux attach -t go-collector
```

### 8.2a 多币种配置（Multi-Symbol Config）

```bash
# 仅 Phase 1（默认，无需配置）
# SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT

# 全部 7 个交易对（SYMBOLS 明确指定）
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT ./bin/go-collector

# 全部 7 个交易对（标志方式）
ENABLE_PHASE2_SYMBOLS=true ./bin/go-collector

# 指定主交易对（默认 ETHUSDT；必须包含在 SYMBOLS 中）
PRIMARY_SYMBOL=ETHUSDT ./bin/go-collector
```

在生产环境中，把这些变量写入 `/etc/ubuntu-wallet/collector.env`（参考 `systemd/env/collector.env.example`）。

### 8.3 健康检查

```bash
# 验证服务是否正常运行（含当前启用交易对）
curl -s http://127.0.0.1:8080/api/healthz | jq '{ok, enabled_symbols, primary_symbol, files}'

# 期望输出示例（.ok 为 true 表示服务正常）：
# {
#   "ok": true,
#   "enabled_symbols": ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT"],
#   "primary_symbol": "ETHUSDT",
#   ...
# }
```

### 8.4 确认数据文件已生成

```bash
# 检查各交易对 K 线文件最新时间戳
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT; do
  echo -n "$sym klines_1h: "
  stat -c '%y' ~/ubuntu-wallet/data/$sym/klines_1h.json 2>/dev/null || echo "not found"
done

# 查看 ETHUSDT 1h K 线的数据条数
python3 -c "import json; d=json.load(open('data/ETHUSDT/klines_1h.json')); print(f'1h K线数量: {len(d)}')"
```

> **注意**：ml-service 和回测脚本依赖 `data/<SYMBOL>/klines_1h.json`、`klines_4h.json`、`klines_1d.json`。请确保 go-collector 已运行并写入足够数据后，再进行训练或推理。

---

## 9. 模型训练（Python Analyzer）

### 9.1 职责说明

`python-analyzer/` 包含完整的训练流水线：

- `labeling.py`：基于三重障碍法（Triple Barrier）生成三分类标签（LONG / SHORT / FLAT）
- `train_event_stack_v3.py`：主训练脚本，构建多周期特征并训练堆叠集成模型
- `walkforward_cv.py`：时间序列 Walk-Forward 交叉验证，避免未来数据泄露

### 9.2 训练前准备

```bash
# 确认数据文件存在（以 ETHUSDT 为例）
ls -lh ~/ubuntu-wallet/data/ETHUSDT/klines_1h.json
ls -lh ~/ubuntu-wallet/data/ETHUSDT/klines_4h.json
ls -lh ~/ubuntu-wallet/data/ETHUSDT/klines_1d.json

# 确认模型输出目录存在
mkdir -p ~/ubuntu-wallet/models

# 激活虚拟环境
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
cd ~/ubuntu-wallet
```

### 9.3 训练命令（标准配置）

```bash
python python-analyzer/train_event_stack_v3.py \
  --data-dir data \
  --model-dir models \
  --label-method ternary \
  --horizon 12 \
  --up-thresh 0.015 \
  --calibration isotonic
```

**参数说明：**

| 参数 | 示例值 | 说明 |
|---|---|---|
| `--data-dir` | `data` | K 线数据所在目录 |
| `--model-dir` | `models` | 模型产物输出目录 |
| `--label-method` | `ternary` | 标签生成方法，`ternary` 为三分类，`triple_barrier` 为三重障碍法 |
| `--horizon` | `12` | 标签时间窗口（单位：bar 数，1h bar × 12 = 12 小时） |
| `--up-thresh` | `0.015` | 上涨阈值（1.5%），超过此值标记为 LONG |
| `--calibration` | `isotonic` | 概率校准方法，`isotonic` 或 `sigmoid` |

### 9.4 Walk-Forward 交叉验证（建议在部署前运行）

```bash
python python-analyzer/walkforward_cv.py \
  --data-dir data \
  --n-splits 5 \
  --gap-bars 12 \
  --label-method ternary \
  --confidence-threshold 0.65 \
  --output-csv /tmp/cv_report.csv

# 查看报告
cat /tmp/cv_report.csv
```

### 9.5 模型产物说明

训练成功后，模型会保存到 `models/<版本目录>/`，包含：

| 文件 | 说明 |
|---|---|
| `lightgbm_event_v3.pkl` | LightGBM 基学习器 |
| `lightgbm_event_v3_scaler.pkl` | LightGBM 特征缩放器 |
| `xgboost_event_v3.json` | XGBoost 基学习器（原生格式） |
| `xgboost_event_v3_scaler.pkl` | XGBoost 特征缩放器 |
| `stacking_event_v3.pkl` | 堆叠元模型（Logistic Regression） |
| `calibration_event_v3.pkl` | 概率校准器（Isotonic Regression） |
| `feature_columns_event_v3.json` | 特征列名列表（用于 schema 验证） |
| `model_meta.json` | 模型元信息（版本、训练时间、参数等） |

训练脚本完成后将模型文件归档至 `models/archive/<版本目录>/`，同时复制到 `models/current/`（ml-service 默认从此目录加载），并将版本信息写入 `models/registry.json`。

---

## 10. 多币种架构（Multi-Symbol）

本仓库支持以下交易对独立训练与运行，采用「**共享代码 + 每币种独立数据/模型目录**」的架构：

| 交易对 | 阶段 | 默认启用 |
|--------|------|---------|
| BTCUSDT | Phase 1 | ✅ |
| ETHUSDT | Phase 1 | ✅ |
| SOLUSDT | Phase 1 | ✅ |
| BNBUSDT | Phase 1 | ✅ |
| XRPUSDT | Phase 2 | ❌（待激活）|
| DOGEUSDT | Phase 2 | ❌（待激活）|
| ADAUSDT | Phase 2 | ❌（待激活）|

### 10.1 目录隔离原则

每个币种有独立的数据目录和模型目录，不相互干扰：

```
data/
  BTCUSDT/
    klines_1h.json
    klines_4h.json
    klines_1d.json
    predictions_log.jsonl
    reports/
  ETHUSDT/
    ...

models/
  BTCUSDT/
    current/          ← 当前激活模型产物
    archive/          ← 版本归档
    registry.json     ← 版本注册表
  ETHUSDT/
    ...
```

### 10.2 每币种配置（configs/symbols.yaml）

所有币种的参数（阈值、TP/SL、horizon、校准方式）集中在 `configs/symbols.yaml`：

```yaml
symbols:
  BTCUSDT:
    enabled: true
    interval: "1h"
    threshold: 0.65
    tp: 0.0175
    sl: 0.009
    horizon: 12
    calibration: "isotonic"
  # ... 其他币种
```

要启用 Phase 2 币种，将对应 `enabled: false` 改为 `enabled: true` 即可。

### 10.3 按币种训练

```bash
# 使用便捷包装脚本（自动从 configs/symbols.yaml 读取参数）
bash scripts/train_symbol.sh BTCUSDT
bash scripts/train_symbol.sh ETHUSDT

# 或手工指定路径（完整控制）
SYMBOL=BTCUSDT
python python-analyzer/train_event_stack_v3.py \
  --data-dir  data/${SYMBOL} \
  --model-dir models/${SYMBOL} \
  --horizon   12 \
  --tp-pct    0.0175 \
  --sl-pct    0.009 \
  --calibration isotonic
```

### 10.4 按币种评估日志

```bash
SYMBOL=BTCUSDT
python scripts/evaluate_from_logs.py \
  --symbol ${SYMBOL}
# 路径和参数自动从 configs/symbols.yaml 派生

# 也可手工指定全部参数（向后兼容）：
python scripts/evaluate_from_logs.py \
  --symbol ${SYMBOL} \
  --log-path  data/${SYMBOL}/predictions_log.jsonl \
  --data-dir  data/${SYMBOL} \
  --threshold 0.65 --tp 0.0175 --sl 0.009 --horizon-bars 12
```

### 10.5 按币种 Drift 监控

```bash
SYMBOL=BTCUSDT
ENABLE_DRIFT_MONITOR=true python scripts/report_drift.py \
  --symbol ${SYMBOL}
# 等价于：
#   --train-stats models/${SYMBOL}/current/train_feature_stats.json
#   --log-path    data/${SYMBOL}/predictions_log.jsonl
#   --output-dir  data/${SYMBOL}/reports
```

### 10.6 合法的单币种向后兼容路径

旧的单币种方式（不带 `--symbol`）依然有效：

```bash
# 旧式（仍然支持）
python scripts/report_drift.py \
  --train-stats models/current/train_feature_stats.json \
  --log-path    data/predictions_log.jsonl \
  --output-dir  data/reports
```

### 10.7 查看当前活跃模型

```bash
SYMBOL=BTCUSDT
cat models/${SYMBOL}/current/model_meta.json | python3 -m json.tool
```

---

## 11. 推理服务（ML Service）

### 11.1 职责说明

`ml-service/` 是基于 FastAPI 的在线推理服务，提供以下功能：

- 接收 K 线数据请求，构建多周期特征，调用模型输出 LONG/SHORT/FLAT 信号
- 记录每次预测到 `data/predictions_log.jsonl`
- 提供 `/healthz` 接口，报告模型状态

### 11.2 启动推理服务

```bash
cd ~/ubuntu-wallet/ml-service
source .venv/bin/activate

# 前台运行（调试时推荐，Ctrl+C 停止）
uvicorn app:app --host 127.0.0.1 --port 9000

# 后台运行（推荐配合日志文件）
nohup uvicorn app:app --host 127.0.0.1 --port 9000 \
  > ~/ubuntu-wallet/logs/ml-service.log 2>&1 &
echo "ml-service 已启动，PID: $!"
```

### 11.3 健康检查

```bash
# 检查服务是否启动、模型是否已加载
curl -s http://127.0.0.1:9000/healthz | jq .

# 期望输出示例（字段说明见下表）：
# {
#   "ok": true,
#   "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
#   "calibration_available": true,
#   "calibration_method": "isotonic",
#   "model_dir": "/home/ubuntu/ubuntu-wallet/models/current",
#   ...
# }
```

**重要字段说明：**

- `ok: true`：服务正常且模型已成功加载（`ok: false` 表示模型未加载）
- `calibration_available: true`：概率校准器已加载（建议确保此为 true，校准后的置信度更可靠）
- `model_version`：当前激活的模型版本标识
- `model_dir`：实际加载模型的目录路径

### 11.4 发起预测请求（手动测试）

```bash
# 手动调用 /predict 接口（通常由 go-collector 自动调用）
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTCUSDT", "interval": "1h"}' | jq .
```

### 11.5 预测日志格式

每次预测结果会追加写入 `data/predictions_log.jsonl`，每行一条记录：

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
  "calibrated_confidence": 0.71,
  "calibration_method": "isotonic",
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "active_model": "event_v3"
}
```

**字段说明：**

| 字段 | 说明 |
|---|---|
| `ts` | 特征对应的 bar 时间（对齐 klines_1h.json） |
| `proba_long/short/flat` | 模型对 LONG/SHORT/FLAT 的概率估计 |
| `signal` | 信号方向（LONG / SHORT / FLAT） |
| `confidence` | 信号置信度 |
| `model_version` | 产生此预测的模型版本 |
| `active_model` | 模型系列名称，用于筛选 A/B 测试结果 |

---

## 12. 回测说明

### 12.1 脚本说明

回测脚本 `scripts/backtest_event_v3_http.py` 实现了三重障碍法回测：

- 对历史每根 1h K 线调用 `/predict` 接口，获取预测概率
- 根据 `threshold` 过滤信号（置信度 >= threshold 才入场）
- 模拟 TP / SL / TIMEOUT 三种出场方式
- 支持参数网格搜索（`--thresholds`、`--tp-grid`、`--sl-grid`）
- 内置多周期方向过滤（4h/1d 趋势约束，方案 B）

### 12.2 完整回测命令（网格搜索）

```bash
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate

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

### 12.3 参数说明

| 参数 | 示例值 | 说明 |
|---|---|---|
| `--data-dir` | `data` | K 线数据目录 |
| `--base-url` | `http://127.0.0.1:9000` | ml-service 地址 |
| `--interval` | `1h` | 交易时间框架 |
| `--since` | `2026-02-01T00:00:00Z` | 回测开始时间（ISO 8601 UTC） |
| `--until` | `2026-03-10T23:00:00Z` | 回测结束时间（ISO 8601 UTC） |
| `--thresholds` | `0.55:0.85:0.02` | 阈值网格（起点:终点:步长），置信度低于此值的信号被忽略 |
| `--tp-grid` | `0.005:0.030:0.0025` | 止盈比例网格 |
| `--sl-grid` | `0.003:0.020:0.001` | 止损比例网格 |
| `--horizon-bars` | `6` | 最大持仓时间（bar 数，1h × 6 = 6 小时）|
| `--fee` | `0.0004` | 单边手续费（0.04%）|
| `--slippage` | `0.0` | 额外滑点（0 表示不考虑）|
| `--objective` | `avg_ret_mdd_daily` | 优化目标函数 |
| `--min-signals-per-week` | `1.0` | 最低信号频率过滤 |
| `--position-mode` | `single` | 持仓模式（`stack`=默认，每个信号都开仓；`single`=单仓，上一笔未平仓时忽略新信号） |

### 12.4 多周期方向过滤规则

脚本内部实现了基于 4h/1d K 线的趋势过滤（方案 B）：

- **做多条件**：4h 趋势必须为 UP，且 1d 趋势不能为 DOWN
- **做空条件**：4h 趋势必须为 DOWN，且 1d 趋势不能为 UP
- 趋势判断基于 SMA(5) 与 SMA(20) 的相对位置（容忍区间 0.1%）

### 12.5 当前推荐参数

详细回测记录见 [README_backtest_event_v3_1h.md](README_backtest_event_v3_1h.md)。

| 参数 | 推荐值 |
|---|---|
| threshold | 0.55（calibrated_confidence 可用时建议 0.65） |
| tp | 1.75% |
| sl | 0.90% |
| horizon | 6 小时（6 根 1h bar） |
| interval | 1h |
| 多周期过滤 | 方案 B（4h/1d 趋势约束） |

---

## 13. 模拟运行与日志评估

### 13.1 模拟交易（历史回放）

`scripts/live_trader_eth_perp_simulated.py` 基于历史 K 线数据回放，模拟完整的交易逻辑（入场、出场、风控）：

```bash
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate

python scripts/live_trader_eth_perp_simulated.py \
  --data-dir data \
  --base-url http://127.0.0.1:9000 \
  --tp 0.0175 \
  --sl 0.007 \
  --threshold 0.65 \
  --horizon 6
```

**输出内容**：逐笔模拟交易记录，包含入场时间、信号方向、置信度、出场原因（TP/SL/TIMEOUT）、模拟盈亏等。

### 13.2 基于预测日志的事后评估

`scripts/evaluate_from_logs.py` 读取线上预测日志，结合真实 K 线数据计算事后收益指标：

```bash
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate

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

**参数说明：**

| 参数 | 说明 |
|---|---|
| `--log-path` | 预测日志文件路径（JSONL 格式） |
| `--active-model` | 筛选指定 active_model 字段的预测记录 |
| `--since / --until` | 评估时间窗口（ISO 8601 UTC） |
| `--threshold` | 信号阈值，低于此置信度的预测不计入 |
| `--tp / --sl` | 止盈/止损比例 |
| `--horizon-bars` | 最大持仓 bar 数 |

**输出内容**：包含 `signals/week`、`win_rate`、`avg_ret`、`profit_factor`、各类 MDD 等指标。

> **注意**：当前 `evaluate_from_logs.py` 不含 4h/1d 多周期过滤逻辑，仅基于 1h 概率做决策。若实盘策略加入了多周期过滤，评估指标可能与实盘存在偏差。

---

## 14. 生产部署（systemd）

### 14.1 前提条件

```bash
# 确认部署路径（systemd 服务单元硬编码此路径）
# 服务器用户：ubuntu，仓库路径：/home/ubuntu/ubuntu-wallet
ls /home/ubuntu/ubuntu-wallet/bin/go-collector      # go-collector 二进制
ls /home/ubuntu/ubuntu-wallet/ml-service/.venv/bin/python  # Python venv
```

### 14.2 配置敏感环境变量

生产环境将 API Key 等敏感信息放到 `/etc/ubuntu-wallet/`：

```bash
# 创建配置目录
sudo mkdir -p /etc/ubuntu-wallet

# 从模板创建配置文件
sudo cp /home/ubuntu/ubuntu-wallet/systemd/env/collector.env.example \
        /etc/ubuntu-wallet/collector.env

# 设置严格权限（仅 root 可读）
sudo chmod 600 /etc/ubuntu-wallet/collector.env
sudo chown root:root /etc/ubuntu-wallet/collector.env

# 填入真实 API Key
sudo nano /etc/ubuntu-wallet/collector.env
```

### 14.3 部署 Go Collector 服务

```bash
# 复制服务单元文件
sudo cp /home/ubuntu/ubuntu-wallet/systemd/go-collector.service \
        /etc/systemd/system/go-collector.service

# 重新加载 systemd 配置
sudo systemctl daemon-reload

# 启用并立即启动服务（开机自启 + 立即启动）
sudo systemctl enable --now go-collector.service

# 检查服务状态
systemctl status go-collector.service --no-pager

# 查看实时日志（Ctrl+C 退出）
journalctl -u go-collector.service -f --no-pager

# 验证健康状态
curl -s http://127.0.0.1:8080/api/healthz | jq .
```

### 14.4 部署 ML Service 服务

```bash
# 复制服务单元文件
sudo cp /home/ubuntu/ubuntu-wallet/systemd/ml-service.service \
        /etc/systemd/system/ml-service.service

# 重新加载 systemd 配置
sudo systemctl daemon-reload

# 启用并立即启动服务
sudo systemctl enable --now ml-service.service

# 检查服务状态
systemctl status ml-service.service --no-pager

# 查看实时日志
journalctl -u ml-service.service -f --no-pager

# 验证健康状态
curl -s http://127.0.0.1:9000/healthz | jq .
```

### 14.5 部署定时评估服务（可选）

```bash
# 复制评估服务和定时器
sudo cp /home/ubuntu/ubuntu-wallet/systemd/evaluate-predictions.service \
        /etc/systemd/system/evaluate-predictions.service
sudo cp /home/ubuntu/ubuntu-wallet/systemd/evaluate-predictions.timer \
        /etc/systemd/system/evaluate-predictions.timer

sudo systemctl daemon-reload
sudo systemctl enable --now evaluate-predictions.timer

# 验证定时器已注册
systemctl list-timers --all | grep evaluate
```

### 14.6 部署健康检查定时器（可选）

每分钟检查 go-collector 健康状态，异常时自动重启并发送 Telegram 告警：

```bash
sudo cp /home/ubuntu/ubuntu-wallet/systemd/check-go-collector.service \
        /etc/systemd/system/check-go-collector.service
sudo cp /home/ubuntu/ubuntu-wallet/systemd/check-go-collector.timer \
        /etc/systemd/system/check-go-collector.timer

sudo systemctl daemon-reload
sudo systemctl enable --now check-go-collector.timer

# 验证定时器
systemctl list-timers --all | grep check-go-collector

# 立即手动执行一次检查（不等下一分钟）
sudo systemctl start check-go-collector.service
journalctl -u check-go-collector.service -n 50 --no-pager
```

> 详细部署步骤请参考 [systemd/DEPLOY-NEW-SERVER.md](systemd/DEPLOY-NEW-SERVER.md)。

### 14.7 其他可选定时服务

仓库还包含以下定时服务，部署方式与上述一致（参考 [docs/DEPLOY_CN.md](docs/DEPLOY_CN.md) 中的详细说明）：

| 服务 | 定时器 | 作用 |
|---|---|---|
| `daily-report.service` | `daily-report.timer` （每天 01:05 UTC） | 每日预测质量报告 |
| `drift-monitor.service` | `drift-monitor.timer` （每 6 小时） | 特征漂移监控 |
| `calibration-report.service` | `calibration-report.timer` （每周一 02:00 UTC） | 校准质量报告 |

---

## 15. 常用命令汇总

```bash
# ── 服务状态查看 ─────────────────────────────────────────
systemctl status go-collector.service --no-pager
systemctl status ml-service.service --no-pager

# ── 服务日志查看（最近 100 行）────────────────────────────
journalctl -u go-collector.service -n 100 --no-pager
journalctl -u ml-service.service -n 100 --no-pager

# ── 实时日志流 ────────────────────────────────────────────
journalctl -u go-collector.service -f --no-pager
journalctl -u ml-service.service -f --no-pager

# ── 健康检查 ──────────────────────────────────────────────
curl -s http://127.0.0.1:8080/api/healthz | jq .
curl -s http://127.0.0.1:9000/healthz | jq .

# ── 服务重启 ──────────────────────────────────────────────
sudo systemctl restart go-collector.service
sudo systemctl restart ml-service.service

# ── 激活 Python 虚拟环境 ──────────────────────────────────
source ~/ubuntu-wallet/ml-service/.venv/bin/activate

# ── 模型训练 ──────────────────────────────────────────────
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate
python python-analyzer/train_event_stack_v3.py \
  --data-dir data --model-dir models \
  --label-method ternary --horizon 12 --up-thresh 0.015 \
  --calibration isotonic

# ── 快速回测（单组参数） ──────────────────────────────────
python scripts/backtest_event_v3_http.py \
  --data-dir data --base-url http://127.0.0.1:9000 \
  --interval 1h \
  --threshold 0.65 \
  --tp-grid 0.0175:0.0175:0.001 \
  --sl-grid 0.007:0.007:0.001 \
  --horizon-bars 6

# ── 日志评估 ──────────────────────────────────────────────
python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl \
  --data-dir data --interval 1h \
  --threshold 0.55 --tp 0.0175 --sl 0.009 \
  --fee 0.0004 --horizon-bars 6

# ── 重新编译 go-collector ────────────────────────────────
cd ~/ubuntu-wallet/go-collector
go build -o ../bin/go-collector .
cd ~/ubuntu-wallet

# ── 查看预测日志最新记录 ──────────────────────────────────
tail -n 5 ~/ubuntu-wallet/data/predictions_log.jsonl | python3 -c \
  "import sys,json; [print(json.dumps(json.loads(l), indent=2, ensure_ascii=False)) for l in sys.stdin]"

# ── 查看模型当前激活版本 ──────────────────────────────────
cat ~/ubuntu-wallet/models/current/model_meta.json | python3 -m json.tool | grep -E "model_version|trained_at"

# ── 更新代码并重启服务 ────────────────────────────────────
cd ~/ubuntu-wallet
git pull
sudo systemctl restart go-collector.service
sudo systemctl restart ml-service.service
```

---

## 16. 故障排查

### 16.1 端口已被占用

```bash
# 检查 8080 / 9000 端口是否被占用
ss -tlnp | grep -E '8080|9000'

# 查找占用进程的详细信息
lsof -i :8080
lsof -i :9000
```

### 16.2 go-collector 无法启动

```bash
# 检查二进制文件是否存在且有执行权限
ls -lh ~/ubuntu-wallet/bin/go-collector

# 如果不存在，重新编译
cd ~/ubuntu-wallet/go-collector
go mod tidy
go build -o ../bin/go-collector .

# 检查环境变量配置
# 生产环境
cat /etc/ubuntu-wallet/collector.env

# 开发环境
cat ~/ubuntu-wallet/.env

# 查看服务启动日志
journalctl -u go-collector.service -n 50 --no-pager
```

### 16.3 ml-service 无法启动

```bash
# 检查 venv 是否存在
ls ~/ubuntu-wallet/ml-service/.venv/bin/python

# 如果 venv 不存在，重新创建
cd ~/ubuntu-wallet/ml-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
deactivate

# 检查 uvicorn 是否安装
~/ubuntu-wallet/ml-service/.venv/bin/python -m uvicorn --version

# 查看服务启动日志
journalctl -u ml-service.service -n 50 --no-pager
```

### 16.4 模型文件缺失

```bash
# 检查模型目录
ls -lh ~/ubuntu-wallet/models/

# 检查 current/ 目录下的模型元信息
ls ~/ubuntu-wallet/models/current/
cat ~/ubuntu-wallet/models/current/model_meta.json | python3 -m json.tool

# 如果 models/current/ 不存在或为空，需要先进行训练
# 参考第 9 节运行训练脚本
```

### 16.5 Python 依赖问题

```bash
# 重新安装依赖
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
pip install --upgrade pip
pip install -r ~/ubuntu-wallet/ml-service/requirements.txt

# 检查 LightGBM 是否可用
python -c "import lightgbm; print('LightGBM 版本:', lightgbm.__version__)"

# 如果出现 libgomp 相关错误，安装系统库
sudo apt install -y libgomp1
```

### 16.6 健康检查失败

```bash
# 确认服务进程是否在运行
ps aux | grep go-collector
ps aux | grep uvicorn

# 确认端口是否在监听
ss -tlnp | grep 8080
ss -tlnp | grep 9000

# 查看服务日志寻找错误原因
journalctl -u go-collector.service -n 50 --no-pager
journalctl -u ml-service.service -n 50 --no-pager
```

### 16.7 Go 构建问题

```bash
# 确认 Go 版本（需要 1.21+）
go version

# 清理缓存并重新构建
cd ~/ubuntu-wallet/go-collector
go clean -cache
go mod tidy
go build -o ../bin/go-collector .
```

### 16.8 数据文件不更新

```bash
# 确认 go-collector 正在运行
systemctl status go-collector.service --no-pager

# 检查数据文件的最后修改时间
ls -lh ~/ubuntu-wallet/data/klines_*.json

# 检查 API Key 配置是否正确
# 健康检查接口通常能反映连接状态
curl -s http://127.0.0.1:8080/api/healthz | jq .
```

---

## 17. 风险提示与免责声明

**请在使用本系统前仔细阅读以下说明：**

1. **研究与学习用途**：本系统主要用于机器学习模型研究、策略回测分析和系统架构学习，不构成任何投资建议。

2. **不承诺收益**：历史回测结果不代表未来实盘收益。回测中出现的高胜率和低回撤，在真实市场中可能因滑点、流动性、市场制度变化等因素而显著恶化。

3. **真实资金使用需充分验证**：如有意将本系统用于真实资金交易，在正式投入前必须：
   - 充分理解系统的每一个组件和逻辑
   - 在 DRY-RUN 模式下长期观察（建议至少数周）
   - 独立评估策略在不同市场环境下的表现
   - 设置严格的风控规则和仓位限制

4. **加密货币市场风险**：加密货币市场波动极大，可能在短时间内发生大幅价格变动，存在本金损失乃至归零的风险。

5. **API Key 安全**：请妥善保管交易所 API Key，绝对不要提交到 Git 仓库，不要分享给他人。如怀疑 Key 已泄露，请立即在交易所管理后台撤销并重新生成。

6. **本仓库作者不对使用本系统所导致的任何直接或间接损失承担责任。**

---

## 更多文档

| 文档 | 说明 |
|---|---|
| [docs/ARCHITECTURE_CN.md](docs/ARCHITECTURE_CN.md) | 详细系统架构说明 |
| [docs/DEPLOY_CN.md](docs/DEPLOY_CN.md) | 详细部署步骤 |
| [docs/MODEL_LIFECYCLE_CN.md](docs/MODEL_LIFECYCLE_CN.md) | 模型版本管理与生命周期 |
| [docs/RUNBOOK_CN.md](docs/RUNBOOK_CN.md) | 运维操作手册 |
| [docs/FAILURE_MODES_CN.md](docs/FAILURE_MODES_CN.md) | 故障模式与应对方案 |
| [docs/ETH_perp_risk_rules.md](docs/ETH_perp_risk_rules.md) | ETH 永续合约风控规则 |
| [README_backtest_event_v3_1h.md](README_backtest_event_v3_1h.md) | event_v3 1h 策略回测详细记录 |
| [systemd/DEPLOY-NEW-SERVER.md](systemd/DEPLOY-NEW-SERVER.md) | 新服务器完整部署说明 |
| [go-collector/OPS-NOTES.md](go-collector/OPS-NOTES.md) | Go Collector 运维笔记 |
