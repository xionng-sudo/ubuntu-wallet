#!/usr/bin/env bash
set -euo pipefail

# 可根据需要修改或通过环境变量覆盖
VENV="${VENV:-venv310}"
DATA_DIR="${DATA_DIR:-data}"
LOG_PATH="${LOG_PATH:-data/predictions_log.jsonl}"
ACTIVE_MODEL="${ACTIVE_MODEL:-event_v3}"
INTERVAL="${INTERVAL:-1h}"

# 评估时间窗口（默认最近 7 天）
# 你可以在调用前 export SINCE/UNTIL 来覆盖
SINCE_DEFAULT=$(date -u -d "-7 days" +"%Y-%m-%dT%H:%M:%SZ")
UNTIL_DEFAULT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

SINCE="${SINCE:-$SINCE_DEFAULT}"
UNTIL="${UNTIL:-$UNTIL_DEFAULT}"

# 当前推荐参数（单仓 + 6 小时 + 多周期过滤）
THRESHOLD="${THRESHOLD:-0.55}"
TP="${TP:-0.0175}"   # 1.75%
SL="${SL:-0.009}"    # 0.90%
FEE="${FEE:-0.0004}"
SLIPPAGE="${SLIPPAGE:-0.0}"
HORIZON_BARS="${HORIZON_BARS:-6}"

echo "=== RUN LIVE EVAL 1H (event_v3) ==="
echo "Using window: since=$SINCE until=$UNTIL"
echo "Params: threshold=$THRESHOLD tp=$TP sl=$SL horizon_bars=$HORIZON_BARS fee=$FEE slippage=$SLIPPAGE"
echo

# 激活虚拟环境（如果已经在 venv 里，可以去掉这一行）
if [ -d "$VENV" ]; then
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
fi

python scripts/evaluate_from_logs.py \
  --log-path "$LOG_PATH" \
  --data-dir "$DATA_DIR" \
  --interval "$INTERVAL" \
  --active-model "$ACTIVE_MODEL" \
  --since "$SINCE" \
  --until "$UNTIL" \
  --threshold "$THRESHOLD" \
  --tp "$TP" \
  --sl "$SL" \
  --fee "$FEE" \
  --slippage "$SLIPPAGE" \
  --horizon-bars "$HORIZON_BARS"
