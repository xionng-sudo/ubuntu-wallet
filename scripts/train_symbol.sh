#!/usr/bin/env bash
# train_symbol.sh — per-symbol training convenience wrapper
#
# Reads per-symbol configuration from configs/symbols.yaml via Python helper,
# then calls python-analyzer/train_event_stack_v3.py with the correct
# --data-dir and --model-dir arguments.
#
# Usage:
#   bash scripts/train_symbol.sh BTCUSDT
#   bash scripts/train_symbol.sh ETHUSDT
#   bash scripts/train_symbol.sh SOLUSDT --calibration sigmoid
#   bash scripts/train_symbol.sh BTCUSDT --dry-run        # print command only
#
# Extra arguments after the symbol are forwarded to the training script.
#
# The script derives paths using configs/symbols.yaml conventions:
#   DATA_DIR  = ${REPO_ROOT}/data/<SYMBOL>
#   MODEL_DIR = ${REPO_ROOT}/models/<SYMBOL>
#
# Environment variable overrides:
#   DATA_BASE   override base data directory   (default: <repo_root>/data)
#   MODEL_BASE  override base model directory  (default: <repo_root>/models)
#   PYTHON      python interpreter to use      (default: python3)

set -euo pipefail

SYMBOL="${1:-}"
if [[ -z "${SYMBOL}" ]]; then
    echo "Usage: $0 <SYMBOL> [extra train args...]" >&2
    echo "Example: $0 BTCUSDT --calibration isotonic" >&2
    exit 1
fi
shift  # remaining args are forwarded to train script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON="${PYTHON:-python3}"
DATA_BASE="${DATA_BASE:-${REPO_ROOT}/data}"
MODEL_BASE="${MODEL_BASE:-${REPO_ROOT}/models}"

DATA_DIR="${DATA_BASE}/${SYMBOL}"
MODEL_DIR="${MODEL_BASE}/${SYMBOL}"

# Load per-symbol config (horizon, tp, sl, threshold, calibration)
SYMBOL_CFG="$("${PYTHON}" - <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("REPO_ROOT", ""), "scripts"))
try:
    from symbol_paths import get_symbol_config
    import os as _os
    sym = _os.environ.get("_TRAIN_SYMBOL", "BTCUSDT")
    cfg = get_symbol_config(sym)
    print(
        f"HORIZON={cfg['horizon']}",
        f"TP={cfg['tp']}",
        f"SL={cfg['sl']}",
        f"THRESHOLD={cfg['threshold']}",
        f"CALIBRATION={cfg['calibration']}",
    )
except Exception as e:
    print(f"# WARNING: could not load symbol config: {e}", file=sys.stderr)
    print("HORIZON=12 TP=0.0175 SL=0.009 THRESHOLD=0.65 CALIBRATION=isotonic")
PYEOF
)" || true

# Export for the here-doc subshell
export REPO_ROOT _TRAIN_SYMBOL="${SYMBOL}"

# Parse config values
eval "${SYMBOL_CFG}" 2>/dev/null || true
HORIZON="${HORIZON:-12}"
TP="${TP:-0.0175}"
SL="${SL:-0.009}"
CALIBRATION="${CALIBRATION:-isotonic}"

echo "[train_symbol.sh] symbol=${SYMBOL}"
echo "[train_symbol.sh] data_dir=${DATA_DIR}"
echo "[train_symbol.sh] model_dir=${MODEL_DIR}"
echo "[train_symbol.sh] horizon=${HORIZON}  tp=${TP}  sl=${SL}  calibration=${CALIBRATION}"

# Ensure directories exist
mkdir -p "${DATA_DIR}" "${MODEL_DIR}"

CMD=(
    "${PYTHON}"
    "${REPO_ROOT}/python-analyzer/train_event_stack_v3.py"
    --data-dir  "${DATA_DIR}"
    --model-dir "${MODEL_DIR}"
    --horizon   "${HORIZON}"
    --tp-pct    "${TP}"
    --sl-pct    "${SL}"
    --calibration "${CALIBRATION}"
    "$@"
)

echo "[train_symbol.sh] running: ${CMD[*]}"
exec "${CMD[@]}"
