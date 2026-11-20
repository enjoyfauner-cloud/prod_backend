#!/bin/sh
# Копирует provisioning в рабочую папку и подставляет секреты Telegram в контакт алертов
# как строки, затем запускает стандартный entrypoint Grafana.
set -e
WORK=/tmp/provisioning
rm -rf "$WORK" && cp -r /etc/grafana/provisioning "$WORK"
TOKEN="${TELEGRAM_BOT_TOKEN:-not-configured}"
CHAT="${TELEGRAM_CHAT_ID:-0}"
# допускаем только символы, которые реально бывают в токене/chat_id (защита от поломки YAML)
case "$TOKEN$CHAT" in *[!A-Za-z0-9:_-]*) echo "bad TELEGRAM_* value" >&2; exit 1;; esac
sed -e "s|__TELEGRAM_BOT_TOKEN__|$TOKEN|" -e "s|__TELEGRAM_CHAT_ID__|$CHAT|" \
  /etc/grafana/templates/contact-points.yml.tmpl > "$WORK/alerting/contact-points.yml"
export GF_PATHS_PROVISIONING="$WORK"
exec /run.sh "$@"
