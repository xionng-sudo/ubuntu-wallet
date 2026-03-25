# ml-service — FastAPI 推理服务

`ml-service` 是 `ubuntu-wallet` 系统的在线推理服务，负责接收 K 线数据、构建多周期特征、调用机器学习模型，并将预测结果（LONG / SHORT / FLAT 信号与置信度）返回给调用方（通常是 `go-collector`）。

---

## 目录

- [服务能力](#服务能力)
- [文件说明](#文件说明)
- [依赖安装](#依赖安装)
- [启动服务](#启动服务)
- [健康检查](#健康检查)
- [接口说明](#接口说明)
- [模型加载逻辑](#模型加载逻辑)
- [预测日志](#预测日志)
- [配置说明](#配置说明)
- [systemd 部署](#systemd-部署)
- [常见问题](#常见问题)

---

## 服务能力

| 功能 | 说明 |
|---|---|
| 多周期特征构建 | 基于 1h / 4h / 1d K 线数据构建特征向量 |
| 模型推理 | LightGBM + XGBoost + 堆叠元模型，输出三分类概率 |
| 概率校准 | Isotonic / Sigmoid 校准，提高置信度可靠性 |
| 预测日志 | 每次预测结果追加写入 per-symbol JSONL 日志文件 |
| 多币种模型分辨 | 按请求 symbol 自动从 `models/<SYMBOL>/current/` 加载对应模型 |
| schema 验证 | 在线推理时检测特征漂移 |

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `app.py` | FastAPI 应用主入口，定义 `/predict`、`/healthz` 等端点；按请求 symbol 自动解析 per-symbol 模型目录和数据目录 |
| `model_loader.py` | 模型加载逻辑（从 `models/<SYMBOL>/current/` 目录加载，默认退回 `MODEL_DIR`） |
| `feature_builder.py` | 多周期特征构造 + schema 验证 |
| `calibration.py` | 概率校准（Isotonic / Sigmoid） |
| `prediction_logger.py` | 预测日志写入（JSONL 格式）；按 symbol 路由到 `data/<SYMBOL>/predictions_log.jsonl` |
| `requirements.txt` | Python 依赖列表 |

---

## 依赖安装

```bash
cd ~/ubuntu-wallet/ml-service

# 创建虚拟环境（首次部署时执行）
python3 -m venv .venv

# 升级 pip 并安装依赖
~/ubuntu-wallet/ml-service/.venv/bin/pip install --upgrade pip
~/ubuntu-wallet/ml-service/.venv/bin/pip install -r requirements.txt

# 验证关键依赖
~/ubuntu-wallet/ml-service/.venv/bin/python -c "import fastapi, uvicorn, lightgbm; print('依赖安装成功')"
```

**`requirements.txt` 主要依赖：**

| 包 | 说明 |
|---|---|
| `fastapi` | Web 框架 |
| `uvicorn[standard]` | ASGI 服务器 |
| `pydantic` | 请求/响应数据验证 |
| `lightgbm` / `xgboost` | 模型推理 |
| `scikit-learn` | 堆叠元模型 + 校准器 |
| `numpy` | 数值计算 |
| `joblib` | 模型序列化/反序列化 |
| `requests` | 日报/评估工具所需 |

---

## 启动服务

```bash
cd ~/ubuntu-wallet/ml-service

# 前台运行（调试时推荐，Ctrl+C 停止）
~/ubuntu-wallet/ml-service/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 9000

# 后台运行（生产环境推荐配合日志文件）
nohup ~/ubuntu-wallet/ml-service/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 9000 \
  > ~/ubuntu-wallet/logs/ml-service.log 2>&1 &
echo "ml-service 已启动，PID: $!"
```

> **注意**：服务只监听 `127.0.0.1`（本地回环地址），不对外暴露。生产环境如需通过 nginx 反代对外提供服务，请自行配置。

---

## 健康检查

```bash
# 检查服务状态与模型加载情况
curl -fsS http://127.0.0.1:9000/healthz | jq .
```

**期望输出：**

```json
{
  "ok": true,
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "calibration_available": true,
  "calibration_method": "isotonic",
  "model_dir": "/home/ubuntu/ubuntu-wallet/models/ETHUSDT/current",
  "loaded_model_dir": "/home/ubuntu/ubuntu-wallet/models/ETHUSDT/current",
  "flags": {
    "ENABLE_EXOG_FEATURES": "false",
    "ENABLE_DRIFT_MONITOR": "false",
    "ENABLE_CALIB_REPORT": "false"
  },
  ...
}
```

**字段说明：**

| 字段 | 说明 |
|---|---|
| `ok` | `true` 表示服务正常且模型已加载，`false` 表示模型未加载 |
| `model_version` | 当前激活模型的版本标识符 |
| `calibration_available` | `true` 表示校准器已加载（建议确保此为 true） |
| `calibration_method` | 校准方法（`isotonic` / `sigmoid`） |
| `model_dir` | MODEL_DIR 配置值（默认为 `models/ETHUSDT/current`） |
| `flags` | 功能开关状态（ENABLE_EXOG_FEATURES / ENABLE_DRIFT_MONITOR / ENABLE_CALIB_REPORT） |

---

## 接口说明

### `POST /predict`

接收 K 线数据或符号 + 时间框架，返回预测信号。

**调用方式（由 go-collector 自动调用）：**

```bash
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTCUSDT", "interval": "1h"}' | jq .
```

**响应字段（示意）：**

```json
{
  "signal": "LONG",
  "confidence": 0.73,
  "calibrated_confidence": 0.71,
  "calibration_method": "isotonic",
  "p_long": 0.73,
  "p_short": 0.12,
  "p_flat": 0.15,
  "cal_p_long": 0.71,
  "cal_p_short": 0.11,
  "cal_p_flat": 0.18,
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6"
}
```

### `GET /healthz`

返回服务健康状态与模型信息（见上节）。

---

## 模型加载逻辑

ml-service 启动时先加载默认模型（`MODEL_DIR`），并在每次 `/predict` 请求时按 `symbol` 自动解析 per-symbol 模型目录。

### 解析优先级

1. **per-symbol**：若 `models/<SYMBOL>/current/` 目录存在，则从该目录加载对应 symbol 的模型（懒加载 + 进程内缓存）。
2. **legacy fallback**：若 per-symbol 目录不存在，退回到 `MODEL_DIR`（默认 `models/ETHUSDT/current/`），保持 ETHUSDT 及旧部署的兼容性。

### 模型文件（每个 per-symbol 目录下）

- `lightgbm_event_v3.pkl` / `lightgbm_event_v3_scaler.pkl`
- `xgboost_event_v3.json` / `xgboost_event_v3_scaler.pkl`
- `stacking_event_v3.pkl`
- `calibration_event_v3.pkl`
- `feature_columns_event_v3.json`
- `model_meta.json`

同时在 `models/` 根目录（`MODELS_BASE_DIR`）下寻找 `registry.json`（如有，用于版本信息查询）。

> **说明**：`models/<SYMBOL>/current/` 是一个**目录**（不是 JSON 文件），由训练脚本在每次训练完成后自动更新（覆盖替换）。

如果 per-symbol 模型目录不存在或模型文件缺失，服务回退到默认模型。若默认模型也不可用，`/healthz` 返回 `"ok": false`，`/predict` 返回 503 错误。

**切换模型版本：**

```bash
# 查看某 symbol 当前激活模型元信息
cat ~/ubuntu-wallet/models/BTCUSDT/current/model_meta.json | python3 -m json.tool | grep -E "model_version|trained_at"

# 查看 ETHUSDT 当前激活模型
cat ~/ubuntu-wallet/models/ETHUSDT/current/model_meta.json | python3 -m json.tool | grep -E "model_version|trained_at"

# 重启服务加载最新模型
sudo systemctl restart ml-service.service

# 验证新模型已加载
curl -fsS http://127.0.0.1:9000/healthz | jq .model_version
```

---

## 预测日志

每次 `/predict` 被调用，结果会追加写入 per-symbol 日志文件（路径相对于仓库根目录）：

```
data/<SYMBOL>/predictions_log.jsonl
```

例如：
- `data/BTCUSDT/predictions_log.jsonl`
- `data/ETHUSDT/predictions_log.jsonl`

> **向下兼容**：若 `symbol` 为空，日志退回到根级路径 `data/predictions_log.jsonl`。
> 若需在迁移期同时写入根级路径（供旧脚本读取），可设置 `PREDICTIONS_LOG_ALSO_ROOT=1`。

**日志格式（每行一条 JSON）：**

```json
{
  "ts": "2026-03-11T19:00:00Z",
  "symbol": "BTCUSDT",
  "interval": "1h",
  "proba_long": 0.73,
  "proba_short": 0.12,
  "proba_flat": 0.15,
  "signal": "LONG",
  "confidence": 0.73,
  "calibrated_confidence": 0.71,
  "calibration_method": "isotonic",
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "active_model": "event_v3"
}
```

**查看最新预测记录：**

```bash
# BTCUSDT 预测日志
tail -n 5 ~/ubuntu-wallet/data/BTCUSDT/predictions_log.jsonl | \
  python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2, ensure_ascii=False)) for l in sys.stdin]"

# ETHUSDT 预测日志
tail -n 5 ~/ubuntu-wallet/data/ETHUSDT/predictions_log.jsonl | \
  python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2, ensure_ascii=False)) for l in sys.stdin]"
```

**评估脚本（使用 per-symbol 日志）：**

```bash
python3 scripts/evaluate_from_logs.py --symbol BTCUSDT
python3 scripts/evaluate_from_logs.py --symbol ETHUSDT
```

---

## 配置说明

ml-service 通过以下方式获取配置：

| 配置项 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| per-symbol 模型基目录 | `MODELS_BASE_DIR` | `../models` | 所有 per-symbol 模型产物的根目录，子目录结构为 `<SYMBOL>/current/` |
| legacy 模型目录 | `MODEL_DIR` | `../models/ETHUSDT/current` | per-symbol 目录缺失时的兜底路径 |
| 数据目录 | `DATA_DIR` | `../data` | K 线数据和预测日志的根目录；per-symbol 数据在 `DATA_DIR/<SYMBOL>/` |
| 根级日志路径 | `PREDICTIONS_LOG_PATH` | `data/predictions_log.jsonl` | symbol=None 时或 `PREDICTIONS_LOG_ALSO_ROOT=1` 时使用 |
| 双写根级日志 | `PREDICTIONS_LOG_ALSO_ROOT` | `0` | 设为 `1` 可在写入 per-symbol 日志的同时额外写入根级日志（迁移期兼容） |
| 监听地址 | 命令行参数 | `--host 127.0.0.1 --port 9000` | 服务监听地址和端口 |

---

## systemd 部署

生产环境通过 systemd 管理 ml-service：

```bash
# 复制服务单元文件
sudo cp ~/ubuntu-wallet/systemd/ml-service.service \
        /etc/systemd/system/ml-service.service

# 重新加载 systemd 配置
sudo systemctl daemon-reload

# 启用并立即启动（开机自启 + 立即启动）
sudo systemctl enable --now ml-service.service

# 查看运行状态
systemctl status ml-service.service --no-pager

# 查看日志
journalctl -u ml-service.service -n 100 --no-pager

# 重启服务
sudo systemctl restart ml-service.service
```

**systemd 服务关键配置（`systemd/ml-service.service`）：**

```ini
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/ubuntu-wallet/ml-service
ExecStart=/home/ubuntu/ubuntu-wallet/ml-service/.venv/bin/python \
  -m uvicorn app:app --host 127.0.0.1 --port 9000
Restart=always
RestartSec=3
```

---

## 常见问题

**Q：服务启动后 `/healthz` 返回 `"ok": false`**

原因：`MODEL_DIR` 指向的目录不存在或其中模型文件缺失。  
解决：先运行训练脚本生成模型，参考主 README 第 9 节。

```bash
# 检查 per-symbol 模型目录
ls -lh ~/ubuntu-wallet/models/ETHUSDT/current/
ls -lh ~/ubuntu-wallet/models/BTCUSDT/current/
cat ~/ubuntu-wallet/models/ETHUSDT/current/model_meta.json | python3 -m json.tool | grep -E "model_version|trained_at"
```

**Q：`uvicorn` 命令找不到**

原因：未使用完整的虚拟环境路径。  
解决：

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 9000
```

**Q：`import lightgbm` 报 `libgomp` 错误**

原因：缺少系统级多线程库。  
解决：

```bash
sudo apt install -y libgomp1
```

**Q：端口 9000 已被占用**

解决：

```bash
# 查找占用进程
lsof -i :9000

# 停止冲突进程或更换端口
uvicorn app:app --host 127.0.0.1 --port 9001
```

**Q：`/predict` 返回特征 schema 不匹配错误**

原因：模型训练时的特征列与当前数据不一致（特征漂移）。  
解决：重新训练模型，或检查 `feature_columns_event_v3.json` 中的特征列是否与当前 K 线数据对齐。

---

## 多币种配置说明

ml-service 现在支持**单实例多币种**推理：一个服务进程即可按请求 `symbol` 自动加载并缓存对应的 per-symbol 模型。

### 模型目录解析逻辑

```
MODELS_BASE_DIR/                     (默认 ~/ubuntu-wallet/models/)
├── BTCUSDT/current/                 → BTCUSDT 请求自动使用
├── ETHUSDT/current/                 → ETHUSDT 请求自动使用
├── SOLUSDT/current/                 → SOLUSDT 请求自动使用
└── ...

MODEL_DIR=/home/ubuntu/ubuntu-wallet/models/ETHUSDT/current  ← legacy 兜底
```

- 若 `models/<SYMBOL>/current/` 存在 → 使用 per-symbol 模型
- 若不存在 → 退回到 `MODEL_DIR`（默认指向 ETHUSDT）

### 预测日志路径

```
DATA_DIR/                            (默认 ~/ubuntu-wallet/data/)
├── BTCUSDT/predictions_log.jsonl   → BTCUSDT 预测日志
├── ETHUSDT/predictions_log.jsonl   → ETHUSDT 预测日志
└── ...
```

### 环境变量参考

```bash
# per-symbol 模型基目录（所有币种共用一个 ml-service 实例时）
MODELS_BASE_DIR=~/ubuntu-wallet/models

# legacy fallback（per-symbol 目录缺失时）
MODEL_DIR=~/ubuntu-wallet/models/ETHUSDT/current

# 数据目录
DATA_DIR=~/ubuntu-wallet/data

# 迁移期双写（同时写根级 predictions_log.jsonl）
PREDICTIONS_LOG_ALSO_ROOT=0
```

### 多实例部署（可选）

若希望为每个币种运行独立服务进程（例如需要端口隔离），仍可设置不同的 `MODEL_DIR`：

```bash
# BTCUSDT 实例（端口 9001）
MODELS_BASE_DIR=~/ubuntu-wallet/models \
  MODEL_DIR=~/ubuntu-wallet/models/BTCUSDT/current \
  uvicorn app:app --host 127.0.0.1 --port 9001

# ETHUSDT 实例（端口 9002）
MODELS_BASE_DIR=~/ubuntu-wallet/models \
  MODEL_DIR=~/ubuntu-wallet/models/ETHUSDT/current \
  uvicorn app:app --host 127.0.0.1 --port 9002
```

### 每币种配置（configs/symbols.yaml）

所有币种的 threshold/tp/sl/horizon/calibration 参数集中在仓库根目录的 `configs/symbols.yaml`：

```yaml
symbols:
  BTCUSDT:
    enabled: true
    interval: "1h"
    threshold: 0.65
    tp: 0.0175
    sl: 0.009
    horizon: 12
    calibration: "isotonic"
  ETHUSDT:
    enabled: true
    # ... 类似配置
```

使用 Python 读取这些配置：

```python
import sys
sys.path.insert(0, "/path/to/ubuntu-wallet/scripts")
from symbol_paths import get_symbol_config, get_symbol_model_dir

cfg = get_symbol_config("BTCUSDT")
model_dir = get_symbol_model_dir("BTCUSDT")
```

### 模型目录布局（多币种模式）

```
models/
  BTCUSDT/
    current/    ← MODEL_DIR for BTCUSDT instance
    archive/
    registry.json
  ETHUSDT/
    current/    ← MODEL_DIR for ETHUSDT instance
    ...
```

### 向后兼容

现有单币种部署（`MODEL_DIR=models/current`）不受影响；多币种为可选扩展。
