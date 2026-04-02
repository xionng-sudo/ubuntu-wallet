# Binance USDT-M 永续交易脚本（DRY-RUN ↔ LIVE）使用与维护手册

> 适用脚本：`scripts/live_trader_perp_binance.py`
> 兼容入口：`scripts/live_trader_eth_perp_binance.py`（已弃用 wrapper，仅兼容旧命令）
> REST 客户端模块：`scripts/binance_futures_rest.py`（PR-2A 新增）

---

## 0. 重要安全声明（必须阅读）

1. **默认模式为 DRY-RUN（安全）**：不会向 Binance 发送任何真实下单请求。
2. 当你使用 `--mode live` 时，会触发：
   - **API Key 存在性检查**（缺失则立即退出）
   - **中文强确认**（必须输入 `xionghan` 才继续）
   - 输入 `no` 立即退出
   - **15 秒倒计时**
   - **启动自检**（server time、exchangeInfo、symbol 可交易状态）
3. **PR-2A 已接入真实 Binance Futures REST 下单**：通过确认后，LIVE 模式会向 Binance 发出真实 MARKET 订单，**产生真实盈亏**。
4. 强烈建议你：
   - 永远不要在不确定时启用 LIVE
   - 在 TESTNET 完整验证后再切到 PROD
   - 不要给 API Key 开启"提现权限"
   - 发现 API Key 泄露立即撤销并重建
   - 先 DRY-RUN 至少跑 24–48 小时观察日志

---

## 1. DRY-RUN 是什么意思？

DRY-RUN = 演练模式：

- 策略逻辑照常运行：拉取信号、过滤、多周期 gate、风控计算、仓位状态维护（本地）
- 但**所有"下单/平仓动作"只打印日志**，不会影响真实账户

常见用途：
- 验证信号频率是否正确
- 验证过滤规则是否符合预期
- 验证"在某一段行情下，会不会频繁反复开仓"
- 与回测/模拟语义对齐（逐条对账）

---

## 2. LIVE（实盘）是什么意思？（PR-2A 更新）

LIVE = 实盘模式（会下真实单的模式）。

从 **PR-2A** 起，LIVE 模式已完整接入 Binance USDT-M Futures REST API：

- 每根 1h bar 收盘后，策略信号触发时：
  - 拉取 Binance markPrice 作为当前价格
  - 按仓位比例计算开仓 notional（USDT）
  - 通过 `exchangeInfo` 自动对齐下单精度（stepSize / tickSize / minQty）
  - 发出真实 **MARKET** 订单（开仓：BUY/SELL；平仓：reduce-only SELL/BUY）
- 所有真实订单会打印 `[LIVE OPEN]` / `[LIVE CLOSE]` 日志并包含 `orderId`

---

## 3. Binance PROD vs TESTNET 的区别

- **PROD（正式环境）**
  - endpoint: `https://fapi.binance.com`
  - 真实资金
  - 真实成交/滑点/手续费/延迟
  - 真实盈亏

- **TESTNET（测试网）**
  - endpoint: `https://testnet.binancefuture.com`
  - 测试资产（通过 Binance testnet 水龙头获取）
  - API Key 需在 Binance Testnet 单独申请（与正式 Key 不通用）
  - 用于验证下单接口是否正确、精度/最小下单量是否正确、撤单/查仓是否正常
  - 行情和流动性不一定等同真实环境（不适合用来评估最终收益）

> **强烈建议**：在切到 `--env prod` 之前，先在 `--env testnet` 把完整 LIVE 流程跑通并验证。

本脚本支持 `--env prod|testnet` 参数，默认：`--env prod`。

---

## 4. 所需 API 权限

在 Binance 创建 API Key 时，LIVE 模式至少需要：

| 权限 | 是否需要 | 说明 |
|------|---------|------|
| 读取（Read）| 必须 | 查账户、持仓、订单状态 |
| 合约交易（Futures Trading）| 必须 | 下单/撤单 |
| 提现（Withdrawal）| **绝对不要开** | 风险极大，与交易无关 |

---

## 5. 安装与环境准备

### 5.1 Python/虚拟环境（示例）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests python-dotenv
```

### 5.2 `.env` 配置（API Key）

项目根目录创建 `.env`，示例：

```
BINANCE_API_KEY=xxxxx
BINANCE_API_SECRET=yyyyy
DATA_DIR=./data
ML_SERVICE_URL=http://127.0.0.1:9000/predict
```

注意：
- DRY-RUN 即使没有 key 也能跑，但会警告。
- **LIVE 模式强依赖 key，缺失则立即退出。**

---

## 6. 数据文件要求（K线）

脚本默认从 `DATA_DIR` 读取：
- `klines_4h.json`
- `klines_1d.json`
- （可选）`klines_15m.json`：仅在你启用 `--use-15m-confirm` 时需要

默认 `DATA_DIR=./data`，可通过环境变量 `DATA_DIR` 或参数 `--data-dir` 指定。

---

## 7. 币种选择（单选 / 多选 / 全选）

### 7.1 单选：`--symbol`

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT
```

### 7.2 多选：`--symbols`（逗号分隔）

```bash
python scripts/live_trader_perp_binance.py --symbols ETHUSDT,BTCUSDT,SOLUSDT
```

### 7.3 全选：`--all-symbols`

全选会从 `configs/symbols.yaml` 解析 symbol 列表。

```bash
python scripts/live_trader_perp_binance.py --all-symbols
```

#### `configs/symbols.yaml` 格式建议

写法 A（推荐）：

```yaml
- symbol: ETHUSDT
- symbol: BTCUSDT
- symbol: SOLUSDT
```

写法 B：

```yaml
- ETHUSDT
- BTCUSDT
```

---

## 8. DRY-RUN 模式运行命令（默认）

> 不写 `--mode` 也默认是 DRY-RUN。

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT
python scripts/live_trader_perp_binance.py --symbols ETHUSDT,BTCUSDT
python scripts/live_trader_perp_binance.py --all-symbols
```

---

## 9. LIVE 模式运行命令（危险）

### 9.1 LIVE 单币（TESTNET 推荐先跑）

```bash
python scripts/live_trader_perp_binance.py --mode live --env testnet --symbol ETHUSDT
```

### 9.2 LIVE 单币（PROD）

```bash
python scripts/live_trader_perp_binance.py --mode live --env prod --symbol ETHUSDT
```

### 9.3 LIVE 多币

```bash
python scripts/live_trader_perp_binance.py --mode live --env prod --symbols ETHUSDT,BTCUSDT
```

### 9.4 LIVE 强确认流程说明

当你执行 `--mode live`，脚本会：

1. 检查 `BINANCE_API_KEY` / `BINANCE_API_SECRET`（缺失则立即退出）
2. 打印危险提示，要求输入 `xionghan` 确认（`no` 或其他均退出）
3. 通过后有 **15 秒倒计时**
4. 倒计时结束后执行**启动自检**：
   - 连接 Binance server time（检查网络 + 时间偏差）
   - 加载 exchangeInfo（检查 symbol 精度）
   - 验证所有目标 symbol 都处于 `TRADING` 状态
5. 自检全部通过后，进入正常交易循环

> 任何自检失败都会安全退出，不会进入交易循环。

---

## 10. 启动前检查清单（LIVE 模式专用）

- [ ] `.env` 已正确配置 `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- [ ] API Key 已开启合约交易权限，未开提现权限
- [ ] 已在 TESTNET 完整跑通过 LIVE 流程
- [ ] DRY-RUN 已运行至少 24 小时且日志无异常
- [ ] `DATA_DIR` 下 `klines_4h.json` / `klines_1d.json` 是最新的
- [ ] ml-service 正在运行（`ML_SERVICE_URL` 可通）
- [ ] 服务器系统时钟与 NTP 对齐（启动自检会报告 drift）
- [ ] Binance 账户有足够 USDT 保证金
- [ ] 已理解脚本在 LIVE 模式下会下真实单、产生真实盈亏

---

## 11. 策略过滤与可选层

### 11.1 分层 gate（layered gate）

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT --use-layered-gate
```

### 11.2 15m 执行确认层

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT --use-15m-confirm
```

警告：启用后会造成实盘/模拟与回测对比不一致。

---

## 12. 运行维护：常用命令与流程

### 12.1 日志管理（建议）

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT > logs/trader_ethusdt.log 2>&1
tail -f logs/trader_ethusdt.log
```

### 12.2 进程管理（简单后台）

```bash
nohup python scripts/live_trader_perp_binance.py --symbol ETHUSDT > logs/trader.log 2>&1 &
echo $!
```

停止时使用 `kill <PID>`。

### 12.3 API Key 轮换

1. Binance 后台创建新 Key（不要开提现权限）
2. 更新服务器 `.env`
3. 重启脚本
4. 确认日志无报错

---

## 13. 排障（Troubleshooting）

### 13.1 报错：找不到 klines 文件
确认 `--data-dir` 或 `DATA_DIR`，并确认目录中存在 `klines_4h.json`、`klines_1d.json`。

### 13.2 报错：ml-service 连接失败
确认服务是否运行、`ML_SERVICE_URL` 是否正确、端口/防火墙是否放行。

### 13.3 LIVE 自检失败：server time 无法连接
检查网络：`curl https://fapi.binance.com/fapi/v1/time`。

### 13.4 LIVE 自检失败：时间偏差（drift）过大
Binance 默认 recvWindow = 5000ms。超过会导致签名超时错误 `-1021`。
修复：`sudo timedatectl set-ntp true`

### 13.5 BinanceAPIError code=-1121（invalid symbol）
symbol 不存在或不是 USDT-M 合约名（应为 `ETHUSDT` 而非 `ETH/USDT`）。自检会提前捕获。

### 13.6 BinanceAPIError code=-1100（precision）
`normalize_qty` 自动对齐精度，若仍报错说明 notional 太小（低于 minQty）。

### 13.7 BinanceAPIError code=-2019（margin insufficient）
保证金不足。减小 `--strategy-funds-usdt` 或 `--position-fraction`，或增加账户保证金。

### 13.8 BinanceAPIError code=-1022（invalid signature）
API Key / Secret 有误（多余空格或引号）。检查 `.env`。

### 13.9 qty 为零或低于 minQty
日志出现 `ValueError: ... rounded qty=0`。原因：notional_usdt / current_price < stepSize。
调整 `--strategy-funds-usdt` 或 `--position-fraction` 使 notional 足够大。

---

## 14. 版本演进计划（简述）

- **PR-1**：通用脚本 + 安全模式切换 + 文档（已完成）
- **PR-2A**：接入 Binance Futures REST 真下单（已完成）：PROD/TESTNET endpoint、HMAC 签名、精度归整、启动自检、markPrice 实时获取
- **PR-2B（计划）**：执行语义与回测对齐（TP/SL/horizon 同-bar 语义、`tie_breaker`、`timeout_exit`、`position_mode`）
- **PR-3（计划）**：更完整的 live vs simulated 对齐；PnL 追踪；close 后 next_allowed_ts 逻辑

---
