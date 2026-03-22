# DEX/CEX 套利扫描器 — 中文文档

> 语言：中文 | 项目版本：MVP v1.0

---

## ⚠️ 当前版本重要说明

> **MVP v1.0 可运行路径基于模拟 DEX 数据。**
>
> - `--cex binance`（默认）：使用 Binance 真实订单簿报价（需要公网访问 `api.binance.com`）
> - `--dex mock`（默认）：使用内置模拟 DEX，**无需任何链上访问或 API Key**
> - `--dex uniswap_v3`：**尚未实现**，调用时会抛出 `NotImplementedError`
>
> **真实 Uniswap V3 链上路由集成计划在下一阶段（P0 路线图）完成。**
> 若需在无公网环境下完整体验扫描流程，请使用 `--cex mock --dex mock --demo`（见第 5 节）。

---

## 1. 项目简介与目标

本模块是一个 **DEX/CEX 套利 MVP 扫描器**，运行在 ubuntu-wallet 仓库之中。

### 核心目标

| 目标 | 说明 |
|------|------|
| 价差发现 | 对比中心化交易所（CEX，如 Binance）与去中心化交易所（DEX，如 Uniswap V3）的买卖盘价格 |
| 成本量化 | 自动计算交易费、Gas 费、滑点，得出**净利润** |
| 风险过滤 | 可配置阈值，过滤掉流动性不足、Gas 过高等高风险机会 |
| 可扩展性 | 通过抽象基类 `BaseDEXQuote` 轻松接入新 DEX |

### 套利原理

```
方向 A (BUY_CEX_SELL_DEX)：
  在 Binance 以 ask 价买入 → 在 DEX 以 bid 价卖出
  毛利润 = (DEX_bid - CEX_ask) / CEX_ask × 交易额

方向 B (BUY_DEX_SELL_CEX)：
  在 DEX 以 ask 价买入 → 在 Binance 以 bid 价卖出
  毛利润 = (CEX_bid - DEX_ask) / DEX_ask × 交易额

净利润 = 毛利润 − CEX 手续费 − DEX 手续费 − Gas 费 − 滑点损失
```

---

## 2. 当前已完成进度

### 已实现模块

```
app/
├── __init__.py
├── market/
│   ├── cex/
│   │   ├── binance.py        ✅ 通过 CCXT 获取 Binance 订单簿报价（真实 CEX）
│   │   └── mock_cex.py       ✅ 离线模拟 CEX（用于无网络环境测试）
│   └── dex/
│       ├── base.py           ✅ Quote 数据类 + BaseDEXQuote 抽象基类
│       ├── mock_dex.py       ✅ 模拟 DEX（含可配置价差和随机噪声）
│       └── uniswap_v3.py     🚧 接口占位符（待实现链上调用，下一阶段）
├── costs/
│   └── calculator.py         ✅ Gas/滑点/手续费计算
├── arbitrage/
│   └── engine.py             ✅ 双向套利机会计算引擎
└── risk/
    └── filters.py            ✅ 可配置风险过滤器

scripts/
└── scan_arbitrage.py         ✅ CLI 扫描入口

tests/
└── test_arbitrage.py         ✅ 26 个单元测试（无需 API Key，无网络调用）

requirements-arbitrage.txt    ✅ 最小依赖清单（仅套利模块）
```

### 数据源状态一览

| 组件 | 状态 | 说明 |
|------|------|------|
| `BinanceCEXQuote` | ✅ 可用 | 调用 Binance 公开 REST API，无需 API Key |
| `MockCEXQuote` | ✅ 可用 | 内置模拟，无需网络，适合 CI/离线演示 |
| `MockDEXQuote` | ✅ 可用 | 模拟 DEX，无需链上连接 |
| `UniswapV3Quote` | 🚧 占位 | 尚未实现，调用时抛出 `NotImplementedError` |

### 默认费率参数

| 参数 | 数值 | 说明 |
|------|------|------|
| CEX 手续费 | 0.1% | Binance Taker |
| DEX 手续费 | 0.3% | Uniswap V3 标准池 |
| Gas（默认） | 30 Gwei × 150,000 units | 约 $13.5（ETH=$3000） |
| 最低净利润 | $1.0 | 风险过滤默认值 |

---

## 3. 本地环境准备

### 系统要求

- Ubuntu 22.04 / macOS 13+（Windows 通过 WSL2 支持）
- Python 3.10 或以上
- pip 23+

### 配置 .env 文件

```bash
cd ubuntu-wallet     # 进入仓库根目录
cp .env.example .env
```

编辑 `.env`，填入 Binance API Key（**可选**，公开接口无需 Key）：

```dotenv
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
```

> **注意**：若不填写 API Key，扫描器将自动使用 Binance 公开 REST 接口，仅支持行情数据查询，不能下单。

---

## 4. 安装命令

### 方案 A：最小依赖（仅套利模块）

```bash
# 进入仓库根目录
cd ubuntu-wallet

# 安装套利模块所需的最小依赖
pip install -r requirements-arbitrage.txt
```

`requirements-arbitrage.txt` 包含：

| 包 | 版本 | 用途 |
|----|------|------|
| `ccxt` | 4.2.70 | 统一加密货币交易所 API（CEX 报价） |
| `python-dotenv` | 1.0.1 | 读取 `.env` 中的 API Key |
| `requests` | 2.31.0 | HTTP 客户端（CCXT 底层依赖） |

### 方案 B：完整 ML/交易栈

```bash
pip install -r python-analyzer/requirements.txt
```

包含上述所有依赖以及 ML 模型训练、回测、Dashboard 所需的全部包。

---

## 5. 运行命令

### 🚀 快速开始（离线演示，无需任何 API Key 或网络）

使用模拟 CEX + 模拟 DEX，强制生成包含 PASS 结果的演示数据：

```bash
python scripts/scan_arbitrage.py --cex mock --dex mock --demo --show-all
```

**预期输出：**

```
NOTE: using mock CEX (offline demo mode)
Fetching mock DEX quotes …
Symbol      Direction             CEX px        DEX px        Gross%    Net $      Net%     Status
----------  --------------------  ------------  ------------  --------  ---------  -------  ------------------------
ETH/USDT    BUY_CEX_SELL_DEX      3,000.7500    3,045.1117    +1.478%   $+84.34    +0.843%  PASS
ETH/USDT    BUY_DEX_SELL_CEX      2,999.2500    3,048.2346    -1.607%   $-224.20   -2.242%  BLOCKED_LOW_PROFIT
BTC/USDT    BUY_CEX_SELL_DEX      65,016.2500   65,876.1927   +1.323%   $+72.52    +0.725%  PASS
BTC/USDT    BUY_DEX_SELL_CEX      64,983.7500   65,956.8225   -1.475%   $-207.28   -2.073%  BLOCKED_LOW_PROFIT
BNB/USDT    BUY_CEX_SELL_DEX      560.1400      568.4527      +1.484%   $+61.57    +0.616%  PASS
BNB/USDT    BUY_DEX_SELL_CEX      559.8600      569.4067      -1.677%   $-254.49   -2.545%  BLOCKED_LOW_PROFIT

Scanned 3 symbol(s) · 6 result(s) · 3 passing filters
```

### 🌐 实时扫描（真实 Binance 数据 + 模拟 DEX）

```bash
python scripts/scan_arbitrage.py --dex mock --show-all
```

> **说明**：此命令默认使用 `--cex binance`（真实 Binance 订单簿），需要能访问 `api.binance.com`。若网络不可用，见[第 7 节排查指南](#7-常见问题排查)。

**预期输出（真实行情，结果因价差随机而异）：**

```
Fetching Binance quotes for: ETH/USDT, BTC/USDT, BNB/USDT …
Fetching mock DEX quotes …
Symbol      Direction             CEX px        DEX px        Gross%    Net $      Net%     Status
----------  --------------------  ------------  ------------  --------  ---------  -------  ------------------------
ETH/USDT    BUY_CEX_SELL_DEX      3,xxx.xxxx    3,xxx.xxxx    +x.xxx%   $+xx.xx    +x.xxx%  BLOCKED_LOW_PROFIT
...

Scanned 3 symbol(s) · 6 result(s) · 0 passing filters
```

> **正常现象**：真实市场中 CEX 与模拟 DEX 的价差通常小于总费用（CEX 0.1% + DEX 0.3% + Gas ~$13.5），因此大部分结果被过滤。实际套利机会需要真实 DEX 路由数据（下一阶段 P0 实现）。

### 指定交易对与金额

```bash
python scripts/scan_arbitrage.py \
  --cex mock --dex mock \
  --symbols ETH/USDT,BTC/USDT,BNB/USDT \
  --amount 10000
```

### 显示所有结果（含被过滤项）

```bash
python scripts/scan_arbitrage.py --cex mock --dex mock --show-all
```

### JSON 格式输出

```bash
python scripts/scan_arbitrage.py --cex mock --dex mock --demo --output json
```

**预期 JSON 输出（截取第一条）：**

```json
[
  {
    "symbol": "ETH/USDT",
    "direction": "BUY_CEX_SELL_DEX",
    "cex_exchange": "mock_cex",
    "dex_exchange": "mock_dex",
    "cex_price": 3000.75,
    "dex_price": 3045.11,
    "trade_amount_usd": 10000.0,
    "gross_profit_usd": 147.84,
    "gross_profit_pct": 1.478,
    "cex_fee_usd": 10.0,
    "dex_fee_usd": 30.0,
    "gas_cost_usd": 13.5,
    "slippage_usd": 10.0,
    "total_cost_usd": 63.5,
    "net_profit_usd": 84.34,
    "net_profit_pct": 0.843,
    "liquidity_usd": 5000000.0,
    "status": "PASS",
    "status_reason": ""
  }
]
```

### 自定义风险阈值

```bash
python scripts/scan_arbitrage.py \
  --cex mock --dex mock \
  --min-profit 5.0 \
  --max-gas 20.0 \
  --max-slippage 0.5 \
  --min-liquidity 50000
```

### 完整参数列表

```
--symbols       交易对，逗号分隔（默认：ETH/USDT,BTC/USDT,BNB/USDT）
--amount        单笔交易额（USD，默认：10000）
--cex           CEX 来源：binance | mock（默认：binance）
--dex           DEX 来源：mock | uniswap_v3（默认：mock）
--output        输出格式：table | json（默认：table）
--min-profit    最低净利润（USD，默认：1.0）
--max-gas       最高 Gas 费（USD，默认：50.0）
--max-slippage  最大滑点 %（默认：1.0）
--min-liquidity 最低流动性（USD，默认：10000）
--show-all      显示所有机会，包括被过滤的（默认：False）
--demo          使用演示模式：模拟 DEX 价格偏高 ~1.5%，保证有 PASS 结果出现
```

---

## 6. 如何查看结果

### 表格字段说明

| 字段 | 说明 |
|------|------|
| Symbol | 交易对 |
| Direction | 套利方向（BUY_CEX_SELL_DEX 或 BUY_DEX_SELL_CEX） |
| CEX px | 在 CEX 使用的成交价（买入用 ask，卖出用 bid） |
| DEX px | 在 DEX 使用的成交价 |
| Gross% | 未扣除费用的毛收益率 |
| Net $ | 扣除所有成本后的净利润（USD） |
| Net% | 净收益率（占交易额） |
| Status | PASS = 通过所有风险过滤；BLOCKED_* = 被拦截及原因 |

### Status 状态码

| 状态 | 含义 |
|------|------|
| `PASS` | 通过所有风险检查 |
| `BLOCKED_LOW_PROFIT` | 净利润或毛利率低于阈值 |
| `BLOCKED_HIGH_GAS` | Gas 费超过上限 |
| `BLOCKED_HIGH_SLIPPAGE` | 滑点超过上限 |
| `BLOCKED_LOW_LIQUIDITY` | DEX 流动性不足（优先于利润过滤判断） |

### 费用明细（JSON 输出中可见）

| 字段 | 说明 |
|------|------|
| `cex_fee_usd` | CEX 手续费（交易额 × 0.1%） |
| `dex_fee_usd` | DEX 手续费（交易额 × 0.3%） |
| `gas_cost_usd` | Gas 费（默认 30 Gwei × 150k units × ETH 价格） |
| `slippage_usd` | 滑点损失（√ 价格冲击公式，交易额/流动性 × 0.5，上限 5%） |
| `total_cost_usd` | 上述四项之和 |

---

## 7. 常见问题排查

### Q1：实时扫描报 `ccxt.NetworkError` 或 `BinanceCEX network error`

**原因**：无法访问 `api.binance.com`（代理环境、防火墙、中国大陆网络限制）。

**诊断步骤**：
```bash
# 步骤 1：测试网络连通性
curl -s --max-time 5 https://api.binance.com/api/v3/ping
# 正常返回：{}
# 超时/失败：网络不可达

# 步骤 2：检查 DNS 解析
nslookup api.binance.com

# 步骤 3：尝试通过代理访问（如已配置）
HTTPS_PROXY=http://your-proxy:port \
  python scripts/scan_arbitrage.py --dex mock --show-all
```

**解决方法**：
- 若网络不可达，使用离线模式：`python scripts/scan_arbitrage.py --cex mock --dex mock --demo`
- 若在中国大陆，可考虑使用 Binance 国际代理端点或 VPN
- 若需要在 CI/CD 中运行，始终使用 `--cex mock`

---

### Q2：出现 `ModuleNotFoundError: No module named 'ccxt'`

**原因**：未安装依赖。

**解决**：
```bash
pip install -r requirements-arbitrage.txt
# 或完整栈：
pip install -r python-analyzer/requirements.txt
```

---

### Q3：出现 `ModuleNotFoundError: No module named 'app'`

**原因**：未从仓库根目录运行。

**解决**：
```bash
cd ubuntu-wallet    # 必须在仓库根目录
python scripts/scan_arbitrage.py --cex mock --dex mock --demo
```

---

### Q4：`--dex uniswap_v3` 报 `NotImplementedError`

**原因**：Uniswap V3 链上接口在本版本（MVP v1.0）中尚未实现，为占位符。

**现状**：`app/market/dex/uniswap_v3.py` 中已定义正确接口和集成指引（需要 `web3.py` + Quoter 合约 + Ethereum RPC）。

**解决**：使用 `--dex mock` 进行端到端测试。

---

### Q5：交易对格式错误（`BadSymbol` 异常）

**原因**：交易对格式不正确（CCXT 要求使用斜杠分隔，如 `ETH/USDT`）。

**正确格式**：
```bash
# ✅ 正确
--symbols ETH/USDT,BTC/USDT

# ❌ 错误
--symbols ETHUSDT,BTCUSDT
```

---

### Q6：没有任何 PASS 结果

**原因**：真实市场中，CEX 与随机模拟 DEX 之间的价差通常小于总手续费（约 0.4% + Gas），属于正常现象。

**验证步骤**：
```bash
# 查看所有结果（含被过滤项）和拦截原因
python scripts/scan_arbitrage.py --cex mock --dex mock --show-all

# 使用演示模式强制生成 PASS 结果
python scripts/scan_arbitrage.py --cex mock --dex mock --demo

# 降低最低利润门槛（用于测试过滤逻辑）
python scripts/scan_arbitrage.py --cex mock --dex mock --min-profit -999 --show-all
```

---

### Q7：运行测试

```bash
cd ubuntu-wallet
python -m unittest tests/test_arbitrage -v
```

26 个测试均无需网络或 API Key。

---

## 8. 部署步骤

### 本地开发环境（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/xionng-sudo/ubuntu-wallet.git
cd ubuntu-wallet

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements-arbitrage.txt

# 4. 配置 API Key（可选，公开接口无需 Key）
cp .env.example .env
# 编辑 .env，填入 BINANCE_API_KEY / BINANCE_API_SECRET

# 5. 验证安装
python -m unittest tests/test_arbitrage -v

# 6. 运行扫描器（离线演示）
python scripts/scan_arbitrage.py --cex mock --dex mock --demo --show-all

# 7. 运行实时扫描（需要 Binance 网络访问）
python scripts/scan_arbitrage.py --dex mock --show-all
```

### Docker 部署（基于现有 Dockerfile 若存在）

若仓库有 `Dockerfile`，可在容器中运行：

```bash
docker build -t ubuntu-wallet .
docker run --env-file .env ubuntu-wallet \
  python scripts/scan_arbitrage.py --cex mock --dex mock --demo
```

### 定时任务部署（cron）

```bash
# 每 5 分钟扫描一次，结果追加到日志
*/5 * * * * cd /opt/ubuntu-wallet && \
  .venv/bin/python scripts/scan_arbitrage.py \
  --output json >> /var/log/arb_scan.jsonl 2>&1
```

### systemd 服务（持续扫描）

参考 `systemd/` 目录下现有模板，新增 `arb-scanner.service`：

```ini
[Unit]
Description=DEX/CEX Arbitrage Scanner
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/ubuntu-wallet
EnvironmentFile=/opt/ubuntu-wallet/.env
ExecStart=/opt/ubuntu-wallet/.venv/bin/python scripts/scan_arbitrage.py \
          --symbols ETH/USDT,BTC/USDT,BNB/USDT \
          --amount 10000 \
          --output json
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

---

## 9. 维护建议

| 维护项 | 频率 | 说明 |
|--------|------|------|
| 更新 `ETH_PRICE_USD` | 每周 | 影响 Gas 费 USD 估算精度（`app/costs/calculator.py` 第 16 行） |
| 检查 Binance 手续费 | 每季度 | VIP 等级会降低费率（`CEX_FEE_RATE`） |
| 监控 Gas 基础费 | 每日 | 链上拥堵时 Gas 费飙升，可通过 `--max-gas` 参数控制 |
| 更新 CCXT 版本 | 每月 | `pip install -U ccxt`，注意 API 兼容性变更 |
| 轮换 API Key | 每 90 天 | 安全最佳实践，旧 Key 在 `.env` 中替换即可 |
| 运行单元测试 | 每次发布前 | `python -m unittest tests/test_arbitrage -v` |

---

## 10. 后续增强计划（路线图）

### 阶段定义

| 阶段 | 名称 | 状态 |
|------|------|------|
| P0 | 需求确认与架构设计 | ✅ 已完成 |
| P1 | DEX/CEX 扫描器 MVP（本 PR） | ✅ 已完成 |
| P2 | 真实 DEX 路由与多金额档位报价 | 🔜 下一阶段 |
| P3 | 模拟盘 | 📋 规划中 |
| P4 | 半自动执行 | 📋 规划中 |
| P5 | 强风控、MEV 评估、机会寿命预测 | 📋 长期 |

---

### P2 — 真实 DEX 路由（下一阶段优先）

- [ ] **实现 Uniswap V3 链上报价** (`app/market/dex/uniswap_v3.py`)
  - 集成 `web3.py` + Quoter 合约（`0xb273...5AB6`，Ethereum mainnet）
  - 支持 0.05% / 0.30% / 1.00% 手续费池
  - 在 `.env` 中配置 `ETHEREUM_RPC_URL`（Infura / Alchemy）
- [ ] **添加 OKX CEX 数据源** (`app/market/cex/okx.py`)
- [ ] **实时 Gas 价格查询**（Etherscan Gas Oracle API）
- [ ] **多金额档位报价**（1k / 10k / 100k USD 分别计算滑点）

### P3 — 多链支持（1–2 个月）

- [ ] **支持 BSC** — PancakeSwap V3
- [ ] **支持 Arbitrum** — Uniswap V3 on Arbitrum（Gas 费更低）
- [ ] **支持 Polygon** — QuickSwap

### P4 — 执行层（2–3 个月）

- [ ] **模拟交易执行器** — 在 fork 网络上验证套利路径
- [ ] **Telegram / 钉钉告警** — PASS 机会实时推送
- [ ] **历史回测** — 对接 `python-analyzer/backtest_multi_tf.py`

### P5 — 智能优化（3–6 个月）

- [ ] **动态路由** — 自动选择最优费率池
- [ ] **Flash Loan 支持** — 无本金套利（Aave / dYdX）
- [ ] **MEV 保护** — 接入 Flashbots / private mempool
- [ ] **机会寿命预测** — 利用 ML 模型预测套利窗口持续时间

### P6 — 生产化（6 个月+）

- [ ] **数据库持久化** — PostgreSQL 存储历史扫描结果
- [ ] **Web Dashboard** — 基于现有 Dash 框架的可视化
- [ ] **多账户资金管理** — 仓位控制与余额监控
- [ ] **跨链套利** — 桥接费用模型
