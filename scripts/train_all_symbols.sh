#!/usr/bin/env bash
# train_all_symbols.sh — train all enabled symbols defined in configs/symbols.yaml
#
# Iterates over every symbol with enabled=true and calls train_symbol.sh for each.
# One symbol failure does not prevent the remaining symbols from being trained.
#
# Usage:
#   bash scripts/train_all_symbols.sh
#   bash scripts/train_all_symbols.sh --dry-run       # print commands only, do not run
#   bash scripts/train_all_symbols.sh --calibration sigmoid
#
# Any extra arguments are forwarded to train_symbol.sh (and on to the training script).
#
# Environment variable overrides (passed through to train_symbol.sh):
#   DATA_BASE   override base data directory   (default: <repo_root>/data)
#   MODEL_BASE  override base model directory  (default: <repo_root>/models)
#   PYTHON      python interpreter to use      (default: python3)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON="${PYTHON:-python3}"
DRY_RUN=0
export REPO_ROOT
export SCRIPTS_DIR="${SCRIPT_DIR}"

# Check for --dry-run in args (consume it before forwarding the rest)
FORWARD_ARGS=()
for arg in "$@"; do
    if [[ "${arg}" == "--dry-run" ]]; then
        DRY_RUN=1
    else
        FORWARD_ARGS+=("${arg}")
    fi
done

# Resolve enabled symbols via scripts.symbol_config
SYMBOLS="$(
    "${PYTHON}" - <<'PYEOF'
import sys, os
repo_root = os.environ.get("REPO_ROOT", "")
script_dir = os.environ.get("SCRIPTS_DIR", "")
if repo_root:
    sys.path.insert(0, repo_root)
if script_dir:
    sys.path.insert(0, script_dir)
try:
    from scripts.symbol_config import list_enabled_symbols
    print("\n".join(list_enabled_symbols()))
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)" || { echo "[train_all_symbols.sh] Failed to list enabled symbols." >&2; exit 1; }

if [[ -z "${SYMBOLS}" ]]; then
    echo "[train_all_symbols.sh] No enabled symbols found in configs/symbols.yaml — nothing to do." >&2
    exit 0
fi

echo "[train_all_symbols.sh] Enabled symbols to train:"
echo "${SYMBOLS}" | sed 's/^/  - /'
echo ""

FAILED=()
SUCCEEDED=()

while IFS= read -r SYM; do
    [[ -z "${SYM}" ]] && continue
    echo "[train_all_symbols.sh] === ${SYM} ==="
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[train_all_symbols.sh] [dry-run] would run: bash ${SCRIPT_DIR}/train_symbol.sh ${SYM} ${FORWARD_ARGS[*]+"${FORWARD_ARGS[@]}"}"
        SUCCEEDED+=("${SYM}")
    else
        if bash "${SCRIPT_DIR}/train_symbol.sh" "${SYM}" "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"; then
            echo "[train_all_symbols.sh] ${SYM}: SUCCESS"
            SUCCEEDED+=("${SYM}")
        else
            echo "[train_all_symbols.sh] ${SYM}: FAILED" >&2
            FAILED+=("${SYM}")
        fi
    fi
    echo ""
done <<< "${SYMBOLS}"

echo "[train_all_symbols.sh] === Summary ==="
echo "[train_all_symbols.sh] Succeeded (${#SUCCEEDED[@]}): ${SUCCEEDED[*]+"${SUCCEEDED[*]}"}"
if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "[train_all_symbols.sh] Failed    (${#FAILED[@]}): ${FAILED[*]}" >&2
    exit 1
fi
echo "[train_all_symbols.sh] All symbols trained successfully."
