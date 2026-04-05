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

# Export first so the Python helper can see them
export REPO_ROOT
export _TRAIN_SYMBOL="${SYMBOL}"

DRY_RUN=0
FORWARD_ARGS=()
for arg in "$@"; do
    if [[ "${arg}" == "--dry-run" ]]; then
        DRY_RUN=1
    else
        FORWARD_ARGS+=("${arg}")
    fi
done

# Load per-symbol config (horizon, tp, sl, threshold, calibration)
SYMBOL_CFG="$("${PYTHON}" - <<'PYEOF'
import sys, os

repo_root = os.environ.get("REPO_ROOT", "")
if not repo_root:
    print("ERROR: REPO_ROOT environment variable is not set", file=sys.stderr)
    sys.exit(1)
sys.path.insert(0, repo_root)

try:
    from scripts.symbol_config import get_symbol_config
except ImportError as e:
    print(f"ERROR: could not import scripts.symbol_config: {e}", file=sys.stderr)
    sys.exit(1)

yaml_path = os.path.join(repo_root, "configs", "symbols.yaml")
if not os.path.isfile(yaml_path):
    print(f"ERROR: configs/symbols.yaml not found at {yaml_path}", file=sys.stderr)
    sys.exit(1)

sym = os.environ.get("_TRAIN_SYMBOL", "")
if not sym:
    print("ERROR: _TRAIN_SYMBOL environment variable is not set", file=sys.stderr)
    sys.exit(1)

try:
    cfg = get_symbol_config(sym)
    print(
        f"HORIZON={cfg['horizon']}",
        f"TP={cfg['tp']}",
        f"SL={cfg['sl']}",
        f"THRESHOLD={cfg['threshold']}",
        f"CALIBRATION={cfg['calibration']}",
    )
except KeyError as e:
    print(f"ERROR: missing required key {e} in symbol config for '{sym}'", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"ERROR: could not load symbol config for '{sym}': {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)" || { echo "[train_symbol.sh] Failed to load symbol config for ${SYMBOL}." >&2; exit 1; }

# Parse config values (no fallback defaults — exit if any value is missing)
eval "${SYMBOL_CFG}"
if [[ -z "${HORIZON:-}" || -z "${TP:-}" || -z "${SL:-}" || -z "${CALIBRATION:-}" ]]; then
    echo "[train_symbol.sh] ERROR: incomplete symbol config for ${SYMBOL} (HORIZON=${HORIZON:-} TP=${TP:-} SL=${SL:-} CALIBRATION=${CALIBRATION:-})" >&2
    exit 1
fi

echo "[train_symbol.sh] symbol=${SYMBOL}"
echo "[train_symbol.sh] data_dir=${DATA_DIR}"
echo "[train_symbol.sh] model_dir=${MODEL_DIR}"
echo "[train_symbol.sh] horizon=${HORIZON}  tp=${TP}  sl=${SL}  calibration=${CALIBRATION}"

# Ensure directories exist
mkdir -p "${DATA_DIR}" "${MODEL_DIR}"

# Detect if user passed --calibration in forwarded args (CLI wins over YAML)
HAS_CAL_OVERRIDE=0
for arg in "${FORWARD_ARGS[@]}"; do
    if [[ "${arg}" == "--calibration" ]]; then
        HAS_CAL_OVERRIDE=1
        break
    fi
done

CMD=(
    "${PYTHON}"
    "${REPO_ROOT}/python-analyzer/train_event_stack_v3.py"
    --data-dir "${DATA_DIR}"
    --model-dir "${MODEL_DIR}"
    --horizon "${HORIZON}"
    --tp-pct "${TP}"
    --sl-pct "${SL}"
)
if [[ "${HAS_CAL_OVERRIDE}" -eq 0 ]]; then
    CMD+=(--calibration "${CALIBRATION}")
fi
if [[ ${#FORWARD_ARGS[@]} -gt 0 ]]; then
    CMD+=("${FORWARD_ARGS[@]}")
fi

echo "[train_symbol.sh] running: ${CMD[*]}"

if [[ "${DRY_RUN}" == "1" ]]; then
    exit 0
fi

exec "${CMD[@]}"
