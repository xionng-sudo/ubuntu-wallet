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

activate_venv() {
    # ✅ 你主用的 venv310（优先）
    if [ -f "venv310/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "venv310/bin/activate"
        info "已激活虚拟环境: venv310"
        info "python: $(command -v python)"
        python -V
        return 0
    fi

    # 兼容 install.sh 创建的 venv
    if [ -f "venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "venv/bin/activate"
        info "已激活虚拟环境: venv"
        info "python: $(command -v python)"
        python -V
        return 0
    fi

    warn "未找到 venv310/ 或 venv/，请先创建虚拟环境并安装依赖（例如执行 scripts/install.sh）"
    return 1
}

cleanup_port() {
    # 用法: cleanup_port 8080 "Go Collector"
    local PORT="$1"
    local NAME="$2"

    if [ -z "${PORT}" ]; then
        return 0
    fi

    if ! command -v lsof >/dev/null 2>&1; then
        warn "未安装 lsof，无法自动清理端口 ${PORT}（建议: sudo apt-get install -y lsof）"
        return 0
    fi

    # 先尝试非 sudo（通常足够）
    local PIDS
    PIDS="$(lsof -t -i :"${PORT}" 2>/dev/null || true)"

    # 如果非 sudo 看不到/杀不掉，再尝试 sudo
    if [ -z "${PIDS}" ] && command -v sudo >/dev/null 2>&1; then
        PIDS="$(sudo lsof -t -i :"${PORT}" 2>/dev/null || true)"
    fi

    if [ -z "${PIDS}" ]; then
        return 0
    fi

    warn "发现端口 ${PORT} 已被占用（${NAME}），将尝试关闭 PID: ${PIDS}"

    # 尝试普通 kill
    kill ${PIDS} 2>/dev/null || true

    # 如需要权限，尝试 sudo kill
    if command -v sudo >/dev/null 2>&1; then
        sudo kill ${PIDS} 2>/dev/null || true
    fi

    sleep 1

    local STILL
    STILL="$(lsof -t -i :"${PORT}" 2>/dev/null || true)"
    if [ -z "${STILL}" ] && command -v sudo >/dev/null 2>&1; then
        STILL="$(sudo lsof -t -i :"${PORT}" 2>/dev/null || true)"
    fi

    if [ -n "${STILL}" ]; then
        warn "PID 仍占用端口 ${PORT}，强制 kill -9: ${STILL}"
        kill -9 ${STILL} 2>/dev/null || true
        if command -v sudo >/dev/null 2>&1; then
            sudo kill -9 ${STILL} 2>/dev/null || true
        fi
    fi
}

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
        activate_venv
        cd python-analyzer
        python main.py --analyze 2>&1 | tee "${PY_LOG}"
        ;;

    "train")
        info "训练 ML 模型..."
        activate_venv
        cd python-analyzer
        python main.py --train 2>&1 | tee "${PY_LOG}"
        ;;

    "predict")
        info "运行预测..."
        activate_venv
        cd python-analyzer
        python main.py --predict 2>&1 | tee "${PY_LOG}"
        ;;

    "dashboard")
        info "启动仪表板..."
        # ✅ 仅 dashboard 模式：Ctrl+C 自动 stop（用绝对路径，避免 cd 后找不到脚本）
        trap '"${PROJECT_DIR}/scripts/run.sh" stop' INT TERM

        # ✅ 策略1：启动前清理 8050
        cleanup_port "${DASH_PORT:-8050}" "Dash Dashboard"

        activate_venv
        cd python-analyzer
        python main.py --dashboard 2>&1 | tee "${PY_LOG}"
        ;;

    "charts")
        info "生成图表..."
        activate_venv
        cd python-analyzer
        python main.py --save-charts 2>&1 | tee "${PY_LOG}"
        ;;

    "full")
        info "启动完整系统..."
        # ✅ 仅 full 模式：Ctrl+C 自动 stop（用绝对路径，避免 cd 后找不到脚本）
        trap '"${PROJECT_DIR}/scripts/run.sh" stop' INT TERM

        # ✅ 启动前清理旧 Go Collector 端口（通常 8080）
        cleanup_port "${COLLECTOR_PORT:-8080}" "Go Collector"

        # ✅ 启动前也清理 Dash 端口（通常 8050）
        cleanup_port "${DASH_PORT:-8050}" "Dash Dashboard"

        # 1. 启动 Go Collector (后台)
        info "启动 Go 数据采集器 (后台)..."
        nohup ./bin/go-collector > "${GO_LOG}" 2>&1 &
        GO_PID=$!
        echo $GO_PID > "${LOG_DIR}/go-collector.pid"
        info "Go Collector PID: ${GO_PID}"

        # ✅ 等待采集器 API 就绪（最多 30 秒）
        info "等待 Go Collector API 就绪..."
        READY=0
        for i in {1..30}; do
            if curl -s "http://localhost:${COLLECTOR_PORT:-8080}/api/status" > /dev/null 2>&1; then
                READY=1
                break
            fi
            sleep 1
        done

        if [ "$READY" -eq 1 ]; then
            info "Go Collector 启动成功 ✓"
        else
            warn "Go Collector API 30 秒仍不可用，将继续启动 Python（可能导致首次采集失败）"
        fi

        # 2. 启动 Python 分析 + 仪表板（严格要求 venv）
        info "启动 Python 分析系统..."
        activate_venv
        cd python-analyzer
        python main.py 2>&1 | tee "${PY_LOG}"
        ;;

    "stop")
        info "停止所有服务..."
        if [ -f "${LOG_DIR}/go-collector.pid" ]; then
            GO_PID=$(cat "${LOG_DIR}/go-collector.pid")
            if kill -0 $GO_PID 2>/dev/null; then
                kill $GO_PID 2>/dev/null || true
                if command -v sudo >/dev/null 2>&1; then
                    sudo kill $GO_PID 2>/dev/null || true
                fi
                info "Go Collector (PID: ${GO_PID}) 已停止"
            fi
            rm -f "${LOG_DIR}/go-collector.pid"
        fi

        # ✅ 兜底：按端口关闭仍在监听的 Go Collector
        cleanup_port "${COLLECTOR_PORT:-8080}" "Go Collector"

        # 停止 Python 进程（Dash 也会一并停掉）
        pkill -f "python main.py" 2>/dev/null || true

        # ✅ 兜底：按端口关闭 Dashboard
        cleanup_port "${DASH_PORT:-8050}" "Dash Dashboard"

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
        if curl -s http://localhost:${DASH_PORT:-8050} > /dev/null 2>&1; then
            echo -e "Dashboard:     ${GREEN}可用${NC} (http://localhost:${DASH_PORT:-8050})"
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
