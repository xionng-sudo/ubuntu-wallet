# ubuntu-wallet 中文运维手册（Runbook）

> 本文档面向日常维护人员。
>
> **快速参考**：常用命令速查请见根目录 [README.md](../README.md) 第 14 节。本文档提供更完整的日常运维规程。
>
> 目标：
> - 说明系统每天/每周该怎么维护
> - 说明哪些指标必须看
> - 说明如何切模型、重启服务、查看日志、恢复故障
> - 让接手的人可以不靠“口头传承”也能维护系统

---

---

## 文档约定（Documentation Conventions）

- **Python 执行方式**：所有脚本统一使用 `~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/<script>.py` 直接调用，**不**使用 `source .../activate` / `deactivate`。
- **pip 安装**：使用 `~/ubuntu-wallet/ml-service/.venv/bin/pip install -r requirements.txt`，不使用 activate/deactivate 包裹。
- **健康检查**：使用 `curl -fsS http://` 而不是 `curl -s http://`。
- **Systemd 定时器时区**：所有 timer 触发时间均以**本机时区**为准，实际触发时间以 `systemctl list-timers` 输出为准，非 UTC。


# 目录

1. [运维目标](#1-运维目标)
2. [系统运行中的关键对象](#2-系统运行中的关键对象)
3. [每日检查清单](#3-每日检查清单)
4. [每周检查清单](#4-每周检查清单)
5. [常用命令速查](#5-常用命令速查)
6. [服务运维](#6-服务运维)
7. [数据运维](#7-数据运维)
8. [模型运维](#8-模型运维)
9. [日志运维](#9-日志运维)
10. [评估运维](#10-评估运维)
11. [模拟交易与 DRY-RUN 运维](#11-模拟交易与-dry-run-运维)
12. [模型切换与回滚流程](#12-模型切换与回滚流程)
13. [异常场景处置](#13-异常场景处置)
14. [必须长期关注的风险信号](#14-必须长期关注的风险信号)
15. [运维最佳实践](#15-运维最佳实践)
16. [Feature Flags 运维说明](#16-feature-flags-运维说明)
17. [多币种运维（Multi-Symbol）](#17-多币种运维multi-symbol)
18. [快速入门（Quick Start）](#18-快速入门quick-start)
19. [完整 Ops + ML 工作流程](#19-完整-ops--ml-工作流程)
20. [漂移监控完整参考（Drift Monitor）](#20-漂移监控完整参考drift-monitor)
21. [新增币种与阈值调试指南](#21-新增币种与阈值调试指南)
22. [故障排查手册（Troubleshooting）](#22-故障排查手册troubleshooting)
23. [脚本参数速查（`--help` 摘要）](#23-脚本参数速查--help-摘要)

---

# 1. 运维目标

日常运维的目标不是“服务别挂”，而是：

1. **数据持续、完整、无错位**
2. **推理服务持续可用**
3. **日志完整可追踪**
4. **评估闭环持续运行**
5. **模型表现稳定可解释**
6. **在系统异常时能快速降级或停机**

如果只关注“进程还活着”，而不关注：
- 数据是否断了
- signal 是否漂了
- calibration 是否崩了
- coverage 是否异常
那么系统即使“在线”，也可能实际上已经失效。

---

# 2. 系统运行中的关键对象

运维时要盯住以下几个核心对象：

## 2.1 go-collector
作用：
- 数据入口
- 决定后续所有模块是否有正确输入

## 2.2 ml-service
作用：
- 推理入口
- 负责模型加载、特征构建、概率输出、日志记录

## 2.3 prediction log
作用：
- 记录真实时间里每一次预测
- 后续评估的核心依据

## 2.4 evaluation loop
作用：
- 检验模型是否真的在真实序列上有效

## 2.5 模型版本
作用：
- 标记当前线上运行的是谁
- 出问题时能快速回溯

## 2.6 simulated / dry-run trader
作用：
- 验证执行链路
- 观察资金曲线
- 检查风控壳是否合理

---

# 3. 每日检查清单

建议每天固定时间检查，最好形成固定仪式。

## 3.1 服务状态检查
检查以下 systemd 单元：

```bash
systemctl status go-collector
systemctl status ml-service
systemctl status evaluate-predictions.timer
```

### 目标
- 服务是 active / running
- timer 正常 scheduled
- 没有频繁重启

---

## 3.2 数据更新检查
检查：
- `klines_1h.json`
- `klines_4h.json`
- `klines_1d.json`

观察：
- 文件修改时间是否持续变化
- 最新时间戳是否接近当前市场时间
- 1h/4h/1d 是否一致更新

---

## 3.3 ml-service 健康检查

> **注意（Note）**：ml-service 端口为 **9000**，不是 8000。

```bash
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool
```

预期输出（Expected output）：
```json
{
    "ok": true,
    "model_dir": "/home/ubuntu/ubuntu-wallet/models",
    "data_dir": "/home/ubuntu/ubuntu-wallet/data",
    "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
    "model_expected_n_features": 120,
    "calibration_available": true,
    "calibration_method": "isotonic"
}
```

**字段说明（Field explanation）：**
- `ok: true`：服务正常 / Service is healthy
- `model_version`：确认当前加载的模型版本 / Current model version
- `calibration_available: true`：校准器正常加载 / Calibration artifact is loaded
- `calibration_method`：当前使用的校准方法 / Calibration method in use

如果 `ok` 为 `false` 或响应为空，先查看 ml-service 日志：
```bash
journalctl -u ml-service -n 50 --no-pager
```

重点看：
- 服务状态（`ok: true` 表示正常）
- `model_version`
- `calibration_available`
- 若有 feature schema version，也要看

---

## 3.4 prediction log 检查
查看各已启用交易对的预测日志（go-collector 每 FAST 周期自动触发，默认 60s）：

```bash
# 快速检查所有 Phase 1 交易对的预测日志时间戳
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT; do
  echo -n "$sym: "
  stat -c '%y' ~/ubuntu-wallet/data/$sym/predictions_log.jsonl 2>/dev/null || echo "not found"
done

# 查看各交易对最新预测条目
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT; do
  echo "=== $sym ===" && tail -n 1 ~/ubuntu-wallet/data/$sym/predictions_log.jsonl 2>/dev/null || true
done
```

重点看：
- 是否持续追加（所有已启用交易对均有日志）
- 最新记录时间是否合理（距当前不超过 2 分钟）
- 字段是否完整
- 是否有大量空值

---

## 3.5 每日评估输出检查
检查当天或最近一次评估结果：
- 是否成功执行
- 是否输出正常指标
- 是否出现 coverage 异常下降或激增
- 是否出现 LONG / SHORT 完全失衡

---

## 3.6 风险信号快速检查
每天都要快速回答：

- 今天预测数量是否明显异常？
- 高置信度信号是否突然过多/过少？
- 最近的 precision 是否明显下滑？
- 是否出现异常连续亏损？
- 是否出现大量 TIMEOUT 而不是 TP？

---

# 4. 每周检查清单

每周至少做一次比“每日检查”更深入的检查。

## 4.1 统计过去 7 天模型表现
建议看：
- total predictions
- coverage
- high-confidence precision
- LONG precision
- SHORT precision
- avg trade return
- max drawdown
- timeout ratio

## 4.2 检查 calibration 是否失效
判断方式：
- 高置信度区间的真实命中率是否明显低于预期
- 例如 0.70 以上概率是否不再对应高质量结果

## 4.3 检查是否需要调 threshold
阈值过低：
- coverage 高但 precision 降

阈值过高：
- precision 高但几乎没信号

每周都要重新评估 threshold 的适配性。

## 4.4 检查 collector 稳定性
- 一周内是否有数据断档
- 是否有网络异常导致的缺失段
- 数据时间对齐是否正常

## 4.5 检查模型是否需要重训
重训信号包括：
- precision 持续下降
- coverage 持续异常
- 市场 regime 明显变化
- 外部因子变化后现有模型不再适应

---

# 5. 常用命令速查

> **唯一正确的 Python 调用风格**：所有脚本一律使用 `~/ubuntu-wallet/ml-service/.venv/bin/python <script>` 直接调用，**不要**用 `source activate` / `deactivate` 包裹。这样不依赖 shell 状态，在 systemd / cron / 子 shell 中均可重现。
>
> 正确示例：`~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py ...`  
> 禁止用法：`source .venv/bin/activate && python ...`

## 5.1 查看服务状态
```bash
systemctl status go-collector
systemctl status ml-service
systemctl status evaluate-predictions.timer
```

预期输出（go-collector 正常状态）：
```
● go-collector.service - ubuntu-wallet go-collector
     Active: active (running) since Mon 2026-03-15 10:00:00 +0800; 2h 30min ago
```

预期输出（timer 正常状态）：
```
● evaluate-predictions.timer - Run prediction evaluator every 6 hours
     Active: active (waiting) since Mon 2026-03-15 06:06:08 +0800; 5h ago
    Trigger: Mon 2026-03-15 18:06:08 +0800; 35min left
```

**输出说明（Output explanation）：**
- `active (running)`：服务正在运行 / Service is actively running
- `active (waiting)`：timer 在等待下次触发 / Timer is waiting for next trigger
- `Trigger: 18:06:08 +0800`：下次触发时间（本机时区，以 `systemctl list-timers` 实际输出为准）

## 5.2 查看日志
```bash
# 查看最近 200 行 systemd 日志
journalctl -u go-collector -n 200 --no-pager
journalctl -u ml-service -n 200 --no-pager
journalctl -u evaluate-predictions.service -n 200 --no-pager

# 实时追踪日志（按 Ctrl+C 停止）
journalctl -u go-collector -f
journalctl -u ml-service -f
```

正常的 go-collector 日志示例（Normal go-collector log example）：
```
Mar 15 10:00:01 ubuntu go-collector[1234]: time="2026-03-15T10:00:01Z" level=info msg="fetching klines" symbol=ETHUSDT interval=1h
Mar 15 10:01:01 ubuntu go-collector[1234]: time="2026-03-15T10:01:01Z" level=info msg="klines saved" count=1 file=klines_1h.json
Mar 15 10:01:02 ubuntu go-collector[1234]: time="2026-03-15T10:01:02Z" level=info msg="signal request" url=http://127.0.0.1:9000/predict
```

**日志术语说明（Log term explanation）：**
- `fetching klines`：正在从交易所拉取 K 线数据 / Fetching candlestick data from exchange
- `klines saved`：K 线已保存到文件 / Klines saved to file
- `signal request`：正在调用 ml-service 获取预测信号 / Calling ml-service for prediction

## 5.3 重启服务
```bash
sudo systemctl restart go-collector
sudo systemctl restart ml-service
```

## 5.4 手工执行评估
```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --symbol ETHUSDT \
  --log-path ~/ubuntu-wallet/data/ETHUSDT/predictions_log.jsonl \
  --data-dir ~/ubuntu-wallet/data/ETHUSDT \
  --interval 1h \
  --active-model event_v3 \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --horizon-bars 6
```

## 5.5 跑模拟交易
```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_eth_perp_simulated.py
```

## 5.6 跑 walk-forward
```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/walkforward_cv.py \
  --data-dir ~/ubuntu-wallet/data \
  --n-splits 5 \
  --gap-bars 12 \
  --label-method ternary \
  --confidence-threshold 0.65 \
  --output-csv /tmp/cv_report.csv
```

---

# 6. 服务运维

# 6.1 go-collector 运维

## 需要关注
- 是否存活
- 是否持续产出数据
- 是否因 API 错误卡住
- 是否出现 panic

## 当 collector 异常时
第一优先级是恢复数据流，而不是继续看���型结果。因为数据入口坏了，后续所有指标都不可信。

## 建议处理步骤
1. 看 `systemctl status go-collector`
2. 看 `journalctl -u go-collector`
3. 检查数据文件修改时间
4. 若配置变更后失效，恢复上一个稳定配置

---

# 6.2 ml-service 运维

## 需要关注
- 是否存活
- 模型是否加载正确
- calibration 是否可用
- `/predict` 是否返回合理
- prediction log 是否写入

## 常见处理动作
### 重启
```bash
sudo systemctl restart ml-service
```

### 健康检查

> **注意（Note）**：ml-service 端口为 **9000**，不是 8000。

```bash
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool
```

## 注意
不要只因为进程活着就认为服务没问题。必须验证：
- 模型版本
- calibration 状态
- 日志是否正常写入

---

# 7. 数据运维

# 7.1 数据连续性检查
每天要确认：
- 最新 K 线时间是否正常
- 是否缺 bar
- 多周期数据是否同步

## 7.2 数据对齐检查
重点检查：
- 1h / 4h / 1d 的时间戳是否能互相对应
- 日志中的 feature_ts 是否与 K 线数据匹配

## 7.3 数据异常处理原则
当数据缺失、错位、不连续时：
- 暂停使用结果做策略决策
- 优先修复数据
- 修复后再恢复评估或交易

---

# 8. 模型运维

# 8.1 模型上线前
必须确保：
- walk-forward 已跑
- threshold 已验证
- calibration 已确认
- model_meta 完整
- feature schema 清晰

## 8.2 模型上线后
要盯：
- precision 是否按预期
- coverage 是否异常
- LONG / SHORT 是否失衡
- 高置信度区间是否仍可靠

## 8.3 需要重训的典型信号
- 连续 7 天 precision 明显下滑
- calibration 严重失真
- 市场 regime 明显切换
- 外生因子结构变化

---

# 9. 日志运维

# 9.1 prediction log 重要性
`predictions_log.jsonl` 是整个闭环的核心证据链。

## 9.2 每天检查
- 文件是否持续增长
- 是否有畸形 JSON
- 字段是否齐全
- 最新时间是否接近实时

## 9.3 日志归档建议
不要无限制写同一个文件。建议：
- 按日期拆分
- 或定期轮转

例如：
- `predictions_2026-03-15.jsonl`

---

# 10. 评估运维

# 10.1 核心关注指标
- coverage
- precision
- win rate
- avg return
- max drawdown
- TP / SL / TIMEOUT 分布

## 10.2 需要特别警惕的现象
### coverage 突然接近 0
可能是：
- threshold 过高
- 多周期过滤太严
- 数据或 schema 出问题

### precision 突然大跌
可能是：
- 市场 regime 变化
- calibration 失效
- 数据漂移
- 特征异常

### TIMEOUT 突然暴增
可能是：
- 趋势延续性变差
- TP/SL/horizon 组合不适应当前市场

---

# 11. 模拟交易与 DRY-RUN 运维

# 11.1 模拟交易的意义
模拟交易不是“看看结果好不好看”，而是验证：
- 顺序逻辑
- 风控逻辑
- 多周期过滤
- 资金曲线是否合理

## 11.2 DRY-RUN 的意义
DRY-RUN 是进入真仓前的最后演练。

## 11.3 DRY-RUN 需要重点关注
- 开仓频率
- 同方向连续信号是否过多
- 风控壳是否触发过度/不足
- 是否有重复下单风险

---

# 12. 模型切换与回滚流程

# 12.1 切换模型前
必须先做：
- 保存当前模型备份
- 记录当前 model_version
- 确保新模型与 calibration artifact 配套
- 确认 feature schema 匹配

## 12.2 切换后
执行：
- 重启或热加载 ml-service
- 看 `/healthz`
- 做一条小规模预测测试
- 确认 prediction log 正常写入

## 12.3 回滚条件
如果出现以下任一情况，应优先考虑回滚：
- `/predict` 大量报错
- schema warning 激增
- prediction log 异常
- precision 短期急剧恶化
- calibration 状态异常

---

# 13. 异常场景处置

# 13.1 collector 停了
处置顺序：
1. 恢复 collector
2. 检查数据是否补齐
3. 再恢复下游任务

## 13.2 ml-service 起不来
处置顺序：
1. 看日志
2. 确认模型文件
3. 确认 Python 环境
4. 必要时回滚模型或代码

## 13.3 评估脚本一直报错
处置顺序：
1. 检查 log 文件格式
2. 检查数据时间对齐
3. 检查参数版本是否一致

## 13.4 signal 突然异常
如果突然：
- 预测数暴增
- 全是 LONG 或全是 SHORT
- 几乎全是 FLAT

应怀疑：
- 数据问题
- 特征构建问题
- schema drift
- 市场 regime 重大变化

---

# 14. 必须长期关注的风险信号

以下信号必须长期盯：

## 14.1 数据风险
- K 线断档
- 多周期错位
- 数据延迟

## 14.2 模型风险
- calibration 崩坏
- 线上线下不一致
- market regime 漂移

## 14.3 策略风险
- coverage 急剧变化
- 连续亏损增加
- MDD 上升
- LONG/SHORT 失衡

## 14.4 工程风险
- prediction log 不写
- service 频繁重启
- timer 不再触发

---

# 15. 运维最佳实践

## 15.1 所有改动都版本化
包括：
- 模型
- threshold
- TP/SL/horizon
- 风控参数

## 15.2 先 DRY-RUN，后真仓
不要跳过这一阶段。

## 15.3 每次只改一类变量
例如：
- 只改 threshold
- 或只改模型
- 或只改 TP/SL

不要一次改很多，否则无法归因。

## 15.4 保留每日记录
建议形成：
- 每日报告
- 每周总结
- 模型上线记录
- 故障处理记录

## 15.5 遇到异常优先降级而不是硬撑
在以下情况下应优先停机或降级：
- 数据不完整
- schema 漂移严重
- prediction log 失真
- 风控异常
- 连续系统错误

---

# 16. Feature Flags 运维说明

## 16.1 Flag 一览

| 环境变量                | 默认  | 功能                            |
|------------------------|-------|---------------------------------|
| `ENABLE_EXOG_FEATURES` | false | 外生特征（资金费率/持仓量/买卖比）|
| `ENABLE_DRIFT_MONITOR` | false | 特征漂移监控（每6h）             |
| `ENABLE_CALIB_REPORT`  | false | 校准质量报告（每周）             |

## 16.2 查看当前 Flag 状态

```bash
# 通过 healthz 端点查看
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool | grep -A5 '"flags"'
```

## 16.3 临时启用 Flag

```bash
# 编辑 EnvironmentFile
sudo nano /etc/ubuntu-wallet/ml-service.env
# 修改 ENABLE_DRIFT_MONITOR=true
sudo systemctl restart ml-service
```

## 16.4 手工运行 Drift Monitor

**推荐方式：直接调用 ml-service 虚拟环境中的 Python（不需要 `source activate`）**

```bash
# 单币种（自动派生路径）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol ETHUSDT \
  --dry-run

# 单币种（完整显式路径）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --train-stats ~/ubuntu-wallet/models/ETHUSDT/current/train_feature_stats.json \
  --log-path    ~/ubuntu-wallet/data/ETHUSDT/predictions_log.jsonl \
  --output-dir  ~/ubuntu-wallet/data/ETHUSDT/reports \
  --dry-run

# 全币种（使用 --models-base-dir 明确指定模型根目录，避免 MODEL_DIR 污染）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models

# 全币种（验证 MODEL_DIR 不影响全量模式）
ENABLE_DRIFT_MONITOR=true \
  MODEL_DIR=~/ubuntu-wallet/models/ETHUSDT/current \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models
```

> **说明**：`--models-base-dir` 是 `--all-symbols` 模式专用参数，优先级高于 `MODELS_BASE_DIR` 环境变量。  
> 永远**不要**在 `--models-base-dir` 中传单币种路径（如 `models/ETHUSDT/current`），必须传根目录（如 `~/ubuntu-wallet/models`）。

详细的 `--models-base-dir` 解析规则与完整 Drift Monitor 参考，请见 [第 20 节](#20-漂移监控完整参考drift-monitor)。

## 16.5 手工运行 Calibration Report

```bash
ENABLE_CALIB_REPORT=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/python-analyzer/calibration_report.py \
  --log-path ~/ubuntu-wallet/data/ETHUSDT/predictions_log.jsonl \
  --output-dir ~/ubuntu-wallet/data/ETHUSDT/reports \
  --dry-run
```

## 16.6 查看报告文件

```bash
ls -lh ~/ubuntu-wallet/data/reports/
# drift_YYYY-MM-DD.{json,md}
# calib_report_YYYY-MM-DD.{json,md,png}
```

## 16.7 Drift Monitor Timer 状态

```bash
sudo systemctl status drift-monitor.timer
sudo systemctl list-timers drift-monitor.timer
journalctl -u drift-monitor.service -n 50 --no-pager
```

---

# 17. 多币种运维（Multi-Symbol）

本节适用于同时运行多个交易对模型的部署场景。

## 17.1 支持的币种与分阶段上线

| 币种 | 阶段 | configs/symbols.yaml 中默认启用 |
|------|------|-------------------------------|
| BTCUSDT | Phase 1 | ✅ |
| ETHUSDT | Phase 1 | ✅ |
| SOLUSDT | Phase 1 | ✅ |
| BNBUSDT | Phase 1 | ✅ |
| XRPUSDT | Phase 2 | ✅ |
| DOGEUSDT | Phase 2 | ✅ |
| ADAUSDT | Phase 2 | ✅ |

> 所有 7 个交易对均已启用（`enabled: true`）。如需临时停用某币种，将对应条目改为 `enabled: false`。

## 17.2 每币种目录布局与必需 Artifact

每个启用的交易对均有独立的目录结构，Drift 监控依赖以下文件：

```
models/
  <SYMBOL>/
    current/                             <- 活跃模型产物（由训练脚本自动 promote）
      model_meta.json                    <- 训练元数据（训练时间、阈值、标签方法等）
      train_feature_stats.json           <- ★ drift 监控必需：各特征的 mean/std/missing_rate
      feature_columns_event_v3.json      <- 特征列列表
      lightgbm_event_v3.pkl
      lightgbm_event_v3_scaler.pkl
      xgboost_event_v3.json
      xgboost_event_v3_scaler.pkl
      stacking_event_v3.pkl
      calibration_event_v3.pkl           <- 可选，仅在 calibration != none 时存在
    archive/                             <- 版本归档
      event_v3-<timestamp>/
        (同 current/ 中所有文件)
    registry.json                        <- 版本历史

data/
  <SYMBOL>/
    klines_1h.json    klines_4h.json    klines_1d.json
    predictions_log.jsonl
    reports/
```

**验证某币种 artifact 完整性**：

```bash
SYMBOL=BTCUSDT
ls ~/ubuntu-wallet/models/${SYMBOL}/current/
# 应包含：model_meta.json  train_feature_stats.json  feature_columns_event_v3.json
#         lightgbm_event_v3.pkl  xgboost_event_v3.json  stacking_event_v3.pkl 等

# 验证 train_feature_stats.json 格式（per-feature mean/std/missing_rate）
python3 -c "
import json
with open('models/${SYMBOL}/current/train_feature_stats.json') as f:
    stats = json.load(f)
feats = list(stats.keys())
print(f'{len(feats)} features. First: {feats[0]} → {stats[feats[0]]}')
"
```

**跨所有启用币种一次性检查**：

```bash
for SYMBOL in BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT; do
  FILE=~/ubuntu-wallet/models/${SYMBOL}/current/train_feature_stats.json
  if [ -f "$FILE" ]; then
    CNT=$(python3 -c "import json; print(len(json.load(open('$FILE'))))")
    echo "${SYMBOL}: OK (${CNT} features)"
  else
    echo "${SYMBOL}: MISSING ← 需要重新训练: bash ~/ubuntu-wallet/scripts/train_symbol.sh ${SYMBOL}"
  fi
done
```

## 17.3 查看某币种当前模型

```bash
SYMBOL=BTCUSDT
cat ~/ubuntu-wallet/models/${SYMBOL}/current/model_meta.json | python3 -m json.tool
```

## 17.4 按币种训练

```bash
# 方式一：单币种便捷包装（自动读取 configs/symbols.yaml 参数）
bash ~/ubuntu-wallet/scripts/train_symbol.sh BTCUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh ETHUSDT --calibration sigmoid

# 方式二：一次训练所有启用币种（失败隔离，互不影响）
bash ~/ubuntu-wallet/scripts/train_all_symbols.sh

# 预演：打印命令但不执行
bash ~/ubuntu-wallet/scripts/train_all_symbols.sh --dry-run

# 方式三：手工指定路径（完整控制）
SYMBOL=BTCUSDT
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir  ~/ubuntu-wallet/data/${SYMBOL} \
  --model-dir ~/ubuntu-wallet/models/${SYMBOL} \
  --horizon 12 --tp-pct 0.0175 --sl-pct 0.009 --calibration isotonic
```

> `train_all_symbols.sh` 在单个币种失败时会继续训练其余币种，最后汇总报告哪些失败。

## 17.5 按币种评估预测日志

```bash
# 自动从 configs/symbols.yaml 派生路径和参数
SYMBOL=BTCUSDT
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py --symbol ${SYMBOL}

# 也可手工覆盖单个参数
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --symbol ${SYMBOL} --threshold 0.68 --tp 0.020
```

## 17.6 按币种 Drift 监控

```bash
# 单币种 drift（推荐：直接使用 ml-service venv）
SYMBOL=BTCUSDT
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol ${SYMBOL}
# 等价于：
#   --train-stats ~/ubuntu-wallet/models/${SYMBOL}/current/train_feature_stats.json
#   --log-path    ~/ubuntu-wallet/data/${SYMBOL}/predictions_log.jsonl
#   --output-dir  ~/ubuntu-wallet/data/${SYMBOL}/reports

# 一次对所有启用币种运行 drift（--models-base-dir 必须是模型根目录，非单币种路径）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models

# 预演（不写文件）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models \
  --dry-run
```

**systemd drift-monitor.service** 已配置为使用 `--all-symbols` 和 `--models-base-dir`，每次触发时自动覆盖全部启用币种。
若某币种 `models/<SYMBOL>/current/train_feature_stats.json` 缺失，该币种会被跳过并记录 WARNING，不影响其他币种。

完整 drift 监控参考（包括 systemd 部署、日志解读、输出目录）见 [第 20 节](#20-漂移监控完整参考drift-monitor)。

## 17.7 批量操作所有启用币种

```bash
# 列出所有 enabled 币种
cd ~/ubuntu-wallet
~/ubuntu-wallet/ml-service/.venv/bin/python -c "
import sys; sys.path.insert(0, 'scripts')
from symbol_paths import list_enabled_symbols
print(list_enabled_symbols())
"

# 一次训练所有启用币种（失败隔离）
bash ~/ubuntu-wallet/scripts/train_all_symbols.sh

# 一次对所有启用币种运行 drift（明确指定模型根目录）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python \
  ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models
```

**缺失 `train_feature_stats.json` 排查**：

```bash
# 快速检查哪些币种缺少训练 artifact
for SYMBOL in BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT; do
  FILE=~/ubuntu-wallet/models/${SYMBOL}/current/train_feature_stats.json
  [ -f "$FILE" ] && echo "${SYMBOL}: OK" || echo "${SYMBOL}: MISSING — 请运行 bash ~/ubuntu-wallet/scripts/train_symbol.sh ${SYMBOL}"
done

# 对缺失的币种补充训练
bash ~/ubuntu-wallet/scripts/train_symbol.sh SOLUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh BNBUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh XRPUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh DOGEUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh ADAUSDT
```

## 17.8 启用/禁用某币种

1. 编辑 `~/ubuntu-wallet/configs/symbols.yaml`
2. 修改目标币种的 `enabled: true/false`
3. 如果禁用，对应 systemd 服务/计时器也应相应暂停或移除

## 17.9 向后兼容说明

**模型目录**：ml-service 优先使用 `models/<SYMBOL>/current/`，若该目录不存在则退回到 `MODEL_DIR`（默认 `models/ETHUSDT/current/`）。

**预测日志**：ml-service 默认写入 `data/<SYMBOL>/predictions_log.jsonl`。若需同时写根级 `data/predictions_log.jsonl`，可设置 `PREDICTIONS_LOG_ALSO_ROOT=1`（迁移期使用）。

离线脚本可通过 `--log-path` / `--train-stats` 等参数显式指定路径，向后兼容行为不变。

## 17.10 自动在线预测（Automatic Online Prediction）

go-collector 的 FAST 收集周期（默认 60s）会对**所有已启用的交易对**自动发送 `POST /predict`。  
无需手动 curl，也无需额外 cron job。

**调用链**（每 60s 执行一次）：
```
go-collector FAST ticker
  └── collectFastAll()
        └── computeAndPersistFeaturesAndSignals()
              ├── computeSymbolFeaturesAndSignals(primary, ...) → POST /predict?symbol=ETHUSDT
              ├── computeSymbolFeaturesAndSignals(BTCUSDT, ...) → POST /predict?symbol=BTCUSDT
              ├── computeSymbolFeaturesAndSignals(SOLUSDT, ...) → POST /predict?symbol=SOLUSDT
              ├── computeSymbolFeaturesAndSignals(BNBUSDT, ...) → POST /predict?symbol=BNBUSDT
              ├── computeSymbolFeaturesAndSignals(XRPUSDT, ...) → POST /predict?symbol=XRPUSDT
              ├── computeSymbolFeaturesAndSignals(DOGEUSDT, ...) → POST /predict?symbol=DOGEUSDT
              └── computeSymbolFeaturesAndSignals(ADAUSDT, ...) → POST /predict?symbol=ADAUSDT
```

每次 `/predict` 调用后，ml-service 将预测结果追加至：
- `data/ETHUSDT/predictions_log.jsonl`
- `data/BTCUSDT/predictions_log.jsonl`
- `data/SOLUSDT/predictions_log.jsonl`
- `data/BNBUSDT/predictions_log.jsonl`
- `data/XRPUSDT/predictions_log.jsonl`
- `data/DOGEUSDT/predictions_log.jsonl`
- `data/ADAUSDT/predictions_log.jsonl`

**ml-service 不可用时**：go-collector 自动降级到规则引擎（`RulesEngine`），该交易对的信号仍会持久化，但不会写 predictions_log。下一个周期会重试。

**验证所有交易对均有自动预测**：
```bash
# 等待 2 分钟后检查
for sym in BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT; do
  echo -n "$sym: "
  stat -c '%y' ~/ubuntu-wallet/data/$sym/predictions_log.jsonl 2>/dev/null || echo "NOT FOUND"
done

# 查看 go-collector 日志中每个交易对的特征计算行
journalctl -u go-collector.service --since "5 minutes ago" | grep "Feature snapshot aligned"
```



---

# 18. 快速入门（Quick Start）

> 适用于：新用户第一次上手，或熟悉系统后需要快速参考。

## 18.1 系统首次部署后必须完成的步骤

```bash
# 1. 进入仓库根目录（所有命令均以此为基准）
cd ~/ubuntu-wallet

# 2. 确认环境变量文件已配置
cat /etc/ubuntu-wallet/ml-service.env   # 检查 APP_ROOT、ENABLE_DRIFT_MONITOR 等

# 3. 训练所有启用币种的模型（首次必须运行）
bash scripts/train_all_symbols.sh

# 4. 启动核心服务
sudo systemctl start go-collector ml-service

# 5. 等待约 2 分钟，验证预测日志已生成（启用币种从 configs/symbols.yaml 动态读取）
for sym in $(~/ubuntu-wallet/ml-service/.venv/bin/python -c "
import sys; sys.path.insert(0,'scripts')
from symbol_paths import list_enabled_symbols
print(' '.join(list_enabled_symbols()))
"); do
  echo -n "$sym: "
  stat -c '%y' data/$sym/predictions_log.jsonl 2>/dev/null || echo "NOT FOUND"
done

# 6. 运行一次全量 drift 检查（验证模型 artifact 完整）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models \
  --dry-run
```

## 18.2 日常操作速查

| 操作 | 命令 |
|------|------|
| 训练单个币种 | `bash scripts/train_symbol.sh BTCUSDT` |
| 训练全部币种 | `bash scripts/train_all_symbols.sh` |
| 单币种回测 | 见 [第 19.2 节](#192-回测工作流) |
| 单币种 drift 检查 | `ENABLE_DRIFT_MONITOR=true ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py --symbol BTCUSDT` |
| 全币种 drift 检查 | `ENABLE_DRIFT_MONITOR=true ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py --all-symbols --models-base-dir ~/ubuntu-wallet/models` |
| 检查 systemd 服务状态 | `sudo systemctl status drift-monitor.service drift-monitor.timer` |
| 查看 drift 日志 | `tail -n 100 data/logs/drift_monitor.log` |

---

# 19. 完整 Ops + ML 工作流程

## 19.1 工作流总览与执行顺序

```
数据采集 (go-collector)
    ↓
模型训练 (train_symbol.sh / train_all_symbols.sh)
    ↓
在线推理 (ml-service) → 写入 predictions_log.jsonl
    ↓
回测验证 (backtest_event_v3_http.py)   ← 需要 ml-service 正在运行
    ↓
漂移监控 (report_drift.py)             ← 需要 train_feature_stats.json
    ↓
决策：是否需要重新训练？
    ↓ (是)
重新训练 → 重新回测 → 重新验证漂移
```

> **关键原则**：  
> - 训练在前，回测在后（回测依赖在线推理，推理依赖已训练的模型）  
> - 回测结果决定是否上线该模型（不通过回测的模型不应上线）  
> - 漂移监控是持续运行的（systemd 每 6 小时一次），不是仅在重训时运行  

## 19.2 回测工作流

### 回测前置检查

运行多符号回测前，请确认：

1. **pred_cache 已清空**（模型更新后必须执行）：
   ```bash
   rm -f data/pred_cache/*.jsonl
   ```

2. **诊断当前模型的概率分布**（可选，用于确认阈值范围合理性）：
   ```bash
   python scripts/diagnose_pred_cache.py --cache-dir data/pred_cache
   ```

3. **阈值网格覆盖 raw p_stack 实际范围**（0.38-0.60）：
   - 在 `run_multisymbol_backtest_phased.py` 中确认 `PHASE1_THRESHOLDS` 从 0.38 开始
   - 不要使用 0.65+ 的阈值，raw p_stack 无法达到该范围

### 概率字段说明

`/predict` 接口返回多个概率字段，用途不同：

| 字段 | 含义 | 入场决策 |
|------|------|---------|
| `p_long` | raw stacking 输出 | ✅ 是 |
| `effective_long` | 同 p_long（raw） | ✅ 是 |
| `cal_p_long` | isotonic 校准后 | ❌ 仅监控 |

回测脚本通过 HTTP 调用本地 ml-service 完成信号生成，因此 **必须先启动 ml-service**。

```bash
# 前提：ml-service 正在运行
sudo systemctl start ml-service
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool

# 基本回测（单币种，使用默认参数网格）
SYMBOL=BTCUSDT
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir  data/${SYMBOL} \
  --base-url  http://127.0.0.1:9000

# 带参数的回测示例（固定 threshold 和 TP/SL，搜索最优配置）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir        data/${SYMBOL} \
  --base-url        http://127.0.0.1:9000 \
  --thresholds      0.38:0.60:0.02 \
  --tp-grid         0.010:0.025:0.005 \
  --sl-grid         0.005:0.015:0.005 \
  --horizon-bars    12 \
  --objective       avg_ret_mdd_daily \
  --position-mode   single

# 关键回测参数说明：
# --data-dir         per-symbol 数据目录（含 klines_1h.json）
# --base-url         ml-service 地址（默认 http://127.0.0.1:9000）
# --thresholds       概率阈值网格，格式 min:max:step（raw p_stack 范围：0.38-0.60）
# --tp-grid          止盈比例网格，格式 min:max:step
# --sl-grid          止损比例网格，格式 min:max:step
# --horizon-bars     持仓最大 bar 数（默认 24）
# --objective        优化目标：pf | avg_ret | avg_ret_mdd_daily | avg_ret_mdd_hourly
# --position-mode    stack（默认，多仓叠加）| single（单仓，更保守）
# --mt-filter-mode   市场趋势过滤：off | long_only | symmetric | layered
# --since / --until  限定回测时间范围（ISO 日期字符串）
# --debug-best       打印最优配置的详细诊断信息
```

## 19.3 何时需要重新训练

满足以下任一条件时应重新训练：

| 触发条件 | 说明 |
|----------|------|
| **漂移报告显示 PSI > 0.2** | 特征分布严重偏移，模型预期效果下降 |
| **漂移报告 mean_drift > 3σ（多个特征）** | 均值显著漂移，超出训练分布 |
| **回测指标明显恶化** | avg_ret 大幅下降，MDD 上升，profit factor < 1.0 |
| **市场 regime 变化** | 如剧烈行情后，模型训练期所用数据已无代表性 |
| **新增训练数据超过 30 天** | 积累了足量新数据，值得重训捕捉新规律 |
| **首次添加新币种** | 新币种必须先训练再上线 |

**重新训练后必须重新验证**：
1. 检查新 `train_feature_stats.json` 是否已生成
2. 重新运行回测，对比训前/训后指标
3. 重新运行 drift 检查（此时应无漂移，因为基线已更新）
4. 如回测通过，重启 ml-service 加载新模型

```bash
# 完整重训 + 验证流程（以 BTCUSDT 为例）
SYMBOL=BTCUSDT

# 步骤 1：重新训练
bash scripts/train_symbol.sh ${SYMBOL}

# 步骤 2：验证 train_feature_stats.json 已更新
ls -lh models/${SYMBOL}/current/train_feature_stats.json

# 步骤 3：重启 ml-service 加载新模型
sudo systemctl restart ml-service

# 步骤 4：等待服务稳定，再运行回测
sleep 5
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir data/${SYMBOL} \
  --base-url http://127.0.0.1:9000 \
  --debug-best

# 步骤 5：运行 drift 检查，确认基线与线上分布一致
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol ${SYMBOL}
```

## 19.4 何时重新运行回测

| 触发条件 | 是否需要重新回测 |
|----------|-----------------|
| 修改 `configs/symbols.yaml` 中的 threshold/tp/sl | **是** |
| 重新训练模型 | **是** |
| 怀疑回测结果不准（数据更新后）| **是** |
| 仅修改日志格式或报告输出 | 否 |
| 仅修改 systemd 配置 | 否 |

---

# 20. 漂移监控完整参考（Drift Monitor）

## 20.1 工作原理

`scripts/report_drift.py` 比较**训练时的特征分布**与**近期线上预测日志中的特征分布**，计算以下指标：

| 指标 | 说明 |
|------|------|
| `mean_drift` | 均值漂移，以训练 std 为单位（值 > 1σ 开始关注，> 2σ 需要重训） |
| `std_drift` | 标准差漂移（值 > 1σ 表示波动性变化） |
| `psi` | Population Stability Index，综合分布距离（< 0.1 稳定，0.1~0.2 警告，> 0.2 需重训） |
| `psi_baseline` | PSI 计算基线来源：`bootstrap`（使用训练样本）或 `gaussian_cdf`（使用均值/方差） |
| `live_missing_rate` | 线上特征缺失率（高缺失率可能表示数据 schema 变化） |

输出文件（每次运行生成两个）：
- `data/<SYMBOL>/reports/drift_YYYY-MM-DD.json`：完整 JSON 格式报告
- `data/<SYMBOL>/reports/drift_YYYY-MM-DD.md`：Markdown 摘要（仅显示 mean_drift > 1σ 的特征）

## 20.2 `--models-base-dir` 解析优先级

`report_drift.py` 在 `--all-symbols` 模式下，通过以下优先级链确定模型根目录：

1. `--models-base-dir <路径>`（CLI 参数，优先级最高）
2. `MODELS_BASE_DIR` 环境变量
3. `$APP_ROOT/models`（若 `APP_ROOT` 环境变量已设置）
4. 脚本位置推导：`<脚本目录>/../models`（兜底，对 CWD 不敏感）

> **重要**：`MODEL_DIR` 环境变量**永远不会被 `--all-symbols` 使用**，因为它是单币种指针，会污染多币种路径解析。

## 20.3 完整参数说明

```
report_drift.py 参数：

--symbol <SYM>           单币种模式，自动派生 train-stats/log-path/output-dir
--all-symbols            全币种模式，从 configs/symbols.yaml 读取所有 enabled 币种
--models-base-dir <路径>  仅 --all-symbols 有效，指定模型根目录
                          （如 ~/ubuntu-wallet/models，而不是 .../models/ETHUSDT/current）
--train-stats <文件>      手动指定 train_feature_stats.json（单币种模式下可覆盖默认值）
--log-path <文件>         手动指定 predictions_log.jsonl
--output-dir <目录>       输出目录（默认 data/<SYMBOL>/reports 或 data/reports）
--window-rows <N>         分析最近 N 行预测记录（默认 200）
--dry-run                 仅计算，不写文件
```

## 20.4 如何手动运行

```bash
cd ~/ubuntu-wallet  # 建议在仓库根目录运行

# 方式 A：单币种（自动路径）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol BTCUSDT

# 方式 B：单币种（完整显式路径，用于调试）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --train-stats models/BTCUSDT/current/train_feature_stats.json \
  --log-path    data/BTCUSDT/predictions_log.jsonl \
  --output-dir  data/BTCUSDT/reports

# 方式 C：全币种（推荐生产用法）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models

# 方式 D：全币种 + 使用环境变量（无需 CLI 参数）
export ENABLE_DRIFT_MONITOR=true
export MODELS_BASE_DIR=~/ubuntu-wallet/models
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py --all-symbols
```

## 20.5 如何解读输出

**控制台输出示例（正常）**：
```
[drift] models base dir: /home/ubuntu/ubuntu-wallet/models
[drift] running for symbol=BTCUSDT
Drift report JSON  → data/BTCUSDT/reports/drift_2026-03-25.json
Drift report MD    → data/BTCUSDT/reports/drift_2026-03-25.md
[drift] running for symbol=ETHUSDT
...
```

**控制台输出示例（有警告）**：
```
WARNING: [SOLUSDT] train-stats file not found, skipping drift: /home/ubuntu/ubuntu-wallet/models/SOLUSDT/current/train_feature_stats.json
```
→ 说明该币种尚未完成训练，需运行 `bash scripts/train_symbol.sh SOLUSDT`

**Markdown 报告关键字段**：
```markdown
# Feature Drift Report — 2026-03-25

- Live rows analysed: **200** (window=200)
- Features monitored: **47**
- Features with mean_drift > 1σ: **3**

## High-Drift Features (mean_drift > 1σ)

| Feature | mean_drift | psi | live_missing_rate |
|---------|-----------|-----|-------------------|
| rsi_1h  | 2.1400 | 0.1823 | 0.000 |
```

**解读标准**：
- `mean_drift > 2.0` + `psi > 0.2`：**严重漂移，建议重训**
- `mean_drift > 1.0` + `psi > 0.1`：**轻度漂移，持续观察**
- `live_missing_rate > 0.1`：**数据 schema 变化，检查 go-collector**

## 20.6 systemd 部署与定时器

### 服务文件（`systemd/drift-monitor.service`）

```ini
[Unit]
Description=ubuntu-wallet feature drift monitor
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
Environment=APP_ROOT=/home/ubuntu/ubuntu-wallet
EnvironmentFile=-/etc/ubuntu-wallet/ml-service.env
WorkingDirectory=/home/ubuntu/ubuntu-wallet
ExecStartPre=/bin/mkdir -p /home/ubuntu/ubuntu-wallet/data/logs
ExecStart=/bin/bash -c '/home/ubuntu/ubuntu-wallet/ml-service/.venv/bin/python \
  /home/ubuntu/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ${APP_ROOT}/models \
  >> ${APP_ROOT}/data/logs/drift_monitor.log 2>&1'

[Install]
WantedBy=multi-user.target
```

> **注意 `WorkingDirectory` 与 `ExecStart` 的路径写法**：
> - `WorkingDirectory` 中不要使用 `${APP_ROOT}` 变量展开（systemd 环境下 `${}` 变量展开有限制），应直接写绝对路径 `/home/ubuntu/ubuntu-wallet`。
> - `ExecStart` 中使用 `/bin/bash -c '...'` 包装后，`bash` 会展开 `${APP_ROOT}`，但**仅当** `APP_ROOT` 已通过 `Environment=` 或 `EnvironmentFile=` 导入到 unit 中。

### 定时器文件（`systemd/drift-monitor.timer`）

```ini
[Unit]
Description=Run feature drift monitor every 6 hours
Requires=drift-monitor.service

[Timer]
OnCalendar=*-*-* 00,06,12,18:05:00
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
```

定时器触发时间以本机时区为准（`OnCalendar=*-*-* 00,06,12,18:05:00`），随机延迟最多 2 分钟以避免集中负载。实际下次触发时间以 `systemctl list-timers drift-monitor.timer` 输出为准。

### 部署步骤

```bash
cd ~/ubuntu-wallet

# 1. 备份当前 systemd unit（可回滚）
sudo mkdir -p /etc/systemd/system/backup-ubuntu-wallet
sudo cp -a /etc/systemd/system/drift-monitor.service \
  /etc/systemd/system/backup-ubuntu-wallet/drift-monitor.service.$(date +%F_%H%M%S) 2>/dev/null || true
sudo cp -a /etc/systemd/system/drift-monitor.timer \
  /etc/systemd/system/backup-ubuntu-wallet/drift-monitor.timer.$(date +%F_%H%M%S) 2>/dev/null || true

# 2. 复制仓库中的 unit 文件到 /etc/systemd/system/
sudo cp -f systemd/drift-monitor.service /etc/systemd/system/drift-monitor.service
sudo cp -f systemd/drift-monitor.timer   /etc/systemd/system/drift-monitor.timer

# 3. 重新加载 systemd 配置
sudo systemctl daemon-reload

# 4. 启用并启动定时器（定时器启动后服务会按计划自动触发）
sudo systemctl enable --now drift-monitor.timer

# 5. 手动触发一次验证（不等下次计划时间）
sudo systemctl start drift-monitor.service

# 6. 查看执行结果
systemctl show -p Result -p ExecMainStatus drift-monitor.service
tail -n 100 data/logs/drift_monitor.log
```

### 验证定时器运行状态

```bash
# 查看所有定时器（含下次触发时间）
sudo systemctl list-timers --all | grep drift

# 查看服务最近执行状态
sudo systemctl status drift-monitor.service --no-pager -l

# 查看日志（journal）
journalctl -u drift-monitor.service -n 100 --no-pager

# 查看文件日志
tail -f ~/ubuntu-wallet/data/logs/drift_monitor.log
```

期望输出：
```
● drift-monitor.service - ubuntu-wallet feature drift monitor
     Loaded: loaded (/etc/systemd/system/drift-monitor.service; static)
     Active: inactive (dead) since 2026-03-25 12:05:03 +0800; 5min ago
    Process: ExecStart=/bin/bash -c ... (code=exited, status=0/SUCCESS)
   Main PID: 1234 (code=exited, status=0/SUCCESS)

Result=success  ExecMainStatus=0
```

---

# 21. 新增币种与阈值调试指南

## 21.1 新增币种完整检查清单

在将新币种加入系统之前，按以下步骤逐一确认：

- [ ] **1. 数据可用性**：确认 `data/<SYMBOL>/klines_1h.json` 存在且数据量足够

```bash
SYMBOL=NEWUSDT
ls -lh data/${SYMBOL}/klines_1h.json
wc -l data/${SYMBOL}/klines_1h.json
# 建议至少 2000 行（约 3 个月 1h K 线）
```

- [ ] **2. 添加到 `configs/symbols.yaml`**

```yaml
# 在 configs/symbols.yaml 中添加：
  NEWUSDT:
    enabled: true
    interval: "1h"
    threshold: 0.65        # 起始用默认值，后续通过回测调整
    tp: 0.020
    sl: 0.010
    horizon: 12
    calibration: "isotonic"
```

- [ ] **3. 训练模型**

```bash
bash scripts/train_symbol.sh NEWUSDT
```

- [ ] **4. 验证训练产物完整**

```bash
ls -lh models/NEWUSDT/current/
# 必须包含：
# - train_feature_stats.json   （drift 监控必需）
# - model_meta.json            （模型元数据）
# - feature_columns_event_v3.json
# - lightgbm_event_v3.pkl（或其他模型文件）
```

- [ ] **5. 验证 train_feature_stats.json 格式**

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python -c "
import json
with open('models/NEWUSDT/current/train_feature_stats.json') as f:
    stats = json.load(f)
print(f'特征数量: {len(stats)}')
first_feat = next(iter(stats.items()))
print(f'示例特征: {first_feat[0]} -> {first_feat[1]}')
"
```

- [ ] **6. 运行 drift 检查（确认 artifact 可被读取）**

```bash
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol NEWUSDT \
  --dry-run
```

- [ ] **7. 重启 ml-service 加载新模型**

```bash
sudo systemctl restart ml-service
# 等待服务启动
sleep 5
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool | grep NEWUSDT
```

- [ ] **8. 运行回测验证参数**

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir data/NEWUSDT \
  --base-url http://127.0.0.1:9000 \
  --debug-best
```

- [ ] **9. 根据回测结果调整阈值（见 21.2 节）**

- [ ] **10. 部署到 systemd drift-monitor（全量运行会自动包含新币种）**

```bash
# 验证新币种已被 --all-symbols 识别
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models \
  --dry-run 2>&1 | grep -E "running for|WARNING.*NEWUSDT"
```

## 21.2 阈值参数调试方法

### 参数位置

所有阈值参数存储在 `configs/symbols.yaml` 中的对应币种配置下：

```yaml
BTCUSDT:
  threshold: 0.65   # 最低进场概率（值越高，信号越少但质量更高）
  tp: 0.0175        # 止盈比例（1.75%）
  sl: 0.009         # 止损比例（0.90%）
  horizon: 12       # 最大持仓 bar 数（1h interval → 12h）
  calibration: isotonic  # 概率校准方法
```

### 调试步骤

```bash
SYMBOL=BTCUSDT

# 步骤 1：用网格搜索找最优参数（回测搜索）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir        data/${SYMBOL} \
  --base-url        http://127.0.0.1:9000 \
  --thresholds      0.55:0.80:0.025 \
  --tp-grid         0.010:0.030:0.0025 \
  --sl-grid         0.005:0.020:0.001 \
  --objective       avg_ret_mdd_daily \
  --position-mode   single \
  --debug-best

# 步骤 2：记录最优参数（阅读 --debug-best 输出中的 best config 部分）

# 步骤 3：将最优参数更新到 configs/symbols.yaml
# 编辑文件，修改 threshold/tp/sl 字段

# 步骤 4：验证修改：用固定参数运行一次回测（缩小网格，确认最优点）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir        data/${SYMBOL} \
  --base-url        http://127.0.0.1:9000 \
  --thresholds      0.63:0.68:0.005 \
  --tp-grid         0.016:0.020:0.001 \
  --sl-grid         0.008:0.011:0.001 \
  --objective       avg_ret_mdd_daily \
  --position-mode   single

# 步骤 5：若参数变化较大（threshold 差 > 0.05 或 tp/sl 差 > 20%），重新训练
bash scripts/train_symbol.sh ${SYMBOL}

# 步骤 6：重训后再次运行回测验证（比较训前/训后指标）
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
  --data-dir data/${SYMBOL} \
  --base-url http://127.0.0.1:9000 \
  --debug-best
```

### 指标解读

| 指标 | 判断标准 |
|------|----------|
| `avg_ret` (平均收益率) | > 0 为正期望，> 0.003 为较好信号 |
| `profit_factor` | > 1.2 可接受，> 1.5 较好 |
| `avg_ret_mdd_daily` | 综合收益/最大回撤，越大越好（首选优化目标） |
| `n_signals` | 信号总数，太少（< 50）会降低统计可信度 |
| `timeout_win_rate` | 超时退出的盈亏比（过高表示 TP 设置偏高） |

## 21.3 只改一个变量原则

每次调参遵循：**只改一类变量，验证后再改下一个**：

1. 先调 `threshold`（影响进场频率与质量）
2. 再调 `tp/sl`（影响每笔盈亏比）
3. 最后调 `horizon`（影响持仓时长）

不要一次同时修改多个参数，否则无法归因。

---

# 22. 故障排查手册（Troubleshooting）

## 22.1 常见故障一览

| 故障现象 | 可能原因 | 定位方法 |
|----------|----------|----------|
| drift 报告全部 skip，无任何 `[drift] running` | `ENABLE_DRIFT_MONITOR=false` | 检查 `/etc/ubuntu-wallet/ml-service.env` |
| drift 报告显示 `train_feature_stats.json not found` | 对应币种未训练 | 运行 `bash scripts/train_symbol.sh <SYMBOL>` |
| `--all-symbols` 路径拼错，如 `models/ETHUSDT/current/BTCUSDT/current/...` | `MODEL_DIR` 污染（旧版 bug） | 升级脚本至最新版，使用 `--models-base-dir` |
| systemd `Result=success` 但日志无输出 | `ENABLE_DRIFT_MONITOR` 未传入 unit | 检查 unit 的 `EnvironmentFile=` 是否正确 |
| systemd `WorkingDirectory` 无效，报路径错误 | `${}` 变量展开失败 | 在 unit 中直接写绝对路径，不使用变量 |
| `backtest_event_v3_http.py` 报 `Connection refused` | ml-service 未启动 | `sudo systemctl start ml-service` |
| ml-service 加载模型失败，日志有 `FileNotFoundError` | `models/current.json` 或 `models/<SYM>/current/` 缺失 | 运行训练脚本生成 artifact |
| go-collector 不写 predictions_log | ml-service 宕机（降级到规则引擎） | 检查 ml-service 状态并重启 |

## 22.2 ENABLE_DRIFT_MONITOR=false（最常见的"无输出"原因）

**现象**：运行 `report_drift.py` 后只打印 `ENABLE_DRIFT_MONITOR=false, skipping.`，没有任何报告生成。

**原因**：环境变量 `ENABLE_DRIFT_MONITOR` 默认值为 `false`，若未显式设置为 `true`，脚本立即退出。

**修复（手动运行）**：

```bash
# 方式 1：在命令前直接赋值
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models

# 方式 2：export 后运行
export ENABLE_DRIFT_MONITOR=true
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models
```

**修复（systemd）**：

```bash
# 检查 EnvironmentFile 内容
cat /etc/ubuntu-wallet/ml-service.env | grep DRIFT

# 如果缺少或为 false，修改：
sudo sed -i 's/ENABLE_DRIFT_MONITOR=false/ENABLE_DRIFT_MONITOR=true/' \
  /etc/ubuntu-wallet/ml-service.env
# 或直接编辑
sudo nano /etc/ubuntu-wallet/ml-service.env
# 添加/修改：ENABLE_DRIFT_MONITOR=true

# 重新触发服务
sudo systemctl daemon-reload
sudo systemctl start drift-monitor.service
```

## 22.3 MODEL_DIR 污染（`--all-symbols` 路径拼错）

**现象**：drift 报告报 `train_feature_stats.json not found`，路径形如：
```
models/ETHUSDT/current/BTCUSDT/current/train_feature_stats.json
```
或：
```
BTCUSDT/current/train_feature_stats.json  （相对路径）
```

**原因（旧版脚本）**：旧版 `report_drift.py` 在 `--all-symbols` 模式下错误地读取 `MODEL_DIR`（单币种指针），将其作为 base dir，导致路径错误拼接。

**修复**：确认使用最新版本的 `scripts/report_drift.py`（包含 `_resolve_models_base_dir()` 函数），并显式传递 `--models-base-dir`：

```bash
# 正确用法（--models-base-dir 必须指向模型根目录，如 .../models，不是 .../models/ETHUSDT/current）
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models

# 即使 MODEL_DIR 被设置为单币种路径，也不会影响 --all-symbols
MODEL_DIR=~/ubuntu-wallet/models/ETHUSDT/current \
ENABLE_DRIFT_MONITOR=true \
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py \
  --all-symbols \
  --models-base-dir ~/ubuntu-wallet/models
```

## 22.4 train_feature_stats.json 缺失

**现象**：

```
WARNING: [BTCUSDT] train-stats file not found, skipping drift: /home/ubuntu/ubuntu-wallet/models/BTCUSDT/current/train_feature_stats.json
```

**修复**：

```bash
# 检查哪些币种缺少 artifact（从 configs/symbols.yaml 动态读取启用币种）
cd ~/ubuntu-wallet
for SYM in $(~/ubuntu-wallet/ml-service/.venv/bin/python -c "
import sys; sys.path.insert(0,'scripts')
from symbol_paths import list_enabled_symbols
print(' '.join(list_enabled_symbols()))
"); do
  FILE=~/ubuntu-wallet/models/${SYM}/current/train_feature_stats.json
  [ -f "$FILE" ] && echo "OK   ${SYM}" || echo "MISS ${SYM}"
done

# 对缺失的币种运行训练
bash ~/ubuntu-wallet/scripts/train_symbol.sh BTCUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh ETHUSDT
# 或一次性训练所有（失败隔离）
bash ~/ubuntu-wallet/scripts/train_all_symbols.sh
```

## 22.5 systemd WorkingDirectory 变量展开失败

**现象**：systemd unit 启动报错，如：
```
WorkingDirectory=/home/ubuntu/ubuntu-wallet: No such file or directory
```
或日志中路径出现 `${APP_ROOT}` 字面量（未被展开）。

**原因**：systemd `WorkingDirectory=` 字段**不支持 `${VAR}` 变量展开**（不同于 `ExecStart` 通过 `bash -c` 展开）。

**修复**：在 `WorkingDirectory=` 中直接写绝对路径，不使用变量：

```ini
# 错误写法（systemd 不展开此处的变量）
WorkingDirectory=${APP_ROOT}

# 正确写法
WorkingDirectory=/home/ubuntu/ubuntu-wallet
```

## 22.6 如何检查 systemd 服务状态

```bash
# 查看服务运行状态（最近 50 行日志）
sudo systemctl status drift-monitor.service --no-pager -l

# 查看服务 journal 日志
journalctl -u drift-monitor.service -n 200 --no-pager

# 查看定时器状态（含下次触发时间）
sudo systemctl list-timers drift-monitor.timer --all

# 查看所有相关 ubuntu-wallet 服务状态
sudo systemctl status go-collector ml-service drift-monitor.timer --no-pager

# 手动触发 oneshot 服务（立即运行，不等计划时间）
sudo systemctl start drift-monitor.service

# 查看服务最终退出状态
systemctl show -p Result -p ExecMainStatus drift-monitor.service
# 期望：Result=success  ExecMainStatus=0
```

## 22.7 如何查看日志与报告

```bash
# drift monitor 文件日志（服务输出重定向到此文件）
tail -n 200 ~/ubuntu-wallet/data/logs/drift_monitor.log

# 查看最新 drift 报告（Markdown 格式，按日期排序）
ls -lt ~/ubuntu-wallet/data/BTCUSDT/reports/drift_*.md | head -5
cat ~/ubuntu-wallet/data/BTCUSDT/reports/drift_$(date +%Y-%m-%d).md

# 批量查看所有币种今日报告（从 configs/symbols.yaml 动态读取启用币种）
for SYM in $(~/ubuntu-wallet/ml-service/.venv/bin/python -c "
import sys; sys.path.insert(0,'scripts')
from symbol_paths import list_enabled_symbols
print(' '.join(list_enabled_symbols()))
"); do
  FILE=~/ubuntu-wallet/data/${SYM}/reports/drift_$(date +%Y-%m-%d).json
  if [ -f "$FILE" ]; then
    echo "=== ${SYM} ==="
    python3 -c "
import json
with open('${FILE}') as f:
    r = json.load(f)
high = [(k,v) for k,v in r['features'].items() if v['mean_drift']>1.0]
print(f'n_live_rows={r[\"n_live_rows\"]} high_drift_features={len(high)}')
for k,v in sorted(high,key=lambda x:-x[1]['mean_drift'])[:3]:
    print(f'  {k}: mean_drift={v[\"mean_drift\"]:.3f} psi={v[\"psi\"]}')
"
  else
    echo "=== ${SYM}: 今日无报告 ==="
  fi
done
```

## 22.8 ml-service 路径问题排查

```bash
# 查看 ml-service 启动日志，确认加载的模型路径
journalctl -u ml-service.service -n 50 --no-pager

# 确认 MODEL_DIR 的实际解析路径
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool | grep -i model

# 验证某币种模型目录存在且有内容
SYMBOL=BTCUSDT
ls -lah ~/ubuntu-wallet/models/${SYMBOL}/current/

# 如模型缺失，运行训练
bash ~/ubuntu-wallet/scripts/train_symbol.sh ${SYMBOL}
sudo systemctl restart ml-service
```

---

# 23. 脚本参数速查（`--help` 摘要）

> 本节集中列出所有常用脚本的参数、默认值与说明，方便一处查阅，无需分散翻阅各章。

---

## 23.1 `scripts/report_drift.py`

```
用途：特征漂移监控 — 比较训练期特征分布与线上预测日志中的特征分布

调用示例：
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/report_drift.py --help

参数列表：

参数                     默认值       说明
----------------------  -----------  -----------------------------------------------
--symbol <SYM>          (无)         单币种模式，自动派生 train-stats/log-path/output-dir
--all-symbols           (关闭)       全币种模式，从 configs/symbols.yaml 读取所有 enabled 币种
--models-base-dir <路径> (见下方)     仅 --all-symbols 有效；模型根目录（必须是绝对路径且存在）
                                     优先级：CLI > MODELS_BASE_DIR 环境变量 > APP_ROOT/models > 脚本推导
--train-stats <文件>    (自动推导)    train_feature_stats.json 路径（单币种时可覆盖默认值）
--log-path <文件>       (自动推导)    predictions_log.jsonl 路径
--output-dir <目录>     data/<SYM>/reports 或 data/reports
--window-rows <N>       200          分析最近 N 行预测记录
--dry-run               (关闭)       仅计算，不写文件

环境变量（优先于脚本默认，低于 CLI 参数）：
  ENABLE_DRIFT_MONITOR   必须为 "true" 才执行，否则退出 0（默认 "false"）
  MODELS_BASE_DIR        --all-symbols 模型根目录（被 --models-base-dir 覆盖）
  APP_ROOT               仓库根目录（用于推导 MODELS_BASE_DIR = APP_ROOT/models）
  MODEL_DIR              ⚠ 单币种推理指针，--all-symbols 模式完全忽略此变量

输出：
  data/<SYM>/reports/drift_YYYY-MM-DD.json   完整 JSON 报告
  data/<SYM>/reports/drift_YYYY-MM-DD.md     Markdown 摘要（仅 mean_drift > 1σ 的特征）
```

---

## 23.2 `scripts/backtest_event_v3_http.py`

```
用途：通过本地 ml-service HTTP 端点进行参数网格搜索回测
前提：ml-service 正在运行（sudo systemctl start ml-service）

调用示例：
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/backtest_event_v3_http.py \
    --data-dir data/BTCUSDT --debug-best

参数列表：

参数                       默认值                说明
------------------------  --------------------  -----------------------------------------------
--data-dir <路径>          (必填)               per-symbol 数据目录（含 klines_1h.json）
--base-url <URL>           http://127.0.0.1:9000 ml-service 地址
--interval                 1h                   K 线周期
--fee                      0.0004               手续费率（单边，如 0.04%）
--slippage                 0.0                  滑点（单边）
--since <ISO8601>          (无，全部)            回测开始时间（本机时区解析）
--until <ISO8601>          (无，全部)            回测结束时间
--horizon-bars             24                   最大持仓 bar 数
--thresholds               0.55:0.85:0.02       概率阈值网格（min:max:step）
--tp-grid                  0.005:0.030:0.0025   止盈比例网格
--sl-grid                  0.003:0.020:0.001    止损比例网格
--min-signals-per-week     5.0                  有效配置最少信号频率
--position-mode            stack                stack（叠加）| single（单仓）
--objective                avg_ret_mdd_daily    优化目标：pf | avg_ret | avg_ret_mdd_daily | avg_ret_mdd_hourly
--timeout-exit             close                超时退出价格：close | open_next
--tie-breaker              SL                   TP/SL 同时触发时优先方：SL | TP
--warmup-bars              200                  预热跳过的 bar 数
--side-source              probs                信号来源：signal | probs
--mt-filter-mode           daily_guard          趋势过滤：off | long_only | symmetric | strict | relaxed | trend_guard | daily_guard | conflict | regime | layered
--sleep-ms                 0                    每次 HTTP 调用间隔（毫秒，调试用）
--debug-best               (关闭)               打印最优配置的详细诊断信息
```

## 23.2a `scripts/live_trader_perp_simulated.py`（PR #29 新增逻辑参数）

```
用途：历史 K 线回放模拟交易（DRY-RUN），与 backtest_event_v3_http.py 共享同一决策管道

调用示例（与回测完全对齐，默认参数完全一致）：
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/live_trader_perp_simulated.py \
    --symbol BTCUSDT --mt-filter-mode daily_guard --side-source probs

逻辑参数（均与 backtest_event_v3_http.py 对齐，默认值相同）：

参数                  默认值              说明
------------------   ----------------    -----------------------------------------------
--mt-filter-mode     daily_guard         趋势过滤：off | daily_guard | layered | ...（共 10 种）
--side-source        probs               信号来源：signal | probs
--timeout-exit       close               超时退出价格：close | open_next
--tie-breaker        SL                  TP/SL 同时触发时优先方：SL | TP
--position-mode      single              持仓模式：single | stack
--warmup-bars        0                   跳过前 N 根 K 线（设为 200 与回测对齐）
--pred-cache-file    (无)                回测生成的 pred_cache JSONL 路径（用于对齐验证）
```

---

## 23.3 `python-analyzer/train_event_stack_v3.py`

```
用途：训练 event_v3 多时间框架 LightGBM + XGBoost + LR 堆叠模型
（通常通过 scripts/train_symbol.sh 或 train_all_symbols.sh 调用，不直接调用）

调用示例：
  ~/ubuntu-wallet/ml-service/.venv/bin/python python-analyzer/train_event_stack_v3.py \
    --data-dir data/BTCUSDT --model-dir models/BTCUSDT

参数列表：

参数                  默认值              说明
------------------   ----------------    -----------------------------------------------
--data-dir <路径>     <repo_root>/data   含 klines_1h.json、klines_4h.json、klines_1d.json 的目录
--model-dir <路径>    <repo_root>/models 模型产物输出目录（训练后写入 <model-dir>/current/）
--p-enter            0.65               最低进场概率（存入 model_meta.json）
--delta              0.0                p_long - p_short 最小差值
--label-method       ternary            标签方法：ternary（前向收益）| triple_barrier（TP/SL/horizon）
--horizon            12                 标签前向 bar 数
--up-thresh          0.015              ternary 方法 LONG 阈值（如 0.015 = 1.5%）
--down-thresh        0.015              ternary 方法 SHORT 阈值
--tp-pct             0.0175             triple_barrier 止盈比例（1.75%）
--sl-pct             0.009              triple_barrier 止损比例（0.9%）
--calibration        isotonic           概率校准：isotonic | sigmoid | none
```

---

## 23.4 `scripts/evaluate_from_logs.py`

```
用途：从 prediction log 评估模型表现（PnL / 胜率 / MDD 等）

调用示例：
  ~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py --symbol BTCUSDT

参数列表：

参数                  默认值（--symbol 时自动推导）   说明
------------------   ----------------------------   -----------------------------------------------
--symbol <SYM>        (无)                          指定币种，自动推导以下参数默认值
--log-path <文件>     data/<SYM>/predictions_log.jsonl  预测日志路径
--data-dir <目录>     data/<SYM>                    含 klines_*.json 的目录
--interval            1h（或来自 symbol config）    K 线周期
--active-model        event_v3                      使用的模型版本名称
--model-version       (无)                          可选版本过滤
--since <ISO8601>     (无，全部)                    评估开始时间
--until <ISO8601>     (无，全部)                    评估结束时间
--threshold           0.65（或来自 symbol config）  进场概率阈值
--tp                  0.0175（或来自 symbol config）止盈比例
--sl                  0.009（或来自 symbol config） 止损比例
--fee                 0.0004                        手续费率（单边）
--slippage            0.0                           滑点（单边）
--horizon-bars        6（或来自 symbol config）     前向 bar 数
--tie-breaker         SL                            同时触发时优先：SL | TP
--timeout-exit        close                         超时退出价格：close | open_next
--mt-filter-mode      symmetric                     趋势过滤：symmetric | layered
```

---

## 23.5 `scripts/train_symbol.sh` 和 `scripts/train_all_symbols.sh`

```
用途：单币种 / 批量训练便捷包装，自动从 configs/symbols.yaml 读取参数

train_symbol.sh：
  bash scripts/train_symbol.sh <SYMBOL> [额外参数转发给训练脚本]
  bash scripts/train_symbol.sh BTCUSDT
  bash scripts/train_symbol.sh ETHUSDT --calibration sigmoid

  环境变量覆盖：
    DATA_BASE   数据根目录（默认 <repo_root>/data）
    MODEL_BASE  模型根目录（默认 <repo_root>/models）
    PYTHON      Python 解释器路径（默认 python3；生产环境建议设为 ~/ubuntu-wallet/ml-service/.venv/bin/python）

train_all_symbols.sh：
  bash scripts/train_all_symbols.sh                    # 训练所有 enabled 币种
  bash scripts/train_all_symbols.sh --dry-run          # 预演，仅打印命令
  bash scripts/train_all_symbols.sh --calibration sigmoid  # 覆盖校准方法

  特性：
    - 单个币种失败不影响其余币种（失败隔离）
    - 最终汇总哪些成功、哪些失败
    - 通过 symbol_paths.list_enabled_symbols() 动态读取 configs/symbols.yaml
```
