# Binance USDT-M 永续交易脚本（DRY-RUN ↔ LIVE）使用与维护手册

> 适用脚本：`scripts/live_trader_perp_binance.py`  
> 兼容入口：`scripts/live_trader_eth_perp_binance.py`（已弃用 wrapper，仅兼容旧命令）

---

## 0. 重要安全声明（必须阅读）

1. **默认模式为 DRY-RUN（安全）**：不会向 Binance 发送真实下单请求。
2. 当你使用 `--mode live` 时，会触发：
   - **中文强确认**（必须输入 `xionghan` 才继续）
   - 输入 `no` 立即退出
   - **15 秒倒计时**
3. **PR-1 当前版本尚未接入真实 Binance 下单逻辑**：即使你通过确认进入 LIVE 分支，脚本也会打印醒目提示并继续以 DRY-RUN 引擎运行（不会真下单）。
4. 强烈建议你：
   - 永远不要在不确定时启用 LIVE
   - 不要给 API Key 开启“提现权限”
   - 发现 API Key 泄露立即撤销并重建

---

## 1. DRY-RUN 是什么意思？

DRY-RUN = 演练模式：

- 策略逻辑照常运行：拉取信号、过滤、多周期 gate、风控计算、仓位状态维护（本地）
- 但**所有“下单/平仓动作”只打印��志**，不会影响真实账户

常见用途：
- 验证信号频率是否正确
- 验证过滤规则是否符合预期
- 验证“在某一段行情下，会不会频繁反复开仓”
- 与回测/模拟语义对齐（逐条对账）

---

## 2. LIVE（实盘）是什么意思？

LIVE = 实盘模式（会下真实单的模式）。

在本仓库的 **PR-1 版本**中：
- LIVE 模式只实现了**安全确认壳**（确认 + 倒计时 + 分支结构）
- **未实现真实下单**（PR-2 才会接入 Binance Futures REST/SDK）

---

## 3. Binance PROD vs TESTNET 的区别

- **PROD（正式环境）**
  - 真实资金
  - 真实成交/滑点/手续费/延迟
  - 真实盈亏

- **TESTNET（测试网）**
  - 测试资产（通常通过水龙头获取）
  - 用于验证下单接口是否正确、精度/最小下单量是否正确、撤单/查仓是否正常
  - 行情和流动性不一定等同真实环境（不适合用来评估最终收益）

本脚本支持 `--env prod|testnet` 参数：
- PR-1 版本不会下单，因此 env 主要是“为 PR-2 预留”
- 默认：`--env prod`

---

## 4. 安装与环境准备

### 4.1 Python/虚拟环境（示例）
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 如果你仓库没有 requirements.txt，请按项目实际依赖安装（本脚本用到了 requests、python-dotenv 等）。

### 4.2 `.env` 配置（API Key）
项目根目录创建 `.env`，示例：
```bash
BINANCE_API_KEY=xxxxx
BINANCE_API_SECRET=yyyyy
DATA_DIR=./data
ML_SERVICE_URL=http://127.0.0.1:9000/predict
```

注意：
- DRY-RUN 即使没有 key 也能跑，但会警告。
- 实盘（PR-2）会强依赖 key。

---

## 5. 数据文件要求（K线）

脚本默认从 `DATA_DIR` 读取：
- `klines_4h.json`
- `klines_1d.json`
- （可选）`klines_15m.json`：仅在你启用 `--use-15m-confirm` 时需要

默认 `DATA_DIR=./data`，可通过：
- 环境变量 `DATA_DIR`
- 或参数 `--data-dir`
指定。

---

## 6. 币种选择（单选 / 多选 / 全选）

### 6.1 单选：`--symbol`
```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT
```

### 6.2 多选：`--symbols`（逗号分隔）
```bash
python scripts/live_trader_perp_binance.py --symbols ETHUSDT,BTCUSDT,SOLUSDT
```

### 6.3 全选：`--all-symbols`
全选会从 `configs/symbols.yaml` 解析 symbol 列表。

```bash
python scripts/live_trader_perp_binance.py --all-symbols
```

#### `configs/symbols.yaml` 格式建议
本脚本使用“容错解析”，常见以下写法都能识别：

**写法 A（推荐）**
```yaml
- symbol: ETHUSDT
- symbol: BTCUSDT
- symbol: SOLUSDT
```

**写法 B**
```yaml
- ETHUSDT
- BTCUSDT
```

---

## 7. DRY-RUN 模式运行命令（默认）

> 不写 `--mode` 也默认是 DRY-RUN。

### 7.1 DRY-RUN 单币
```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT
```

### 7.2 DRY-RUN 多币
```bash
python scripts/live_trader_perp_binance.py --symbols ETHUSDT,BTCUSDT
```

### 7.3 DRY-RUN 全币
```bash
python scripts/live_trader_perp_binance.py --all-symbols
```

---

## 8. LIVE 模式运行命令（危险）

### 8.1 LIVE 单币
```bash
python scripts/live_trader_perp_binance.py --mode live --env prod --symbol ETHUSDT
```

### 8.2 LIVE 多币
```bash
python scripts/live_trader_perp_binance.py --mode live --env prod --symbols ETHUSDT,BTCUSDT
```

### 8.3 LIVE 全币
```bash
python scripts/live_trader_perp_binance.py --mode live --env prod --all-symbols
```

### 8.4 LIVE 强确认流程说明
当你执行 `--mode live`，脚本会打印类似：

- “【危险】你正在尝试开启实盘模式（LIVE）”
- 需要输入：
  - 输入 `xionghan`：继续
  - 输入 `no`：退出
  - 其他：退出
- 通过后会有 **15 秒倒计时**

> PR-1 注意：即便你通过确认并倒计时结束，也会看到醒目提示说明“当前版本未接入真实下单”，仍不会真下单。

---

## 9. 策略过滤与可选层

### 9.1 分层 gate（layered gate）
启用：
```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT --use-layered-gate
```

说明：
- 默认关闭（保持 legacy 行为）
- 该模式用于观察新的 gate 分层逻辑（ALLOW_STRONG/ALLOW_WEAK/REJECT）

### 9.2 15m 执行确认层（会造成 live vs backtest 不可比）
启用：
```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT --use-15m-confirm
```

警告：
- backtest/evaluate 不一定支持该确认层
- 启用后会造成“实盘/模拟 与 回测对比不一致”

---

## 10. 运行维护：常用命令与流程（非常重要）

### 10.1 启动前检查清单
1. `.env` 是否存在并加载（特别是 ML_SERVICE_URL、DATA_DIR）
2. `DATA_DIR` 下是否有 `klines_4h.json`、`klines_1d.json`
3. ml-service 是否在运行：
   - URL：`ML_SERVICE_URL`（默认 `http://127.0.0.1:9000/predict`）
4. 时间是否正确（服务器 UTC 时间）
5. 先 DRY-RUN 至少跑 24 小时观察日志

### 10.2 快速连通性测试（ml-service）
```bash
curl -s -X POST http://127.0.0.1:9000/predict \
  -H 'Content-Type: application/json' \
  -d '{"interval":"1h","as_of_ts":"2026-04-02T00:00:00Z"}' | head
```

### 10.3 日志管理（建议）
建议把输出重定向到文件：
```bash
python scripts/live_trader_perp_binance.py --symbol ETHUSDT > logs/trader_ethusdt.log 2>&1
tail -f logs/trader_ethusdt.log
```

### 10.4 进程管理（示例）
- 简单后台运行：
```bash
nohup python scripts/live_trader_perp_binance.py --symbol ETHUSDT > logs/trader.log 2>&1 &
echo $!
```
- 停止：
```bash
kill <PID>
```

> 更推荐 systemd/supervisor，这里先给最基础方式。

### 10.5 API Key 轮换（维护流程）
1. Binance 后台创建新 Key（不要开提现权限）
2. 更新服务器 `.env`
3. 重启脚本
4. 确认日志无报错

---

## 11. 排障（Troubleshooting）

### 11.1 报错：找不到 klines 文件
- 确认 `--data-dir` 或 `DATA_DIR`
- 确认目录中存在 `klines_4h.json`、`klines_1d.json`

### 11.2 报错：ml-service 连接失败
- 确认服务是否运行
- 确认 `--ml-service-url` 或 `ML_SERVICE_URL`
- 确认端口/防火墙

### 11.3 为什么 LIVE 也不下单？
- PR-1 版本未接入真实下单逻辑：这是设计如此（先把安全开关与通用脚手架落地）
- 等 PR-2 会把 `EthPerpStrategyEngineBinance` 的 `_exchange_open_position/_exchange_close_position` 替换为真实 Binance 下单/撤单

---

## 12. 版本演进计划（简述）
- PR-1：通用脚本 + 安全模式切换 + 文档（当前）
- PR-2：接入 Binance Futures 真下单（PROD/TESTNET endpoint、签名、精度、异常恢复、查仓、撤单等）
- PR-3：把 exit 语义与回测/模拟进一步对齐（TP/SL/horizon 同-bar 语义等，若要实盘也一致需更复杂的成交建模）

---
