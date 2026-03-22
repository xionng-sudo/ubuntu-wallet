# ubuntu-wallet 升级 / 更新流程（生产运维）

适用：
- systemd 管理的服务：`go-collector.service`、`ml-service.service`、`check-go-collector.timer`
- 代码目录：`/home/ubuntu/ubuntu-wallet`

目标：
- 安全更新代码并重启服务
- unit 变更时正确 `daemon-reload`
- 提供回滚与排障手册

> **参考**：更多运维操作请查阅 [../docs/RUNBOOK_CN.md](../docs/RUNBOOK_CN.md) 和 [../README.md](../README.md) 第 14 节（常用命令汇总）。

---

## 0) 更新前检查（推荐）

```bash
cd /home/ubuntu/ubuntu-wallet
git status
systemctl status go-collector.service --no-pager || true
systemctl status ml-service.service --no-pager || true
systemctl status check-go-collector.timer --no-pager || true
```

---

## 1) 常规更新（代码有变更，需要重启服务）

### 1.1 拉取最新代码
```bash
cd /home/ubuntu/ubuntu-wallet
git pull --ff-only
```

> 如果 `git pull` 报错（例如本地有改动），先 `git status`，确认是否需要 `git stash` 或放弃本地改动。

### 1.2 重新构建 go-collector（二进制在固定路径 A）
```bash
cd /home/ubuntu/ubuntu-wallet/go-collector
go mod tidy
go build -o /home/ubuntu/ubuntu-wallet/bin/go-collector .
```

### 1.3 重新安装/升级 ml-service 依赖（requirements.txt 变更时必须）
```bash
cd /home/ubuntu/ubuntu-wallet/ml-service
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
deactivate
```

### 1.4 重启服务
```bash
sudo systemctl restart ml-service.service
sudo systemctl restart go-collector.service
```

### 1.5 验收
```bash
curl -fsS http://127.0.0.1:9000/docs | head
curl -fsS --max-time 3 http://127.0.0.1:8080/api/healthz | jq .
```

---

## 2) unit 文件变更（systemd/*.service 或 *.timer 有更新）

如果本次更新包含 `systemd/*.service` 或 `systemd/*.timer` 的改动：

### 2.1 覆盖安装到 /etc/systemd/system
```bash
sudo cp /home/ubuntu/ubuntu-wallet/systemd/*.service /etc/systemd/system/
sudo cp /home/ubuntu/ubuntu-wallet/systemd/*.timer /etc/systemd/system/
```

### 2.2 daemon-reload + 重启/重载
```bash
sudo systemctl daemon-reload

sudo systemctl restart ml-service.service
sudo systemctl restart go-collector.service

# timer 有变更时建议一起重启 timer
sudo systemctl restart check-go-collector.timer
```

---

## 3) 修改 env（/etc/ubuntu-wallet/*.env）后的操作

env 改动不会自动生效，需要重启服务：

```bash
sudo systemctl restart ml-service.service
sudo systemctl restart go-collector.service
sudo systemctl restart check-go-collector.timer
```

---

## 4) 快速回滚（按 commit 回退）

### 4.1 找到你要回滚到的 commit
```bash
cd /home/ubuntu/ubuntu-wallet
git log --oneline -n 20
```

### 4.2 回滚（示例：回到某个 commit）
```bash
cd /home/ubuntu/ubuntu-wallet
git reset --hard <commit_sha>
```

然后按“常规更新”重新 build + 重启。

> 如果你希望更安全可控的回滚，建议用 tag 或 release 分发二进制（后续可以再完善）。

---

## 5) 常用排障命令

```bash
# 服务日志
journalctl -u go-collector.service -n 200 --no-pager
journalctl -u ml-service.service -n 200 --no-pager
journalctl -u check-go-collector.service -n 200 --no-pager

# timer 列表
systemctl list-timers --all | grep check-go-collector || true

# 查看自愈脚本日志文件（如果你写入了该文件）
tail -n 200 /home/ubuntu/ubuntu-wallet/data/logs/check-go-collector.log || true
```
