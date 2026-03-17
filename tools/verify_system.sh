#!/usr/bin/env bash
# tools/verify_system.sh
# Lightweight system verify script for ubuntu-wallet ml-service.
# Non-destructive. Writes a report to /tmp/verify_report_<ts>.txt
#
# Usage:
#   mkdir -p tools
#   cat > tools/verify_system.sh <<'SH'  # (or use your editor) then paste this file
#   chmod +x tools/verify_system.sh
#   ./tools/verify_system.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT="/tmp/verify_report_$(date +%s).txt"
VENVS=("ml-service/.venv" ".venv" "venv-analyzer")
SUDO="sudo"

echo "Verify run at: $(date -u +'%Y-%m-%dT%H:%M:%SZ')" | tee "$REPORT"
echo "Repo root: $ROOT" | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

cd "$ROOT" || exit 1

note() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$REPORT"; }
run_cmd() { echo "\$ $*" | tee -a "$REPORT"; bash -c "$*" 2>&1 | tee -a "$REPORT"; }

note "GIT: branch & head"
run_cmd "git rev-parse --abbrev-ref HEAD || true"
run_cmd "git rev-parse --short HEAD || true"
echo "" >> "$REPORT"

note "SYSTEMD: ml-service state"
if $SUDO -n true 2>/dev/null; then
  run_cmd "$SUDO systemctl is-active ml-service || true"
  run_cmd "$SUDO systemctl status ml-service -n 20 --no-pager || true"
else
  echo "SKIP: sudo not available for systemctl checks" | tee -a "$REPORT"
fi
echo "" >> "$REPORT"

note "NETWORK: listening ports (9000/8000)"
run_cmd "ss -ltnp | grep -E '9000|8000' || true"
echo "" >> "$REPORT"

note "HTTP: /healthz"
run_cmd "curl -sS http://127.0.0.1:9000/healthz || true"
echo "" >> "$REPORT"

note "HTTP: POST /predict smoke test (symbol=ETHUSDT interval=1h)"
run_cmd "curl -sS -X POST http://127.0.0.1:9000/predict -H 'Content-Type: application/json' -d '{\"symbol\":\"ETHUSDT\",\"interval\":\"1h\"}' || true"
echo "" >> "$REPORT"

# feature schema check (if a venv exists)
ACTIVATE=""
for v in "${VENVS[@]}"; do
  if [ -f "$v/bin/activate" ]; then
    ACTIVATE="$v/bin/activate"
    break
  fi
done

note "FEATURE SCHEMA: export_feature_schema.py --rebuild --validate-inference-row"
if [ -n "$ACTIVATE" ]; then
  echo "Activating venv: $ACTIVATE" | tee -a "$REPORT"
  (
    set -o pipefail
    . "$ACTIVATE"
    python3 scripts/export_feature_schema.py --model-dir models/current --data-dir data --rebuild --validate-inference-row 2>&1 | tee -a "$REPORT" || true
  )
else
  echo "SKIP: no venv found (${VENVS[*]}) - cannot run export_feature_schema" | tee -a "$REPORT"
fi
echo "" >> "$REPORT"

note "REGISTRY: models/registry.json and rollback dry-run"
if [ -f models/registry.json ]; then
  run_cmd "cat models/registry.json"
  run_cmd "python3 scripts/rollback_model.py --model-dir models --dry-run || true"
else
  echo "models/registry.json missing" | tee -a "$REPORT"
fi
echo "" >> "$REPORT"

note "MODELS: check models/current and top-level artifacts"
run_cmd "test -d models/current && echo 'models/current exists' || echo 'models/current missing'"
run_cmd "ls -la models | sed -n '1,160p' || true"
echo "" >> "$REPORT"

note "LOGS: recent ml-service errors (journalctl -p err)"
if $SUDO -n true 2>/dev/null; then
  run_cmd "$SUDO journalctl -u ml-service -p err -n 200 --no-pager || true"
else
  echo "SKIP: sudo not available for journalctl" | tee -a "$REPORT"
fi
echo "" >> "$REPORT"

echo "Report saved to: $REPORT" | tee -a "$REPORT"
echo "End of report." | tee -a "$REPORT"
