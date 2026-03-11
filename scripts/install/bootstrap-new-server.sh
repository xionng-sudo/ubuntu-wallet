#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ubuntu/ubuntu-wallet"
ETC_DIR="/etc/ubuntu-wallet"
SYSTEMD_DIR="/etc/systemd/system"
SUDOERS_FILE="/etc/sudoers.d/ubuntu-go-collector-restart"

log() { printf "[%s] %s\n" "$(date +'%F %T')" "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Please run as root: sudo $0"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

ensure_file_exists() { [[ -f "$1" ]] || die "Missing file: $1"; }
ensure_dir_exists() { [[ -d "$1" ]] || die "Missing directory: $1"; }

# Load env file safely (no 'set -a' global side effects)
load_env_file() {
  local f="$1"
  # shellcheck disable=SC1090
  set +u
  source "$f"
  set -u
}

is_empty_var() {
  local name="$1"
  [[ -z "${!name-}" ]]
}

validate_envs_or_return_missing() {
  local missing=()

  # telegram.env required fields
  if [[ -f "${ETC_DIR}/telegram.env" ]]; then
    load_env_file "${ETC_DIR}/telegram.env"
    is_empty_var "TELEGRAM_BOT_TOKEN" && missing+=("telegram.env:TELEGRAM_BOT_TOKEN")
    is_empty_var "TELEGRAM_CHAT_ID" && missing+=("telegram.env:TELEGRAM_CHAT_ID")
  else
    missing+=("telegram.env:(file missing)")
  fi

  # collector.env "required" fields:
  # 这里我们只强制最基础的可运行配置；交易所 key 是否必填取决于你程序逻辑。
  if [[ -f "${ETC_DIR}/collector.env" ]]; then
    load_env_file "${ETC_DIR}/collector.env"
    is_empty_var "COLLECTOR_PORT" && missing+=("collector.env:COLLECTOR_PORT")
    is_empty_var "ML_SERVICE_URL" && missing+=("collector.env:ML_SERVICE_URL")
  else
    missing+=("collector.env:(file missing)")
  fi

  if (( ${#missing[@]} > 0 )); then
    printf "%s\n" "${missing[@]}"
    return 1
  fi
  return 0
}

main() {
  need_root

  require_cmd systemctl
  require_cmd visudo
  require_cmd python3
  require_cmd git

  ensure_dir_exists "${REPO_DIR}"
  ensure_dir_exists "${REPO_DIR}/.git"

  ensure_dir_exists "${REPO_DIR}/go-collector"
  ensure_file_exists "${REPO_DIR}/go-collector/go.mod"
  ensure_file_exists "${REPO_DIR}/go-collector/main.go"

  ensure_dir_exists "${REPO_DIR}/ml-service"
  ensure_file_exists "${REPO_DIR}/ml-service/requirements.txt"
  ensure_file_exists "${REPO_DIR}/ml-service/app.py"

  ensure_file_exists "${REPO_DIR}/systemd/go-collector.service"
  ensure_file_exists "${REPO_DIR}/systemd/ml-service.service"
  ensure_file_exists "${REPO_DIR}/systemd/check-go-collector.service"
  ensure_file_exists "${REPO_DIR}/systemd/check-go-collector.timer"
  ensure_file_exists "${REPO_DIR}/systemd/env/collector.env.example"
  ensure_file_exists "${REPO_DIR}/systemd/env/telegram.env.example"

  log "Creating ${ETC_DIR}"
  mkdir -p "${ETC_DIR}"
  chmod 755 "${ETC_DIR}"

  log "Installing env files from examples (will NOT overwrite existing)"
  if [[ ! -f "${ETC_DIR}/collector.env" ]]; then
    cp "${REPO_DIR}/systemd/env/collector.env.example" "${ETC_DIR}/collector.env"
    log "Created ${ETC_DIR}/collector.env (EDIT THIS FILE!)"
  else
    log "Exists: ${ETC_DIR}/collector.env (skip)"
  fi

  if [[ ! -f "${ETC_DIR}/telegram.env" ]]; then
    cp "${REPO_DIR}/systemd/env/telegram.env.example" "${ETC_DIR}/telegram.env"
    log "Created ${ETC_DIR}/telegram.env (EDIT THIS FILE!)"
  else
    log "Exists: ${ETC_DIR}/telegram.env (skip)"
  fi

  chmod 600 "${ETC_DIR}"/*.env || true
  chown root:root "${ETC_DIR}"/*.env || true

  log "Installing systemd units"
  cp "${REPO_DIR}/systemd/go-collector.service" "${SYSTEMD_DIR}/go-collector.service"
  cp "${REPO_DIR}/systemd/ml-service.service" "${SYSTEMD_DIR}/ml-service.service"
  cp "${REPO_DIR}/systemd/check-go-collector.service" "${SYSTEMD_DIR}/check-go-collector.service"
  cp "${REPO_DIR}/systemd/check-go-collector.timer" "${SYSTEMD_DIR}/check-go-collector.timer"

  log "Installing sudoers: ${SUDOERS_FILE}"
  cat > "${SUDOERS_FILE}" <<'EOF'
ubuntu ALL=NOPASSWD: /bin/systemctl restart go-collector
EOF
  chmod 440 "${SUDOERS_FILE}"
  visudo -cf "${SUDOERS_FILE}"

  log "Reloading systemd"
  systemctl daemon-reload

  log "Ensuring ${REPO_DIR}/bin exists"
  sudo -u ubuntu mkdir -p "${REPO_DIR}/bin"

  if command -v go >/dev/null 2>&1; then
    log "Building go-collector -> ${REPO_DIR}/bin/go-collector"
    sudo -u ubuntu bash -lc "cd '${REPO_DIR}/go-collector' && go mod tidy && go build -o '${REPO_DIR}/bin/go-collector' ."
  else
    log "Go not found; skip go-collector build."
    log "Install Go then run:"
    log "  cd ${REPO_DIR}/go-collector && go build -o ${REPO_DIR}/bin/go-collector ."
  fi

  log "Setting up ml-service venv (best-effort)"
  sudo -u ubuntu bash -lc "
    cd '${REPO_DIR}/ml-service'
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -U pip
    pip install -r requirements.txt
  " || log "ml-service venv setup failed; check: journalctl -u ml-service.service -n 200 --no-pager"

  log "Validating env files before starting services"
  if missing="$(validate_envs_or_return_missing)"; then
    log "Env validation OK. Starting services."
    systemctl enable --now ml-service.service
    systemctl enable --now go-collector.service
    systemctl enable --now check-go-collector.timer
  else
    echo
    echo "============================================================"
    echo "Env validation FAILED. I will NOT start services yet."
    echo
    echo "Missing/empty fields:"
    echo "$missing" | sed 's/^/  - /'
    echo
    echo "Please edit and fill:"
    echo "  sudo nano ${ETC_DIR}/collector.env"
    echo "  sudo nano ${ETC_DIR}/telegram.env"
    echo
    echo "Then start services:"
    echo "  sudo systemctl enable --now ml-service.service"
    echo "  sudo systemctl enable --now go-collector.service"
    echo "  sudo systemctl enable --now check-go-collector.timer"
    echo "============================================================"
  fi

  echo
  echo "Done."
}

main "$@"
