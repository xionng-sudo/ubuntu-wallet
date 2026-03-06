#!/bin/bash
# ============================================
# ETH Crypto Prediction System
# Ubuntu 22.04 一键安装脚本
# ============================================

set -e

echo "=========================================="
echo "  ETH Crypto Prediction System Installer"
echo "  目标系统: Ubuntu 22.04"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ─── 步骤 1: 系统更新 ───
info "步骤 1/8: 更新系统包..."
sudo apt update && sudo apt upgrade -y

# ─── 步骤 2: 安装基础工具 ───
info "步骤 2/8: 安装基础工具..."
sudo apt install -y \
    build-essential \
    git \
    curl \
    wget \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    jq

# ─── 步骤 3: 安装 Python 3.11 ───
info "步骤 3/8: 安装 Python 3.11..."
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# 设置 Python 3.11 为默认
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
python3 --version

# ─── 步骤 4: 安装 Go 1.21 ───
info "步骤 4/8: 安装 Go 1.21..."
GO_VERSION="1.21.13"
if ! command -v go &>/dev/null || [[ "$(go version)" != *"go1.21"* ]]; then
    wget -q "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -O /tmp/go.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz

    # 添加到 PATH
    echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
    echo 'export GOPATH=$HOME/go' >> ~/.bashrc
    echo 'export PATH=$PATH:$GOPATH/bin' >> ~/.bashrc
    export PATH=$PATH:/usr/local/go/bin
fi
go version

# ─── 步骤 5: 安装 TA-Lib 系统库 ───
info "步骤 5/8: 安装 TA-Lib C 库..."
if ! ldconfig -p | grep -q libta_lib; then
    cd /tmp
    wget -q http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
    tar -xzf ta-lib-0.4.0-src.tar.gz
    cd ta-lib/
    ./configure --prefix=/usr
    make -j$(nproc)
    sudo make install
    sudo ldconfig
    cd -
    rm -rf /tmp/ta-lib /tmp/ta-lib-0.4.0-src.tar.gz
fi
info "TA-Lib 安装完成"

# ─── 步骤 6: 设置 Python 虚拟环境 ───
info "步骤 6/8: 设置 Python 虚拟环境..."
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${PROJECT_DIR}"

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip setuptools wheel

info "安装 Python 依赖 (这可能需要几分钟)..."
pip install -r python-analyzer/requirements.txt

# ─── 步骤 7: 编译 Go 程序 ───
info "步骤 7/8: 编译 Go 数据采集器..."
cd "${PROJECT_DIR}/go-collector"
go mod tidy
go build -o ../bin/go-collector .
cd "${PROJECT_DIR}"

info "Go 采集器编译完成: bin/go-collector"

# ─── 步骤 8: 创建必要目录和配置 ───
info "步骤 8/8: 创建目录和配置..."
mkdir -p data models bin logs

# 复制环境变量模板
if [ ! -f .env ]; then
    cp .env.example .env
    warn "已创建 .env 文件，请编辑填入你的 API 密钥！"
    warn "编辑命令: nano .env"
fi

# ─── 完成 ───
echo ""
echo "=========================================="
echo -e "${GREEN}  安装完成!${NC}"
echo "=========================================="
echo ""
echo "接下来的步骤:"
echo ""
echo "  1. 配置 API 密钥:"
echo "     nano .env"
echo ""
echo "  2. 启动 Go 数据采集器:"
echo "     ./bin/go-collector"
echo ""
echo "  3. 启动 Python 分析 (新终端):"
echo "     source venv/bin/activate"
echo "     cd python-analyzer"
echo "     python main.py"
echo ""
echo "  4. 打开浏览器访问仪表板:"
echo "     http://localhost:8050"
echo ""
echo "=========================================="
