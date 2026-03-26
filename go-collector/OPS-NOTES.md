# go-collector 运维笔记：自愈健康检查 + Telegram 告警（systemd）

适用环境：Ubuntu 22.04 + systemd  
目标：go-collector 常驻运行；每分钟检查一次 `/api/healthz`；异常自动重启并 Telegram 通知；带 cooldown 防抖。

> **参考文档**：完整部署步骤请参阅 [../README.md](../README.md) 第 13 节（生产部署）和 [../systemd/DEPLOY-NEW-SERVER.md](../systemd/DEPLOY-NEW-SERVER.md)。

---

## 1. 组件清单（你已部署/新增的内容）

### 1.1 systemd 单元
- `go-collector.service`：go-collector 主服务（`Restart=always`）
- `check-go-collector.timer`：每 60 秒触发一次检查
- `check-go-collector.service`：oneshot 检查任务（跑完即退出）

### 1.2 脚本
- `/home/ubuntu/ubuntu-wallet/check-go-collector.sh`：健康检查 + cooldown + 重启
- `/home/ubuntu/ubuntu-wallet/notify-telegram.sh`：Telegram 发送脚本（Bot API）

### 1.3 运行时文件/日志
- 锁文件（防并发）：`/home/ubuntu/ubuntu-wallet/data/tmp/check-go-collector.lock`
- 最近重启记录：`/tmp/go-collector.last-restart`
- 检查脚本输出日志（service 重定向追加）：`/home/ubuntu/ubuntu-wallet/data/logs/check-go-collector.log`

---

## 2. go-collector 主服务（go-collector.service）

### 2.1 启用与启动
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now go-collector.service
systemctl status go-collector.service --no-pager
```

### 2.2 查看日志
```bash
journalctl -u go-collector.service -n 200 --no-pager
```

### 2.3 验证 healthz
端口来自 go-collector 的 `COLLECTOR_PORT=8080`：
```bash
curl -fsS --max-time 3 http://127.0.0.1:8080/api/healthz | jq .
```

---

## 3. 每分钟自愈检查（check-go-collector.timer / service）

### 3.1 启用 timer
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now check-go-collector.timer
systemctl status check-go-collector.timer --no-pager
```

### 3.2 验证 timer 是否在跑
```bash
systemctl list-timers --all | grep check-go-collector || true
journalctl -u check-go-collector.service -n 50 --no-pager
```

### 3.3 立即手动执行一次检查（不等下一分钟）
```bash
sudo systemctl start check-go-collector.service
journalctl -u check-go-collector.service -n 50 --no-pager
tail -n 200 /home/ubuntu/ubuntu-wallet/check-go-collector.log
```

---

## 4. check-go-collector.sh 行为规则（核心逻辑）

### 4.1 触发重启的典型条件
满足任意一个会进入“可能重启”逻辑：
- `jq` 不存在（脚本无法解析 healthz JSON）
- healthz curl 请求失败（连接失败、超时、非 200）
- healthz JSON 中 `.ok != true`

### 4.2 cooldown 防抖（避免频繁重启）
- 默认 cooldown：`COOLDOWN_SEC=300`（5 分钟）
- 5 分钟内已经重启过则：
  - 不重启（SKIP）
  - 但仍然发 Telegram 通知（避免“默默不工作”）

### 4.3 并发控制
- 使用 `flock` + 锁文件：`/home/ubuntu/ubuntu-wallet/data/tmp/check-go-collector.lock`
- 目的：防止 timer 重叠执行导致重复重启/重复告警
- **注意**：锁文件路径必须使用项目数据目录下的可写路径。历史上曾使用 `/run/ubuntu-wallet/check-go-collector.lock`，但该目录需要 root 权限创建，导致 service 以 `ubuntu` 用户运行时反复失败（`Permission denied`）。已在 Phase 2 修复为当前路径（见 `scripts/ops/check-go-collector.sh`）。

---

## 5. Telegram 告警（notify-telegram.sh）

### 5.1 必需环境变量
必须在 `check-go-collector.service` 环境中存在：
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

否则脚本会退出（通常表现为“没收到消息，但 service 显示运行成功”）。

### 5.2 消息类型（你当前配置）
- 重启：`go-collector RESTART: <reason>`
- 冷却跳过：`go-collector SKIP (cooldown x/y): <reason>`

### 5.3 测试发送（不重启）
```bash
/home/ubuntu/ubuntu-wallet/check-go-collector.sh --test-notify
```

---

## 6. sudo 权限（最小化）

检查脚本会执行：
```bash
sudo systemctl restart go-collector
```

你当前 sudoers（已通过 `visudo -c`）：
```
ubuntu ALL=NOPASSWD: /bin/systemctl restart go-collector
```

建议保持最小权限：只允许这一条命令，不要放宽到 `systemctl *`。

---

## 7. 常见故障排查

### 7.1 没有收到 Telegram
1) 确认环境变量确实注入：
```bash
systemctl cat check-go-collector.service
```

2) 看检查服务日志：
```bash
journalctl -u check-go-collector.service -n 200 --no-pager
tail -n 200 /home/ubuntu/ubuntu-wallet/check-go-collector.log
```

### 7.2 timer 不触发
```bash
systemctl status check-go-collector.timer --no-pager
systemctl list-timers --all | grep check-go-collector || true
```

### 7.3 go-collector 反复重启
- 看 healthz 是否长期失败：
```bash
curl -fsS --max-time 3 http://127.0.0.1:8080/api/healthz | jq .
```

- 看是否处于 cooldown：
```bash
ls -l /tmp/go-collector.last-restart 2>/dev/null || true
journalctl -u check-go-collector.service -n 200 --no-pager
```

---

## 8. 多币种 K 线采集（Multi-Symbol Kline Collection）

### 8.1 支持的交易对

| 阶段 | 交易对 | 默认启用 |
|------|--------|---------|
| Phase 1 | BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT | ✅ 默认启用 |
| Phase 2 | XRPUSDT, DOGEUSDT, ADAUSDT | ⚙️ 需显式启用 |

### 8.2 配置方式

**方式 A（推荐）：`SYMBOLS` 环境变量**
```bash
# 仅 Phase 1（默认）
# 无需配置，不设置 SYMBOLS 即可

# 全部 7 个交易对
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT
```

**方式 B：`ENABLE_PHASE2_SYMBOLS` 标志**
```bash
# 开启 Phase 2（在 Phase 1 基础上追加）
ENABLE_PHASE2_SYMBOLS=true
```

> **优先级**：`SYMBOLS` > `ENABLE_PHASE2_SYMBOLS` > 默认 Phase 1。

**主交易对：`PRIMARY_SYMBOL`**
```bash
# 默认 ETHUSDT（向下兼容），建议显式设置
PRIMARY_SYMBOL=ETHUSDT
```

主交易对用于：`/api/market`、特征计算、健康检查文件探针、`LEGACY_ETHUSDT_COMPAT` 根目录文件、1m/5m 额外采集。  
> ⚠️ `PRIMARY_SYMBOL` 必须包含在 `SYMBOLS` 中，否则服务启动时报错退出。

### 8.3 文件写入路径

每个交易对的 K 线写入独立子目录：
```
data/
  BTCUSDT/
    klines_1h.json   klines_4h.json   klines_1d.json   klines_15m.json
  ETHUSDT/
    klines_1h.json   klines_4h.json   klines_1d.json   klines_15m.json   klines_1m.json   klines_5m.json
  SOLUSDT/ ...
  BNBUSDT/ ...
  XRPUSDT/ ...   (Phase 2)
  DOGEUSDT/ ...  (Phase 2)
  ADAUSDT/ ...   (Phase 2)
```

> 主交易对（`PRIMARY_SYMBOL`，默认 ETHUSDT）同时写入 1m/5m 数据，用于特征计算。

### 8.4 向下兼容模式（Legacy root-level paths）

默认情况下，`LEGACY_ETHUSDT_COMPAT=true`，收集器同时将**主交易对**（默认 ETHUSDT）的 K 线写到根目录：
```
data/klines_1h.json   data/klines_4h.json   data/klines_1d.json  ...
```

这些根目录文件始终对应 `PRIMARY_SYMBOL` 的数据（默认 ETHUSDT），**不随 SYMBOLS 列表顺序变化**。  
当所有下游消费者迁移完毕后，在 `collector.env` 中设置：
```bash
LEGACY_ETHUSDT_COMPAT=false
```

### 8.5 验证各交易对最新时间戳

```bash
# 列出所有交易对最新 klines_1h.json 修改时间
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT; do
  echo -n "$sym: "
  stat -c '%y' /home/ubuntu/ubuntu-wallet/data/$sym/klines_1h.json 2>/dev/null || echo "not found"
done

# 或通过 healthz 查看
curl -fsS http://127.0.0.1:8080/api/healthz | jq '{enabled_symbols, primary_symbol, files}'
```

---

## 9. 多币种自动在线预测（Multi-Symbol Automatic Online Prediction）

### 9.1 工作原理

每次 FAST 收集周期（默认 60 秒）完成后，go-collector 会对**所有已启用的交易对**依次：

1. 计算 FeatureSnapshot（1h / 4h / 1d K 线特征）
2. 调用本地规则引擎（`RulesEngine`）
3. 向 ml-service 发送 `POST /predict`（携带 `symbol` 字段）
4. ml-service 将预测结果写入 `data/<SYMBOL>/predictions_log.jsonl`

主交易对（primary symbol，默认 ETHUSDT）的信号写入 `data/signals/`（根级，向下兼容）。  
其他交易对的信号写入 `data/<SYMBOL>/signals/`（按交易对独立目录）。

> **重要**：不需要手动 `curl /predict`；系统会在每个 FAST 周期自动为所有已启用交易对发送预测请求。

### 9.2 验证自动预测日志

运行约 2 分钟后，检查各交易对的预测日志是否持续追加：

```bash
# 检查所有已启用交易对的预测日志时间戳
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT; do
  echo -n "$sym predictions_log: "
  stat -c '%y' /home/ubuntu/ubuntu-wallet/data/$sym/predictions_log.jsonl 2>/dev/null || echo "not found"
done

# 查看各交易对最新预测条目
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT; do
  echo "=== $sym ==="
  tail -n 2 /home/ubuntu/ubuntu-wallet/data/$sym/predictions_log.jsonl 2>/dev/null || echo "(not found)"
done
```

### 9.3 验证 go-collector 日志中有多币种预测调用

```bash
# 应当看到多个不同交易对的特征计算日志（每 60s 一轮）
journalctl -u go-collector.service --since "5 minutes ago" | grep -E '\[(BTC|ETH|SOL|BNB)USDT\]' | tail -30
```

正常输出示例：
```
[ETHUSDT] Feature snapshot aligned: schema=87 computed=80 missing=7
[BTCUSDT] Feature snapshot aligned: schema=87 computed=80 missing=7
[SOLUSDT] Feature snapshot aligned: schema=87 computed=80 missing=7
[BNBUSDT] Feature snapshot aligned: schema=87 computed=80 missing=7
```

### 9.4 配置说明

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SYMBOLS` | BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT | 启用的交易对列表 |
| `ENABLE_PHASE2_SYMBOLS` | false | 设为 `true` 可同时启用 Phase 2 交易对（XRPUSDT, DOGEUSDT, ADAUSDT） |
| `PRIMARY_SYMBOL` | ETHUSDT | 主交易对（根级信号文件 + `/api/signal` 端点） |
| `ML_SERVICE_URL` | http://127.0.0.1:9000/predict | ml-service 预测端点 |
| `COLLECT_FAST_INTERVAL` | 60s | 自动预测触发频率 |

---

## 10. 安全提醒（强烈建议）
- Telegram Bot Token 不要出现在聊天记录/日志/截图中；如已泄露，建议立即在 BotFather 重新生成并替换。
- 更安全做法：把 token 放到 root-only env 文件（600 权限）并用 `EnvironmentFile=` 注入，而不是明文写在 unit override 里。
