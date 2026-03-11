# ubuntu-wallet 新服务器部署说明（最佳实践）

适用环境：
- Ubuntu 22.04
- 用户：`ubuntu`
- 仓库路径固定：`/home/ubuntu/ubuntu-wallet`
- go-collector 二进制固定输出：`/home/ubuntu/ubuntu-wallet/bin/go-collector`（路径 A）

原则：
- **代码 / 脚本 / systemd 模板进 Git**
- **API key、Telegram token/chat_id 只放服务器 `/etc/ubuntu-wallet/*.env`，永不进 Git**
- `.venv/`、日志、缓存等运行产物不进 Git，新机器重建

---

## 目录与文件约定

仓库内：
- `scripts/ops/check-go-collector.sh`
- `scripts/ops/notify-telegram.sh`
- `systemd/go-collector.service`
- `systemd/ml-service.service`
- `systemd/check-go-collector.service`
- `systemd/check-go-collector.timer`
- `systemd/env/collector.env.example`
- `systemd/env/telegram.env.example`

服务器本地（敏感信息，不进 Git）：
- `/etc/ubuntu-wallet/collector.env`（交易所 key + collector 配置）
- `/etc/ubuntu-wallet/telegram.env`（Telegram token/chat_id）

---

## 1) 基础依赖安装

```bash
sudo apt update
sudo apt install -y git curl jq python3 python3-venv
```

Go（如未安装）请按你的 Go 安装方式完成安装，并确保：
```bash
go version
```

---

## 2) 拉取仓库

```bash
cd ~
git clone https://github.com/xionghan889-tech/ubuntu-wallet.git
cd ubuntu-wallet
mkdir -p bin
```

---

## 3) 创建敏感配置文件（不进 Git）

### 3.1 创建目录
```bash
sudo mkdir -p /etc/ubuntu-wallet
sudo chmod 755 /etc/ubuntu-wallet
```

### 3.2 使用 example 快速生成（推荐）
```bash
sudo cp /home/ubuntu/ubuntu-wallet/systemd/env/collector.env.example /etc/ubuntu-wallet/collector.env
sudo cp /home/ubuntu/ubuntu-wallet/systemd/env/telegram.env.example /etc/ubuntu-wallet/telegram.env

sudo chmod 600 /etc/ubuntu-wallet/*.env
sudo chown root:root /etc/ubuntu-wallet/*.env

# 填写真实 key/token
sudo nano /etc/ubuntu-wallet/collector.env
sudo nano /etc/ubuntu-wallet/telegram.env
```

> 注意：`/etc/ubuntu-wallet/*.env` 只存在于服务器本地，严禁提交到 GitHub。

---

## 4) 构建 go-collector 到固定路径（路径 A）

```bash
cd ~/ubuntu-wallet/go-collector
go mod tidy
go build -o ~/ubuntu-wallet/bin/go-collector .
ls -lh ~/ubuntu-wallet/bin/go-collector
```

---

## 5) 安装 ml-service（创建 venv + 安装依赖）

```bash
cd ~/ubuntu-wallet/ml-service
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
deactivate
```

---

## 6) 安装 systemd unit（从仓库模板复制）

```bash
sudo cp ~/ubuntu-wallet/systemd/go-collector.service /etc/systemd/system/go-collector.service
sudo cp ~/ubuntu-wallet/systemd/ml-service.service /etc/systemd/system/ml-service.service
sudo cp ~/ubuntu-wallet/systemd/check-go-collector.service /etc/systemd/system/check-go-collector.service
sudo cp ~/ubuntu-wallet/systemd/check-go-collector.timer /etc/systemd/system/check-go-collector.timer
sudo systemctl daemon-reload
```

---

## 7) sudoers（允许自愈脚本重启 go-collector）

```bash
sudo tee /etc/sudoers.d/ubuntu-go-collector-restart >/dev/null <<'EOF'
ubuntu ALL=NOPASSWD: /bin/systemctl restart go-collector
EOF
sudo visudo -cf /etc/sudoers.d/ubuntu-go-collector-restart
```

---

## 8) 启动服务

```bash
sudo systemctl enable --now ml-service.service
sudo systemctl enable --now go-collector.service
sudo systemctl enable --now check-go-collector.timer
```

查看状态：
```bash
systemctl status ml-service.service --no-pager
systemctl status go-collector.service --no-pager
systemctl status check-go-collector.timer --no-pager
```

---

## 9) 验收（必须做）

### 9.1 ml-service
```bash
curl -fsS http://127.0.0.1:9000/docs | head
```

### 9.2 go-collector healthz
```bash
curl -fsS --max-time 3 http://127.0.0.1:8080/api/healthz | jq .
```

### 9.3 自愈 timer 是否在跑
```bash
systemctl list-timers --all | grep check-go-collector || true
journalctl -u check-go-collector.service -n 80 --no-pager
tail -n 200 /home/ubuntu/ubuntu-wallet/check-go-collector.log || true
```

---

## 常见故障排查

### A) 某个服务起不来
```bash
journalctl -u go-collector.service -n 200 --no-pager
journalctl -u ml-service.service -n 200 --no-pager
```

### B) Telegram 不发消息
- 确认 `/etc/ubuntu-wallet/telegram.env` 权限是 `600 root:root`
- 确认 `systemd/check-go-collector.service` 里有 `EnvironmentFile=/etc/ubuntu-wallet/telegram.env`
- 查看：
  ```bash
  journalctl -u check-go-collector.service -n 200 --no-pager
  ```

### C) ml-service 端口 9000 连接拒绝
- 查看：
  ```bash
  systemctl status ml-service.service --no-pager
  journalctl -u ml-service.service -n 200 --no-pager
  ```
- 通常原因：`.venv` 未创建、依赖没装、或 `requirements.txt` 安装失败

---

## 安全注意事项（强制）
- **任何 token/key 泄露（包括聊天记录里出现过）都应立即作废并更换**
- 不要把 `/etc/ubuntu-wallet/*.env` 放进仓库或复制到公开位置
- 建议关闭 SSH 密码登录，使用密钥认证
- 每次修改 unit 后务必：
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl restart <service>
  ```
