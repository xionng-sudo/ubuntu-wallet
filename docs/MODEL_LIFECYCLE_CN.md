# ubuntu-wallet 模型生命周期文档

> 本文档目标：
> - 完整说明模型从数据准备到最终退役的每一个阶段
> - 让每个阶段都有可执行的命令、可检查的验收标准
> - 避免模型上线/回滚/退役时依靠口头传承
>
> 适用对象：
> - 模型训练工程师
> - 模型运维工程师
> - 系统接手人员

---

# 目录

1. [模型生命周期总览](#1-模型生命周期总览)
2. [阶段一：数据准备](#2-阶段一数据准备)
3. [阶段二：特征工程](#3-阶段二特征工程)
4. [阶段三：模型训练](#4-阶段三模型训练)
5. [阶段四：Walk-Forward 交叉验证](#5-阶段四walk-forward-交叉验证)
6. [阶段五：概率校准（Calibration）](#6-阶段五概率校准calibration)
7. [阶段六：候选模型评估](#7-阶段六候选模型评估)
8. [阶段七：模型晋升为生产（Promotion）](#8-阶段七模型晋升为生产promotion)
9. [阶段八：生产期间持续监控](#9-阶段八生产期间持续监控)
10. [阶段九：模型回滚](#10-阶段九模型回滚)
11. [阶段十：模型退役（Retirement）](#11-阶段十模型退役retirement)
12. [模型文件与目录规范](#12-模型文件与目录规范)
13. [model_meta.json 字段说明](#13-model_metajson-字段说明)
14. [生命周期检查清单](#14-生命周期检查清单)
15. [常见问题](#15-常见问题)

---

# 1. 模型生命周期总览

`ubuntu-wallet` 中的模型生命周期分为以下阶段：

```
数据准备
  ↓
特征工程
  ↓
模型训练（train_event_stack_v3.py）
  ↓
Walk-Forward 交叉验证（walkforward_cv.py）
  ↓
概率校准（Calibration）
  ↓
候选模型评估（evaluate_from_logs.py / backtest）
  ↓
模型晋升为生产（promotion）
  ↓
生产期间持续监控（evaluate-predictions.timer）
  ↓
回滚（rollback）或退役（retirement）
```

每个模型在文件系统层面有明确的状态：

| 状态        | 英文         | 说明                             |
|-------------|--------------|----------------------------------|
| 候选        | candidate    | 训练完成，但未在生产环境运行     |
| 生产        | production   | 当前线上推理使用的模型           |
| 归档        | archived     | 已被替换，但保留备份             |
| 退役        | retired      | 明确不再使用，可安全清除         |

---

# 2. 阶段一：数据准备

## 2.1 数据来源

系统支持三个交易所的数据采集：
- **Binance**（主数据源）
- **OKX**
- **Coinbase**

采集由 `go-collector` 负责，采集到的原始数据存放在：

```
~/ubuntu-wallet/data/raw/
├── klines_1h.json      # 1小时 K线数据 / 1-hour candlestick data
├── klines_4h.json      # 4小时 K线数据 / 4-hour candlestick data
└── klines_1d.json      # 日线 K线数据 / daily candlestick data
```

## 2.2 检查数据是否完整

在训练前，必须确认数据完整性：

```bash
# 查看各文件最后修改时间
ls -lh ~/ubuntu-wallet/data/raw/klines_*.json

# 预期输出（Expected output）示例：
# -rw-rw-r-- 1 ubuntu ubuntu 45M Mar 15 10:30 klines_1d.json
# -rw-rw-r-- 1 ubuntu ubuntu 120M Mar 15 10:28 klines_4h.json
# -rw-rw-r-- 1 ubuntu ubuntu 480M Mar 15 10:25 klines_1h.json
#
# 说明（Explanation）：
# 45M / 120M / 480M 表示文件大小，越大说明历史越长
# 时间戳应接近当前时间（< 2小时前）
```

```bash
# 检查文件内容是否可以被正常解析（JSON格式）
python3 -c "
import json
for f in ['klines_1h', 'klines_4h', 'klines_1d']:
    data = json.load(open(f'data/raw/{f}.json'))
    print(f'{f}: {len(data)} 条记录 / {len(data)} records')
"
```

预期输出（Expected output）：
```
klines_1h: 8760 条记录 / 8760 records    # 约1年的1h数据
klines_4h: 2190 条记录 / 2190 records    # 约1年的4h数据
klines_1d: 365 条记录 / 365 records      # 约1年的日线数据
```

## 2.3 检查时间戳连续性

```bash
python3 -c "
import json
from datetime import datetime

data = json.load(open('data/raw/klines_1h.json'))
timestamps = [item['time'] if isinstance(item, dict) else item[0] for item in data]
print(f'最早时间 (oldest): {timestamps[0]}')
print(f'最新时间 (latest): {timestamps[-1]}')
print(f'总条数 (total records): {len(timestamps)}')
"
```

## 2.4 数据不完整时的处理

- **数据断档超过 24 小时**：建议先修复数据再训练
- **修复方法**：确保 `go-collector` 正常运行，或手动补采缺失段
- **时区统一**：所有时间戳应为 UTC

---

# 3. 阶段二：特征工程

## 3.1 特征体系说明

`ubuntu-wallet` 使用三个时间周期的特征：

| 周期   | 角色           | 说明                           |
|--------|----------------|--------------------------------|
| 1h     | 主周期         | 主要特征来源，训练标签基于此  |
| 4h     | 中期趋势过滤器 | 用于过滤信号方向               |
| 1d     | 长期趋势过滤器 | 避免逆大趋势方向入场           |

特征包括（来自 `go-collector/features/` 和 `ml-service/feature_builder.py`）：

- 技术指标：RSI、MACD、Bollinger Bands、ATR、EMA 等
- 价格特征：open/high/low/close/volume 及其派生值
- 多周期趋势特征：4h trend、1d trend、多时间框架对齐

## 3.2 特征 Schema 管理

训练时会生成 `feature_schema.json`，记录模型期望的特征列表和顺序。

**重要**：训练侧和推理侧（ml-service）必须使用完全相同的 feature schema。

```bash
# 查看训练生成的 feature schema
cat ~/ubuntu-wallet/data/models/current/feature_schema.json | python3 -m json.tool | head -50

# 预期输出（Expected output）示例：
# {
#     "version": "event_v3",
#     "n_features": 120,
#     "feature_names": [
#         "close_1h",
#         "rsi_14_1h",
#         "macd_1h",
#         ...
#     ]
# }
```

## 3.3 特征验证

在训练前，建议用数据验证特征构建：

```bash
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate

python3 -c "
import sys
sys.path.insert(0, 'ml-service')
from feature_builder import build_event_v3_feature_row
result = build_event_v3_feature_row(data_dir='data', expected_n_features=None)
print(f'特征数量 (feature count): {result.X_row.shape[1]}')
print(f'特征时间戳 (feature_ts): {result.feature_ts}')
print(f'前5个特征值 (first 5 values): {result.X_row[0, :5]}')
"

deactivate
```

---

# 4. 阶段三：模型训练

## 4.1 训练脚本

主要训练脚本：`python-analyzer/train_event_stack_v3.py`

模型类型：event_v3（三分类堆叠模型）
- 基础模型：LightGBM + XGBoost
- 元模型（meta learner）：LogisticRegression
- 输出：3类概率（LONG / FLAT / SHORT）

## 4.2 准备训练环境

```bash
cd ~/ubuntu-wallet

# 如果还没有 venv-analyzer，创建一个
python3 -m venv venv-analyzer
source venv-analyzer/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r python-analyzer/requirements.txt
deactivate
```

> 说明：训练使用独立的 `venv-analyzer`，推理服务使用 `ml-service/.venv`，两者分开管理。

## 4.3 执行训练

```bash
cd ~/ubuntu-wallet
source venv-analyzer/bin/activate

python python-analyzer/train_event_stack_v3.py \
  --label-method triple_barrier \
  --tp-pct 0.0175 \
  --sl-pct 0.009 \
  --calibration isotonic

deactivate
```

### 参数说明

| 参数             | 含义                             | 推荐值         |
|------------------|----------------------------------|----------------|
| `--label-method` | 标签生成方法                     | `triple_barrier` |
| `--tp-pct`       | 止盈百分比（Take Profit）        | `0.0175`（1.75%）|
| `--sl-pct`       | 止损百分比（Stop Loss）          | `0.009`（0.9%）|
| `--calibration`  | 概率校准方法（Calibration method）| `isotonic`     |

### 训练期间输出示例（Training output example）

```
[INFO] Loading klines_1h.json: 8760 bars
[INFO] Loading klines_4h.json: 2190 bars
[INFO] Loading klines_1d.json: 365 bars
[INFO] Generating labels with triple_barrier method...
[INFO] tp_pct=0.0175 sl_pct=0.009 horizon=6 bars
[INFO] Label distribution: LONG=2847 FLAT=3012 SHORT=2901
[INFO] Training LightGBM base model...
[INFO] Training XGBoost base model...
[INFO] Training meta LogisticRegression...
[INFO] Calibrating with isotonic regression...
[INFO] Saving model to: data/models/event_v3_20260315_120000/
[INFO] Training complete. model_version=event_v3_20260315_120000
```

**输出术语解释（Output term explanation）：**
- `8760 bars`：8760条 K 线，约 1 年的小时线 / 8760 hourly candlestick records, approx. 1 year
- `triple_barrier`：三重障碍标签法，同时设置止盈/止损/时间窗口
- `Label distribution`：标签分布，应大致均衡，避免严重倾斜
- `calibrating with isotonic`：使用等温回归进行概率校准

## 4.4 训练产物

训练完成后，`data/models/` 目录下会生成新的模型目录：

```
data/models/event_v3_20260315_120000/
├── model.pkl               # 主模型文件（Main model file）
├── calibrator.pkl          # 校准器文件（Calibration artifact）
├── feature_schema.json     # 特征 schema（Feature schema）
└── model_meta.json         # 模型元数据（Model metadata）
```

## 4.5 查看训练结果

```bash
cat ~/ubuntu-wallet/data/models/event_v3_20260315_120000/model_meta.json | python3 -m json.tool

# 预期输出（Expected output）：
# {
#     "model_version": "event_v3_20260315_120000",
#     "active_model": "event_v3",
#     "train_period": {
#         "start": "2025-01-01",
#         "end": "2026-03-15"
#     },
#     "label_config": {
#         "method": "triple_barrier",
#         "tp_pct": 0.0175,
#         "sl_pct": 0.009,
#         "horizon": 6
#     },
#     "calibration_method": "isotonic",
#     "n_features": 120,
#     "created_at": "2026-03-15T12:00:00Z",
#     "status": "candidate"
# }
```

---

# 5. 阶段四：Walk-Forward 交叉验证

## 5.1 为什么需要 Walk-Forward

普通交叉验证（k-fold）会导致未来数据泄漏（data leakage）。Walk-Forward 验证确保：
- 每个验证集都在对应训练集的**时间之后**（Time-sequential split）
- 有 gap（间隔）防止标签泄漏
- 结果更接近真实上线后的表现

## 5.2 执行 Walk-Forward 验证

```bash
cd ~/ubuntu-wallet
source venv-analyzer/bin/activate

python python-analyzer/walkforward_cv.py \
  --data-dir ~/ubuntu-wallet/data \
  --n-splits 5 \
  --gap-bars 12 \
  --label-method ternary \
  --confidence-threshold 0.65 \
  --output-csv /tmp/cv_report.csv

deactivate
```

### 参数说明

| 参数                    | 含义                                     | 推荐值  |
|-------------------------|------------------------------------------|---------|
| `--n-splits`            | 折数（Number of folds）                  | `5`     |
| `--gap-bars`            | 训练集与验证集之间的间隔 K 线数          | `12`    |
| `--label-method`        | 标签方法                                 | `ternary` |
| `--confidence-threshold`| 评估时的置信度阈值                       | `0.65`  |
| `--output-csv`          | 结果输出路径                             | `/tmp/cv_report.csv` |

### Walk-Forward 输出示例（Output example）

```
Fold 1/5:
  Train: 2024-01-01 ~ 2024-09-01 (6144 bars)
  Gap:   12 bars (12 hours)
  Test:  2024-09-02 ~ 2025-01-01 (2928 bars)
  Precision@0.65: 0.612
  Coverage: 0.187
  Avg Return: 0.0082

Fold 2/5:
  ...

=== Walk-Forward Summary ===
Mean Precision: 0.607 ± 0.031
Mean Coverage:  0.183 ± 0.024
Mean Avg Return: 0.0079 ± 0.0012
```

**输出术语解释（Output term explanation）：**
- `Train: 6144 bars`：训练集使用了 6144 条 K 线
- `Gap: 12 bars`：训练集与验证集之间跳过 12 根 K 线（约 12 小时），防止标签泄漏
- `Precision@0.65`：置信度超过 0.65 时的精准率 / Precision for signals above confidence 0.65
- `Coverage`：高置信度信号占总 bar 的比例 / Ratio of high-confidence signals to total bars
- `Avg Return`：每笔交易的平均收益率 / Average return per trade

## 5.3 验收标准

Walk-Forward 结果用于判断模型是否值得继续推进：

| 指标                  | 推荐要求           | 不达标则暂缓上线    |
|-----------------------|--------------------|---------------------|
| Mean Precision@0.65   | > 0.58             | ✗                   |
| Mean Coverage         | 0.10 ~ 0.30        | 过高或过低均需检查  |
| Avg Return            | > 0.005            | ✗                   |
| Precision 折间方差    | < 0.06             | 稳定性不足          |

---

# 6. 阶段五：概率校准（Calibration）

## 6.1 什么是概率校准

模型输出的原始概率（raw probability）往往不是真实的置信度。
例如：模型输出 0.8，但实际命中率只有 0.62。

概率校准（Calibration）通过一个变换函数将原始概率映射到更准确的真实概率。

`ubuntu-wallet` 使用两种校准方法（在训练时由 `--calibration` 参数指定）：

| 方法       | 英文名称          | 适用场景               |
|------------|-------------------|------------------------|
| 等温回归   | `isotonic`        | 数据量较多时更准确     |
| Sigmoid    | `sigmoid`         | 数据量少时更稳定       |

## 6.2 校准产物

训练后会生成 `calibrator.pkl`：

```bash
ls -lh ~/ubuntu-wallet/data/models/event_v3_20260315_120000/

# 预期输出（Expected output）：
# total 8.2M
# -rw-r--r-- 1 ubuntu ubuntu 7.8M Mar 15 12:01 model.pkl
# -rw-r--r-- 1 ubuntu ubuntu 324K Mar 15 12:01 calibrator.pkl
# -rw-r--r-- 1 ubuntu ubuntu 12K  Mar 15 12:01 feature_schema.json
# -rw-r--r-- 1 ubuntu ubuntu 1.2K Mar 15 12:01 model_meta.json
#
# 说明（Explanation）：
# model.pkl        主模型文件，通常几兆到几十兆
# calibrator.pkl   校准器，通常几十KB到几百KB
# feature_schema   特征列表，几KB
# model_meta       元数据，通常<2KB
```

## 6.3 验证校准是否有效

```bash
cd ~/ubuntu-wallet
source venv-analyzer/bin/activate

python3 -c "
import pickle, numpy as np
model = pickle.load(open('data/models/event_v3_20260315_120000/calibrator.pkl', 'rb'))
print(f'校准器类型 (calibrator type): {type(model).__name__}')
print('校准器加载成功 (calibrator loaded successfully)')
"

deactivate
```

## 6.4 检查 ml-service 校准状态

```bash
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# 预期输出（Expected output）：
# {
#     "ok": true,
#     "model_dir": "/home/ubuntu/ubuntu-wallet/models",
#     "data_dir": "/home/ubuntu/ubuntu-wallet/data",
#     "model_version": "event_v3_20260315_120000",
#     "model_expected_n_features": 120,
#     "calibration_available": true,
#     "calibration_method": "isotonic"
# }
#
# 字段说明（Field explanation）：
# ok                   : true 表示服务和模型加载正常
# model_version        : 当前加载的模型版本（即模型目录名）
# calibration_available: true 表示校准器已加载，false 表示未找到校准器
# calibration_method   : 使用的校准方法（isotonic 或 sigmoid）
```

**注意**：如果 `calibration_available` 为 `false`，推理仍可运行，但使用的是原始概率，阈值可能不准确。

---

# 7. 阶段六：候选模型评估

## 7.1 什么是候选模型评估

候选模型（candidate model）是训练完成但尚未上线的模型。
在正式上线前，需要通过以下评估确认其质量：

1. **历史回测**（Backtest）：在已知历史数据上模拟运行
2. **Walk-Forward 验证**：如第 5 章所述
3. **与当前生产模型对比**：确认候选模型不劣于现有模型

## 7.2 对候选模型执行回测

```bash
cd ~/ubuntu-wallet
source venv-analyzer/bin/activate

python scripts/backtest_event_v3_http.py \
  --model-dir ~/ubuntu-wallet/data/models/event_v3_20260315_120000 \
  --data-dir ~/ubuntu-wallet/data \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --output-csv /tmp/backtest_candidate.csv

deactivate
```

### 参数说明

| 参数          | 含义                          | 说明                    |
|---------------|-------------------------------|-------------------------|
| `--threshold` | 信号触发阈值                  | 0.55 表示置信度超过55%时发出信号 |
| `--tp`        | 止盈百分比（Take Profit）     | 0.0175 = 1.75%          |
| `--sl`        | 止损百分比（Stop Loss）       | 0.007 = 0.7%            |
| `--fee`       | 手续费率（Trading fee）       | 0.0004 = 0.04% 单边      |

### 回测输出示例（Backtest output example）

```
=== Backtest Results ===
Total trades:    312
Coverage:        0.183 (18.3% of bars generated a signal)
Precision:       0.621 (62.1% of trades were profitable)
Win rate:        0.621
Avg return:      0.0089 per trade
Max drawdown:    -0.048 (-4.8%)
TP rate:         0.521
SL rate:         0.289
TIMEOUT rate:    0.190
LONG trades:     187
SHORT trades:    125
LONG precision:  0.634
SHORT precision: 0.601
```

**输出术语解释（Output term explanation）：**
- `Coverage 0.183`：每 100 个 bar 中约有 18.3 个 bar 产生交易信号
- `Precision 0.621`：在有信号的交易中，62.1% 盈利
- `Win rate`：胜率（与 Precision 相同）
- `Avg return`：每笔交易平均收益率 0.89%
- `Max drawdown -0.048`：最大回撤 4.8%（资金从最高点下跌的最大幅度）
- `TP rate 0.521`：52.1% 的交易达到止盈（Take Profit）
- `SL rate 0.289`：28.9% 的交易触发止损（Stop Loss）
- `TIMEOUT rate 0.190`：19.0% 的交易在 horizon 时间内未触发 TP/SL，按时间平仓

## 7.3 候选模型与生产模型对比

如果系统已有运行中的生产模型，必须对比：

```bash
# 生产模型回测
python scripts/backtest_event_v3_http.py \
  --model-dir ~/ubuntu-wallet/data/models/current \
  --data-dir ~/ubuntu-wallet/data \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --output-csv /tmp/backtest_production.csv

# 候选模型回测（同上，使用候选目录）
```

对比关注点：

| 指标           | 候选模型应满足       |
|----------------|----------------------|
| Precision      | ≥ 生产模型           |
| Avg Return     | ≥ 生产模型           |
| Max Drawdown   | ≤ 生产模型（绝对值） |
| Coverage       | 不应差异过大（±5%）  |

## 7.4 候选模型验收标准

候选模型进入生产的最低要求：

- [ ] Walk-Forward Mean Precision ≥ 0.58
- [ ] 历史回测 Precision ≥ 0.58
- [ ] 历史回测 Max Drawdown ≤ 8%
- [ ] feature_schema.json 已生成且字段正确
- [ ] calibrator.pkl 存在且可加载
- [ ] model_meta.json 已填写完整
- [ ] 手工测试 `/predict` 返回正确

---

# 8. 阶段七：模型晋升为生产（Promotion）

## 8.1 晋升前必须完成的检查

```bash
# 检查候选模型目录完整性
ls -lh ~/ubuntu-wallet/data/models/event_v3_20260315_120000/

# 确认 ml-service 当前模型版本
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# 记录当前生产模型版本（做好回滚准备）
echo "当前生产模型（Current production model）: $(cat ~/ubuntu-wallet/data/models/current/model_meta.json | python3 -c 'import sys,json; print(json.load(sys.stdin)["model_version"])')"
```

## 8.2 晋升步骤

### 步骤 1：归档当前生产模型

```bash
# 获取当前生产模型版本
CURRENT_VERSION=$(cat ~/ubuntu-wallet/data/models/current/model_meta.json | python3 -c 'import sys,json; print(json.load(sys.stdin)["model_version"])')
echo "归档当前模型（Archiving current model）: $CURRENT_VERSION"

# 将当前模型移至 archive
mkdir -p ~/ubuntu-wallet/data/models/archive
mv ~/ubuntu-wallet/data/models/current ~/ubuntu-wallet/data/models/archive/$CURRENT_VERSION
```

### 步骤 2：晋升候选模型为生产

```bash
# 新候选模型版本
NEW_VERSION="event_v3_20260315_120000"

# 复制候选模型到 current 目录
cp -r ~/ubuntu-wallet/data/models/$NEW_VERSION ~/ubuntu-wallet/data/models/current

# 更新 model_meta.json 中的 status 字段
python3 -c "
import json
meta_path = 'data/models/current/model_meta.json'
with open(meta_path) as f:
    meta = json.load(f)
meta['status'] = 'production'
meta['promoted_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open(meta_path, 'w') as f:
    json.dump(meta, f, indent=2)
print('model_meta.json 已更新 / updated')
print(json.dumps(meta, indent=2))
"
```

### 步骤 3：重启 ml-service 加载新模型

```bash
sudo systemctl restart ml-service
```

等待 5 秒后验证：

```bash
sleep 5
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool
```

预期输出（Expected output）：
```json
{
    "ok": true,
    "model_version": "event_v3_20260315_120000",
    "calibration_available": true,
    "calibration_method": "isotonic"
}
```

**验证要点（Verification points）：**
- `ok` 必须为 `true`
- `model_version` 应为新版本号
- `calibration_available` 应为 `true`（除非新模型无校准器）

### 步骤 4：发送一条测试预测请求

```bash
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ETHUSDT", "interval": "1h"}' | python3 -m json.tool

# 预期输出（Expected output）示例：
# {
#     "signal": "FLAT",
#     "confidence": 0.4823,
#     "calibrated_confidence": 0.4521,
#     "calibration_method": "isotonic",
#     "model_version": "event_v3_20260315_120000",
#     "reasons": [
#         "no_signal: p_long=0.3012 p_short=0.2155 p_flat=0.4833 threshold=0.65",
#         "feature_ts=2026-03-15T09:00:00+00:00",
#         "as_of_ts=latest"
#     ]
# }
#
# 字段说明（Field explanation）：
# signal              : LONG / SHORT / FLAT，当前信号方向
# confidence          : 模型原始置信度（0~1）
# calibrated_confidence: 校准后置信度（更可靠）
# model_version       : 确认是新模型
# reasons             : 信号决策的具体原因
```

### 步骤 5：确认 prediction log 正常写入

```bash
# 等候一段时间让 go-collector 调用 /predict
sleep 10

# 检查 log 文件
tail -3 ~/ubuntu-wallet/data/predictions_log.jsonl | python3 -m json.tool

# 预期输出（Expected output）示例：
# {
#     "ts": "2026-03-15T10:00:00+00:00",
#     "symbol": "ETHUSDT",
#     "interval": "1h",
#     "signal": "FLAT",
#     "confidence": 0.4823,
#     "calibrated_confidence": 0.4521,
#     "model_version": "event_v3_20260315_120000",
#     "active_model": "event_v3",
#     ...
# }
```

## 8.3 晋升后观察期

新模型上线后，建议至少观察 48 小时：

- [ ] `/healthz` 持续正常
- [ ] prediction log 持续写入
- [ ] 无 schema warning 爆发
- [ ] 评估 timer 正常执行
- [ ] precision 不出现急剧下滑
- [ ] LONG/SHORT 比例不严重失衡

---

# 9. 阶段八：生产期间持续监控

## 9.1 自动化评估

系统通过 systemd timer 每 6 小时自动评估一次：

```
evaluate-predictions.timer → evaluate-predictions.service → evaluate_from_logs.py
```

### 查看自动评估状态

```bash
# 查看 timer 状态
systemctl status evaluate-predictions.timer

# 预期输出（Expected output）：
# ● evaluate-predictions.timer - Run prediction evaluator every 6 hours
#      Loaded: loaded (/etc/systemd/system/evaluate-predictions.timer; enabled; vendor preset: enabled)
#      Active: active (waiting) since Mon 2026-03-15 06:06:08 UTC; 3h 58min ago
#     Trigger: Mon 2026-03-15 12:06:08 UTC; 1min 52s left
#
# 字段说明（Field explanation）：
# active (waiting)     : timer 正常等待下次触发 / Timer is active and waiting for next trigger
# Trigger: 12:06:08 UTC: 下次将在 12:06:08 UTC 触发 / Next trigger time

# 查看最近一次评估执行日志
journalctl -u evaluate-predictions.service -n 50 --no-pager

# 预期看到（Expected to see）：
# Mar 15 06:06:25 ubuntu evaluate-predictions.sh[1234]: total trades=47 precision=0.638...
```

## 9.2 查看评估日志文件

```bash
# 评估日志存储在固定位置
tail -100 ~/ubuntu-wallet/logs/evaluate_predictions.log

# 预期输出（Expected output）示例：
# [2026-03-15 06:06:25] Running evaluation...
# [2026-03-15 06:06:27] Log path: /home/ubuntu/ubuntu-wallet/data/predictions_log.jsonl
# [2026-03-15 06:06:27] Total predictions in log: 342
# [2026-03-15 06:06:28] Trades evaluated (threshold=0.55): 47
# [2026-03-15 06:06:28] Precision: 0.638
# [2026-03-15 06:06:28] Coverage: 0.184
# [2026-03-15 06:06:28] Avg return: 0.00891
# [2026-03-15 06:06:28] Max drawdown: -0.0312
# [2026-03-15 06:06:28] TP/SL/TIMEOUT: 30/14/3
# [2026-03-15 06:06:28] LONG trades: 28, precision: 0.643
# [2026-03-15 06:06:28] SHORT trades: 19, precision: 0.631
```

## 9.3 手动执行评估

```bash
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate

python scripts/evaluate_from_logs.py \
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

## 9.4 需要警惕的监控信号

下表列出需要人工介入的信号：

| 信号                       | 阈值              | 可能原因               | 建议动作         |
|----------------------------|-------------------|------------------------|------------------|
| Precision 连续下降          | 连续 7 天 < 0.52  | 市场 regime 变化       | 考虑重训         |
| Coverage 异常               | < 0.05 或 > 0.50  | 阈值问题或数据异常     | 检查数据和阈值   |
| TIMEOUT rate 激增           | > 0.50            | TP/SL 与市场不适配     | 调整参数         |
| LONG/SHORT 严重失衡         | 一方 < 5%         | 多周期过滤或数据问题   | 检查数据对齐     |
| calibration_available=false | -                 | 校准器文件丢失         | 立即检查模型目录 |

---

# 10. 阶段九：模型回滚

## 10.1 何时需要回滚

出现以下任一情况应立即回滚：

- `/predict` 返回大量错误（HTTP 5xx）
- `calibration_available` 突然变为 `false`
- precision 在 24 小时内急剧下滑（如超过 10%）
- feature schema warning 激增（说明特征不匹配）
- prediction log 停止写入

## 10.2 回滚步骤

### 步骤 1：确认归档中有可用的上一版本

```bash
ls -lh ~/ubuntu-wallet/data/models/archive/
# 应该能看到上一个生产模型版本目录
```

### 步骤 2：备份当前问题模型

```bash
PROBLEM_VERSION=$(cat ~/ubuntu-wallet/data/models/current/model_meta.json | python3 -c 'import sys,json; print(json.load(sys.stdin)["model_version"])')
echo "问题模型（Problem model）: $PROBLEM_VERSION"

# 将问题模型移至归档（标记状态）
python3 -c "
import json
meta_path = 'data/models/current/model_meta.json'
with open(meta_path) as f:
    meta = json.load(f)
meta['status'] = 'rollback_pending'
meta['rollback_reason'] = 'manual rollback at $(date -u)'
with open(meta_path, 'w') as f:
    json.dump(meta, f, indent=2)
"

mv ~/ubuntu-wallet/data/models/current ~/ubuntu-wallet/data/models/archive/$PROBLEM_VERSION
```

### 步骤 3：恢复上一版本

```bash
STABLE_VERSION="event_v3_20260301_120000"   # 替换为实际稳定版本号

cp -r ~/ubuntu-wallet/data/models/archive/$STABLE_VERSION ~/ubuntu-wallet/data/models/current

# 更新状态
python3 -c "
import json
meta_path = 'data/models/current/model_meta.json'
with open(meta_path) as f:
    meta = json.load(f)
meta['status'] = 'production'
meta['restored_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open(meta_path, 'w') as f:
    json.dump(meta, f, indent=2)
print('已恢复稳定版本 (Restored stable version):', meta['model_version'])
"
```

### 步骤 4：重启服务并验证

```bash
sudo systemctl restart ml-service
sleep 5

# 验证模型版本
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# 确认版本是稳定版本
# 确认 calibration_available: true
# 确认 ok: true
```

### 步骤 5：发送测试请求确认功能恢复

```bash
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ETHUSDT", "interval": "1h"}' | python3 -m json.tool
```

## 10.3 回滚后记录

记录此次回滚事件：
- 发生时间
- 回滚原因
- 回滚前的模型版本
- 回滚后的模型版本
- 恢复时间

建议保存到：`~/ubuntu-wallet/data/reports/rollback_history.md`

---

# 11. 阶段十：模型退役（Retirement）

## 11.1 何时退役模型

以下情况可考虑退役（彻底删除或标记为 retired）：

- 已被归档超过 6 个月
- 与当前特征 schema 完全不兼容
- 对应数据集已被删除
- 团队确认不再使用

## 11.2 退役步骤

```bash
# 标记退役状态
RETIRED_VERSION="event_v3_20250101_120000"

python3 -c "
import json
meta_path = f'data/models/archive/{RETIRED_VERSION}/model_meta.json'
with open(meta_path) as f:
    meta = json.load(f)
meta['status'] = 'retired'
meta['retired_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open(meta_path, 'w') as f:
    json.dump(meta, f, indent=2)
print('模型已退役（Model retired）:', meta['model_version'])
" 2>/dev/null || echo "注意：模型目录不存在或路径有误 (Note: model directory may not exist)"

# 可选：释放磁盘空间
# rm -rf ~/ubuntu-wallet/data/models/archive/$RETIRED_VERSION
```

**安全建议**：退役前确认该模型未被任何服务引用，再执行删除。

---

# 12. 模型文件与目录规范

## 12.1 推荐目录结构

```
~/ubuntu-wallet/data/models/
├── current/                         # 当前生产模型（Production）
│   ├── model.pkl
│   ├── calibrator.pkl
│   ├── feature_schema.json
│   └── model_meta.json
│
├── archive/                         # 历史版本（Archived）
│   ├── event_v3_20260301_120000/
│   │   ├── model.pkl
│   │   ├── calibrator.pkl
│   │   ├── feature_schema.json
│   │   └── model_meta.json
│   └── event_v3_20260215_120000/
│       └── ...
│
└── candidates/                      # 候选模型（Candidates，尚未上线）
    └── event_v3_20260315_120000/
        └── ...
```

## 12.2 命名规范

模型版本号格式：`{active_model}_{YYYYMMDD}_{HHMMSS}`

例如：`event_v3_20260315_120000`

- `event_v3`：模型类型（model type）
- `20260315`：训练完成日期（training date）
- `120000`：训练完成时间 UTC（training time UTC）

## 12.3 环境变量配置

ml-service 通过以下环境变量找到模型：

```bash
MODEL_DIR=/home/ubuntu/ubuntu-wallet/models   # 模型根目录
DATA_DIR=/home/ubuntu/ubuntu-wallet/data      # 数据根目录
```

这些变量可在 systemd 服务文件中配置，或通过 `.env` 文件管理。

---

# 13. model_meta.json 字段说明

每个模型目录下的 `model_meta.json` 文件记录模型的完整元数据：

```json
{
    "model_version": "event_v3_20260315_120000",
    "active_model": "event_v3",
    "train_period": {
        "start": "2025-01-01",
        "end": "2026-03-15"
    },
    "label_config": {
        "method": "triple_barrier",
        "tp_pct": 0.0175,
        "sl_pct": 0.009,
        "horizon": 6
    },
    "threshold_config": {
        "p_enter": 0.65,
        "delta": 0.0
    },
    "calibration_method": "isotonic",
    "n_features": 120,
    "walk_forward_summary": {
        "mean_precision": 0.607,
        "mean_coverage": 0.183,
        "mean_avg_return": 0.0079
    },
    "created_at": "2026-03-15T12:00:00Z",
    "promoted_at": null,
    "status": "candidate"
}
```

**字段说明（Field explanation）：**

| 字段                   | 说明                                       |
|------------------------|--------------------------------------------|
| `model_version`        | 唯一版本标识符                             |
| `active_model`         | 模型类型：`event_v3` 为三分类堆叠模型      |
| `train_period`         | 训练数据起止时间                           |
| `label_config`         | 训练时使用的标签配置                       |
| `threshold_config`     | 推理时建议使用的阈值配置                   |
| `calibration_method`   | 校准方法                                   |
| `n_features`           | 模型期望的特征数量                         |
| `walk_forward_summary` | Walk-Forward 验证摘要                      |
| `status`               | 模型状态：candidate / production / archived / retired |

---

# 14. 生命周期检查清单

## 训练前检查

- [ ] 数据文件存在且最新（klines_1h/4h/1d.json）
- [ ] 数据时间连续，无明显断档
- [ ] venv-analyzer 已安装所有依赖
- [ ] 磁盘空间充足（训练需要至少 5GB）

## 训练完成后检查

- [ ] model.pkl 已生成
- [ ] calibrator.pkl 已生成
- [ ] feature_schema.json 已生成且字段正确
- [ ] model_meta.json 已填写完整

## Walk-Forward 完成后检查

- [ ] Mean Precision ≥ 0.58
- [ ] CV 结果输出到文件并保存

## 上线前检查

- [ ] 候选模型目录完整
- [ ] 与生产模型回测对比合格
- [ ] 当前生产模型已备份到 archive
- [ ] 手工测试 `/predict` 返回正确

## 上线后检查（前 48 小时）

- [ ] `/healthz` 持续正常
- [ ] `model_version` 显示为新版本
- [ ] prediction log 持续写入
- [ ] evaluate timer 正常执行
- [ ] precision 未出现急剧下滑

## 需要回滚的触发条件

- [ ] `/predict` 持续返回 5xx 错误
- [ ] `calibration_available: false` 持续存在
- [ ] precision 24 小时内下滑超过 10%
- [ ] prediction log 停止写入超过 2 小时

---

# 15. 常见问题

## Q1: 模型加载失败，日志显示 "No module named lightgbm"

**原因**：ml-service 的 `.venv` 缺少依赖，或使用了错误的 Python 环境。

**解决**：
```bash
cd ~/ubuntu-wallet/ml-service
source .venv/bin/activate
pip install -r requirements.txt
deactivate

sudo systemctl restart ml-service
```

---

## Q2: Walk-Forward 结果很差（Precision < 0.5）

**可能原因**：
- 训练数据量不足
- 标签参数（TP/SL）不适合当前市场
- 特征工程有问题

**建议**：
- 增大训练数据量（至少 2 年以上历史）
- 检查标签分布是否严重失衡
- 使用 `analysis_tool.py` 分析特征重要性

---

## Q3: `/healthz` 返回 `calibration_available: false`

**可能原因**：
- `calibrator.pkl` 文件不存在于模型目录
- 文件名不符合预期（检查 `model_loader.py`）

**解决**：
```bash
# 检查模型目录
ls -lh ~/ubuntu-wallet/data/models/current/

# 如果 calibrator.pkl 不存在，从训练产物复制
# 或重新运行校准步骤
```

---

## Q4: 新模型上线后 prediction log 中 model_version 仍然是旧版本

**原因**：ml-service 尚未重启，仍然使用旧模型。

**解决**：
```bash
sudo systemctl restart ml-service
sleep 5
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool
# 确认 model_version 已更新
```

---

## Q5: 训练报错 "MemoryError" 或 "killed"

**原因**：内存不足。LightGBM/XGBoost 在大数据集下内存消耗较大。

**解决**：
- 确保至少 8GB 可用内存
- 减少训练数据量（减少历史范围）
- 关闭其他内存占用较高的进程
- 增加 swap：
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```
