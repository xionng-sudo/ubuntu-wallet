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
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool
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
查看：
- `data/logs/predictions_log.jsonl`

重点看：
- 是否持续追加
- 最新记录时间是否合理
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

## 5.1 查看服务状态
```bash
systemctl status go-collector
systemctl status ml-service
systemctl status evaluate-predictions.timer
```

预期输出（go-collector 正常状态）：
```
● go-collector.service - ubuntu-wallet go-collector
     Active: active (running) since Mon 2026-03-15 10:00:00 UTC; 2h 30min ago
```

预期输出（timer 正常状态）：
```
● evaluate-predictions.timer - Run prediction evaluator every 6 hours
     Active: active (waiting) since Mon 2026-03-15 06:06:08 UTC; 5h ago
    Trigger: Mon 2026-03-15 18:06:08 UTC; 35min left
```

**输出说明（Output explanation）：**
- `active (running)`：服务正在运行 / Service is actively running
- `active (waiting)`：timer 在等待下次触发 / Timer is waiting for next trigger
- `Trigger: 18:06:08 UTC`：下次触发时间 / Next trigger time

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
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --data-dir ~/ubuntu-wallet/data \
  --interval 1h \
  --active-model event_v3 \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --horizon-bars 6
deactivate
```

## 5.5 跑模拟交易
```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/scripts/live_trader_eth_perp_simulated.py
deactivate
```

## 5.6 跑 walk-forward
```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/python-analyzer/walkforward_cv.py \
  --data-dir ~/ubuntu-wallet/data \
  --n-splits 5 \
  --gap-bars 12 \
  --label-method ternary \
  --confidence-threshold 0.65 \
  --output-csv /tmp/cv_report.csv
deactivate
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
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool
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
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool | grep -A5 '"flags"'
```

## 16.3 临时启用 Flag

```bash
# 编辑 EnvironmentFile
sudo nano /etc/ubuntu-wallet/ml-service.env
# 修改 ENABLE_DRIFT_MONITOR=true
sudo systemctl restart ml-service
```

## 16.4 手工运行 Drift Monitor

```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
ENABLE_DRIFT_MONITOR=true python ~/ubuntu-wallet/scripts/report_drift.py \
  --train-stats ~/ubuntu-wallet/models/current/train_feature_stats.json \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --output-dir ~/ubuntu-wallet/data/reports \
  --dry-run
```

## 16.5 手工运行 Calibration Report

```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
ENABLE_CALIB_REPORT=true python ~/ubuntu-wallet/python-analyzer/calibration_report.py \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --output-dir ~/ubuntu-wallet/data/reports \
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
| XRPUSDT | Phase 2 | ❌ |
| DOGEUSDT | Phase 2 | ❌ |
| ADAUSDT | Phase 2 | ❌ |

**激活 Phase 2 币种**：编辑 `configs/symbols.yaml`，将对应 `enabled: false` 改为 `enabled: true`，然后完成数据准备与初次训练后即可上线。

## 17.2 每币种目录布局

```
data/
  BTCUSDT/
    klines_1h.json    klines_4h.json    klines_1d.json
    predictions_log.jsonl
    reports/
  ETHUSDT/  ...

models/
  BTCUSDT/
    current/           <- 活跃模型产物
    archive/           <- 版本归档
    registry.json
  ETHUSDT/  ...
```

## 17.3 查看某币种当前模型

```bash
SYMBOL=BTCUSDT
cat ~/ubuntu-wallet/models/${SYMBOL}/current/model_meta.json | python3 -m json.tool
```

## 17.4 按币种训练

```bash
# 方式一：使用便捷包装脚本（自动读取 configs/symbols.yaml 参数）
bash ~/ubuntu-wallet/scripts/train_symbol.sh BTCUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh ETHUSDT

# 方式二：手工指定路径（完整控制）
SYMBOL=BTCUSDT
python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir  ~/ubuntu-wallet/data/${SYMBOL} \
  --model-dir ~/ubuntu-wallet/models/${SYMBOL} \
  --horizon 12 --tp-pct 0.0175 --sl-pct 0.009 --calibration isotonic
```

## 17.5 按币种评估预测日志

```bash
# 自动从 configs/symbols.yaml 派生路径和参数
SYMBOL=BTCUSDT
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py --symbol ${SYMBOL}

# 也可手工覆盖单个参数
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --symbol ${SYMBOL} --threshold 0.68 --tp 0.020
```

## 17.6 按币种 Drift 监控

```bash
SYMBOL=BTCUSDT
ENABLE_DRIFT_MONITOR=true python ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol ${SYMBOL}
# 等价于：
#   --train-stats ~/ubuntu-wallet/models/${SYMBOL}/current/train_feature_stats.json
#   --log-path    ~/ubuntu-wallet/data/${SYMBOL}/predictions_log.jsonl
#   --output-dir  ~/ubuntu-wallet/data/${SYMBOL}/reports
```

## 17.7 批量操作所有启用币种

```bash
# 列出所有 enabled 币种
python3 -c "
import sys; sys.path.insert(0,'~/ubuntu-wallet/scripts')
from symbol_paths import list_enabled_symbols
print(list_enabled_symbols())
"

# 批量训练所有 Phase 1 币种
for SYMBOL in BTCUSDT ETHUSDT SOLUSDT BNBUSDT; do
  echo "=== Training ${SYMBOL} ==="
  bash ~/ubuntu-wallet/scripts/train_symbol.sh "${SYMBOL}"
done

# 批量 drift 检查
for SYMBOL in BTCUSDT ETHUSDT SOLUSDT BNBUSDT; do
  ENABLE_DRIFT_MONITOR=true python ~/ubuntu-wallet/scripts/report_drift.py \
    --symbol "${SYMBOL}"
done
```

## 17.8 启用/禁用某币种

1. 编辑 `~/ubuntu-wallet/configs/symbols.yaml`
2. 修改目标币种的 `enabled: true/false`
3. 如果禁用，对应 systemd 服务/计时器也应相应暂停或移除

## 17.9 向后兼容说明

旧式单币种路径（`data/klines_1h.json`、`models/current/`）仍然有效。
如果只运行一个币种且不希望改变目录结构，可继续沿用旧路径，仅在需要多币种时迁移。

