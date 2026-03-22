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
| 预测日志 | 每次预测结果追加写入 JSONL 日志文件 |
| 模型版本管理 | `models/current/` 目录作为当前生产模型，训练后自动更新 |
| schema 验证 | 在线推理时检测特征漂移 |

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `app.py` | FastAPI 应用主入口，定义 `/predict`、`/healthz` 等端点 |
| `model_loader.py` | 模型加载逻辑（从 MODEL_DIR 目录直接加载，默认 `models/current/`） |
| `feature_builder.py` | 多周期特征构造 + schema 验证 |
| `calibration.py` | 概率校准（Isotonic / Sigmoid） |
| `prediction_logger.py` | 预测日志写入（JSONL 格式） |
| `requirements.txt` | Python 依赖列表 |

---

## 依赖安装

```bash
cd ~/ubuntu-wallet/ml-service

# 创建虚拟环境（首次部署时执行）
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate

# 升级 pip 并安装依赖
pip install --upgrade pip
pip install -r requirements.txt

# 验证关键依赖
python -c "import fastapi, uvicorn, lightgbm; print('依赖安装成功')"

deactivate
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
source .venv/bin/activate

# 前台运行（调试时推荐，Ctrl+C 停止）
uvicorn app:app --host 127.0.0.1 --port 9000

# 后台运行（生产环境推荐配合日志文件）
nohup uvicorn app:app --host 127.0.0.1 --port 9000 \
  > ~/ubuntu-wallet/logs/ml-service.log 2>&1 &
echo "ml-service 已启动，PID: $!"
```

> **注意**：服务只监听 `127.0.0.1`（本地回环地址），不对外暴露。生产环境如需通过 nginx 反代对外提供服务，请自行配置。

---

## 健康检查

```bash
# 检查服务状态与模型加载情况
curl -s http://127.0.0.1:9000/healthz | jq .
```

**期望输出：**

```json
{
  "ok": true,
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "calibration_available": true,
  "calibration_method": "isotonic",
  "model_dir": "/home/ubuntu/ubuntu-wallet/models/current",
  "loaded_model_dir": "/home/ubuntu/ubuntu-wallet/models/current",
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
| `model_dir` | MODEL_DIR 配置值（默认为 `models/current`） |
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

ml-service 启动时通过以下步骤加载模型：

1. 从 `MODEL_DIR`（默认 `models/current/`）目录直接读取模型文件：
   - `lightgbm_event_v3.pkl` / `lightgbm_event_v3_scaler.pkl`
   - `xgboost_event_v3.json` / `xgboost_event_v3_scaler.pkl`
   - `stacking_event_v3.pkl`
   - `calibration_event_v3.pkl`
   - `feature_columns_event_v3.json`
   - `model_meta.json`
2. 同时在 `models/` 根目录寻找 `registry.json`（如有，用于版本信息查询）

> **说明**：`models/current/` 是一个**目录**（不是 JSON 文件），由训练脚本在每次训练完成后自动更新（覆盖替换）。`MODEL_DIR` 默认值为 `ml-service/` 所在目录的上级路径下的 `models/current`。

如果 `models/current/` 不存在或模型文件缺失，服务会启动但 `/healthz` 会返回 `"ok": false`，`/predict` 会返回 503 错误。

**切换模型版本：**

使用 `scripts/rollback_model.py` 或手动将模型文件复制到 `models/current/` 后，重启 ml-service 即可生效：

```bash
# 查看当前激活模型元信息
cat ~/ubuntu-wallet/models/current/model_meta.json | python3 -m json.tool | grep -E "model_version|trained_at"

# 查看所有历史版本
ls ~/ubuntu-wallet/models/archive/ 2>/dev/null || ls ~/ubuntu-wallet/models/

# 重启服务加载新模型
sudo systemctl restart ml-service.service

# 验证新模型已加载
curl -s http://127.0.0.1:9000/healthz | jq .model_version
```

---

## 预测日志

每次 `/predict` 被调用，结果会追加写入 `data/predictions_log.jsonl`（路径相对于仓库根目录）。

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
tail -n 5 ~/ubuntu-wallet/data/predictions_log.jsonl | \
  python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2, ensure_ascii=False)) for l in sys.stdin]"
```

---

## 配置说明

ml-service 通过以下方式获取配置：

| 配置项 | 方式 | 说明 |
|---|---|---|
| 模型目录 | 相对路径 `../models` | 相对于 `ml-service/` 的工作目录 |
| 数据目录 | 相对路径 `../data` | K 线数据和预测日志所在目录 |
| 监听地址 | 命令行参数 | `--host 127.0.0.1 --port 9000` |

如需自定义模型目录，可通过环境变量 `MODEL_DIR` 覆盖默认值（具体支持情况以 `app.py` 当前实现为准）。

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

原因：`models/current/` 目录不存在或其中模型文件缺失。  
解决：先运行训练脚本生成模型，参考主 README 第 9 节。

```bash
# 检查模型目录
ls -lh ~/ubuntu-wallet/models/
cat ~/ubuntu-wallet/models/current/model_meta.json | python3 -m json.tool | grep -E "model_version|trained_at"
```

**Q：`uvicorn` 命令找不到**

原因：未激活虚拟环境。  
解决：

```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 9000
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
