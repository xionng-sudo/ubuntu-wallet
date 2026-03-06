#!/bin/bash
# ============================================
# ETH Crypto Prediction System
# 一键启动脚本
# ============================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${PROJECT_DIR}"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# 加载环境变量
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    info "环境变量已加载"
else
    warn ".env 文件不存在，使用默认配置"
fi

# 创建目录
mkdir -p data models logs

# 日志文件
LOG_DIR="${PROJECT_DIR}/logs"
GO_LOG="${LOG_DIR}/go-collector.log"
PY_LOG="${LOG_DIR}/python-analyzer.log"

MODE=${1:-"full"}

case "$MODE" in
    "collector")
        info "仅启动 Go 数据采集器..."
        ./bin/go-collector 2>&1 | tee "${GO_LOG}"
        ;;

    "analyze")
        info "仅运行 Python 分析..."
        source venv/bin/activate
        cd python-analyzer
        python main.py --analyze 2>&1 | tee "${PY_LOG}"
        ;;

    "train")
        info "训练 ML 模型..."
        source venv/bin/activate
        cd python-analyzer
        python main.py --train 2>&1 | tee "${PY_LOG}"
        ;;

    "predict")
        info "运行预测..."
        source venv/bin/activate
        cd python-analyzer
        python main.py --predict 2>&1 | tee "${PY_LOG}"
        ;;

    "dashboard")
        info "启动仪表板..."
        source venv/bin/activate
        cd python-analyzer
        python main.py --dashboard 2>&1 | tee "${PY_LOG}"
        ;;

    "charts")
        info "生成图表..."
        source venv/bin/activate
        cd python-analyzer
        python main.py --save-charts 2>&1 | tee "${PY_LOG}"
        ;;

    "full")
        info "启动完整系统..."

        # 1. 启动 Go Collector (后台)
        info "启动 Go 数据采集器 (后台)..."
        nohup ./bin/go-collector > "${GO_LOG}" 2>&1 &
        GO_PID=$!
        echo $GO_PID > "${LOG_DIR}/go-collector.pid"
        info "Go Collector PID: ${GO_PID}"

        # 等待采集器启动
        sleep 3

        # 检查采集器是否正常
        if curl -s http://localhost:${COLLECTOR_PORT:-8080}/api/status > /dev/null 2>&1; then
            info "Go Collector 启动成功 ✓"
        else
            warn "Go Collector 可能未完全启动，继续..."
        fi

        # 2. 启动 Python 分析 + 仪表板
        info "启动 Python 分析系统..."
        source venv/bin/activate
        cd python-analyzer
        python main.py 2>&1 | tee "${PY_LOG}"
        ;;

    "stop")
        info "停止所有服务..."
        if [ -f "${LOG_DIR}/go-collector.pid" ]; then
            GO_PID=$(cat "${LOG_DIR}/go-collector.pid")
            if kill -0 $GO_PID 2>/dev/null; then
                kill $GO_PID
                info "Go Collector (PID: ${GO_PID}) 已停止"
            fi
            rm -f "${LOG_DIR}/go-collector.pid"
        fi
        # 停止 Python 进程
        pkill -f "python main.py" 2>/dev/null || true
        info "所有服务已停止"
        ;;

    "status")
        echo ""
        echo "========== 系统状态 =========="
        # Go Collector 状态
        if [ -f "${LOG_DIR}/go-collector.pid" ]; then
            GO_PID=$(cat "${LOG_DIR}/go-collector.pid")
            if kill -0 $GO_PID 2>/dev/null; then
                echo -e "Go Collector:  ${GREEN}运行中${NC} (PID: ${GO_PID})"
            else
                echo -e "Go Collector:  ${YELLOW}已停止${NC}"
            fi
        else
            echo -e "Go Collector:  ${YELLOW}未启动${NC}"
        fi

        # Python 状态
        PY_PID=$(pgrep -f "python main.py" 2>/dev/null || true)
        if [ -n "$PY_PID" ]; then
            echo -e "Python 分析:   ${GREEN}运行中${NC} (PID: ${PY_PID})"
        else
            echo -e "Python 分析:   ${YELLOW}未启动${NC}"
        fi

        # API 状态
        if curl -s http://localhost:${COLLECTOR_PORT:-8080}/api/status > /dev/null 2>&1; then
            echo -e "Collector API: ${GREEN}可用${NC} (port ${COLLECTOR_PORT:-8080})"
            curl -s http://localhost:${COLLECTOR_PORT:-8080}/api/status | jq . 2>/dev/null || true
        else
            echo -e "Collector API: ${YELLOW}不可用${NC}"
        fi

        # Dashboard 状态
        if curl -s http://localhost:8050 > /dev/null 2>&1; then
            echo -e "Dashboard:     ${GREEN}可用${NC} (http://localhost:8050)"
        else
            echo -e "Dashboard:     ${YELLOW}不可用${NC}"
        fi
        echo "=============================="
        ;;

    *)
        echo "用法: $0 {full|collector|analyze|train|predict|dashboard|charts|stop|status}"
        echo ""
        echo "  full       - 启动完整系统 (默认)"
        echo "  collector  - 仅启动 Go 数据采集器"
        echo "  analyze    - 仅运行技术分析"
        echo "  train      - 训练 ML 模型"
        echo "  predict    - 运行预测"
        echo "  dashboard  - 启动可视化仪表板"
        echo "  charts     - 生成并保存图表"
        echo "  stop       - 停止所有服务"
        echo "  status     - 查看系统状态"
        exit 1
        ;;
esac
