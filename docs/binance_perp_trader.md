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
   - **PROD 二次确认**（仅当 `--env prod` 时触发，必须输入 `PROD` 才继续）
   - **15 秒倒计时**
   - **启动自检**（server time、exchangeInfo、symbol 可交易状态）
3. **PR-2A 已接入真实 Binance Futures REST 下单**：通过确认后，LIVE 模式会向 Binance 发出真实 MARKET 订单，**产生真实盈亏**。
4. **`--env` 默认为 `testnet`（安全）**：如需连接真实资金环境，必须显式传 `--env prod`，并通过额外确认。
5. 强烈建议你：
   - 永远不要在不确定时启用 LIVE
   - 在 TESTNET 完整验证后再切到 PROD
   - 不要给 API Key 开启"提现权限"
   - 发现 API Key 泄露立即撤销并重建
   - 先 DRY-RUN 至少跑 24–48 小时观察日志

---

## 1. PR-2A 当前范围与限制（重要）

**PR-2A 是执行基础设施层（execution plumbing），不是完整的实盘策略系统。**

已实现：
- Binance Futures REST 下单（MARKET 开仓 / reduce-only 平仓）
- PROD / TESTNET endpoint 切换
- HMAC-SHA256 签名、timestamp、recvWindow
- 下单精度自动归整（stepSize / tickSize / minQty）
- 启动自检（server time、exchangeInfo、symbol 状态）
- markPrice 实时获取

**尚未实现（待 PR-2B）**：
- next-bar open entry（信号 bar t → 下一根 bar open 入场）
- same-bar TP / SL 检测
- tie_breaker（同 bar TP/SL 共存时的优先级）
- timeout_exit / horizon 退出
- position_mode=single 完整状态机
- 持仓生命周期闭环（已开仓后何时退出、如何 reconcile 与交易所真实持仓）

> **因此：在 PR-2B 完成之前，不建议直接将 PR-2A 用于 PROD 实盘。**
> 建议仅在 TESTNET 上验证 PR-2A 的基础设施功能。

---

## 2. DRY-RUN 是什么意思？

DRY-RUN = 演练模式：

- 策略逻辑照常运行：拉取信号、过滤、多周期 gate、风控计算、仓位状态维护（本地）
- 但**所有"下单/平仓动作"只打印日志**，不会影响真实账户

常见用途：
- 验证信号频率是否正确
- 验证过滤规则是否符合预期
- 验证"在某一段行情下，会不会频繁反复开仓"
- 与回测/模拟语义对齐（逐条对账）

---

## 3. LIVE（实盘）是什么意思？（PR-2A）

LIVE = 实盘模式（会下真实单的模式）。

从 **PR-2A** 起，LIVE 模式已接入 Binance USDT-M Futures REST API：

- 每根 1h bar 收盘后，策略信号触发时：
  - 拉取 Binance markPrice 作为当前价格
  - 按仓位比例计算开仓 notional（USDT）
  - 通过 `exchangeInfo` 自动对齐下单精度（stepSize / tickSize / minQty）
  - 发出真实 **MARKET** 订单（开仓：BUY/SELL；平仓：reduce-only SELL/BUY）
- 所有真实订单会打印 `[LIVE OPEN]` / `[LIVE CLOSE]` 日志并包含 `orderId`

注意：当前 live trader 缺少完整的持仓生命周期管理（见第 1 节）。

---

## 4. Binance PROD vs TESTNET 的区别

- **PROD（正式环境）**
  - endpoint: `https://fapi.binance.com`
  - 真实资金，真实盈亏
  - 需要显式传 `--env prod` + 额外 PROD 确认（输入 `PROD`）
  - 不建议在 PR-2B 完成前使用

- **TESTNET（测试网）**
  - endpoint: `https://testnet.binancefuture.com`
  - 测试资产（通过 Binance testnet 水龙头获取）
  - API Key 需在 Binance Testnet 单独申请（与正式 Key 不通用）
  - **`--env` 默认值即为 `testnet`**（安全默认）
  - 建议所有功能先在 testnet 验证

---

## 5. 所需 API 权限

| 权限 | 是否需要 | 说明 |
|------|---------|------|
| 读取（Read）| 必须 | 查账户、持仓、订单状态 |
| 合约交易（Futures Trading）| 必须 | 下单/撤单 |
| 提现（Withdrawal）| **绝对不要开** | 风险极大，与交易无关 |

---

## 6. 安装与环境准备

### 6.1 Python/虚拟环境（示例）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests python-dotenv
```

### 6.2 `.env` 配置（API Key）

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

## 7. 数据文件要求（K线）

脚本默认从 `DATA_DIR` 读取：
- `klines_4h.json`
- `klines_1d.json`
- （可选）`klines_15m.json`：仅在你启用 `--use-15m-confirm` 时需要

---

## 8. 币种选择（单选 / 多选 / 全选）

### 8.1 单选：`--symbol`

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT
```

### 8.2 多选：`--symbols`（逗号分隔）

```bash
python scripts/live_trader_perp_binance.py --symbols ETHUSDT,BTCUSDT,SOLUSDT
```

### 8.3 全选：`--all-symbols`

```bash
python scripts/live_trader_perp_binance.py --all-symbols
```

---

## 9. DRY-RUN 模式运行命令（默认）

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT
python scripts/live_trader_perp_binance.py --symbols ETHUSDT,BTCUSDT
python scripts/live_trader_perp_binance.py --all-symbols
```

---

## 10. LIVE 模式运行命令

### 10.1 LIVE + TESTNET（推荐先跑）

```bash
python scripts/live_trader_perp_binance.py --mode live --env testnet --symbol ETHUSDT
```

由于 `--env` 默认是 `testnet`，以下命令等价：

```bash
python scripts/live_trader_perp_binance.py --mode live --symbol ETHUSDT
```

### 10.2 LIVE + PROD（危险，需双重确认）

```bash
python scripts/live_trader_perp_binance.py --mode live --env prod --symbol ETHUSDT
```

此命令会要求：
1. 输入 `xionghan` 确认 LIVE 模式
2. 再次输入 `PROD` 确认使用真实资金环境
3. 15 秒倒计时
4. 启动自检
5. 打印 PR-2A 范围声明

### 10.3 LIVE 强确认流程完整说明

当你执行 `--mode live`，脚本会：

1. 检查 API Key（缺失则退出）
2. 输入 `xionghan` 确认 LIVE 模式（`no` 或其他均退出）
3. （仅 `--env prod`）输入 `PROD` 确认真实资金环境
4. 15 秒倒计时
5. 启动自检：server time + 时间偏差、exchangeInfo、symbol TRADING 状态
6. 打印 PR-2A 范围声明（明确当前版本限制）
7. 进入交易循环

> 任何自检失败或确认不通过，都会安全退出，不会进入交易循环。

---

## 11. TESTNET 验收清单（PR-2A 基础设施验证）

在使用 PR-2A 进行任何真实交易前，请按以下步骤在 TESTNET 完整验证：

1. **启动自检通过**
   ```bash
   python scripts/live_trader_perp_binance.py --mode live --env testnet --symbol ETHUSDT
   # 确认看到: [OK] Server time / [OK] Loaded exchangeInfo / [OK] ETHUSDT: status=TRADING
   ```

2. **markPrice 获取正常**
   - 确认日志中出现 `price=<数字>` 而非 `price=0.0`

3. **开仓订单发出并成交（TESTNET）**
   - 等待信号触发后，确认日志出现 `[LIVE OPEN] ... orderId=<id>`
   - 在 Binance testnet 界面或 API 查询到该订单

4. **查询订单状态**
   ```python
   from scripts.binance_futures_rest import BinanceFuturesClient
   c = BinanceFuturesClient(api_key="...", api_secret="...", env="testnet")
   print(c.get_order("ETHUSDT", order_id=<orderId>))
   ```

5. **reduce-only 平仓订单（TESTNET）**
   - 确认 `[LIVE CLOSE]` 日志和 `orderId`
   - 确认该订单为 `reduceOnly=true`、方向与持仓相反

6. **持仓状态查询**
   ```python
   print(c.get_position_risk("ETHUSDT"))
   ```
   - 开仓后确认 `positionAmt != 0`
   - 平仓后确认 `positionAmt == 0`

7. **脚本重启后状态**
   - 重启脚本，确认启动自检仍然通过
   - 当前 PR-2A 不持久化内部持仓状态，重启后内存状态重置（与交易所实际持仓可能不一致，这是 PR-2A 已知限制）

---

## 12. 启动前检查清单（LIVE 模式专用）

- [ ] `.env` 已正确配置 `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- [ ] API Key 已开启合约交易权限，未开提现权限
- [ ] 已在 TESTNET 完整跑通上述验收清单
- [ ] DRY-RUN 已运行至少 24 小时且日志无异常
- [ ] `DATA_DIR` 下 `klines_4h.json` / `klines_1d.json` 是最新的
- [ ] ml-service 正在运行（`ML_SERVICE_URL` 可通）
- [ ] 服务器系统时钟与 NTP 对齐
- [ ] Binance 账户有足够 USDT 保证金
- [ ] 已理解 PR-2A 的范围限制（无完整持仓生命周期管理，不建议 PROD 实盘）

---

## 13. 策略过滤与可选层

### 13.1 分层 gate（layered gate）

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT --use-layered-gate
```

### 13.2 15m 执行确认层

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT --use-15m-confirm
```

警告：启用后会造成实盘/模拟与回测对比不一致。

---

## 14. 运行维护：常用命令与流程

### 14.1 日志管理

```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT > logs/trader_ethusdt.log 2>&1
tail -f logs/trader_ethusdt.log
```

### 14.2 进程管理

```bash
nohup python scripts/live_trader_perp_binance.py --symbol ETHUSDT > logs/trader.log 2>&1 &
echo $!
```

停止时使用 `kill <PID>`。

---

## 15. 排障（Troubleshooting）

### 15.1 报错：找不到 klines 文件
确认 `--data-dir` 或 `DATA_DIR`，并确认目录中存在 `klines_4h.json`、`klines_1d.json`。

### 15.2 报错：ml-service 连接失败
确认服务是否运行、`ML_SERVICE_URL` 是否正确、端口/防火墙是否放行。

### 15.3 LIVE 自检失败：server time 无法连接
检查网络：`curl https://fapi.binance.com/fapi/v1/time` 或 TESTNET：`curl https://testnet.binancefuture.com/fapi/v1/time`

### 15.4 LIVE 自检失败：时间偏差（drift）过大
超过 `MAX_ACCEPTABLE_CLOCK_DRIFT_MS`（2000ms）时告警，超过 Binance recvWindow（5000ms）时会触发 `-1021`。
修复：`sudo timedatectl set-ntp true`

### 15.5 BinanceAPIError code=-1121（invalid symbol）
symbol 不是 USDT-M 合约名。启动自检会提前捕获。

### 15.6 BinanceAPIError code=-1100（precision）
`normalize_qty` 自动对齐精度；如仍报错，说明 notional 太小（低于 minQty）。

### 15.7 BinanceAPIError code=-2019（margin insufficient）
减小 `--strategy-funds-usdt` 或 `--position-fraction`，或增加账户保证金。

### 15.8 BinanceAPIError code=-1022（invalid signature）
`BINANCE_API_KEY` / `BINANCE_API_SECRET` 配置有误（多余空格或引号）。

### 15.9 qty 为零或低于 minQty
`ValueError: ... rounded qty=0`：notional_usdt / current_price < stepSize。
调整 `--strategy-funds-usdt` 或 `--position-fraction`。

---

## 16. 版本演进计划（简述）

- **PR-1**：通用脚本 + 安全模式切换 + 文档（已完成）
- **PR-2A**：接入 Binance Futures REST 真下单基础设施（已完成）：PROD/TESTNET、HMAC 签名、精度归整、启动自检、markPrice 获取、PROD 双重确认
- **PR-2B（计划）**：执行语义与回测对齐（TP/SL/horizon 同-bar 语义、`tie_breaker`、`timeout_exit`、`position_mode`、持仓生命周期闭环）
- **PR-3（计划）**：更完整的 live vs simulated 对齐；PnL 追踪；close 后 next_allowed_ts 逻辑

---
