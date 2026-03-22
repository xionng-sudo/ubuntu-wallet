# DEX/CEX 套利扫描器 — 技术文档

> 语言：中文 | 项目版本：MVP v1.0

---

## 1. 项目简介与目标

本模块是一个 **DEX/CEX 套利 MVP 扫描器**，运行在 ubuntu-wallet 仓库之中。

### 核心目标

| 目标 | 说明 |
|------|------|
| 价差发现 | 实时对比中心化交易所（CEX，如 Binance）与去中心化交易所（DEX，如 Uniswap V3）的买卖盘价格 |
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
│   │   └── binance.py        ✅ 通过 CCXT 获取 Binance 订单簿报价
│   └── dex/
│       ├── base.py           ✅ Quote 数据类 + BaseDEXQuote 抽象基类
│       ├── mock_dex.py       ✅ 模拟 DEX（含价差和随机噪声）
│       └── uniswap_v3.py     🚧 接口占位符（待实现链上调用）
├── costs/
│   └── calculator.py         ✅ Gas/滑点/手续费计算
├── arbitrage/
│   └── engine.py             ✅ 双向套利机会计算引擎
└── risk/
    └── filters.py            ✅ 可配置风险过滤器

scripts/
└── scan_arbitrage.py         ✅ CLI 扫描入口

tests/
└── test_arbitrage.py         ✅ 单元测试（无需 API Key）
```

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

- Ubuntu 22.04 / macOS 13+
- Python 3.10 或以上
- pip 23+

### 配置 .env 文件

```bash
cd /home/runner/work/ubuntu-wallet/ubuntu-wallet
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

```bash
# 进入仓库根目录
cd /home/runner/work/ubuntu-wallet/ubuntu-wallet

# 安装依赖（ccxt, python-dotenv 等已在 requirements.txt 中）
pip install -r python-analyzer/requirements.txt
```

若只需最小依赖：

```bash
pip install ccxt==4.2.70 python-dotenv requests aiohttp
```

---

## 5. 运行命令

### 快速扫描（使用模拟 DEX）

```bash
python scripts/scan_arbitrage.py
```

### 指定交易对与金额

```bash
python scripts/scan_arbitrage.py \
  --symbols ETH/USDT,BTC/USDT,BNB/USDT \
  --amount 10000
```

### 显示所有结果（含被过滤项）

```bash
python scripts/scan_arbitrage.py --show-all
```

### JSON 格式输出

```bash
python scripts/scan_arbitrage.py --output json | python -m json.tool
```

### 自定义风险阈值

```bash
python scripts/scan_arbitrage.py \
  --min-profit 5.0 \
  --max-gas 20.0 \
  --max-slippage 0.5 \
  --min-liquidity 50000
```

### 完整参数列表

```
--symbols       交易对，逗号分隔（默认：ETH/USDT,BTC/USDT,BNB/USDT）
--amount        单笔交易额（USD，默认：10000）
--dex           DEX 来源：mock | uniswap_v3（默认：mock）
--output        输出格式：table | json（默认：table）
--min-profit    最低净利润（USD，默认：1.0）
--max-gas       最高 Gas 费（USD，默认：50.0）
--max-slippage  最大滑点 %（默认：1.0）
--min-liquidity 最低流动性（USD，默认：10000）
--show-all      显示所有机会，包括被过滤的（默认：False）
```

---

## 6. 如何查看结果

### 表格输出示例

```
Symbol      Direction             CEX px        DEX px        Gross%    Net $     Net%     Status
----------  --------------------  ------------  ------------  --------  ---------  -------  ------------------------
ETH/USDT    BUY_CEX_SELL_DEX      3001.0000     3025.5000     +0.816%   $+27.56    +0.276%  PASS
ETH/USDT    BUY_DEX_SELL_CEX      3028.0000     3000.0000     -0.924%   $-132.40   -1.324%  BLOCKED_LOW_PROFIT
BTC/USDT    BUY_CEX_SELL_DEX      65010.0000    65350.0000    +0.523%   $+1.85     +0.019%  PASS
```

### 字段说明

| 字段 | 说明 |
|------|------|
| Symbol | 交易对 |
| Direction | 套利方向 |
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
| `BLOCKED_LOW_LIQUIDITY` | DEX 流动性不足 |

---

## 7. 常见问题排查

### Q1：出现 `ccxt.NetworkError` 连接失败

**原因**：网络不通或 Binance API 被封锁。

**解决**：
```bash
# 检查网络
curl -s https://api.binance.com/api/v3/ping
# 若返回 {} 则网络正常
```

若在中国大陆，需使用代理或切换至 Binance 境外接口。

### Q2：没有任何 PASS 结果

**原因**：模拟 DEX 的价差不总是大于手续费+Gas，属于正常现象。

**解决**：
```bash
# 查看所有结果，包括被过滤的
python scripts/scan_arbitrage.py --show-all

# 降低最低利润门槛
python scripts/scan_arbitrage.py --min-profit 0.01 --show-all
```

### Q3：`ModuleNotFoundError: No module named 'app'`

**原因**：未从仓库根目录运行。

**解决**：
```bash
cd /home/runner/work/ubuntu-wallet/ubuntu-wallet
python scripts/scan_arbitrage.py
```

### Q4：`uniswap_v3` 报 NotImplementedError

**原因**：Uniswap V3 链上接口尚未实现，当前为占位符。

**解决**：使用 `--dex mock` 进行测试。

### Q5：运行测试报错

```bash
cd /home/runner/work/ubuntu-wallet/ubuntu-wallet
python -m pytest tests/test_arbitrage.py -v
# 或
python -m unittest tests/test_arbitrage -v
```

---

## 8. 部署步骤

### 开发环境部署

```bash
# 1. 克隆仓库
git clone <repo_url>
cd ubuntu-wallet

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r python-analyzer/requirements.txt

# 4. 配置 API Key
cp .env.example .env
vi .env  # 填入 BINANCE_API_KEY / BINANCE_API_SECRET

# 5. 运行扫描器
python scripts/scan_arbitrage.py --symbols ETH/USDT,BTC/USDT --amount 5000
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
| 更新 `ETH_PRICE_USD` | 每周 | 影响 Gas 费 USD 估算精度 |
| 检查 Binance 手续费 | 每季度 | VIP 等级会降低费率 |
| 监控 Gas 基础费 | 每日 | 链上拥堵时 Gas 费飙升 |
| 更新 CCXT 版本 | 每月 | `pip install -U ccxt` |
| 轮换 API Key | 每 90 天 | 安全最佳实践 |

---

## 10. 后续增强计划（路线图）

### P0 — 核心功能完善（立即）

- [ ] **实现 Uniswap V3 链上报价** (`app/market/dex/uniswap_v3.py`)
  - 集成 `web3.py` + Quoter 合约
  - 支持 0.05% / 0.30% / 1.00% 手续费池
- [ ] **添加 OKX CEX 数据源** (`app/market/cex/okx.py`)
- [ ] **实时 Gas 价格查询** (Etherscan Gas Oracle API)

### P1 — 多链支持（1–2 个月）

- [ ] **支持 BSC** — PancakeSwap V3
- [ ] **支持 Arbitrum** — Uniswap V3 on Arbitrum（Gas 费更低）
- [ ] **支持 Polygon** — QuickSwap

### P2 — 执行层（2–3 个月）

- [ ] **模拟交易执行器** — 在 fork 网络上验证套利路径
- [ ] **Telegram / 钉钉告警** — PASS 机会实时推送
- [ ] **历史回测** — 对接 `python-analyzer/backtest_multi_tf.py`

### P3 — 智能优化（3–6 个月）

- [ ] **动态路由** — 自动选择最优费率池
- [ ] **Flash Loan 支持** — 无本金套利（Aave / dYdX）
- [ ] **MEV 保护** — 接入 Flashbots / private mempool

### P4 — 生产化（6 个月+）

- [ ] **数据库持久化** — PostgreSQL 存储历史扫描结果
- [ ] **Web Dashboard** — 基于现有 Dash 框架的可视化
- [ ] **多账户资金管理** — 仓位控制与余额监控

### P5 — 研究方向（长期）

- [ ] **跨链套利** — 桥接费用模型
- [ ] **NFT 套利** — OpenSea vs. Blur
- [ ] **永续合约资金费率套利** — 结合 `go-collector/` 的资金费率数据
