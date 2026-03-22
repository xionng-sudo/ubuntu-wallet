# DEX/CEX 套利扫描器 — 中文文档

> 语言：中文 | 项目版本：v1.1（含链上执行）

---

## ⚠️ 运行环境说明

| 模式 | 命令示例 | 网络要求 |
|------|---------|---------|
| 完全离线演示 | `--cex mock --dex mock --demo` | 无需任何网络 |
| 实时报价（仅扫描） | `--cex binance --dex uniswap_v3` | Binance API + Ethereum RPC |
| 链上 DEX 执行 | `--dex uniswap_v3 --execute` | 上述 + 钱包私钥 |

> **注**：`--dex uniswap_v3` 会通过 QuoterV2 合约获取**真实**链上报价，而非模拟数据。执行需要钱包私钥，请确保理解风险后再使用 `--execute`。

---

## 1. 项目简介与目标

本模块是一个 **DEX/CEX 套利扫描与执行器**，运行在 ubuntu-wallet 仓库之中。

### 核心目标

| 目标 | 说明 |
|------|------|
| 价差发现 | 对比中心化交易所（CEX，如 Binance）与去中心化交易所（DEX，Uniswap V3）的买卖盘价格 |
| 成本量化 | 自动计算交易费、Gas 费、滑点，得出**净利润** |
| 风险过滤 | 可配置阈值，过滤掉流动性不足、Gas 过高等高风险机会 |
| 链上执行 | 通过 Uniswap V3 SwapRouter02 执行 DEX 侧交易（含 ERC-20 授权、余额校验、Gas 估算） |
| 链上风控 | Quote TTL 校验、MEV 风险警告、路由元数据验证 |

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
│       └── uniswap_v3.py     ✅ 真实链上报价（QuoterV2）
├── costs/
│   └── calculator.py         ✅ Gas/滑点/手续费计算
├── arbitrage/
│   └── engine.py             ✅ 双向套利机会计算引擎
├── risk/
│   ├── filters.py            ✅ 可配置风险过滤器
│   └── chain_risk.py         ✅ 链上风险评估（TTL/MEV/路由校验）
├── execution/
    ├── wallet.py             ✅ 私钥加载与账户管理
    ├── erc20.py              ✅ ERC-20 授权管理
    ├── swap_executor.py      ✅ Uniswap V3 swap 执行器（DEX 侧）
    ├── cex_executor.py       ✅ Binance 市价单执行器（CEX 侧）
    └── arbitrage_executor.py ✅ DEX/CEX 双边套利编排器

scripts/
└── scan_arbitrage.py         ✅ CLI 扫描+执行入口

tests/
└── test_arbitrage.py         ✅ 79 个单元测试（无需 API Key，无网络调用）

requirements-arbitrage.txt    ✅ 最小依赖清单（含 web3）
```

requirements-arbitrage.txt    ✅ 最小依赖清单（仅套利模块）
```

### 数据源状态一览

| 组件 | 状态 | 说明 |
|------|------|------|
| `BinanceCEXQuote` | ✅ 可用 | 调用 Binance 公开 REST API，无需 API Key |
| `MockCEXQuote` | ✅ 可用 | 内置模拟，无需网络，适合 CI/离线演示 |
| `MockDEXQuote` | ✅ 可用 | 模拟 DEX，无需链上连接 |
| `UniswapV3Quote` | ✅ 可用 | 调用链上 QuoterV2，需 `ETHEREUM_RPC_URL` |
| `UniswapV3SwapExecutor` | ✅ 可用 | 链上 swap 执行，需 `ETHEREUM_RPC_URL` + `WALLET_PRIVATE_KEY` |
| `BinanceCEXExecutor` | ✅ 可用 | Binance 市价单，需 `BINANCE_API_KEY` + `BINANCE_API_SECRET` |
| `ArbitrageExecutor` | ✅ 可用 | 双边编排器，需上述全部配置 |

### 默认费率参数

| 参数 | 数值 | 说明 |
|------|------|------|
| CEX 手续费 | 0.1% | Binance Taker |
| DEX 手续费 | 0.3% | Uniswap V3 标准池 |
| Gas（默认） | 30 Gwei × 150,000 units | 约 $13.5（ETH=$3000） |
| 最低净利润 | $1.0 | 风险过滤默认值 |
| 执行滑点保护 | 0.5% | `amountOutMinimum = expected × 99.5%` |
| Quote TTL | 30 秒 | 超时报价拒绝执行 |
| 执行 deadline | now+60s | 交易 deadline，过期自动回滚 |

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

编辑 `.env`：

```dotenv
# CEX 报价（可选，公开接口无需 Key）
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here

# 链上报价与执行（--dex uniswap_v3 时必填）
ETHEREUM_RPC_URL=https://mainnet.infura.io/v3/YOUR_PROJECT_ID

# 链上执行钱包（--execute 时必填）
# ⚠️ 警告：私钥拥有资金控制权，请勿提交到版本控制
WALLET_PRIVATE_KEY=0xYOUR_64HEX_PRIVATE_KEY
```

> **Ethereum RPC 推荐**：[Infura](https://infura.io) 或 [Alchemy](https://alchemy.com)（均有免费套餐）。

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
| `web3` | ≥6.0.0 | Ethereum 交互（UniswapV3Quote + 链上执行） |

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

### 🌐 真实链上报价扫描（Binance + Uniswap V3）

先确保 `.env` 中设置了 `ETHEREUM_RPC_URL`，然后：

```bash
python scripts/scan_arbitrage.py \
  --cex binance \
  --dex uniswap_v3 \
  --symbols ETH/USDT,BTC/USDT \
  --amount 10000 \
  --show-all
```

> 这会调用链上 QuoterV2 合约，价格反映真实 Uniswap V3 池深度，需要以太坊 RPC 连接。

### ⚡ 链上执行 DEX 侧（需要私钥）

```bash
# 先确认报价（仅扫描）
python scripts/scan_arbitrage.py \
  --cex binance --dex uniswap_v3 \
  --symbols ETH/USDT --amount 5000

# 执行 DEX 侧（⚠️ 真实交易，消耗真实资金）
python scripts/scan_arbitrage.py \
  --cex binance --dex uniswap_v3 \
  --symbols ETH/USDT --amount 5000 \
  --execute \
  --slippage-tolerance 0.5
```

**执行流程（DEX 侧）：**
1. 扫描 PASS 机会
2. 链上风险评估（Quote TTL / MEV / 路由元数据校验）
3. 检查钱包 tokenIn 余额
4. 若 allowance 不足，自动发送 ERC-20 `approve` 交易
5. `eth_estimateGas` 干跑（捕获链上回滚）
6. 签名并广播 `exactInputSingle` 交易
7. 等待 receipt 并输出结果

---

### 🔄 双边闭环执行（DEX + CEX，需要 API Key + 私钥）

这是完整的套利执行路径，同时执行 DEX 侧（链上 swap）和 CEX 侧（Binance 市价单）。

**前置条件**

```dotenv
ETHEREUM_RPC_URL=https://mainnet.infura.io/v3/YOUR_KEY   # 以太坊 RPC
WALLET_PRIVATE_KEY=0xYOUR_64HEX_PRIVATE_KEY               # DEX swap 钱包
BINANCE_API_KEY=your_api_key                              # CEX 下单（Spot 权限）
BINANCE_API_SECRET=your_api_secret
```

**执行命令**

```bash
# ⚠️ 真实交易 + 真实订单 — 请确认已理解风险
python scripts/scan_arbitrage.py \
  --cex binance --dex uniswap_v3 \
  --symbols ETH/USDT --amount 5000 \
  --execute-both \
  --slippage-tolerance 0.5
```

**双边执行顺序**

| 方向 | 第一腿（买入） | 第二腿（卖出） |
|------|------------|------------|
| `BUY_CEX_SELL_DEX` | CEX 买入（Binance 市价买） | DEX 卖出（Uniswap swap） |
| `BUY_DEX_SELL_CEX` | DEX 买入（Uniswap swap） | CEX 卖出（Binance 市价卖） |

**失败处理逻辑**

| 情景 | 处理方式 | 资金暴露 |
|------|---------|---------|
| 第一腿失败 | 立即终止，第二腿不执行 | 无 |
| 第一腿成功、第二腿失败 | 记录 `partial=True`，stderr 告警 | **有**（需手动平仓） |
| 两腿均成功 | 记录 `success=True` | 无（已平仓） |

**结果记录**

每次执行结果自动追加到 `data/arb_results.jsonl`：

```json
{
  "opportunity_symbol": "ETH/USDT",
  "direction": "BUY_CEX_SELL_DEX",
  "buy_leg": {"leg": "CEX", "success": true, "detail": {...}},
  "sell_leg": {"leg": "DEX", "success": true, "detail": {...}},
  "success": true,
  "partial": false,
  "elapsed_seconds": 3.2,
  "timestamp": 1742000000.0,
  "warnings": [],
  "error": null
}
```

> **注**：Binance API Key 需要 **Spot Trading** 权限（只需创建/查询订单，不需提现权限）。建议新建专用 Key 并绑定 IP 白名单。

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
--symbols           交易对，逗号分隔（默认：ETH/USDT,BTC/USDT,BNB/USDT）
--amount            单笔交易额（USD，默认：10000）
--cex               CEX 来源：binance | mock（默认：binance）
--dex               DEX 来源：mock | uniswap_v3（默认：mock）
--output            输出格式：table | json（默认：table）
--min-profit        最低净利润（USD，默认：1.0）
--max-gas           最高 Gas 费（USD，默认：50.0）
--max-slippage      最大滑点 %（默认：1.0）
--min-liquidity     最低流动性（USD，默认：10000）
--show-all          显示所有机会，包括被过滤的（默认：False）
--demo              演示模式：模拟 DEX 价格偏高 ~1.5%，保证有 PASS 结果出现
--execute           仅执行 DEX 侧链上 swap（需要 ETHEREUM_RPC_URL + WALLET_PRIVATE_KEY）
--execute-both      双边闭环执行 DEX + CEX（需要以上 + BINANCE_API_KEY + BINANCE_API_SECRET）
--slippage-tolerance  执行时最大可接受滑点 %（默认：0.5）
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

## 6.5 链上执行支持规格

### 支持的链与路由器

| 项目 | 值 |
|------|------|
| **链** | Ethereum mainnet（chain ID 1） |
| **DEX 路由器** | Uniswap V3 SwapRouter02 (`0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45`) |
| **报价合约** | QuoterV2 (`0x61fFE014bA17989E743c5F6cB21bF9697530B21e`) |
| **Swap 类型** | `exactInputSingle`（单跳，直接 token → token） |
| **多链支持** | BSC / Arbitrum / Polygon → P3 路线图 |

### 支持的代币

| 代币符号 | 合约地址 | 精度 |
|---------|---------|------|
| ETH/WETH | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` | 18 |
| BTC/WBTC | `0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599` | 8 |
| USDT | `0xdAC17F958D2ee523a2206206994597C13D831ec7` | 6 |
| USDC | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | 6 |
| DAI | `0x6B175474E89094C44Da98b954EedeAC495271d0F` | 18 |

### 链上执行安全措施

| 措施 | 说明 |
|------|------|
| Quote TTL 校验 | 报价超过 30s 拒绝执行，防止陈旧价格执行 |
| 余额检查 | 执行前验证钱包 tokenIn 余额是否充足 |
| ERC-20 授权 | 自动发送 `approve(MAX_UINT256)` 补足授权 |
| Gas 估算干跑 | `eth_estimateGas` 捕获链上回滚（流动性不足、路由无效等） |
| 滑点保护 | `amountOutMinimum = expected × (1 - tolerance)`，默认 tolerance=0.5% |
| 交易 deadline | `now + 60s`，超时后交易自动回滚 |
| MEV 风险警告 | 净利润 < 0.3% 时发出 MEV 警告（可通过 Flashbots Protect 缓解） |

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

### Q1b：链上报价失败 `ConnectionError` 或 `ETHEREUM_RPC_URL is not set`

**原因**：`ETHEREUM_RPC_URL` 未配置或 RPC 节点不可达。

**诊断步骤**：
```bash
# 验证 RPC 连通性
curl -s -X POST \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"net_version","params":[],"id":1}' \
  $ETHEREUM_RPC_URL
# 正常返回: {"jsonrpc":"2.0","id":1,"result":"1"}  (mainnet=1)
```

**解决方法**：
- 在 `.env` 中设置 `ETHEREUM_RPC_URL=https://mainnet.infura.io/v3/YOUR_KEY`
- 若无 RPC，注册免费 Infura / Alchemy 账号
- 若只需测试扫描逻辑，使用 `--dex mock`

---

### Q2：出现 `ModuleNotFoundError: No module named 'ccxt'` 或 `'web3'`

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

### Q4：执行时报 `Quote is stale`

**原因**：从 DEX 获取报价到执行之间超过了 30 秒 TTL，防止陈旧价格执行。

**解决**：立即重新运行扫描后再加 `--execute` 执行。

---

### Q5：执行时报 `Gas estimation failed — swap would likely revert`

**原因**：链上 `eth_estimateGas` 回滚，可能原因：流动性不足、滑点过紧、路由无效或报价已过期。

**诊断**：
- 降低 `--amount` 减少价格冲击
- 放宽 `--slippage-tolerance`（如设为 `1.0`）
- 确认 `ETHEREUM_RPC_URL` 为主网节点

---

### Q6：交易对格式错误（`BadSymbol` 异常）

**原因**：交易对格式不正确（CCXT 要求使用斜杠分隔，如 `ETH/USDT`）。

**正确格式**：
```bash
# ✅ 正确
--symbols ETH/USDT,BTC/USDT

# ❌ 错误
--symbols ETHUSDT,BTCUSDT
```

---

### Q7：没有任何 PASS 结果

**原因**：真实市场中，CEX 与 Uniswap V3 之间的实际价差通常小于总手续费（约 0.4% + Gas），属于正常现象。

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

### Q8：运行测试

```bash
cd ubuntu-wallet
python -m unittest tests.test_arbitrage -v
```

63 个测试均无需网络或 API Key。

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

# 3. 安装依赖（含 web3）
pip install -r requirements-arbitrage.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 BINANCE_API_KEY / ETHEREUM_RPC_URL / WALLET_PRIVATE_KEY

# 5. 验证安装
python -m unittest tests.test_arbitrage -v

# 6. 运行扫描器（离线演示）
python scripts/scan_arbitrage.py --cex mock --dex mock --demo --show-all

# 7. 运行链上报价扫描（需要 Binance + Ethereum RPC）
python scripts/scan_arbitrage.py --cex binance --dex uniswap_v3 --show-all

# 8. 执行 DEX 侧（需要 WALLET_PRIVATE_KEY，发送真实交易）
python scripts/scan_arbitrage.py --cex binance --dex uniswap_v3 --execute
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
  --cex binance --dex uniswap_v3 \
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
          --cex binance --dex uniswap_v3 \
          --symbols ETH/USDT,BTC/USDT \
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
| 更新 web3 版本 | 每月 | `pip install -U web3`，注意 API 变更 |
| 轮换 API Key | 每 90 天 | 安全最佳实践，旧 Key 在 `.env` 中替换即可 |
| 轮换 WALLET_PRIVATE_KEY | 按需 | 若怀疑泄露立即替换，确认新钱包余额再停用旧密钥 |
| 运行单元测试 | 每次发布前 | `python -m unittest tests.test_arbitrage -v` |

---

## 10. 后续增强计划（路线图）

### 阶段定义

| 阶段 | 名称 | 状态 |
|------|------|------|
| P0 | 需求确认与架构设计 | ✅ 已完成 |
| P1 | DEX/CEX 扫描器 MVP | ✅ 已完成 |
| P2 | 链上报价 + DEX 执行（本 PR） | ✅ 已完成 |
| P3 | CEX 执行 + DEX/CEX 双边闭环（本 PR） | ✅ 已完成 |
| P4 | 多链支持（BSC / Arbitrum / Polygon） | 🔜 下一阶段 |
| P5 | 高级风控（MEV 保护、Flash Loan） | 📋 规划中 |
| P6 | 生产化（数据库、Dashboard、监控） | 📋 长期 |

---

### P3 — 多链支持（1–2 个月）

- [ ] **支持 BSC** — PancakeSwap V3（`--chain bsc`）
- [ ] **支持 Arbitrum** — Uniswap V3 on Arbitrum（Gas 费更低）
- [ ] **支持 Polygon** — QuickSwap
- [ ] **添加 OKX CEX 数据源** (`app/market/cex/okx.py`)
- [ ] **实时 Gas 价格查询**（Etherscan Gas Oracle API）
- [ ] **多金额档位报价**（1k / 10k / 100k USD 分别计算滑点）

### P4 — CEX 执行闭环（2–3 个月）

- [ ] **Binance 订单执行** — 通过 CCXT `create_order()` 完成 CEX 侧下单
- [ ] **双侧原子套利** — 同时提交 DEX swap + CEX 订单，最小化暴露时间
- [ ] **Telegram / 钉钉告警** — PASS 机会实时推送
- [ ] **历史回测** — 对接 `python-analyzer/backtest_multi_tf.py`

### P5 — 高级风控（3–6 个月）

- [ ] **MEV 保护** — 接入 Flashbots Protect（`eth_sendPrivateTransaction`）
- [ ] **Flash Loan 支持** — 无本金套利（Aave / dYdX）
- [ ] **动态路由** — 自动选择最优费率池（0.05% / 0.30% / 1.00%）
- [ ] **机会寿命预测** — 利用 ML 模型预测套利窗口持续时间

### P6 — 生产化（6 个月+）

- [ ] **数据库持久化** — PostgreSQL 存储历史扫描结果
- [ ] **Web Dashboard** — 基于现有 Dash 框架的可视化
- [ ] **多账户资金管理** — 仓位控制与余额监控
- [ ] **跨链套利** — 桥接费用模型
