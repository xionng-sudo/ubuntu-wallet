#!/usr/bin/env bash
set -euo pipefail

URL="http://127.0.0.1:8080/api/healthz"
LOCK_FILE="/run/ubuntu-wallet/check-go-collector.lock"

COOLDOWN_SEC=300
LAST_RESTART_FILE="/tmp/go-collector.last-restart"

NOTIFY_SCRIPT="/home/ubuntu/ubuntu-wallet/scripts/ops/notify-telegram.sh"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
now_epoch() { date +%s; }
log_err() { echo "[$(ts)] $*" >&2; }

notify() {
  local msg="$1"
  if [[ -x "${NOTIFY_SCRIPT}" ]]; then
    "${NOTIFY_SCRIPT}" "$msg" || true
  fi
}

if [[ "${1:-}" == "--test-notify" ]]; then
  notify "test: check-go-collector telegram notify (no restart)"
  exit 0
fi

# Ensure lock dir exists
mkdir -p "$(dirname "$LOCK_FILE")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

maybe_restart() {
  local reason="$1"

  local now last=0 delta=0
  now="$(now_epoch)"
  if [[ -f "$LAST_RESTART_FILE" ]]; then
    last="$(stat -c %Y "$LAST_RESTART_FILE" 2>/dev/null || echo 0)"
  fi
  delta=$(( now - last ))

  if (( delta < COOLDOWN_SEC )); then
    log_err "$reason -> cooldown active (${delta}s < ${COOLDOWN_SEC}s), skip restart"
    notify "go-collector SKIP (cooldown ${delta}s/${COOLDOWN_SEC}s): ${reason}"
    return 0
  fi

  log_err "$reason -> restarting go-collector"
  notify "go-collector RESTART: ${reason}"
  sudo systemctl restart go-collector
  : > "$LAST_RESTART_FILE"
}

if ! command -v jq >/dev/null 2>&1; then
  maybe_restart "jq not found"
  exit 0
fi

out=""
if ! out="$(curl -fsS --max-time 3 "$URL" 2>/dev/null)"; then
  maybe_restart "healthz curl failed"
  exit 0
fi

ok="$(echo "$out" | jq -r '.ok // false' 2>/dev/null || echo "false")"
if [[ "$ok" != "true" ]]; then
  staleness="$(echo "$out" | jq -r '.staleness_sec // empty' 2>/dev/null || true)"
  split="$(echo "$out" | jq -r '.health_require_signals_split // empty' 2>/dev/null || true)"
  files_summary="$(echo "$out" | jq -c '.files' 2>/dev/null | head -c 800 || true)"
  maybe_restart "healthz ok=$ok staleness_sec=${staleness:-unknown} require_split=${split:-unknown} files=$files_summary"
  exit 0
fi

exit 0
