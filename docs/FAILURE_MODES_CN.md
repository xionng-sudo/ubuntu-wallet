# ubuntu-wallet 故障模式与恢复手册（Failure Modes & Recovery）

> 本文档目标：
> - 明确系统常见故障模式
> - 提供一线排查与恢复步骤
> - 降低“系统出问题只能靠猜”的风险
>
> 本文档覆盖：
> - Go 采集层
> - Python 推理层
> - 训练与评估层
> - 数据层
> - 模型层
> - 模拟/DRY-RUN 层
> - systemd 调度层

---

# 目录

1. [故障处理总原则](#1-故障处理总原则)
2. [故障分级](#2-故障分级)
3. [采集层故障](#3-采集层故障)
4. [数据层故障](#4-数据层故障)
5. [模型与特征层故障](#5-模型与特征层故障)
6. [在线推理层故障](#6-在线推理层故障)
7. [日志与评估层故障](#7-日志与评估层故障)
8. [模拟交易与 DRY-RUN 层故障](#8-模拟交易与-dry-run-层故障)
9. [systemd 与部署层故障](#9-systemd-与部署层故障)
10. [恢复流程模板](#10-恢复流程模板)
11. [停机与降级策略](#11-停机与降级策略)
12. [事故复盘建议](#12-事故复盘建议)

---

# 1. 故障处理总原则

在 `ubuntu-wallet` 中，排障不要一上来就改代码。先按以下顺序判断：

1. **数据是否正常**
2. **服务是否正常**
3. **模型是否正常**
4. **日志是否正常**
5. **评估是否正常**
6. **策略逻辑是否正常**

一个很常见的误区是：
- 看到结果变差，就急着怀疑模型不行；
- 但实际问题可能是 collector 断了、K 线错位了、schema 漂移了。

---

# 2. 故障分级

## P0：必须立刻处理
影响：
- 数据不再更新
- `/predict` 不可用
- prediction log 不写
- 真仓存在误下单风险
- 风控失效

处理原则：
- 立即降级或停机
- 优先保护系统与资金

## P1：高优先级
影响：
- 覆盖率异常
- 模型概率明显漂移
- 评估脚本持续报错
- 定时任务失效

处理原则：
- 当天处理
- 必要时先切回上个稳定版本

## P2：中优先级
影响：
- 文档缺失
- 报告缺字段
- 某些可视化结果异常

处理原则：
- 计划修复
- 不一定影响即时运行

---

# 3. 采集层故障

# 3.1 故障：go-collector 进程不存在或退出

## 现象
- `systemctl status go-collector` 显示 failed / inactive
- 数据文件长时间不更新

## 可能原因
- 程序 panic
- 环境变量丢失
- 网络失败
- API 限流或认证错误
- 路径权限不足

## 排查步骤
```bash
systemctl status go-collector --no-pager
journalctl -u go-collector -n 200 --no-pager

# 也可检查 go-collector 的 HTTP healthz 端点（端口 8080）
curl -s --max-time 3 http://127.0.0.1:8080/api/healthz | python3 -m json.tool
```

**go-collector `/api/healthz` 正常响应示例（Normal response example）：**
```json
{
    "ok": true,
    "staleness_sec": 45,
    "files": {
        "klines_1h": {"exists": true, "last_modified": "2026-03-15T10:00:00Z"},
        "klines_4h": {"exists": true, "last_modified": "2026-03-15T08:00:00Z"},
        "klines_1d": {"exists": true, "last_modified": "2026-03-15T00:00:00Z"}
    }
}
```

**字段说明（Field explanation）：**
- `ok: true`：采集器状态正常 / Collector is healthy
- `staleness_sec: 45`：数据距离现在 45 秒前更新，正常 / Data was updated 45 seconds ago, normal
- `staleness_sec` 很大（如 > 3600）：数据很久没有更新，需要检查

## 处理方法
1. 修复配置或网络问题
2. 手工执行二进制检查
3. 确认数据目录权限
4. 重新启动：
```bash
sudo systemctl restart go-collector
```

## 恢复后验证
- 数据文件是否恢复更新
- 最新时间戳是否补齐
- 日志中是否不再报错

---

# 3.2 故障：collector 还活着，但数据不更新

## 现象
- systemd 状态正常
- 但 `klines_1h.json` 等文件修改时间不变

## 可能原因
- 程序进入死循环
- API 返回空结果
- 写文件逻辑失效
- 内部线程卡死

## 排查步骤
- 查看 collector 日志中是否持续有请求与写入记录
- 对比文件最新时间戳与当前时间
- 检查磁盘空间

## 临时处理
- 重启 collector
- 若重启恢复，需记录为潜在稳定性问题
- 若反复发生，应追查具体逻辑分支

---

# 3.3 故障：collector 频繁重启

## 现象
- `systemctl status` 显示 restart counter 很高

## 可能原因
- 配置错误
- 某条数据导致 panic
- systemd Restart 配置过激
- 网络超时后程序未优雅处理

## 处理方法
- 先停止自动重启，手工运行查看首个报错
- 修复后再恢复服务
- 若问题与外部接口稳定性有关，加入重试/熔断逻辑

---

# 4. 数据层故障

# 4.1 故障：K 线文件存在，但有明显断档

## 现象
- 时间戳不连续
- 某段历史缺失

## 影响
- 特征构建失真
- 标签构建失真
- 回测与评估结果不可信

## 排查方法
- 写脚本检查时间差
- 对比交易所实际历史数据
- 检查 collector 日志对应时段

## 处理方法
- 补采缺失段
- 补采后重新生成衍生数据
- 若缺失段发生在线上模型使用期间，谨慎解释该时间段评估结果

---

# 4.2 故障：1h / 4h / 1d 时间对齐错误

## 现象
- 多周期过滤结果异常
- trend_4h / trend_1d 与实际不符
- 模拟交易结果不合理

## 影响
- 多周期过滤失真
- 训练与推理逻辑不一致

## 排查方法
- 检查各周期文件的时间戳边界
- 确认 4h / 1d 是否以正确的 bar 结束时间对应 1h 数据

## 处理方法
- 明确统一时区与 bar close 规则
- 修改聚合逻��或读取逻辑
- 补充单元测试或数据校验脚本

---

# 4.3 故障：数据文件被写坏或出现畸形 JSON

## 现象
- Python 读取 JSON 报错
- 只写入了半截内容

## 可能原因
- 程序中断
- 多进程竞争写
- 磁盘/IO 异常

## 处理方法
- 备份损坏文件
- 恢复到最近可用备份
- 重新采集或重新导出
- 如果是日志文件，考虑分段恢复可解析部分

---

# 5. 模型与特征层故障

# 5.1 故障：模型文件存在，但无法加载

## 现象
- ml-service 启动时报模型加载错误
- `/healthz` 异常
- `/predict` 无法工作

## 可能原因
- 模型文件损坏
- 路径错误
- 训练产物与线上加载逻辑不兼容
- 依赖版本不匹配

## 排查方法
- 查看 `journalctl -u ml-service`
- 手工用 Python 载入模型测试
- 检查模型目录结构

## 处理方法
- 切回上一个稳定模型
- 重新训练或重新导出模型
- 确认 Python 包版本一致

---

# 5.2 故障：calibration artifact 缺失或损坏

## 现象
- `/healthz` 中 `calibration_available=false`
- 或推理时校准流程报错

## 影响
- 系统退化为 raw probability
- 阈值逻辑可能与预期不一致

## 处理方法
- 如果服务支持降级，可先降级继续运行
- 尽快恢复配套 calibration artifact
- 确认 model 与 calibrator 是否同版本生成

---

# 5.3 故障：feature schema 漂移

## 现象
- 推理日志中 schema warning 激增
- 预测结果突然异常
- coverage / precision 明显变化

## 可能原因
- 训练与线上特征列不一致
- 数据字段变更
- 某些特征不再生成
- 特征顺序变化

## 处理方法
1. 停止认为“这是市场变了”
2. 先确认 schema 是否一致
3. 对比训练侧 `feature_columns_event_v3.json` 与线上实际特征列
4. 必要时回滚到上一个稳定模型/代码版本

## 恢复后验证
- schema warning 下降
- `/predict` 输出恢复合理
- 日志字段正常

---

# 5.4 故障：模型输出概率分布异常

## 现象
- 几乎永远输出接近 0.5
- 或几乎永远高于 0.9
- 或几乎没有 FLAT

## 可能原因
- 特征异常
- calibration 失真
- 训练/推理不一致
- 模型文件不对应

## 排查方法
- 抽样对比 raw probability 与 calibrated probability
- 对比旧模型与当前模型同一输入输出
- 检查最近 feature drift

---

# 6. 在线推理层故障

# 6.1 故障：ml-service 起不来

## 现象
- `systemctl status ml-service` failed
- 端口不监听

## 常见原因
- 依赖缺失
- 路径错误
- 模型加载失败
- 端口冲突

## 排查命令
```bash
systemctl status ml-service --no-pager
journalctl -u ml-service -n 200 --no-pager
# 检查端口 9000 是否被占用
ss -ltnp | grep 9000
```

**`ss -ltnp | grep 9000` 输出说明（Output explanation）：**
- 有输出（如 `LISTEN 0 128 127.0.0.1:9000`）：端口已被占用，可能是服务已运行
- 无输出：端口未监听，ml-service 未成功启动

## 处理方法
- 修正 ExecStart 路径（查看服务文件 `/etc/systemd/system/ml-service.service`）
- 激活正确 venv（应使用 `ml-service/.venv/`，不是系统 Python）
- 修复模型或配置
- 修改端口冲突（杀掉占用 9000 端口的进程）

---

# 6.2 故障：/healthz 正常，但 /predict 报错

## 现象
- 服务在线
- 但推理失败

## 排查命令

> **注意（Note）**：ml-service 端口为 **9000**，不是 8000。

```bash
# 确认 /healthz 正常
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# 构造最小测试请求
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ETHUSDT", "interval": "1h"}' | python3 -m json.tool

# 查看详细错误
journalctl -u ml-service -n 100 --no-pager
```

## 常见原因
- 输入 payload 错误
- feature_builder 异常
- 某些外部数据缺失（klines 文件不存在）
- schema 校验未通过

## 处理方法
- 构造最小测试请求逐步排除
- 查看 ml-service 日志中的堆栈信息
- 检查 `data/raw/klines_*.json` 是否存在且内容正常
- 确认 `MODEL_DIR` 和 `DATA_DIR` 环境变量指向正确目录

---

# 6.3 故障：推理成功，但 prediction log 不写

## 现象
- `/predict` 返回正常
- 日志文件没有新增

## 常见原因
- logger 异常被吞掉
- 路径无权限
- 文件锁/IO 问题

## 处理方法
- 检查日志目录权限
- 在服务日志中查 logger warning/error
- 必要时临时改到可写路径验证

---

# 7. 日志与评估层故障

# 7.1 故障：evaluate_from_logs.py 无结果或 trade=0

## 现象
- 日志很多，但评估显示没有有效交易

## 可能原因
- threshold 太高
- 时间对齐错误
- 信号字段与评估逻辑不一致
- horizon/TP/SL 参数与记录不匹配

## 排查方法
- 用更低 threshold 对照测试
- 抽几条日志手工核对后续 K 线
- 确认使用的是 calibrated probability 而不是错误字段

---

# 7.2 故障：评估结果过于完美，不可信

## 现象
- 胜率异常高
- 没有止损
- MDD 为 0 且样本不少

## 可能原因
- 标签泄漏
- 时间错位
- 评估使用了未来数据
- 阈值基于结果反推

## 处理方法
- 人工抽样几笔交易逐条核对
- 检查 walk-forward gap
- 检查 evaluate_from_logs 使用的时间窗口

---

# 7.3 故障：evaluation timer 不触发

## 现象
- `evaluate-predictions.timer` 存在
- 但对应 service 没有运行记录

## 排查命令
```bash
systemctl status evaluate-predictions.timer
systemctl list-timers | grep evaluate
journalctl -u evaluate-predictions.service -n 200 --no-pager
```

## 可能原因
- timer 未 enable/start
- OnCalendar 配置问题
- service ExecStart 路径错误

## 处理方法
- 重新 `daemon-reload`
- `enable` + `start` timer
- 手工执行 service 对应命令验证

---

# 8. 模拟交易与 DRY-RUN 层故障

# 8.1 故障：simulated trader 无法跑通

## 常见原因
- `/predict` 不可用
- 数据不完整
- 多周期文件缺失
- 风控配置错误

## 排查顺序
1. 先确认 ml-service 正常
2. 再确认 1h/4h/1d 数据齐全
3. 再看模拟脚本本身日志

---

# 8.2 故障：模拟交易结果极差或极怪

## 现象
- 全部 TIMEOUT
- 几乎不交易
- 连续大量开仓
- 资金曲线与回测完全不一致

## 可能原因
- 多周期过滤条件写错
- TP/SL/horizon 参数不一致
- 评估与模拟使用的阈值不同
- `/predict` 输入与回测输入不一致

## 处理方法
- 固定一小段历史，手工逐条核对
- 比较回测脚本与模拟脚本对相同 bar 的决策

---

# 8.3 故障：DRY-RUN 重复触发相同方向信号

## 可能原因
- 去重逻辑不足
- 同一 bar 重复处理
- position state 管理异常

## 处理方法
- 检查是否有“已开仓不再开同向仓”逻辑
- 检查 bar 时间是否重复消费

---

# 9. systemd 与部署层故障

# 9.1 故障：service 文件修改后不生效

## 原因
忘记 reload。

## 处理方法
```bash
sudo systemctl daemon-reload
sudo systemctl restart ml-service
```

---

# 9.2 故障：systemd 启动时找不到 Python 或脚本

## 原因
ExecStart 写的是相对路径，或 venv 路径错误。

## 处理方法
- 在 service 文件中使用绝对路径
- 明确指定 venv 中的 Python

---

# 9.3 故障：权限问题导致服务异常

## 现象
- 能手工运行
- systemd 运行失败

## 原因
systemd 运行用户和手工用户不同。

## 处理方法
- 明确 service 的 `User=`
- 给该用户所需目录权限
- 检查 `.env` 和数据目录权限

---

# 10. 恢复流程模板

下面是一套标准恢复模板，建议每次事故都按这个顺序来。

## Step 1：先判断故障级别
- 是否影响真实交易？
- 是否影响数据采集？
- 是否影响推理？
- 是否影响评估？

## Step 2：确定故障层级
先定位是：
- 采集层
- 数据层
- 模型层
- 推理层
- 日志层
- 评估层
- 执行层

## Step 3：优先降级保护
如果无法快速确认系统正确性：
- 停掉真仓
- 停掉 DRY-RUN 下单部分
- 保留数据采集与日志

## Step 4：恢复最小可用能力
优先恢复顺序建议：
1. 数据采集
2. ml-service
3. prediction log
4. evaluation
5. 模拟/执行

## Step 5：验证恢复效果
至少验证：
- 服务状态
- `/healthz`
- 数据更新
- 日志写入
- 一条人工测试预测

---

# 11. 停机与降级策略

以下场景建议立即停机或降级：

## 11.1 立即停机
- 数据明显错位
- 线上特征 schema 严重漂移
- 连续推理报错
- 风控壳失效
- 真仓环境下重复下单风险存在

## 11.2 降级运行
可保留：
- collector
- ml-service
- prediction log

暂时停掉：
- 真仓执行
- 自动下单
- 高风险脚本

## 11.3 安全恢复
恢复时要逐层恢复，不要一下全开：
1. collector
2. ml-service
3. 日志
4. 评估
5. simulated
6. DRY-RUN
7. 真仓（最后）

---

# 12. 事故复盘建议

每次出现 P0 / P1 事故后，建议记录：

## 12.1 基础信息
- 发生时间
- 发现时间
- 恢复时间
- 影响范围

## 12.2 故障描述
- 现象是什么
- 哪个层级出问题
- 哪些模块受影响

## 12.3 根因
- 配置错误
- 代码错误
- 数据错误
- 外部接口异常
- 运维误操作

## 12.4 处理过程
- 看了哪些日志
- 做了哪些临时修复
- 最终怎么恢复

## 12.5 后续改进
- 是否要补测试
- 是否要补文档
- 是否要加告警
- 是否要改部署方式

---

# 附录：建议优先增加的故障防护

建议未来在第二阶段增强中补齐：

1. feature drift 监控
2. prediction log 轮转与校验
3. current production model registry
4. 自动 promote / rollback
5. daily report + anomaly detection
6. collector 心跳监控
7. ml-service 基础 metrics 导出

---

# 附录 B：端口与关键路径速查

| 服务           | 端口  | 关键路径                                          |
|----------------|-------|---------------------------------------------------|
| ml-service     | 9000  | `http://127.0.0.1:9000/healthz`                  |
| go-collector   | 8080  | `http://127.0.0.1:8080/api/healthz`              |
| venv (ml-service) | -  | `~/ubuntu-wallet/ml-service/.venv/`              |
| venv (analyzer)   | -  | `~/ubuntu-wallet/venv-analyzer/`                 |
| 敏感配置目录    | -     | `/etc/ubuntu-wallet/`                            |
| 评估日志        | -     | `~/ubuntu-wallet/logs/evaluate_predictions.log`  |
| 健康检查日志    | -     | `~/ubuntu-wallet/check-go-collector.log`         |
| systemd 日志查看 | -   | `journalctl -u <service-name> -n 200 --no-pager` |

---

# 13. 外生特征 / 漂移监控 / 校准报告 故障模式

## 13.1 外生特征采集失败（ENABLE_EXOG_FEATURES=true）

### 故障现象
- go-collector 日志：`exog: collect failed (non-fatal): ...`
- `data/raw/exog_ETHUSDT.jsonl` 不更新或不存在

### 可能原因
| 原因 | 说明 |
|------|------|
| Binance Futures API 不可用 | fapi.binance.com 连接超时或返回非200 |
| IP 被限频（429） | 请求过于频繁 |
| 网络问题 | 服务器出口 IP 无法访问 Binance |

### 处理步骤
1. 检查日志：`journalctl -u go-collector -n 100 --no-pager | grep exog`
2. 手动测试 API：`curl "https://fapi.binance.com/fapi/v1/openInterest?symbol=ETHUSDT"`
3. 外生特征失败为**非致命错误**，不影响主流程。若持续失败可将 `ENABLE_EXOG_FEATURES=false` 关闭。
4. 若 `exog_ETHUSDT.jsonl` 文件存在但太旧，`load_exog_features` 仍会返回最后一条数据（非0）。

### 降级策略
- 关闭 `ENABLE_EXOG_FEATURES`：外生特征返回全0，不影响推理
- 外生特征在训练 schema 缺失时也会被 zero-fill，安全降级

---

## 13.2 漂移监控报错或无法运行

### 故障现象
- `journalctl -u drift-monitor.service -n 50` 显示错误
- `data/reports/drift_*.json` 未生成

### 可能原因
| 原因 | 诊断 |
|------|------|
| `ENABLE_DRIFT_MONITOR=false` | 脚本会打印 "skipping." 并退出0 |
| `train_feature_stats.json` 不存在 | 未运行训练或文件未生成 |
| `predictions_log.jsonl` 不存在 | 尚无预测记录 |
| venv 未激活 / 依赖缺失 | 检查 ExecStart 路径 |

### 处理步骤
1. 手动验证：
   ```bash
   ENABLE_DRIFT_MONITOR=true python scripts/report_drift.py \
     --train-stats data/models/current/train_feature_stats.json \
     --log-path data/predictions_log.jsonl \
     --output-dir data/reports \
     --dry-run
   ```
2. 确认 `train_feature_stats.json` 存在（由训练脚本生成）
3. 若 predictions_log 为空，drift monitor 运行但没有有意义的结果，属正常

### 影响评估
- Drift monitor 为**辅助监控**，不影响推理服务
- 可以安全设置 `ENABLE_DRIFT_MONITOR=false` 跳过

---

## 13.3 校准报告故障

### 故障现象
- `journalctl -u calibration-report.service -n 50` 显示错误
- `data/reports/calib_report_*.json` 未生成

### 可能原因
| 原因 | 诊断 |
|------|------|
| `ENABLE_CALIB_REPORT=false` | 脚本打印 "skipping." 并退出0 |
| `predictions_log.jsonl` 为空 | 无预测记录 |
| 无 `outcome` 字段 | 缺少实际结果数据，reliability curve 无法绘制（仍会输出置信度分布报告）|
| matplotlib 未安装 | PNG 图不生成，JSON/MD 仍正常输出 |

### 处理步骤
1. 手动测试：
   ```bash
   ENABLE_CALIB_REPORT=true python python-analyzer/calibration_report.py \
     --log-path data/predictions_log.jsonl \
     --output-dir data/reports \
     --dry-run
   ```
2. 无 `outcome` 字段属预期情况（实盘日志不包含已知结果）；报告会输出置信度分布统计
3. 如不需要 PNG 图，添加 `--no-plot` 参数

### 影响评估
- 校准报告为**分析工具**，不影响推理服务
- 可安全设置 `ENABLE_CALIB_REPORT=false`
