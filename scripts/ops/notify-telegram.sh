#!/usr/bin/env bash
set -euo pipefail

BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"
TEXT="${1:-}"

if [[ -z "$BOT_TOKEN" || -z "$CHAT_ID" || -z "$TEXT" ]]; then
  exit 0
fi

HOST="$(hostname)"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# escape minimal for Telegram HTML parse mode
ESC_TEXT="$(printf '%s' "$TEXT" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')"
MSG="<b>${HOST}</b> <code>${TS}</code>\n${ESC_TEXT}"

curl -fsS --max-time 5 \
  -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}" \
  -d "text=${MSG}" \
  -d "parse_mode=HTML" \
  -d "disable_web_page_preview=true" >/dev/null || true
