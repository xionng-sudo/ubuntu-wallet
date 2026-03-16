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

> **阅读前先区分两层含义：**
> 1. 文中凡标注“当前实现 / Current implementation”的章节，表示当前仓库代码和服务配置里已经存在的实际行为；
> 2. 文中凡标注“推荐 / Recommended”的目录规范、候选/生产/归档术语，表示团队运维约定或未来可演进方向，**不是仓库当前内建的自动化模型注册/切换能力**。
>
> **命令与接口核对范围（本次按当前源码复核）**：
> - `python-analyzer/train_event_stack_v3.py`
> - `python-analyzer/walkforward_cv.py`
> - `scripts/backtest_event_v3_http.py`
> - `scripts/evaluate_from_logs.py`
> - `ml-service/app.py` 中的 `PredictRequest`
>
> **再次强调**：第 2~7 章主要对应当前仓库里已有脚本；第 8~11 章描述的是基于这些脚本进行的**人工运维流程**。仓库当前并没有一个“候选 → 生产 → 归档 → 回滚”的内建状态机或模型注册中心。

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

`ubuntu-wallet` 中的模型生命周期可以按下面这个**运维视角**来理解：

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
人工上线 / 晋升（promotion as ops process）
  ↓
生产期间持续监控（evaluate-predictions.timer）
  ↓
人工回滚（rollback）或退役（retirement）
```

在团队运维流程里，可以用下面这些**流程状态标签**描述模型所处阶段：

> **注意**：这些标签用于沟通“候选 / 生产 / 归档 / 退役”流程，**不是当前仓库里自动写入文件系统或 `model_meta.json` 的内建状态字段**。

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

训练时会生成 `feature_columns_event_v3.json`，记录模型期望的特征列名和顺序。

**重要**：训练侧和推理侧（ml-service）必须使用完全相同的特征列列表（`feature_columns_event_v3.json`）。

```bash
# 查看训练生成的特征列文件（模型默认输出到 ~/ubuntu-wallet/models/）
cat ~/ubuntu-wallet/models/feature_columns_event_v3.json | python3 -m json.tool | head -30

# 预期输出（Expected output）示例（特征列名列表）：
# [
#     "close_1h",
#     "volume_1h",
#     "rsi_14_1h",
#     "macd_1h",
#     "ema20_1h",
#     "atr_14_1h",
#     ...
# ]
# （列表长度即 model_meta.json 中的 n_features 字段）
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
[train_event_v3] data_dir=/home/ubuntu/ubuntu-wallet/data  model_dir=/home/ubuntu/ubuntu-wallet/models
[train_event_v3] label_method=triple_barrier  horizon=12  tp_pct=0.0175  sl_pct=0.009  calibration=isotonic
[train_event_v3] building multi-tf features ...
[train_event_v3] creating labels using method=triple_barrier ...
[train_event_v3] samples=6789, features=120
[train_event_v3] label distribution: {0: 2234, 1: 2321, 2: 2234}
[train_event_v3] lgb test accuracy=0.4123  proba shape=(1000, 3)
[train_event_v3] xgb test accuracy=0.4015  proba shape=(1000, 3)
[train_event_v3] building out-of-fold stacking features ...
[train_event_v3] stacking test accuracy=0.4456
[train_event_v3] saved /home/ubuntu/ubuntu-wallet/models/lightgbm_event_v3.pkl
[train_event_v3] saved /home/ubuntu/ubuntu-wallet/models/xgboost_event_v3.json (XGBoost native format)
[train_event_v3] saved /home/ubuntu/ubuntu-wallet/models/stacking_event_v3.pkl
[train_event_v3] saved /home/ubuntu/ubuntu-wallet/models/feature_columns_event_v3.json (120 columns)
[train_event_v3] saved calibration (isotonic) to /home/ubuntu/ubuntu-wallet/models/calibration_event_v3.pkl
[train_event_v3] training complete. trained_at=2026-03-15T12:00:00Z
[train_event_v3] updated /home/ubuntu/ubuntu-wallet/models/model_meta.json
```

**输出术语解释（Output term explanation）：**
- `data_dir=.../data`：读取 K 线数据的目录 / Directory containing klines files
- `model_dir=.../models`：模型输出目录（默认为仓库根目录下的 `models/`）/ Model output directory
- `samples=6789, features=120`：训练样本数和特征数 / Number of training samples and features
- `label distribution: {0: 2234, 1: 2321, 2: 2234}`：三类标签分布，0=SHORT 1=FLAT 2=LONG，应大致均衡
- `training complete. trained_at=...`：训练完成，`trained_at` 是此次训练的时间戳（也是模型版本的一部分）

## 4.4 训练产物

训练完成后，模型文件保存在 `--model-dir` 指定的目录（**默认为 `~/ubuntu-wallet/models/`**，即仓库根目录下的 `models/` 目录）：

```
~/ubuntu-wallet/models/
├── lightgbm_event_v3.pkl          # LightGBM 基础模型（Base model 1）
├── lightgbm_event_v3_scaler.pkl   # LightGBM 特征缩放器（Feature scaler）
├── xgboost_event_v3.json          # XGBoost 基础模型（Base model 2，原生 JSON 格式）
├── xgboost_event_v3_scaler.pkl    # XGBoost 特征缩放器
├── stacking_event_v3.pkl          # 堆叠元模型 LogisticRegression（Meta learner）
├── feature_columns_event_v3.json  # 特征列名列表（Feature column names）
├── calibration_event_v3.pkl       # 概率校准器（Calibration artifact）
├── calibration_event_v3_meta.json # 校准器元数据
└── model_meta.json                # 模型完整元数据（Model metadata）
```

> **说明（Note）**：训练脚本默认将所有文件写入同一个目录（`models/`），不创建以版本号命名的子目录。
> 若要管理多个版本（如生产 vs 候选），可以使用 `--model-dir` 参数指定不同目录，例如：
> ```bash
> python python-analyzer/train_event_stack_v3.py \
>   --model-dir ~/ubuntu-wallet/models/v20260315/ \
>   --label-method triple_barrier --tp-pct 0.0175 --sl-pct 0.009 --calibration isotonic
> ```

## 4.5 查看训练结果

```bash
cat ~/ubuntu-wallet/models/model_meta.json | python3 -m json.tool

# 预期输出（Expected output）：
# {
#     "active_model": "event_v3",
#     "trained_at": "2026-03-15T12:00:00Z",
#     "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
#     "feature_schema_version": "multi_tf_v1",
#     "n_features": 120,
#     "label_config": {
#         "method": "triple_barrier",
#         "horizon": 12,
#         "up_thresh": 0.015,
#         "down_thresh": 0.015,
#         "tp_pct": 0.0175,
#         "sl_pct": 0.009
#     },
#     "threshold_config": {
#         "p_enter": 0.65,
#         "delta": 0.0
#     },
#     "calibration_info": {
#         "method": "isotonic",
#         "artifact": "calibration_event_v3.pkl"
#     },
#     "train_periods": {
#         "train_start": "2025-01-01T00:00:00Z",
#         "train_end": "2026-01-01T00:00:00Z",
#         "val_start": "2026-01-01T00:00:00Z",
#         "val_end": "2026-03-15T00:00:00Z"
#     },
#     "event_v3": {
#         "trained_at": "2026-03-15T12:00:00Z",
#         "p_enter": 0.65,
#         "delta": 0.0,
#         "paths": {
#             "lightgbm_model": "lightgbm_event_v3.pkl",
#             "lightgbm_scaler": "lightgbm_event_v3_scaler.pkl",
#             "xgboost_model": "xgboost_event_v3.json",
#             "xgboost_scaler": "xgboost_event_v3_scaler.pkl",
#             "stacking_model": "stacking_event_v3.pkl",
#             "feature_columns": "feature_columns_event_v3.json"
#         }
#     }
# }
#
# 关键字段说明（Key field explanation）：
# model_version  : 格式为 "event_v3:lightgbm:{trained_at}"，trained_at 是训练完成时的 UTC 时间戳
# n_features     : 模型期望的输入特征数量
# threshold_config.p_enter: ml-service 默认使用的置信度阈值（p_enter=0.65 表示信号触发门槛）
# calibration_info.artifact: 校准器文件名（calibration_event_v3.pkl）
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

训练后在模型目录中生成 `calibration_event_v3.pkl`（注意：文件名是 `calibration_event_v3.pkl`，不是 `calibrator.pkl`）：

```bash
ls -lh ~/ubuntu-wallet/models/

# 预期输出（Expected output）：
# total 18M
# -rw-r--r-- 1 ubuntu ubuntu 4.2M Mar 15 12:01 lightgbm_event_v3.pkl
# -rw-r--r-- 1 ubuntu ubuntu 156K Mar 15 12:01 lightgbm_event_v3_scaler.pkl
# -rw-r--r-- 1 ubuntu ubuntu 8.1M Mar 15 12:01 xgboost_event_v3.json
# -rw-r--r-- 1 ubuntu ubuntu 156K Mar 15 12:01 xgboost_event_v3_scaler.pkl
# -rw-r--r-- 1 ubuntu ubuntu 324K Mar 15 12:01 stacking_event_v3.pkl
# -rw-r--r-- 1 ubuntu ubuntu  18K Mar 15 12:01 feature_columns_event_v3.json
# -rw-r--r-- 1 ubuntu ubuntu 280K Mar 15 12:01 calibration_event_v3.pkl
# -rw-r--r-- 1 ubuntu ubuntu  312 Mar 15 12:01 calibration_event_v3_meta.json
# -rw-r--r-- 1 ubuntu ubuntu 1.4K Mar 15 12:01 model_meta.json
#
# 说明（Explanation）：
# lightgbm_event_v3.pkl      LightGBM 模型，通常几MB
# xgboost_event_v3.json      XGBoost 模型，原生 JSON 格式（避免 pickle 版本兼容问题）
# stacking_event_v3.pkl      堆叠元模型（LogisticRegression）
# feature_columns_event_v3   特征列名列表，json 数组
# calibration_event_v3.pkl   校准器，joblib 格式，通常几百KB
# model_meta.json            元数据，纯 JSON，1-2KB
```

## 6.3 验证校准是否有效

```bash
cd ~/ubuntu-wallet
source ml-service/.venv/bin/activate

python3 -c "
import sys, joblib
sys.path.insert(0, 'ml-service')
from calibration import load_calibration, default_calibration_path
cal = load_calibration(default_calibration_path('models'))
if cal is None:
    print('校准器未找到 (Calibration artifact not found)')
else:
    print(f'校准器类型 (calibrator type): {type(cal).__name__}')
    print(f'方法 (method): {cal.method}')
    print(f'类别数 (n_classes): {cal.n_classes}')
    print(f'训练时间 (trained_at): {cal.trained_at}')
    print('校准器加载成功 (calibration loaded successfully)')
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
#     "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
#     "model_expected_n_features": 120,
#     "calibration_available": true,
#     "calibration_method": "isotonic"
# }
#
# 字段说明（Field explanation）：
# ok                   : true 表示服务和模型加载正常
# model_version        : 格式 "event_v3:lightgbm:{trained_at}"，来自 model_meta.json
# calibration_available: true 表示 calibration_event_v3.pkl 已加载
# calibration_method   : 使用的校准方法（isotonic 或 sigmoid）
```

**注意**：如果 `calibration_available` 为 `false`，推理仍可运行，但使用的是原始概率，阈值可能不准确。

---

# 7. 阶段六：候选模型评估

## 7.1 什么是候选模型评估

候选模型（candidate model）在本文中是一个**运维标签**：表示“准备拿来比较或上线的那组模型文件”。
它**不是**当前仓库自动维护的状态，也不意味着仓库里一定存在 `candidate/production` 这类目录或状态字段。
在正式上线前，需要通过以下评估确认其质量：

1. **历史回测**（Backtest）：在已知历史数据上模拟运行
2. **Walk-Forward 验证**：如第 5 章所述
3. **与当前生产模型对比**：确认候选模型不劣于现有模型

## 7.2 对候选模型执行回测

> **重要说明（Important）**：`backtest_event_v3_http.py` 通过调用**正在运行的 ml-service `/predict` 端点**来获取预测，**不是**直接读取模型文件。因此：
> - 运行回测前，ml-service 必须已启动并加载了目标模型
> - 要测试候选模型，需先让 ml-service 实际加载候选模型文件，再运行回测
> - **当前仓库提交的 `systemd/ml-service.service` 并未内置 `Environment=MODEL_DIR=...`**，所以仅在 shell 里 `export MODEL_DIR=...` 后执行 `systemctl restart ml-service`，并不能保证 systemd 服务切到候选目录

### 方法一：网格搜索（寻找最佳阈值/TP/SL 组合）

```bash
# 确保 ml-service 正在运行并加载了目标模型
# （如需测试候选模型，先按第 7.3/第 8 章的方法让 ml-service 实际加载那组文件）

cd ~/ubuntu-wallet
source venv-analyzer/bin/activate

python scripts/backtest_event_v3_http.py \
  --data-dir ~/ubuntu-wallet/data \
  --base-url http://127.0.0.1:9000 \
  --thresholds "0.55:0.75:0.05" \
  --tp-grid "0.015:0.025:0.0025" \
  --sl-grid "0.007:0.012:0.001" \
  --horizon-bars 6 \
  --fee 0.0004 \
  --position-mode single \
  --objective avg_ret_mdd_daily

deactivate
```

### 参数说明

| 参数              | 含义                                        | 示例                     |
|-------------------|---------------------------------------------|--------------------------|
| `--data-dir`      | K 线数据目录（必需）                        | `~/ubuntu-wallet/data`   |
| `--base-url`      | ml-service 地址                             | `http://127.0.0.1:9000`  |
| `--thresholds`    | 阈值网格 `start:end:step`                   | `"0.55:0.75:0.05"` → 测试 0.55, 0.60, ..., 0.75 |
| `--tp-grid`       | 止盈网格 `start:end:step`（小数，非百分比） | `"0.015:0.025:0.0025"` → 测试 1.5%~2.5% |
| `--sl-grid`       | 止损网格 `start:end:step`                   | `"0.007:0.012:0.001"` → 测试 0.7%~1.2% |
| `--horizon-bars`  | 最大持仓 K 线数（TIMEOUT 触发条件）         | `6`（6 小时后按收盘平仓）|
| `--fee`           | 单边手续费率                                | `0.0004`（0.04%）        |
| `--position-mode` | `single`=持仓中不开新仓 / `stack`=允许叠仓  | `single`（推荐）         |
| `--objective`     | 最优化目标                                  | `avg_ret_mdd_daily`（日级别 MDD 最优）|

### 回测输出示例（Backtest output example）

```
Precomputing predictions for 312 bars via http://127.0.0.1:9000 ...

=== BEST CONFIG (grid objective) ===
threshold=0.65 tp=1.75% sl=0.70% fee/side=0.0400% slippage/side=0.0000% horizon=6 timeout_exit=close tie=SL objective=avg_ret_mdd_daily position_mode=single
metrics: signals/week=18.32 n_trade=312 (long=187 short=125) TP=162 SL=90 TO=60 win_rate=0.621 avg_ret=0.891% profit_factor=2.34
decompose: avg_ret_tp=2.15% avg_ret_sl=-0.87% avg_ret_to=0.12% timeout_win_rate=0.55
risk/realism: MDD(trade_seq)=4.80% MDD(hourly)=3.21% MDD(daily)=5.12% max_consec_losses=4 bars_to_exit(min/median/p90/max)=1/4.0/6.0/6
```

**输出术语解释（Output term explanation）：**
- `Precomputing predictions for 312 bars`：依次为 312 根 K 线调用 `/predict`，可能需要几分钟
- `threshold=0.65`：最优阈值（网格搜索选出的最佳配置）
- `n_trade=312 (long=187 short=125)`：共 312 笔交易，做多 187 笔，做空 125 笔
- `TP=162 SL=90 TO=60`：分别达到止盈/止损/超时平仓的笔数 / Trades reaching TP / SL / Timeout
- `win_rate=0.621`：胜率 62.1% / Win rate
- `avg_ret=0.891%`：每笔平均收益率 / Average return per trade
- `profit_factor=2.34`：盈利因子（总盈利 / 总亏损）/ Profit factor
- `MDD(daily)=5.12%`：按日统计的最大回撤 5.12% / Maximum drawdown measured daily
- `max_consec_losses=4`：最多连续亏损 4 笔 / Maximum consecutive losses

## 7.3 候选模型与生产模型对比

由于 `backtest_event_v3_http.py` 依赖运行中的 ml-service，比较候选模型与生产模型需要：

**方法：分两次运行，中间切换 ml-service 使用的模型**

### 步骤 1：对生产模型执行回测并记录结果

```bash
# 此时 ml-service 加载的是生产模型
curl -s http://127.0.0.1:9000/healthz | python3 -c "import sys,json; m=json.load(sys.stdin); print('生产模型 (Production model):', m['model_version'])"

source ~/ubuntu-wallet/venv-analyzer/bin/activate
python scripts/backtest_event_v3_http.py \
  --data-dir ~/ubuntu-wallet/data \
  --thresholds "0.55:0.75:0.05" \
  --tp-grid "0.015:0.025:0.0025" \
  --sl-grid "0.007:0.012:0.001" \
  --horizon-bars 6 --fee 0.0004 --position-mode single \
  | tee /tmp/backtest_production.txt
deactivate
```

### 步骤 2：让 ml-service 临时加载候选模型，对候选模型回测

```bash
# 当前仓库提交的 systemd/ml-service.service 默认没有 Environment=MODEL_DIR=...
# 因此这里要么：
# 1) 临时修改 systemd override，让服务读候选目录；或
# 2) 直接把候选文件复制到 ~/ubuntu-wallet/models/ 后重启，再做对比
#
# 下面示例演示“临时 systemd override”做法：
# sudo systemctl edit ml-service
# 写入：
#   [Service]
#   Environment=MODEL_DIR=/home/ubuntu/ubuntu-wallet/models/v20260315
# 保存退出后：
sudo systemctl daemon-reload
sudo systemctl restart ml-service
sleep 5
curl -s http://127.0.0.1:9000/healthz | python3 -c "import sys,json; m=json.load(sys.stdin); print('候选模型 (Candidate model):', m['model_version'])"

source ~/ubuntu-wallet/venv-analyzer/bin/activate
python scripts/backtest_event_v3_http.py \
  --data-dir ~/ubuntu-wallet/data \
  --thresholds "0.55:0.75:0.05" \
  --tp-grid "0.015:0.025:0.0025" \
  --sl-grid "0.007:0.012:0.001" \
  --horizon-bars 6 --fee 0.0004 --position-mode single \
  | tee /tmp/backtest_candidate.txt
deactivate
```

### 步骤 3：对比两个结果

```bash
echo "=== 生产模型（Production）===" && grep -E "threshold=|win_rate|avg_ret|MDD" /tmp/backtest_production.txt
echo "=== 候选模型（Candidate）===" && grep -E "threshold=|win_rate|avg_ret|MDD" /tmp/backtest_candidate.txt
```

> **如果这里只是做候选模型评估、还没有决定正式上线**：请撤销刚才的临时 systemd override（例如再次 `sudo systemctl edit ml-service` 清空 override，随后 `sudo systemctl daemon-reload && sudo systemctl restart ml-service`），避免服务持续指向候选目录。

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
- [ ] 历史回测 win_rate ≥ 0.58
- [ ] 历史回测 MDD(daily) ≤ 8%
- [ ] `feature_columns_event_v3.json` 已生成且列数正确
- [ ] `calibration_event_v3.pkl` 存在且可加载
- [ ] `model_meta.json` 包含正确的 `model_version`、`threshold_config`、`calibration_info` 字段
- [ ] 手工测试 `/predict` 返回正确（`signal` 字段为 LONG/FLAT/SHORT，`model_version` 正确）

## 7.5 先区分：当前仓库实现 vs 推荐团队流程

在继续阅读“晋升 / 回滚 / 退役”之前，**请先明确区分以下两层含义**：

### A. 当前仓库里**已经实现**的行为（As implemented today）

- `train_event_stack_v3.py` 默认把模型文件写到 `~/ubuntu-wallet/models/`
- `ml-service` 只会从一个目录加载模型：`MODEL_DIR`（默认也是 `~/ubuntu-wallet/models/`）
- 所谓“晋升新模型”在当前实现里，本质上是：
  1. 准备好一组新的模型文件
  2. 覆盖或复制到当前 `MODEL_DIR`
  3. 重启 `ml-service`
- 所谓“回滚模型”在当前实现里，本质上是：
  1. 恢复上一份备份文件
  2. 重新放回 `MODEL_DIR`
  3. 重启 `ml-service`

**当前仓库没有内建以下机制：**
- 没有自动维护 `current / archive / candidates` 目录树
- 没有模型注册表（model registry）
- 没有“候选 → 生产”自动切换命令
- 没有通过 `status=production/candidate/archived` 驱动系统行为的逻辑

### B. 文档里**推荐**的团队运维流程（Recommended operational workflow）

- 用“候选模型 / 生产模型 / 回滚 / 退役”等词，是为了帮助团队建立清晰的运维流程
- 这些词在本文中主要表示**流程概念**，不代表仓库已经实现了完整的模型版本管理系统
- 如果团队未来要做更规范的版本管理，可以额外引入：
  - 独立版本目录（例如 `models/v20260315/`）
  - 统一备份目录（例如 `models_backup/`）
  - 甚至进一步演进到 `current / archive / candidates` 这样的目录约定

> **一句话总结**：下面第 8~11 章里的“晋升 / 回滚 / 退役”，在当前仓库中应理解为**人工运维流程**，而不是仓库已内建的自动化模型管理功能。

---

# 8. 阶段七：当前实现下的模型上线 / 晋升流程（Current implementation）

## 8.1 当前实现的上线前检查

```bash
# 检查新训练的模型目录完整性（默认输出到 models/，若使用 --model-dir 则替换路径）
ls -lh ~/ubuntu-wallet/models/

# 确认 ml-service 当前模型版本（生产模型）
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# 记录当前生产模型版本（做好回滚准备）
echo "当前生产模型（Current production model）: $(curl -s http://127.0.0.1:9000/healthz | python3 -c 'import sys,json; print(json.load(sys.stdin)["model_version"])')"
```

## 8.2 当前实现的上线步骤

> **当前实现说明**：ml-service 通过 `MODEL_DIR` 环境变量决定加载哪个目录的模型。
> 默认值是 `<repo_root>/models/`（即 `~/ubuntu-wallet/models/`）。
> 若训练时使用了 `--model-dir ~/ubuntu-wallet/models/v20260315/`，则需将 `MODEL_DIR` 指向该目录。
> 但**当前仓库提交的** `systemd/ml-service.service` **本身并没有写入 `Environment=MODEL_DIR=...`**，所以在默认部署下它实际上依赖 `ml-service/app.py` 的默认目录 `../models`。
>
> **重要**：这里的“晋升”不是仓库内建的 promotion 机制，而是人工把新模型文件放到活动目录并重启服务。

### 步骤 1：备份当前活动模型（人工操作）

```bash
# 将当前生产模型备份到带时间戳的目录
BACKUP_DIR=~/ubuntu-wallet/models_backup/$(date -u +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"
cp ~/ubuntu-wallet/models/*.pkl ~/ubuntu-wallet/models/*.json "$BACKUP_DIR/" 2>/dev/null || true
echo "备份完成（Backup completed）: $BACKUP_DIR"
ls -lh "$BACKUP_DIR"
```

### 步骤 2a：若新模型训练到默认目录（`models/`）

训练脚本默认将模型写入 `models/`，此时新模型已在活动目录，直接跳到步骤 3。

### 步骤 2b：若新模型训练到独立版本目录（推荐的人工作业方式）

```bash
# 训练时使用: python train_event_stack_v3.py --model-dir ~/ubuntu-wallet/models/v20260315/ ...
# 晋升时将该目录内容复制到 models/（ml-service 的默认加载目录）
NEW_MODEL_DIR=~/ubuntu-wallet/models/v20260315

# 复制所有模型文件到默认目录
cp "$NEW_MODEL_DIR"/*.pkl "$NEW_MODEL_DIR"/*.json ~/ubuntu-wallet/models/
echo "已复制到 models/ 目录 (Copied to models/ directory)"
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
    "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
    "calibration_available": true,
    "calibration_method": "isotonic"
}
```

**验证要点（Verification points）：**
- `ok` 必须为 `true`
- `model_version` 应为新训练的版本（格式：`event_v3:lightgbm:{trained_at}`）
- `calibration_available` 应为 `true`

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
#     "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
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
# model_version       : 确认是新模型（格式 event_v3:lightgbm:{ISO时间戳}）
# reasons             : 信号决策的具体原因（含各类概率值和阈值）
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
#     "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
#     "active_model": "event_v3",
#     ...
# }
# 注意：model_version 格式是 "event_v3:lightgbm:{ISO时间戳}"
```

## 8.3 上线后观察期

新模型上线后，建议至少观察 48 小时：

- [ ] `/healthz` 持续正常
- [ ] prediction log 持续写入
- [ ] 无 schema warning 爆发
- [ ] 评估 timer 正常执行
- [ ] precision 不出现急剧下滑
- [ ] LONG/SHORT 比例不严重失衡

---

# 9. 阶段八：生产期间持续监控（当前实现）

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

# 10. 阶段九：当前实现下的模型回滚（Current implementation）

## 10.1 何时需要回滚（人工恢复备份）

出现以下任一情况应立即回滚：

- `/predict` 返回大量错误（HTTP 5xx）
- `calibration_available` 突然变为 `false`
- precision 在 24 小时内急剧下滑（如超过 10%）
- feature schema warning 激增（说明特征不匹配）
- prediction log 停止写入

## 10.2 当前实现的回滚步骤

> **当前实现说明**：这里的“回滚”指人工把旧备份文件恢复到 `MODEL_DIR` 并重启服务。仓库本身没有“回滚到上一版本”的内建命令。

### 步骤 1：确认有可用的备份

```bash
ls -lh ~/ubuntu-wallet/models_backup/
# 应该能看到之前备份的目录，如 20260315_100000/
```

### 步骤 2：记录当前问题模型版本

```bash
curl -s http://127.0.0.1:9000/healthz | python3 -c "
import sys, json
m = json.load(sys.stdin)
print(f'问题模型（Problem model）: {m[\"model_version\"]}')
print(f'已加载目录（Model dir）: {m[\"model_dir\"]}')
"
```

### 步骤 3：将备份模型文件恢复到 models/ 目录

```bash
# 替换 BACKUP_TIMESTAMP 为实际备份目录名（见步骤1的 ls 输出）
BACKUP_TIMESTAMP="20260315_100000"
BACKUP_DIR=~/ubuntu-wallet/models_backup/$BACKUP_TIMESTAMP

echo "恢复备份（Restoring backup from）: $BACKUP_DIR"
cp "$BACKUP_DIR"/*.pkl "$BACKUP_DIR"/*.json ~/ubuntu-wallet/models/
echo "文件已复制 (Files copied)"
```

### 步骤 4：重启服务并验证

```bash
sudo systemctl restart ml-service
sleep 5

# 验证版本已恢复
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool

# 确认 ok: true、calibration_available: true
# 确认 model_version 是稳定版本（不是刚才的问题版本）
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

建议保存到：`~/ubuntu-wallet/data/reports/rollback_history.md`，记录内容包括：
- 发生时间（When）
- 回滚原因（Why）
- 回滚前的 model_version（格式：`event_v3:lightgbm:{ISO时间戳}`）
- 回滚后的 model_version
- 恢复时间（Recovery time）

---

# 11. 阶段十：模型退役（以团队流程为主，不是内建机制）

## 11.1 何时退役模型

以下情况可考虑退役（彻底删除或标记为 retired）：

- 已被归档超过 6 个月
- 与当前特征 schema 完全不兼容
- 对应数据集已被删除
- 团队确认不再使用

## 11.2 推荐的退役记录步骤

```bash
# 退役某个备份版本（仅需记录，不必立即删除）
BACKUP_TIMESTAMP="20250101_120000"
BACKUP_DIR=~/ubuntu-wallet/models_backup/$BACKUP_TIMESTAMP
META_PATH="${BACKUP_DIR}/model_meta.json"
RETIRED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - <<EOF
import json, sys

meta_path = "${META_PATH}"
retired_at = "${RETIRED_AT}"

try:
    with open(meta_path) as f:
        meta = json.load(f)
    meta['status'] = 'retired'
    meta['retired_at'] = retired_at
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    print('模型已标记退役（Model marked retired）:', meta.get('model_version', 'unknown'))
except FileNotFoundError:
    print(f'注意：文件不存在 (Note: file not found): {meta_path}', file=sys.stderr)
    sys.exit(1)
EOF

# 可选：释放磁盘空间（确认不再需要后再执行）
# rm -rf "$BACKUP_DIR"
```

**安全建议**：退役前确认该模型未被任何服务引用，再执行删除。

---

# 12. 模型文件与目录规范：当前实现 vs 推荐布局

## 12.1 当前实现的模型目录结构

训练脚本默认将所有模型文件写入 `~/ubuntu-wallet/models/`（扁平目录，不分子目录）：

```
~/ubuntu-wallet/models/                    # ml-service 默认加载目录（MODEL_DIR 默认值）
├── lightgbm_event_v3.pkl                 # LightGBM 基础模型
├── lightgbm_event_v3_scaler.pkl          # LightGBM 特征缩放器
├── xgboost_event_v3.json                 # XGBoost 基础模型（原生 JSON）
├── xgboost_event_v3_scaler.pkl           # XGBoost 特征缩放器
├── stacking_event_v3.pkl                 # 堆叠元模型（LogisticRegression）
├── feature_columns_event_v3.json         # 特征列名列表
├── calibration_event_v3.pkl              # 概率校准器
├── calibration_event_v3_meta.json        # 校准器元数据
└── model_meta.json                       # 模型完整元数据
```

## 12.2 推荐的版本化布局（团队约定 / 未来目标状态示意，不是当前仓库内建结构）

若要保留多个历史版本，建议用 `--model-dir` 指定独立目录：

```
~/ubuntu-wallet/
├── models/                               # 当前活动加载目录（通常作为生产模型目录使用）
│   ├── lightgbm_event_v3.pkl
│   └── ...（同上）
│
├── models_backup/                        # 按时间戳备份的旧版本
│   ├── 20260301_100000/                  # 某次备份，可用于回滚
│   │   ├── lightgbm_event_v3.pkl
│   │   └── ...
│   └── 20260215_120000/
│       └── ...
│
└── models/v20260315/                     # 若训练时指定 --model-dir，可存到此
    ├── lightgbm_event_v3.pkl
    └── ...
```

> **再次强调**：上面的 `models_backup/`、`models/v20260315/` 是推荐的人工作业布局，不是代码里自动维护的目录树。

若团队未来想继续演进到更强的 roadmap / target-state 流程，也**可以另外设计**如下目录规范：

```text
data/models/current/
data/models/archive/
data/models/candidates/
```

> **但这只是未来目标状态示意**。当前仓库没有自动创建、维护或切换这些 `data/models/current|archive|candidates` 目录；如果文档里提到这些词，请一律理解为团队流程术语，而不是已实现功能。

## 12.3 模型版本号格式

`model_meta.json` 中的 `model_version` 字段格式为：

```
event_v3:lightgbm:{trained_at}
```

例如：`event_v3:lightgbm:2026-03-15T12:00:00Z`

- `event_v3`：模型架构类型（三分类堆叠模型）
- `lightgbm`：主基础模型
- `2026-03-15T12:00:00Z`：训练完成时间（UTC ISO 8601）

> **注意**：模型版本号中含 `:` 和 `T`，不适合直接用作目录名。若要用目录名区分版本，建议使用纯日期时间格式，如 `v20260315_120000`。

## 12.4 环境变量配置

ml-service 通过以下环境变量找到模型：

```bash
MODEL_DIR=/home/ubuntu/ubuntu-wallet/models   # 模型根目录
DATA_DIR=/home/ubuntu/ubuntu-wallet/data      # 数据根目录
```

这些变量可在 systemd 服务文件中配置，或通过 `.env` 文件管理。

---

# 13. model_meta.json 字段说明

`~/ubuntu-wallet/models/model_meta.json` 由 `train_event_stack_v3.py` 在训练完成后自动写入。实际内容示例：

```json
{
    "active_model": "event_v3",
    "trained_at": "2026-03-15T12:00:00Z",
    "model_version": "event_v3:lightgbm:2026-03-15T12:00:00Z",
    "feature_schema_version": "multi_tf_v1",
    "n_features": 120,
    "label_config": {
        "method": "triple_barrier",
        "horizon": 12,
        "up_thresh": 0.015,
        "down_thresh": 0.015,
        "tp_pct": 0.0175,
        "sl_pct": 0.009
    },
    "threshold_config": {
        "p_enter": 0.65,
        "delta": 0.0
    },
    "calibration_info": {
        "method": "isotonic",
        "artifact": "calibration_event_v3.pkl"
    },
    "train_periods": {
        "train_start": "2025-01-01T00:00:00Z",
        "train_end": "2026-01-01T00:00:00Z",
        "val_start": "2026-01-01T00:00:00Z",
        "val_end": "2026-03-15T00:00:00Z"
    },
    "event_v3": {
        "trained_at": "2026-03-15T12:00:00Z",
        "p_enter": 0.65,
        "delta": 0.0,
        "paths": {
            "lightgbm_model": "lightgbm_event_v3.pkl",
            "lightgbm_scaler": "lightgbm_event_v3_scaler.pkl",
            "xgboost_model": "xgboost_event_v3.json",
            "xgboost_scaler": "xgboost_event_v3_scaler.pkl",
            "stacking_model": "stacking_event_v3.pkl",
            "feature_columns": "feature_columns_event_v3.json"
        }
    }
}
```

**字段说明（Field explanation）：**

| 字段                          | 说明                                                          |
|-------------------------------|---------------------------------------------------------------|
| `active_model`                | 模型架构类型，固定为 `"event_v3"`                             |
| `trained_at`                  | 训练完成时间（UTC ISO 8601）                                  |
| `model_version`               | 唯一版本标识，格式：`event_v3:lightgbm:{trained_at}`          |
| `feature_schema_version`      | 特征架构版本，固定为 `"multi_tf_v1"`                          |
| `n_features`                  | 模型期望的输入特征数量                                        |
| `label_config.method`         | 标签方法：`ternary` 或 `triple_barrier`                       |
| `label_config.horizon`        | 前瞻 K 线数（用于标签生成）                                   |
| `label_config.tp_pct`         | triple_barrier 止盈百分比                                    |
| `label_config.sl_pct`         | triple_barrier 止损百分比                                    |
| `threshold_config.p_enter`    | ml-service 推理使用的信号触发阈值（来自 `--p-enter` 参数）    |
| `threshold_config.delta`      | 多空概率差最小要求（来自 `--delta` 参数，默认 0.0）           |
| `calibration_info.method`     | 校准方法：`isotonic` 或 `sigmoid` 或 `null`（未校准）         |
| `calibration_info.artifact`   | 校准器文件名，固定为 `"calibration_event_v3.pkl"`             |
| `train_periods`               | 训练集和验证集时间范围                                        |
| `event_v3.paths`              | 各模型文件的相对路径（相对于 `MODEL_DIR`）                    |

---

# 14. 生命周期检查清单

## 训练前检查

- [ ] 数据文件存在且最新（klines_1h/4h/1d.json）
- [ ] 数据时间连续，无明显断档
- [ ] venv-analyzer 已安装所有依赖
- [ ] 磁盘空间充足（训练需要至少 5GB）

## 训练完成后检查

- [ ] `lightgbm_event_v3.pkl` 已生成
- [ ] `xgboost_event_v3.json` 已生成
- [ ] `stacking_event_v3.pkl` 已生成
- [ ] `calibration_event_v3.pkl` 已生成（若 `--calibration` 非 `none`）
- [ ] `feature_columns_event_v3.json` 已生成且列数与 `n_features` 一致
- [ ] `model_meta.json` 包含正确的 `model_version`、`threshold_config`、`calibration_info`

## Walk-Forward 完成后检查

- [ ] Mean Precision ≥ 0.58
- [ ] CV 结果输出到文件并保存

## 上线前检查

- [ ] `~/ubuntu-wallet/models/` 中已经放入**计划上线的那组模型文件**（不是旧版本残留）
- [ ] `models_backup/` 中已有上一版本的备份
- [ ] 回测对比完成（win_rate 和 MDD 满足要求）
- [ ] 手工测试 `/predict` 返回正确（`model_version` 为新值）

## 上线后检查（前 48 小时）

- [ ] `/healthz` 持续正常（`ok: true`）
- [ ] `model_version` 显示为新版本（格式：`event_v3:lightgbm:{ISO时间戳}`）
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
- `calibration_event_v3.pkl` 文件不存在于 `MODEL_DIR` 目录
- 训练时使用了 `--calibration none`

**解决**：
```bash
# 检查模型目录，确认 calibration_event_v3.pkl 是否存在
ls -lh ~/ubuntu-wallet/models/ | grep calibration

# 若文件缺失，重新训练时加上 --calibration isotonic
# 或检查训练日志中是否有 calibration 失败警告
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
