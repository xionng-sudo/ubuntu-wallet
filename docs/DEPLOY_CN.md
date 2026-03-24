# ubuntu-wallet 中文部署手册

> 仓库：`xionng-sudo/ubuntu-wallet`
>
> **快速参考**：如需快速上手，请先阅读根目录 [README.md](../README.md)（包含快速开始与常用命令）。本文档提供更详细的分步骤部署说明，适合生产环境部署人员。
>
> 本文档目标：
> - 从 0 到 1 在一台新服务器上部署 `ubuntu-wallet`
> - 让 `go-collector`、`ml-service`、评估任务能够稳定运行
> - 明确训练、推理、评估、模拟交易各自的部署方式
> - 给出常见部署错误和排查方法
>
> 本文档主要面向：
> - 新服务器部署人员
> - 项目维护者
> - 准备上线 DRY-RUN / 准实盘环境的操作者

> **准确性说明**：
> - 文中的 systemd 路径、端口、环境变量文件路径，均以当前仓库 `systemd/` 下服务文件为准；
> - 文中的训练 / 回测 / 评估命令参数，均以当前脚本源码中的 `argparse` 定义为准；
> - 模型默认目录以 `python-analyzer/train_event_stack_v3.py` 和 `ml-service` 的当前实现为准，即仓库根目录下的 `models/`，不是 `data/models/`。

---

# 目录

1. [部署目标与推荐环境](#1-部署目标与推荐环境)
2. [部署前准备](#2-部署前准备)
3. [新服务器初始化](#3-新服务器初始化)
4. [获取代码与目录规划](#4-获取代码与目录规划)
5. [Python 环境部署](#5-python-环境部署)
6. [Go 环境部署](#6-go-环境部署)
7. [环境变量与配置文件](#7-环境变量与配置文件)
8. [数据目录部署](#8-数据目录部署)
9. [模型目录部署](#9-模型目录部署)
10. [go-collector 部署](#10-go-collector-部署)
11. [ml-service 部署](#11-ml-service-部署)
12. [评估任务部署](#12-评估任务部署)
13. [模拟交易脚本部署](#13-模拟交易脚本部署)
14. [systemd 完整部署](#14-systemd-完整部署)
15. [部署后验证流程](#15-部署后验证流程)
16. [升级流程](#16-升级流程)
17. [回滚流程](#17-回滚流程)
18. [部署常见错误与解决方法](#18-部署常见错误与解决方法)
19. [推荐上线方式](#19-推荐上线方式)
20. [附录：推荐命令清单](#20-附录推荐命令清单)

---

# 1. 部署目标与推荐环境

## 1.1 你要部署的是什么

`ubuntu-wallet` 不是一个单独程序，而是一套系统，至少包含：

- Go 数据采集服务
- Python 在线推理服务
- 训练与分析脚本
- 日志评估脚本
- 模拟交易 / DRY-RUN 脚本
- systemd 服务和定时器

因此部署时建议把它理解成：

> “一个小型的量化研究 + 在线推理服务系统”

---

## 1.2 推荐部署环境

建议：

- 操作系统：Ubuntu 22.04 LTS 或相近版本
- CPU：2 核及以上
- 内存：4GB 起步，建议 8GB
- 磁盘：至少 30GB，可长期保留日志与模型
- Python：3.10+
- Go：1.21+（或以仓库 `go.mod` 要求为准）
- systemd：可用
- 网络：能够访问数据源与交易所接口（如需要）

---

## 1.3 推荐部署模式

建议分三阶段：

### 阶段 A：离线研究机
只跑：
- 训练
- 回测
- walk-forward
- 日志评估

### 阶段 B：在线 DRY-RUN 机
跑：
- go-collector
- ml-service
- prediction log
- evaluate timer
- 模拟交易或 DRY-RUN

### 阶段 C：谨慎真仓机
在 B 的基础上增加：
- 真正交易执行
- 更严格风控
- API 权限隔离

**强烈建议先完成 A 和 B，再考虑 C。**

---

# 2. 部署前准备

## 2.1 需要确认的事项

在部署前，请明确：

- 你是否只做研究，不做实时推理？
- 你是否要部署 online `/predict` 服务？
- 你是否要跑 ETH 永续模�� / DRY-RUN？
- 你是否已经有模型文件？
- 你是否准备好 API Key（若 collector 或执行器需要）？

---

## 2.2 必备信息

建议提前准备：

- 仓库地址：`https://github.com/xionng-sudo/ubuntu-wallet`
- SSH key 或 GitHub Token（若服务器需要拉私有依赖或后续推送）
- `.env` 所需变量值
- Python 依赖安装权限
- Go 编译环境
- systemd 管理权限（sudo）

---

# 3. 新服务器初始化

## 3.1 更新系统包
```bash
sudo apt update
sudo apt upgrade -y
```

## 3.2 安装基础工具
```bash
sudo apt install -y \
  ca-certificates git curl wget unzip jq vim htop tree \
  build-essential software-properties-common \
  python3 python3-venv python3-pip
```

## 3.3 验证基础依赖
```bash
git --version
python3 --version
pip3 --version
systemctl --version | head -n 1
```

如果 `python3 --version` 低于 `3.10`，建议先升级 Python 再继续后续步骤。

## 3.4 安装 Go
如果系统仓库版本过旧，建议安装官方 Go。

示例（版本号请根据实际需要调整）：

```bash
cd /tmp
wget https://go.dev/dl/go1.22.2.linux-amd64.tar.gz
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.22.2.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
source ~/.bashrc
go version
```

---

# 4. 获取代码与目录规划

## 4.1 克隆仓库
建议统一部署在固定目录，例如 `/opt/ubuntu-wallet` 或 `~/ubuntu-wallet`。

示例：
```bash
cd ~
git clone https://github.com/xionng-sudo/ubuntu-wallet.git
cd ubuntu-wallet
```

## 4.2 推荐目录结构

> **注意**：当前仓库默认模型目录在 `~/ubuntu-wallet/models/`。`data/` 目录主要用于原始数据、派生数据、日志和报告，不建议把默认生产模型目录写成 `data/models/`。

建议最终整理为：

```text
~/ubuntu-wallet/
├── bin/
├── data/
│   ├── raw/
│   ├── derived/
│   ├── logs/
│   └── reports/
├── docs/
├── go-collector/
├── ml-service/
├── models/
├── python-analyzer/
├── scripts/
└── systemd/
```

## 4.3 创建运行目录
```bash
mkdir -p ~/ubuntu-wallet/bin
mkdir -p ~/ubuntu-wallet/data/raw
mkdir -p ~/ubuntu-wallet/data/derived
mkdir -p ~/ubuntu-wallet/data/logs
mkdir -p ~/ubuntu-wallet/data/reports
mkdir -p ~/ubuntu-wallet/models
```

---

# 5. Python 环境部署

## 5.1 为什么建议拆成两个 venv

建议至少两个虚拟环境：

- `venv-ml-service`（即 `ml-service/.venv/`，由 systemd 服务使用）：在线推理服务
- `venv-analyzer`：训练、回测、评估、模拟交易

理由：
- 避免训练依赖和服务依赖冲突
- 服务环境更精简
- 便于运维和升级

---

## 5.2 部署 ml-service 环境

> **重要**：systemd 服务文件（`systemd/ml-service.service`）使用的 venv 路径是 `ml-service/.venv/`，建议使用此路径以保持一致。

```bash
cd ~/ubuntu-wallet/ml-service
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
deactivate
```

## 5.3 部署 analyzer 环境

> 训练、回测、评估、模拟交易使用独立的 `venv-analyzer` 环境（与 ml-service 推理环境分开）。

```bash
cd ~/ubuntu-wallet
python3 -m venv venv-analyzer
source ml-service/.venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r python-analyzer/requirements.txt
deactivate
```

## 5.4 验证环境
```bash
# 验证 ml-service venv
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python --version   # 应显示 Python 3.10.x 或以上
python -c "import fastapi, uvicorn, pydantic; print('ml-service 依赖正常 / ml-service deps OK')"
deactivate

# 验证 analyzer venv（如已创建）
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python --version
python -c "import lightgbm, xgboost, sklearn; print('analyzer 依赖正常 / analyzer deps OK')"
deactivate
```

---

# 6. Go 环境部署

## 6.1 拉依赖
```bash
cd ~/ubuntu-wallet/go-collector
go mod download
```

## 6.2 编译
```bash
go build -o ~/ubuntu-wallet/bin/go-collector main.go
```

## 6.3 验证
```bash
~/ubuntu-wallet/bin/go-collector --help
```

如果程序没有 `--help`，至少执行一次并观察是否报缺少配置或环境变量，而不是编译错误。

---

# 7. 环境变量与配置文件

## 7.1 基础做法
复制模板：

```bash
cd ~/ubuntu-wallet
cp .env.example .env
```

## 7.2 需要配置的内容
具体字段以 `.env.example` 为准，常见包括：

- 数据目录
- 模型目录
- 日志目录
- 服务端口
- API Key / Secret
- 运行模式（test / dry-run / live）
- 交易对（如 ETHUSDT）
- threshold 配置
- TP / SL / horizon 配置

## 7.3 安全建议
- `.env` 不要提交到 Git
- 真实 API Key 不要写死在代码里
- 若部署真仓，使用最小权限原则
- 若支持只读 API，请优先使用只读

## 7.4 systemd 环境文件
建议后续将运行所需环境变量迁移到：
- `systemd/env/` 下的 env 文件
- 或 `/etc/default/ubuntu-wallet-*`

这样便于服务托管。

---

# 8. 数据目录部署

## 8.1 数据目录建议
```bash
mkdir -p ~/ubuntu-wallet/data/raw
mkdir -p ~/ubuntu-wallet/data/derived
mkdir -p ~/ubuntu-wallet/data/logs
mkdir -p ~/ubuntu-wallet/data/reports
```

## 8.2 需要重点关注的文件
常见关键数据：
- `klines_1h.json`
- `klines_4h.json`
- `klines_1d.json`
- `predictions_log.jsonl`
- `~/ubuntu-wallet/models/`（单独维护，不属于 `data/`）

## 8.3 权限要求
确保运行服务的用户对以下目录有写权限：
- `data/logs`
- `data/reports`
- `models`（如训练新模型或人工切换模型）
- 采集器写入目录

---

# 9. 模型目录部署

## 9.1 模型目录说明

`train_event_stack_v3.py` 默认将模型文件输出到 `~/ubuntu-wallet/models/`（即仓库根目录下的 `models/` 目录）。`ml-service` 也默认从此目录加载模型。

实际输出的文件（7 个）：

```text
~/ubuntu-wallet/models/
├── lightgbm_event_v3.pkl          # LightGBM 模型
├── lightgbm_event_v3_scaler.pkl   # LightGBM 特征缩放器
├── xgboost_event_v3.json          # XGBoost 模型（原生 JSON）
├── xgboost_event_v3_scaler.pkl    # XGBoost 特征缩放器
├── stacking_event_v3.pkl          # 堆叠元模型
├── feature_columns_event_v3.json  # 特征列名列表
├── calibration_event_v3.pkl       # 概率校准器
└── model_meta.json                # 模型元数据
```

## 9.2 如果还没有模型

先使用 `python-analyzer/train_event_stack_v3.py` 训练（见第 16 章），训练产物自动写入 `models/` 目录。

## 9.3 模型切换建议

不要手工覆盖生产模型而不保留旧版本。建议在每次切换前备份：
- 用 `cp ~/ubuntu-wallet/models/*.pkl ~/ubuntu-wallet/models/*.json ~/ubuntu-wallet/models_backup/$(date -u +%Y%m%d_%H%M%S)/` 备份当前模型
- 新模型文件准备好后，复制到 `models/` 目录
- 重启 ml-service 加载新模型

---

# 10. go-collector 部署

## 10.1 手工启动测试

> **注意**：ml-service 的 venv 在 `ml-service/.venv/`（由 systemd 服务文件使用），训练/分析脚本使用 `venv-analyzer/`（如有）。对于手工测试 go-collector 二进制文件，直接运行即可：

```bash
cd ~/ubuntu-wallet
source .env 2>/dev/null || true
~/ubuntu-wallet/bin/go-collector
```

## 10.2 观察点
确认：
- 程序能启动
- 没有直接 panic
- 能正常写出数据文件
- 数据时间戳是连续更新的

## 10.3 collector 重点检查
检查：
- `data/raw/` 或项目配置的数据目录里是否生成新文件
- 文件更新时间是否持续变化
- 内容是否为空

---

# 11. ml-service 部署

## 11.1 手工启动测试

> **注意**：systemd 服务文件中 ml-service 使用的 venv 路径是 `ml-service/.venv/`。手工测试时建议使用相同路径：

```bash
cd ~/ubuntu-wallet/ml-service
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# 启动服务（前台运行，Ctrl+C 停止）
uvicorn app:app --host 127.0.0.1 --port 9000
# 或：
python -m uvicorn app:app --host 127.0.0.1 --port 9000
```

启动成功时会输出（Successful startup output）：
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9000 (Press CTRL+C to quit)
```

**输出说明（Output explanation）：**
- `Application startup complete`：应用启动完成，模型加载成功
- `Uvicorn running on http://127.0.0.1:9000`：服务正在监听 9000 端口

## 11.2 健康检查

> **注意（Note）**：ml-service 默认监听端口 **9000**，不是 8000。

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
- `model_expected_n_features`：模型期望的特征数量 / Number of features the model expects
- `calibration_available: true`：校准器已加载（建议为 true）/ Calibration artifact is loaded
- `calibration_method`：校准方法（isotonic 或 sigmoid）/ Calibration method used

如果 `ok` 为 `false`，说明模型未加载，需要检查模型目录是否正确。

重点确认：
- 服务活着（`ok: true`）
- 当前模型版本正确（`model_version` 不为空）
- `calibration_available` 为 `true`（若为 false，推理仍可运行，但建议检查模型目录）

## 11.3 推理测试
如果已知 `/predict` 入参格式，发送最小测试请求。

若当前不方便构造完整请求，也至少确认：
- 服务可启动
- 模型加载无报错
- `/healthz` 可访问

---

# 12. 评估任务部署

## 12.1 手工测试评估脚本

> **注意**：`evaluate-predictions.service` 与 `daily-report.service` 都使用 `ml-service/.venv`（见 systemd 服务文件），手工运行时也建议使用相同 venv。该 venv 需要安装 `ml-service/requirements.txt`，其中包含共享评估/日报脚本依赖的 `requests`。

```bash
cd ~/ubuntu-wallet
source ~/ubuntu-wallet/ml-service/.venv/bin/activate

python scripts/evaluate_from_logs.py \
  --log-path data/predictions_log.jsonl \
  --data-dir data \
  --interval 1h \
  --active-model event_v3 \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --horizon-bars 6

deactivate
```

## 12.2 验证点
确认：
- 能读取 prediction log
- 能读取 K 线数据
- 能输出评估结果
- 没有时间对齐异常

---

# 13. 模拟交易脚本部署

## 13.1 历史回放模拟
```bash
cd ~/ubuntu-wallet
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python scripts/live_trader_eth_perp_simulated.py
```

## 13.2 关注点
- 是否能按 bar 顺序运行
- 是否能调通 `/predict`
- 是否能生成交易日志和权益曲线
- 是否因数据不完整中断

## 13.3 DRY-RUN 交易脚本
```bash
python scripts/live_trader_eth_perp_binance.py --mode dry-run
```

## 13.4 当前建议
优先运行：
- `go-collector`
- `ml-service`
- `evaluate-predictions.timer`
- `live_trader_eth_perp_simulated.py`

真仓前不要急着直接实盘。

---

# 14. systemd 完整部署

## 14.0 创建敏感配置文件（重要，不进 Git）

systemd 服务文件通过 `EnvironmentFile` 加载环境变量：
- `go-collector.service` 读取 `/etc/ubuntu-wallet/collector.env`
- `check-go-collector.service` 读取 `/etc/ubuntu-wallet/telegram.env`

**必须在启动服务前创建这些文件**：

```bash
# 创建配置目录
sudo mkdir -p /etc/ubuntu-wallet
sudo chmod 755 /etc/ubuntu-wallet

# 从模板复制并填写
sudo cp ~/ubuntu-wallet/systemd/env/collector.env.example /etc/ubuntu-wallet/collector.env
sudo cp ~/ubuntu-wallet/systemd/env/telegram.env.example /etc/ubuntu-wallet/telegram.env

# 设置安全权限（只有 root 可读）
sudo chmod 600 /etc/ubuntu-wallet/*.env
sudo chown root:root /etc/ubuntu-wallet/*.env

# 编辑填写真实值
sudo nano /etc/ubuntu-wallet/collector.env
# 填写内容示例（示例值，不要用真实 key）：
# COLLECTOR_PORT=8080
# COLLECT_INTERVAL=60s
# ML_SERVICE_URL=http://127.0.0.1:9000/predict
# BINANCE_API_KEY=（如需要）
# BINANCE_API_SECRET=（如需要）

sudo nano /etc/ubuntu-wallet/telegram.env
# 填写内容：
# TELEGRAM_BOT_TOKEN=（你的 Telegram Bot Token）
# TELEGRAM_CHAT_ID=（你的 Telegram Chat ID）
```

> **安全警告（Security warning）**：`/etc/ubuntu-wallet/*.env` 文件含有 API Key 等敏感信息，**绝对不要**提交到 Git 仓库或复制到公开位置。

## 14.1 配置 sudoers（允许自愈脚本重启 go-collector）

```bash
sudo tee /etc/sudoers.d/ubuntu-go-collector-restart >/dev/null <<'EOF'
ubuntu ALL=NOPASSWD: /bin/systemctl restart go-collector
EOF
sudo visudo -cf /etc/sudoers.d/ubuntu-go-collector-restart
```

## 14.2 复制 service 文件
```bash
sudo cp ~/ubuntu-wallet/systemd/go-collector.service /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/ml-service.service /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/evaluate-predictions.service /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/evaluate-predictions.timer /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/daily-report.service /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/daily-report.timer /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/check-go-collector.service /etc/systemd/system/
sudo cp ~/ubuntu-wallet/systemd/check-go-collector.timer /etc/systemd/system/
```

## 14.3 创建日志目录

```bash
mkdir -p ~/ubuntu-wallet/data/logs
```

## 14.4 重新加载
```bash
sudo systemctl daemon-reload
```

## 14.5 启用服务
```bash
sudo systemctl enable go-collector
sudo systemctl enable ml-service
sudo systemctl enable evaluate-predictions.timer
sudo systemctl enable daily-report.timer
sudo systemctl enable check-go-collector.timer
```

## 14.6 启动服务
```bash
sudo systemctl start ml-service
sudo systemctl start go-collector
sudo systemctl start evaluate-predictions.timer
sudo systemctl start check-go-collector.timer
```

## 14.7 查看状态

```bash
systemctl status go-collector --no-pager
systemctl status ml-service --no-pager
systemctl status evaluate-predictions.timer --no-pager
systemctl status check-go-collector.timer --no-pager
```

预期输出（Expected output for ml-service）：
```
● ml-service.service - ubuntu-wallet ml-service (FastAPI)
     Loaded: loaded (/etc/systemd/system/ml-service.service; enabled; vendor preset: enabled)
     Active: active (running) since Mon 2026-03-15 10:00:00 UTC; 5s ago
   Main PID: 12345 (python)
      Tasks: 2 (limit: 4915)
     Memory: 512.0M
        CPU: 3.421s
     CGroup: /system.slice/ml-service.service
             └─12345 /home/ubuntu/ubuntu-wallet/ml-service/.venv/bin/python -m uvicorn app:app ...

Mar 15 10:00:00 ubuntu python[12345]: INFO:     Application startup complete.
Mar 15 10:00:00 ubuntu python[12345]: INFO:     Uvicorn running on http://127.0.0.1:9000
```

**输出说明（Output explanation）：**
- `active (running)`：服务正在运行 / Service is running
- `enabled`：已设置开机自启 / Set to start on boot
- `Memory: 512.0M`：内存使用约 512MB（模型加载后）/ Memory usage after model load
- 最后两行日志来自 uvicorn，表示应用启动完成

---

# 15. 部署后验证流程

部署完成后，不要只看“服务启动了”，还要做功能验证。

## 15.1 collector 验证
- [ ] 进程存活
- [ ] 数据文件持续更新
- [ ] 时间戳连续
- [ ] 无明显空文件

## 15.2 ml-service 验证
- [ ] `/healthz` 正常
- [ ] 模型版本正确
- [ ] 校准器状态正确
- [ ] 推理不报错

## 15.3 prediction log 验证
- [ ] 调一次 `/predict` 后有日志写入
- [ ] 字段完整
- [ ] 时间正确
- [ ] 阈值字段正确

## 15.4 evaluate timer 验证
- [ ] timer 正常触发
- [ ] 评估脚本执行成功
- [ ] 日志中无持续性报错

## 15.5 模拟交易验证
- [ ] 模拟脚本可跑完
- [ ] 输出权益曲线
- [ ] TP / SL / horizon 逻辑合理

---

# 16. 升级流程

## 16.1 拉取最新代码
```bash
cd ~/ubuntu-wallet
git pull origin main
```

## 16.2 更新 Python 依赖
```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
pip install -r ~/ubuntu-wallet/ml-service/requirements.txt
deactivate

# 如果有 venv-analyzer（用于训练/分析）
if [ -d ~/ubuntu-wallet/venv-analyzer ]; then
  source ~/ubuntu-wallet/ml-service/.venv/bin/activate
  pip install -r ~/ubuntu-wallet/python-analyzer/requirements.txt
  deactivate
fi
```

## 16.3 重新编译 Go collector
```bash
cd ~/ubuntu-wallet/go-collector
go build -o ~/ubuntu-wallet/bin/go-collector main.go
```

## 16.4 重启服务
```bash
sudo systemctl restart go-collector
sudo systemctl restart ml-service
sudo systemctl restart evaluate-predictions.timer
```

## 16.5 升级后验证
- `/healthz`
- collector 数据更新
- 评估脚本正常执行
- prediction log 正常写入

---

# 17. 回滚流程

## 17.1 代码回滚
如果使用 Git：
```bash
cd ~/ubuntu-wallet
git log --oneline
git checkout <previous_commit_or_tag>
```

## 17.2 模型回滚
在换模型前备份旧模型文件，回滚时恢复备份：
```bash
# 恢复备份到 models/ 目录
BACKUP_TIMESTAMP="20260315_100000"
cp ~/ubuntu-wallet/models_backup/$BACKUP_TIMESTAMP/*.pkl \
   ~/ubuntu-wallet/models_backup/$BACKUP_TIMESTAMP/*.json \
   ~/ubuntu-wallet/models/
sudo systemctl restart ml-service
```

## 17.3 服务回滚
```bash
sudo systemctl restart ml-service
```

## 17.4 回滚后验证
- `/healthz` 中 model_version 是否为回滚版本
- `/predict` 是否正常
- 日志是否恢复正常

---

# 18. 部署常见错误与解决方法

## 18.1 `pip install` 失败
### 可能原因
- Python 版本不匹配
- 缺少编译依赖
- 网络问题

### 解决方法
- 升级 pip
- 安装 build-essential
- 重试指定镜像源（如内部环境需要）

---

## 18.2 `go build` 失败
### 可能原因
- Go 版本过旧
- 依赖没有拉全

### 解决方法
```bash
go version
go mod tidy
go mod download
go build ./...
```

---

## 18.3 systemd 启动失败
### 检查
```bash
systemctl status ml-service
journalctl -u ml-service -n 200 --no-pager
```

### 常见原因
- ExecStart 路径错误
- WorkingDirectory 错误
- venv Python 路径错误
- 环境变量缺失
- 权限不足

---

## 18.4 `/healthz` 正常但 `/predict` 失败
### 常见原因
- 输入 payload 不完整
- 特征构造失败
- schema drift
- 数据文件缺失

### 解决方法
- 先手工测试最小输入
- 查看 `feature_builder.py` 日志
- 检查数据目录

---

## 18.5 prediction log 不写
### ���见原因
- 日志目录不存在
- 权限不足
- 文件路径配置错

### 解决方法
```bash
mkdir -p ~/ubuntu-wallet/data/logs
chmod -R u+rw ~/ubuntu-wallet/data/logs
```

---

# 19. 推荐上线方式

## 19.1 最推荐：分阶段上线
### 第 1 阶段
只部署：
- 训练
- 回测
- walk-forward
- 日志评估

### 第 2 阶段
再加：
- go-collector
- ml-service
- prediction logging
- evaluate timer

### 第 3 阶段
再加：
- 模拟交易
- DRY-RUN 交易脚本

### 第 4 阶段
最后才考虑：
- 真仓执行

---

## 19.2 真仓前最低要求
- 至少 2 周 DRY-RUN
- 足够样本量
- 风控规则已验证
- model_version、threshold 版本固定并记录
- 有回滚路径

---

# 20. 附录：推荐命令清单

## 查看服务状态
```bash
systemctl status go-collector
systemctl status ml-service
systemctl status evaluate-predictions.timer
```

## 查看最近日志
```bash
journalctl -u go-collector -n 200 --no-pager
journalctl -u ml-service -n 200 --no-pager

# 实时追踪
journalctl -u go-collector -f
```

## ml-service 健康检查
```bash
# 注意：ml-service 端口为 9000
curl -s http://127.0.0.1:9000/healthz | python3 -m json.tool
```

## go-collector 健康检查
```bash
curl -s http://127.0.0.1:8080/api/healthz | python3 -m json.tool
```

## 重启服务
```bash
sudo systemctl restart go-collector
sudo systemctl restart ml-service
```

## 手工跑评估
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

## 手工模拟
```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/scripts/live_trader_eth_perp_simulated.py
deactivate
```

## 手工训练
```bash
source ~/ubuntu-wallet/ml-service/.venv/bin/activate
python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --label-method triple_barrier \
  --tp-pct 0.0175 \
  --sl-pct 0.009 \
  --calibration isotonic
deactivate
```

---

## 21. Feature Flags / Stage 控制说明

系统通过环境变量控制可选功能的开启与关闭。所有 flag 默认为 `false`（关闭），需要显式启用。

### 21.1 Flag 一览

| 环境变量                | 默认值  | 功能说明                                    | 建议启用阶段        |
|------------------------|---------|---------------------------------------------|---------------------|
| `ENABLE_EXOG_FEATURES` | `false` | 外生特征采集（资金费率/持仓量/买卖比）       | DRY-RUN 验证后启用  |
| `ENABLE_DRIFT_MONITOR` | `false` | 特征漂移监控（每6小时运行，生成报告）        | 上线后建议开启      |
| `ENABLE_CALIB_REPORT`  | `false` | 校准质量报告（每周生成可靠性曲线+Brier分数） | 上线后建议开启      |

### 21.2 设置方式

在 `/etc/ubuntu-wallet/ml-service.env` 中添加（参考 `systemd/env/ml-service.env.example`）：

```ini
ENABLE_EXOG_FEATURES=true
ENABLE_DRIFT_MONITOR=true
ENABLE_CALIB_REPORT=true
```

systemd 服务通过 `EnvironmentFile` 加载：

```ini
EnvironmentFile=-/etc/ubuntu-wallet/ml-service.env
```

修改后需要重启相关服务：

```bash
sudo systemctl daemon-reload
sudo systemctl restart ml-service go-collector
```

### 21.3 各阶段建议配置

| Stage         | EXOG_FEATURES | DRIFT_MONITOR | CALIB_REPORT |
|---------------|---------------|---------------|--------------|
| 离线研究       | false         | false         | false        |
| DRY-RUN       | false         | true          | false        |
| 生产上线初期   | false         | true          | true         |
| 生产稳定后     | true          | true          | true         |

### 21.4 Drift Monitor 相关文件

- 脚本：`scripts/report_drift.py`
- systemd：`systemd/drift-monitor.service` / `systemd/drift-monitor.timer`
- 输出：`data/reports/drift_YYYY-MM-DD.{json,md}`
- 训练统计：`models/current/train_feature_stats.json`（由训练脚本生成）

### 21.5 Calibration Report 相关文件

- 脚本：`python-analyzer/calibration_report.py`
- systemd：`systemd/calibration-report.service` / `systemd/calibration-report.timer`
- 输出：`data/reports/calib_report_YYYY-MM-DD.{json,md,png}`

---

# 22. 多币种部署（Multi-Symbol）

## 22.1 架构原则

- **共用代码**：训练脚本、推理服务、评估脚本完全共享，不为每个币种单独维护一套代码。
- **数据隔离**：每个币种有独立的 `data/<SYMBOL>/` 目录。
- **模型隔离**：每个币种有独立的 `models/<SYMBOL>/` 目录（含 `current/`、`archive/`、`registry.json`）。
- **配置隔离**：`configs/symbols.yaml` 文件为每个币种单独配置 threshold/tp/sl/horizon/calibration。

## 22.2 分阶段上线计划

| 阶段 | 币种 | 默认启用 |
|------|------|---------|
| Phase 1（初始上线）| BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT | `enabled: true` |
| Phase 2（灰度扩展）| XRPUSDT, DOGEUSDT, ADAUSDT | `enabled: false`（手动激活）|

## 22.3 激活新币种（Phase 2 示例）

```bash
# 1. 编辑配置文件，将 enabled: false 改为 true
nano ~/ubuntu-wallet/configs/symbols.yaml
# 修改目标币种: enabled: true

# 2. 准备数据目录
mkdir -p ~/ubuntu-wallet/data/XRPUSDT
mkdir -p ~/ubuntu-wallet/models/XRPUSDT

# 3. 确保 go-collector 已在采集该币种数据
# (需在 go-collector 配置中添加该币种，具体见 go-collector/OPS-NOTES.md)

# 4. 等待数据积累（建议至少 1000 根 1h K 线），然后初次训练
bash ~/ubuntu-wallet/scripts/train_symbol.sh XRPUSDT

# 5. 验证训练产物
ls ~/ubuntu-wallet/models/XRPUSDT/current/
cat ~/ubuntu-wallet/models/XRPUSDT/current/model_meta.json | python3 -m json.tool
```

## 22.4 每币种训练命令

```bash
# 使用便捷包装脚本（推荐）
bash ~/ubuntu-wallet/scripts/train_symbol.sh BTCUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh ETHUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh SOLUSDT
bash ~/ubuntu-wallet/scripts/train_symbol.sh BNBUSDT

# 手工训练（完整控制）
SYMBOL=BTCUSDT
python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir  ~/ubuntu-wallet/data/${SYMBOL} \
  --model-dir ~/ubuntu-wallet/models/${SYMBOL} \
  --horizon 12 --tp-pct 0.0175 --sl-pct 0.009 --calibration isotonic
```

## 22.5 每币种评估命令

```bash
# 新式（推荐）：从 configs/symbols.yaml 自动派生所有参数
SYMBOL=BTCUSDT
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py --symbol ${SYMBOL}

# 旧式（向后兼容）：手工指定全部参数
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --symbol ${SYMBOL} \
  --log-path  ~/ubuntu-wallet/data/${SYMBOL}/predictions_log.jsonl \
  --data-dir  ~/ubuntu-wallet/data/${SYMBOL} \
  --threshold 0.65 --tp 0.0175 --sl 0.009 --horizon-bars 12
```

## 22.6 每币种 Drift 监控命令

```bash
SYMBOL=BTCUSDT
ENABLE_DRIFT_MONITOR=true python ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol ${SYMBOL}

# 等价于：
# python ~/ubuntu-wallet/scripts/report_drift.py \
#   --train-stats ~/ubuntu-wallet/models/${SYMBOL}/current/train_feature_stats.json \
#   --log-path    ~/ubuntu-wallet/data/${SYMBOL}/predictions_log.jsonl \
#   --output-dir  ~/ubuntu-wallet/data/${SYMBOL}/reports
```

## 22.7 部署后每币种验证清单

对每个新上线的币种，依次执行：

```bash
SYMBOL=BTCUSDT

# 1. 检查模型产物是否完整
ls ~/ubuntu-wallet/models/${SYMBOL}/current/
# 期望文件：lightgbm_event_v3.pkl, xgboost_event_v3.json, stacking_event_v3.pkl,
#            feature_columns_event_v3.json, train_feature_stats.json, model_meta.json

# 2. 检查 model_meta.json
cat ~/ubuntu-wallet/models/${SYMBOL}/current/model_meta.json | python3 -m json.tool

# 3. 验证 train_feature_stats.json 格式
python3 -c "
import json
p = '~/ubuntu-wallet/models/${SYMBOL}/current/train_feature_stats.json'.replace('~', __import__('os').path.expanduser('~'))
with open(p) as f: d = json.load(f)
k = next(iter(d))
print('features:', len(d), 'sample:', k, d[k])
"

# 4. 手工跑一次 drift 报告（dry-run）
ENABLE_DRIFT_MONITOR=true python ~/ubuntu-wallet/scripts/report_drift.py \
  --symbol ${SYMBOL} --dry-run

# 5. 手工跑一次评估
python ~/ubuntu-wallet/scripts/evaluate_from_logs.py --symbol ${SYMBOL}
```

## 22.8 向后兼容说明

现有的单币种路径（`data/klines_1h.json`、`models/current/`）不受影响。
所有脚本在不传 `--symbol` 时行为与之前完全相同。
