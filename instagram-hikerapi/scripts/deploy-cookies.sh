#!/bin/bash
set -euo pipefail

# deploy-cookies.sh -- копирует cookies.json на your server и перезапускает Cobalt

SECRETS_DIR="$HOME/.secrets/cobalt"
COBALT_SERVER="${COBALT_SERVER:?Set COBALT_SERVER=user@host (SSH target for your self-hosted Cobalt instance)}"

if [ ! -f "$SECRETS_DIR/cookies.json" ]; then
  echo "ERROR: $SECRETS_DIR/cookies.json not found" >&2
  exit 1
fi

echo "Deploying cookies to your server..." >&2
scp "$SECRETS_DIR/cookies.json" "$COBALT_SERVER:/opt/cobalt/cookies.json"

echo "Restarting Cobalt..." >&2
ssh "$COBALT_SERVER" "cd /opt/cobalt && docker compose restart cobalt-api" 2>&1

sleep 3

# Проверяем что куки загрузились
LOGS=$(ssh "$COBALT_SERVER" "docker logs cobalt-api 2>&1 | tail -3")
if echo "$LOGS" | grep -q "cookies loaded successfully"; then
  echo "OK: cookies deployed and loaded" >&2
else
  echo "WARNING: cookies may not have loaded. Check logs:" >&2
  echo "$LOGS" >&2
fi
