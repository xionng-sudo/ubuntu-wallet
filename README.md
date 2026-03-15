# 🔮 ETH Crypto Prediction & ML Trading System

> High-precision, continuously-evaluable ML trading pipeline for ETH perpetual futures.
> Built on Go data collection + Python ML training + FastAPI inference + systematic evaluation loop.

**→ For a full technical and operational guide see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

---

## Quick Start

```bash
# 1. Install dependencies
./scripts/install.sh

# 2. Build and start Go collector (collects kline data)
cd go-collector && go build -o go-collector . && ./go-collector &

# 3. Train the event_v3 model (needs klines_1h/4h/1d.json in data/)
source ml-service/.venv/bin/activate
python python-analyzer/train_event_stack_v3.py \
  --data-dir data --model-dir models \
  --label-method ternary --horizon 12 --up-thresh 0.015 \
  --calibration isotonic

# 4. Start ml-service (inference API on port 9000)
cd ml-service && uvicorn app:app --host 127.0.0.1 --port 9000

# 5. Verify health
curl http://127.0.0.1:9000/healthz

# 6. Run backtest
python scripts/backtest_event_v3_http.py \
  --data-dir data --base-url http://127.0.0.1:9000 \
  --threshold 0.65 --tp-grid 0.0175:0.0175:0.001 \
  --sl-grid 0.007:0.007:0.001 --horizon-bars 6

# 7. Simulate / replay live trading
python scripts/live_trader_eth_perp_simulated.py \
  --data-dir data --tp 0.0175 --sl 0.007 --threshold 0.65

# 8. Evaluate logged predictions
python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl --data-dir data \
  --threshold 0.55 --tp 0.0175 --sl 0.007 --horizon-bars 6
```

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  Market Data Collection                         │
│  Binance API  ──┐                                               │
│  OKX API      ──┤── go-collector (port 8080) ──► data/         │
│  Coinbase API ──┘   klines_1h/4h/1d.json                       │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                ML Training Pipeline (offline)                   │
│                                                                 │
│  labeling.py ──────────────────────────────────┐               │
│    make_ternary_labels()                        │               │
│    make_triple_barrier_labels()                 │               │
│                                                 ▼               │
│  train_event_stack_v3.py                  LightGBM + XGBoost   │
│    build_multi_tf_feature_df()           (base models, 3-class) │
│    ── 1h features                               │               │
│    ── 4h features (prefix tf4h_)          LogisticRegression    │
│    ── 1d features (prefix tf1d_)         (stacking meta-model) │
│                                                 │               │
│  walkforward_cv.py                       calibration.py         │
│    (time-series CV, no leakage)          (isotonic/sigmoid)     │
│                                                 │               │
│                              ┌─────────────────▼──────────┐    │
│                              │   models/ directory         │    │
│                              │   lightgbm_event_v3.pkl     │    │
│                              │   xgboost_event_v3.json     │    │
│                              │   stacking_event_v3.pkl     │    │
│                              │   calibration_event_v3.pkl  │    │
│                              │   feature_columns_event_v3.json│  │
│                              │   model_meta.json           │    │
│                              └────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
         │ (model artifacts)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ml-service (FastAPI, port 9000)               │
│                                                                 │
│  POST /predict                                                  │
│    feature_builder.py ── builds multi-tf features              │
│      schema_validation() ── detects online/offline drift       │
│    model_loader.py ── loads models + calibration               │
│    calibration.py ── calibrate_proba()                         │
│    prediction_logger.py ── logs to data/predictions_log.jsonl  │
│                                                                 │
│  Response: { signal, confidence, calibrated_confidence, ... }  │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                Strategy / Decision Layer                        │
│                                                                 │
│  Multi-timeframe filter (Scheme B):                             │
│    LONG allowed  : 4h=UP and 1d≠DOWN                           │
│    SHORT allowed : 4h=DOWN and 1d≠UP                           │
│                                                                 │
│  Execution:                                                     │
│    backtest_event_v3_http.py  (offline grid search)            │
│    live_trader_eth_perp_simulated.py  (historical replay)      │
│    live_trader_eth_perp_binance.py    (live DRY-RUN / real)    │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                Evaluation Loop (closed-loop)                    │
│                                                                 │
│  evaluate_from_logs.py  (scheduled every 6h via systemd timer) │
│    reads data/predictions_log.jsonl                            │
│    simulates triple-barrier exits on real klines               │
│    reports: win_rate, avg_ret, MDD, coverage, TP/SL/TO dist    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Go collector | `go-collector/` | Market data ingestion (klines, trader data) |
| Training pipeline | `python-analyzer/train_event_stack_v3.py` | Train LightGBM+XGBoost+stacking model |
| Labeling | `python-analyzer/labeling.py` | Ternary & triple-barrier label generation |
| Walk-forward CV | `python-analyzer/walkforward_cv.py` | Time-series CV without leakage |
| ML inference API | `ml-service/app.py` | FastAPI: `/predict`, `/healthz` |
| Feature builder | `ml-service/feature_builder.py` | Multi-tf feature construction + schema validation |
| Model loader | `ml-service/model_loader.py` | Load models + calibration artifacts |
| Calibration | `ml-service/calibration.py` | Isotonic/Platt calibration |
| Prediction logger | `ml-service/prediction_logger.py` | JSONL log with raw+calibrated probabilities |
| Backtest engine | `scripts/backtest_event_v3_http.py` | Triple-barrier backtest + grid search |
| Simulated trader | `scripts/live_trader_eth_perp_simulated.py` | Historical replay with risk engine |
| Evaluation | `scripts/evaluate_from_logs.py` | Evaluate logged predictions vs real outcomes |
| Multi-TF utils | `scripts/mt_trend_utils.py` | MTTrendContext (4h/1d trend filters) |

---

## Best Practices

### Signal quality over quantity
- Use threshold ≥ 0.65 (or calibrated_confidence ≥ 0.65) to ensure high precision
- Multi-timeframe filter reduces false breakouts significantly
- Triple-barrier labels better align training with actual exit logic

### Calibration
After training, check `/healthz` for `calibration_available: true`. When calibration is loaded,
the system uses calibrated probabilities for thresholding and logs both raw and calibrated
probabilities for each prediction. This makes confidence more reliable for decision making.

### Walk-forward validation
Before deploying a new model, always run walkforward_cv.py to check for temporal leakage
and understand generalization across different time periods:

```bash
python python-analyzer/walkforward_cv.py \
  --data-dir data --n-splits 5 --gap-bars 12 \
  --label-method ternary --confidence-threshold 0.65 \
  --output-csv /tmp/cv_report.csv
```

### Evaluation loop
The predictions_log.jsonl is the key monitoring artifact. Schedule evaluate_from_logs.py
via `systemd/evaluate-predictions.timer` to get automatic performance reports every 6 hours.

---

## Recommended Strategy Parameters

Based on backtesting and live evaluation (as of 2026-03):

| Parameter | Value | Notes |
|-----------|-------|-------|
| threshold | 0.65 | Use calibrated_confidence when available |
| tp | 1.75% | Take-profit |
| sl | 0.70% | Stop-loss |
| horizon | 6h | Max holding period |
| interval | 1h | Trading timeframe |
| 4h filter | UP required for LONG | Multi-TF Scheme B |
| 1d filter | Not DOWN for LONG | Multi-TF Scheme B |

---

## Deployment (Production)

```bash
# Install and enable systemd services
sudo cp systemd/go-collector.service /etc/systemd/system/
sudo cp systemd/ml-service.service /etc/systemd/system/
sudo cp systemd/evaluate-predictions.service /etc/systemd/system/
sudo cp systemd/evaluate-predictions.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now go-collector ml-service
sudo systemctl enable --now evaluate-predictions.timer

# Monitor
sudo journalctl -fu ml-service
sudo journalctl -fu evaluate-predictions
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for complete deployment, maintenance,
and troubleshooting documentation.

---

## 📑 目录 (Original Chinese Documentation)

1. [系统架构](#系统架构)
2. [技术栈与库版本](#技术栈与库版本)
3. [环境准备 (Ubuntu 22.04)](#环境准备)
4. [详细安装步骤](#详细安装步骤)
5. [配置 API 密钥](#配置-api-密钥)
6. [编译和构建](#编译和构建)
7. [运行系统](#运行系统)
8. [各模块说明](#各模块说明)
9. [图形可视化说明](#图形可视化说明)
10. [常见问题](#常见问题)

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                    ETH 预测系统架构                           │
├──────────────────┬──────────────────┬────────────────────────┤
│   Binance API    │     OKX API      │    Coinbase API        │
└────────┬─────────┴────────┬─────────┴──────────┬─────────────┘
         │                  │                    │
         ▼                  ▼                    ▼
┌──────────────────────────────────────────────────────────────┐
│              Go 数据采集服务 (端口 8080)                       │
│  • 获取 Top50 交易员数据     • 获取前100次交易记录              │
│  • 实时K线和行情数据         • REST API 接口                  │
│  • 价格层级分析              • 每5分钟自动更新                  │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP API
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              Python 分析引擎                                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐     │
│  │  技术分析    │ │  ML 预测    │ │  自动学习机器人       │     │
│  │• SMA/EMA    │ │• XGBoost   │ │• 增量训练            │      │
│  │• MACD/RSI   │ │• LightGBM  │ │• 交易员数据融合       │     │
│  │• 布林带/ADX  │ │• LSTM       │ │• 模型自动更新        │     │
│  │• 一目均衡表  │ │• 集成投票    │ │• 特征自动工程        │     │
│  └─────────────┘ └─────────────┘ └─────────────────────┘     │
│  ┌─────────────┐ ┌──────────────────────────────────────┐    │
│  │  提醒系统    │ │  Dash 可视化仪表板 (端口 8050)         │    │
│  │• 买卖信号   │  │• K线图 + 技术指标                     │    │
│  │• RSI 超买卖 │  │• 交易员分析图                         │    │
│  │• AI 预测    │  │• 预测结果图                           │    │
│  │• 成交量异常 │  │• 实时更新 (30秒)                      │    │
│  └─────────────┘ └──────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

---

## 技术栈与库版本

### Go 语言 (数据采集)

| 组件 | 版本 | 用途 |
|------|------|------|
| Go | 1.21.x | 编程语言 |
| gorilla/websocket | v1.5.1 | WebSocket 连接 |
| robfig/cron/v3 | v3.0.1 | 定时任务 |
| sirupsen/logrus | v1.9.3 | 日志管理 |

### Python (分析与预测)

| 库名 | 版本 | 用途 |
|------|------|------|
| Python | 3.10/3.11/3.12 | 编程语言 |
| **数据处理** | | |
| numpy | 1.26.4 | 数值计算 |
| pandas | 2.2.1 | 数据处理 |
| scipy | 1.12.0 | 科学计算 |
| **技术分析** | | |
| ta | 0.11.0 | 技术指标 |
| ta-lib | 0.4.28 | 专业技术分析 |
| pandas-ta | 0.3.14b1 | Pandas技术分析扩展 |
| **机器学习** | | |
| scikit-learn | 1.4.1 | 经典ML |
| xgboost | 2.0.3 | 梯度提升 |
| lightgbm | 4.3.0 | 轻量级梯度提升 |
| **深度学习** | | |
| torch (PyTorch) | 2.2.1 | LSTM神经网络 |
| tensorflow | 2.15.0 | 深度学习 (备选) |
| **可视化** | | |
| plotly | 5.19.0 | 交互式图表 |
| dash | 2.16.1 | Web 仪表板 |
| dash-bootstrap-components | 1.5.0 | UI 组件 |
| matplotlib | 3.8.3 | 静态图表 |
| mplfinance | 0.12.10b0 | 金融K线图 |
| kaleido | 0.2.1 | 图表导出 |
| **交易所API** | | |
| ccxt | 4.2.70 | 统一交易所接口 |
| requests | 2.31.0 | HTTP 请求 |
| websocket-client | 1.7.0 | WebSocket |
| aiohttp | 3.9.3 | 异步HTTP |
| **其他工具** | | |
| python-dotenv | 1.0.1 | 环境变量 |
| joblib | 1.3.2 | 模型序列化 |
| flask | 3.0.2 | Web框架 (API) |
| transformers | 4.38.2 | NLP/情绪分析 |

### 系统依赖

| 组件 | 版本 | 用途 |
|------|------|------|
| Ubuntu | 22.04 LTS | 操作系统 |
| build-essential | - | C/C++ 编译工具 |
| TA-Lib C 库 | 0.4.0 | 技术分析底层库 |

---

## 环境准备

### 确认系统版本

```bash
# 确认 Ubuntu 版本
lsb_release -a
# 应显示: Ubuntu 22.04.x LTS
```

### 确保有 sudo 权限

```bash
sudo whoami
# 应显示: root
```

---

## 详细安装步骤

### 方法一：生产部署（systemd + ml-service + go-collector，自愈，推荐）

适用场景：云服务器长期运行 / 自动重启 / 监控自愈。

部署后会启动：
- `ml-service`（FastAPI，仅监听 `127.0.0.1:9000`）
- `go-collector`（HTTP API 默认 `8080`，以你的程序/环境变量为准）
- `check-go-collector.timer`（每分钟健康检查，异常自动重启并 Telegram 通知）

#### 1）克隆项目
```bash
cd ~
git clone https://github.com/xionghan889-tech/ubuntu-wallet.git
cd ubuntu-wallet
```

#### 2）一键部署（需要 root）
```bash
sudo bash scripts/install/bootstrap-new-server.sh
```

脚本会自动：
- 从 `systemd/env/*.example` 生成 `/etc/ubuntu-wallet/*.env`（如已存在不会覆盖）
- 安装 systemd unit 到 `/etc/systemd/system/`
- 安装 sudoers（允许自愈脚本无密码重启 go-collector）
- 尝试构建 go-collector、创建 ml-service 的 `.venv`
- 检查 env 必填项；若缺失会提示你补录，并不会强行启动服务

#### 3）填写敏感配置（不进 Git）
```bash
sudo nano /etc/ubuntu-wallet/collector.env
sudo nano /etc/ubuntu-wallet/telegram.env
```

如脚本未自动启动服务，手动启动：
```bash
sudo systemctl enable --now ml-service.service
sudo systemctl enable --now go-collector.service
sudo systemctl enable --now check-go-collector.timer
```

#### 4）验收
```bash
curl -fsS http://127.0.0.1:9000/docs | head
curl -fsS --max-time 3 http://127.0.0.1:8080/api/healthz | jq .
systemctl list-timers --all | grep check-go-collector || true
journalctl -u check-go-collector.service -n 80 --no-pager
```

详细说明见：
- `systemd/DEPLOY-NEW-SERVER.md`

---

### 方法二：本地研究/开发（scripts/install.sh + scripts/run.sh）

适用场景：本地跑完整研究流程（`python-analyzer` + Dash 8050）。

> 注意：该方式与生产 systemd 部署是两条路线，生产机器建议优先用“方法一”。

```bash
# 1. 克隆项目
git clone https://github.com/xionghan889-tech/ubuntu-wallet.git
cd ubuntu-wallet

# 2. 给脚本添加执行权限
chmod +x scripts/install.sh scripts/run.sh

# 3. 运行安装脚本
bash scripts/install.sh
```
### 方法二: 手动逐步安装

#### 步骤 1: 更新系统

```bash
sudo apt update && sudo apt upgrade -y
```

#### 步骤 2: 安装系统依赖

```bash
sudo apt install -y \
    build-essential \
    git \
    curl \
    wget \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    jq \
    libffi-dev \
    libssl-dev
```

#### 步骤 3: 安装 Python 3.11

```bash
# 添加 deadsnakes PPA (提供最新 Python 版本)
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update

# 安装 Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# 验证安装
python3.11 --version
# 输出: Python 3.11.x
```

#### 步骤 4: 安装 Go 1.21

```bash
# 下载 Go
GO_VERSION="1.21.13"
wget "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -O /tmp/go.tar.gz

# 解压安装
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf /tmp/go.tar.gz
rm /tmp/go.tar.gz

# 配置环境变量
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
echo 'export GOPATH=$HOME/go' >> ~/.bashrc
echo 'export PATH=$PATH:$GOPATH/bin' >> ~/.bashrc
source ~/.bashrc

# 验证安装
go version
# 输出: go version go1.21.13 linux/amd64
```

#### 步骤 5: 安装 TA-Lib C 库

```bash
# TA-Lib Python 包需要底层 C 库支持
cd /tmp
wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar -xzf ta-lib-0.4.0-src.tar.gz
cd ta-lib/
./configure --prefix=/usr
make -j$(nproc)
sudo make install
sudo ldconfig
cd -

# 验证
ldconfig -p | grep ta_lib
# 应显示 libta_lib 库路径
```

#### 步骤 6: 创建 Python 虚拟环境

```bash
# 回到项目目录
cd ~/ubuntu-wallet

# 创建虚拟环境
python3.11 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 升级 pip
pip install --upgrade pip setuptools wheel

# 安装 Python 依赖
pip install -r python-analyzer/requirements.txt

# 验证关键包
python -c "import numpy; print(f'NumPy: {numpy.__version__}')"
python -c "import pandas; print(f'Pandas: {pandas.__version__}')"
python -c "import sklearn; print(f'Scikit-learn: {sklearn.__version__}')"
python -c "import xgboost; print(f'XGBoost: {xgboost.__version__}')"
python -c "import torch; print(f'PyTorch: {torch.__version__}')"
python -c "import plotly; print(f'Plotly: {plotly.__version__}')"
python -c "import dash; print(f'Dash: {dash.__version__}')"
python -c "import ccxt; print(f'CCXT: {ccxt.__version__}')"
```

> **注意**: 如果 `ta-lib` Python 包安装失败，可以跳过，系统使用 `ta` 和 `pandas-ta` 作为替代。
> 如果 PyTorch 安装过慢，可以先安装 CPU 版本：
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cpu
> ```

#### 步骤 7: 编译 Go 程序

```bash
# 进入 Go 项目目录
cd go-collector

# 下载依赖
go mod tidy

# 编译
go build -o ../bin/go-collector .

# 验证
ls -la ../bin/go-collector
# 应显示可执行文件

# 回到项目根目录
cd ..
```

#### 步骤 8: 配置环境和目录

```bash
# 创建必要目录
mkdir -p data models bin logs

# 创建环境配置文件
cp .env.example .env

# 编辑 .env 填入你的 API 密钥
nano .env
```

---

## 配置 API 密钥

### 获取 Binance API Key

1. 访问 [Binance](https://www.binance.com/) 注册账号
2. 进入 **个人中心** → **API 管理**
3. 创建新 API Key（建议只开启 **读取** 权限，不要开交易权限）
4. 复制 API Key 和 Secret Key

### 获取 OKX API Key

1. 访问 [OKX](https://www.okx.com/) 注册账号
2. 进入 **设置** → **API**
3. 创建新 API Key，设置 Passphrase（必须记住）
4. 权限选择 **只读**
5. 复制 API Key、Secret Key、Passphrase

### 获取 Coinbase API Key

1. 访问 [Coinbase](https://www.coinbase.com/) 注册账号
2. 进入 **Settings** → **API**
3. 创建新 API Key
4. 复制 API Key 和 Secret

### 编辑 .env 文件

```bash
nano .env
```

```env
# 填入你的密钥
BINANCE_API_KEY=你的Binance_API_Key
BINANCE_API_SECRET=你的Binance_Secret

OKX_API_KEY=你的OKX_API_Key
OKX_API_SECRET=你的OKX_Secret
OKX_PASSPHRASE=你的OKX_Passphrase

COINBASE_API_KEY=你的Coinbase_API_Key
COINBASE_API_SECRET=你的Coinbase_Secret

COLLECTOR_PORT=8080
COLLECTOR_API_URL=http://localhost:8080
DATA_DIR=./data
MODEL_DIR=./models
```

> **⚠️ 重要**: 即使不填 API Key，系统也能运行（使用模拟数据）。但要获取真实交易员数据，需要填入有效的 API Key。

## Klines 历史回溯（Lookback）

默认情况下，go-collector 拉取 K 线会取最近窗口（`GetKlines(..., limit=500)`）。为了支持回测/特征���程，需要拉取更长历史时，可以对 15m/1h/4h/1d 启用 lookback 分页拉取（1m/5m 仍保持轻量）。

### 环境变量（go-collector）

- `KLINES_LOOKBACK_ENABLED`：是否启用 lookback（默认：true）
  - `false` 时，所有 interval 都回退到最近 500 根 K 线（`GetKlines(..., 500)`）
- `KLINES_15M_LOOKBACK_DAYS`：15m 回溯天数（默认：90）
- `KLINES_1H_LOOKBACK_DAYS`：1h 回溯天数（默认：180）
- `KLINES_4H_LOOKBACK_DAYS`：4h 回溯天数（默认：365）
- `KLINES_1D_LOOKBACK_DAYS`：1d 回溯天数（默认：730）
- `KLINES_LOOKBACK_MAX_PAGES`：lookback 分页最大页数（默认：2000），用于防止极端情况下死循环/无限请求
KLINES_LOOKBACK_MODE=on_startup（默认，推荐生产）
KLINES_LOOKBACK_MODE=always（每次 FAST tick 都回溯，可能限频/日志刷屏）
KLINES_LOOKBACK_MODE=off（不回溯，只拉最近窗口）

> 将某个 `KLINES_*_LOOKBACK_DAYS` 设为 `0` 会让该 interval 回退到最近 500 根。

### systemd 示例（/etc/ubuntu-wallet/collector.env）
```env
KLINES_LOOKBACK_ENABLED=true
KLINES_15M_LOOKBACK_DAYS=90
KLINES_1H_LOOKBACK_DAYS=180
KLINES_4H_LOOKBACK_DAYS=365
KLINES_1D_LOOKBACK_DAYS=730
KLINES_LOOKBACK_MAX_PAGES=2000
```

### docker 示例
```bash
docker run --rm \
  -e KLINES_LOOKBACK_ENABLED=true \
  -e KLINES_15M_LOOKBACK_DAYS=90 \
  -e KLINES_1H_LOOKBACK_DAYS=180 \
  -e KLINES_4H_LOOKBACK_DAYS=365 \
  -e KLINES_1D_LOOKBACK_DAYS=730 \
  -e KLINES_LOOKBACK_MAX_PAGES=2000 \
  your-image:latest
```
---

## 编译和构建

```bash
# 确保在项目根目录
cd ~/ubuntu-wallet

# 编译 Go 采集器
cd go-collector
go mod tidy
go build -o ../bin/go-collector .
cd ..

# 验证
./bin/go-collector --help 2>/dev/null || echo "编译成功"
```

---

## 运行系统

### 方式一: 一键启动完整系统

```bash
chmod +x scripts/run.sh

# 启动完整系统 (Go采集器 + Python分析 + 可视化仪表板)
bash scripts/run.sh full
```
###关闭
./scripts/run.sh stop

### 方式二: 分步启动

#### 终端 1: 启动 Go 数据采集器

```bash
cd ~/ubuntu-wallet

# 加载环境变量
export $(grep -v '^#' .env | xargs)

# 启动采集器
./bin/go-collector
```

你会看到类似输出：
```
INFO[2024-01-01T12:00:00] ========================================
INFO[2024-01-01T12:00:00]   ETH Crypto Trader Data Collector
INFO[2024-01-01T12:00:00] ========================================
INFO[2024-01-01T12:00:00] Starting initial data collection...
INFO[2024-01-01T12:00:05] [Binance] Fetched 50 top traders
INFO[2024-01-01T12:00:10] [OKX] Fetched 50 top traders
INFO[2024-01-01T12:00:15] Data collection completed successfully!
INFO[2024-01-01T12:00:15] API server starting on port 8080
```

#### 终端 2: 启动 Python 分析系统

```bash
cd ~/ubuntu-wallet

# 激活虚拟环境
source venv/bin/activate

# 进入 Python 目录
cd python-analyzer

# === 可选运行模式 ===

# 完整运行（分析 + 训练 + 预测 + 仪表板）
python main.py

# 仅分析
python main.py --analyze

# 仅训练模型
python main.py --train

# 仅预测
python main.py --predict

# 仅启动仪表板
python main.py --dashboard

# 生成并保存图表
python main.py --save-charts

# 查看交易员分析
python main.py --traders
```

### 方式三: 使用管理脚本

```bash
# 查看所有命令
bash scripts/run.sh

# 启动完整系统
bash scripts/run.sh full

# 仅启动采集器
bash scripts/run.sh collector

# 仅分析
bash scripts/run.sh analyze

# 训练模型
bash scripts/run.sh train

# 运行预测
bash scripts/run.sh predict

# 启动仪表板
bash scripts/run.sh dashboard

# 查看系统状态
bash scripts/run.sh status

# 停止所有服务
bash scripts/run.sh stop
```

### 访问仪表板

在浏览器中打开:
```
http://localhost:8050
```

如果是远程服务器，使用服务器 IP:
```
http://你的服务器IP:8050
```

---

## 各模块说明

### 1. Go 数据采集器 (`go-collector/`)

| 文件 | 功能 |
|------|------|
| `main.go` | 主程序入口、HTTP API 服务器、数据调度 |
| `collector/binance.go` | Binance 交易所数据采集 (期货排行榜) |
| `collector/okx.go` | OKX 交易所数据采集 (跟单排行) |
| `collector/coinbase.go` | Coinbase 数据采集 (市场交易) |
| `models/models.go` | 数据模型定义 |

**API 接口:**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/traders` | GET | 获取所有交易所的Top交易员 |
| `/api/trades` | GET | 获取所有交易记录 |
| `/api/trades?exchange=binance` | GET | 按交易所过滤交易 |
| `/api/price-levels` | GET | 获取价格层级分析 |
| `/api/market` | GET | 获取当前市场数据 |
| `/api/klines?interval=1h` | GET | 获取K线数据 |
| `/api/status` | GET | 获取系统状态 |
| `/api/all-data` | GET | 获取全部数据 |

### 2. Python 分析引擎 (`python-analyzer/`)

| 文件 | 功能 |
|------|------|
| `main.py` | 主入口，系统调度 |
| `config.py` | 配置管理 |
| `data_collector.py` | Python端数据采集 (ccxt库) |
| `technical_analysis.py` | 20+ 种技术分析指标 |
| `ml_predictor.py` | ML预测: XGBoost + LightGBM + LSTM |
| `visualization.py` | Dash 可视化仪表板 |
| `alerts.py` | 交易信号提醒系统 |

### 3. 技术分析指标清单

| 类别 | 指标 | 说明 |
|------|------|------|
| **趋势** | SMA (7,25,99,200) | 简单移动平均线 |
| | EMA (12,26,50,200) | 指数移动平均线 |
| | MACD | 移动平均收敛/发散 |
| | ADX | 平均趋向指数 |
| | Ichimoku | 一目均衡表 |
| | Parabolic SAR | 抛物线止损转向 |
| **动量** | RSI (14) | 相对强弱指数 |
| | Stochastic | 随机振荡器 |
| | Williams %R | 威廉指标 |
| | CCI | 商品频道指数 |
| | ROC | 变动率 |
| | MFI | 资金流量指数 |
| **波动率** | Bollinger Bands | 布林带 |
| | ATR | 真实波幅 |
| | Keltner Channel | 肯特纳通道 |
| **成交量** | OBV | 能量潮 |
| | VWAP | 成交量加权均价 |
| | Volume Profile | 成交量分布 |

### 4. 机器学习模型

| 模型 | 类型 | 特点 |
|------|------|------|
| XGBoost | 梯度提升树 | 高效、可解释性强、特征重要度 |
| LightGBM | 轻量梯度提升 | 更快训练、更低内存 |
| LSTM | 深度学习 (RNN) | 捕捉时间序列长期依赖 |
| 集成投票 | 集成学习 | 多模型投票、提高稳定性 |

### 5. 自动学习机器人

系统支持自动学习，定期执行以下流程:

1. **数据采集**: 从三个交易所获取最新数据
2. **特征工程**: 自动产生 60+ 特征
3. **模型训练**: 重新训练所有模型
4. **预测更新**: 更新预测结果
5. **信号检查**: 检查并发送交易提醒

自动更新间隔: 每 5 分钟 (可在 config.py 中调整)

---

## 图形可视化说明

### 仪表板包含以下图表

1. **K线图** (主图)
   - 蜡烛图 + 布林带 + 均线
   - 买卖信号标注（三角形标记）
   - 支持 1m/5m/15m/1h/4h/1d 时间框架
   - 具体时间精确显示 (格式: YYYY-MM-DD HH:MM)

2. **MACD 子图**
   - MACD 线 + 信号线 + 柱状图
   - 金叉/死叉标识

3. **RSI 子图**
   - RSI 曲线
   - 超买(70)/超卖(30) 标线

4. **成交量子图**
   - 成交量柱状图（红绿区分涨跌）
   - 20日成交量均线

5. **交易员分析图**
   - 各交易所 Top10 ROI
   - 买卖比例饼图
   - 价格区间买卖统计
   - 收益排名柱状图

6. **AI 预测图**
   - 价格走势 + 预测标注
   - 各模型置信度对比
   - 信号强度历史
   - 预测方向分布

7. **价格层级图**
   - 各价格区间买卖量对比
   - 水平柱状图展示

**所有图表均包含具体时间戳，格式: `YYYY-MM-DD HH:MM:SS`**

---

## 项目文件结构

```
ubuntu-wallet/
├── .env.example              # 环境变量模板
├── .gitignore                # Git忽略规则
├── README.md                 # 本文档
│
├── go-collector/             # Go 数据采集服务
│   ├── go.mod                # Go 模块定义
│   ├── main.go               # 主入口 + HTTP API
│   ├── collector/
│   │   ├── binance.go        # Binance 采集器
│   │   ├── okx.go            # OKX 采集器
│   │   └── coinbase.go       # Coinbase 采集器
│   └── models/
│       └── models.go         # 数据模型
│
├── python-analyzer/          # Python 分析引擎
│   ├── requirements.txt      # Python 依赖
│   ├── config.py             # 配置文件
│   ├── data_collector.py     # 数据采集 (ccxt)
│   ├── technical_analysis.py # 技术分析 (20+指标)
│   ├── ml_predictor.py       # ML预测 (XGBoost/LightGBM/LSTM)
│   ├── visualization.py      # Dash 可视化仪表板
│   ├── alerts.py             # 交易信号提醒
│   └── main.py               # 主入口
│
├── scripts/
│   ├── install.sh            # 一键安装脚本
│   └── run.sh                # 运行管理脚本
│
├── data/                     # 数据目录 (运行时生成)
├── models/                   # 模型目录 (训练后生成)
├── logs/                     # 日志目录
└── bin/                      # 编译输出
    └── go-collector          # Go 可执行文件
```

---

## 常见问题

### Q: 没有 API Key 能运行吗？
A: 可以。系统在无法连接交易所 API 时会自动使用模拟数据运行，你可以先测试完整流程，再补充真实 API Key。

### Q: ta-lib 安装失败怎么办？
A: ta-lib 的 Python 包依赖底层 C 库。确保先安装 C 库：
```bash
# 如果之前的安装步骤失败了，试试：
sudo apt install -y libta-lib-dev
# 或从源码编译 (参考安装步骤 5)
```
如果仍然失败，可以跳过 ta-lib，系统使用 `ta` 和 `pandas-ta` 作为替代方案。

### Q: PyTorch 安装很慢或失败？
A: 使用 CPU 版本:
```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### Q: 仪表板无法访问？
A: 检查:
```bash
# 确认端口是否在监听
ss -tlnp | grep 8050

# 如果是云服务器，确认安全组/防火墙允许 8050 端口
sudo ufw allow 8050
```

### Q: 模型准确率低？
A: 这是正常的。加密货币市场波动大，50-60% 的准确率已经不错。建议:
- 收集更多历史数据 (增大 limit)
- 调整 `config.py` 中的超参数
- 让系统运行更长时间，自动学习会逐步优化

### Q: 如何在后台运行？
A: 使用 `nohup` 或 `tmux`:
```bash
# 方法1: nohup
nohup bash scripts/run.sh full &

# 方法2: tmux (推荐)
sudo apt install tmux
tmux new -s eth-predictor
bash scripts/run.sh full
# 按 Ctrl+B 然后按 D 分离会话
# tmux attach -t eth-predictor  回到会话
```

### Q: 如何升级？
```bash
cd ~/ubuntu-wallet
git pull
source venv/bin/activate
pip install -r python-analyzer/requirements.txt --upgrade
cd go-collector && go mod tidy && go build -o ../bin/go-collector . && cd ..
```

---

## ⚠️ 免责声明

本系统仅供学习和研究使用。加密货币交易存在极高风险，任何预测都不能保证准确。请勿将本系统的任何输出作为唯一决策依据。投资有风险，入市需谨慎。
