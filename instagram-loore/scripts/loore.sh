#!/usr/bin/env bash
set -euo pipefail

# loore.sh -- call one Loore REST method (POST https://mcp.loore.ai/v1/<method>)
# Usage:
#   bash loore.sh <method> ['<json body>']
# Examples:
#   bash loore.sh skill-list
#   bash loore.sh ig-profile '{"username":"@brand"}'
#   bash loore.sh ig-user-reels '{"username":"@brand","limit":50}'
#   LOORE_IDEMPOTENCY_KEY="scan-2026-11-07" bash loore.sh tracking-refresh '{"account":"@brand"}'
#
# Env:
#   LOORE_API_KEY          required, key from app.loore.ai -> Settings -> API & MCP
#   LOORE_IDEMPOTENCY_KEY  optional, sent as Idempotency-Key for paid or long operations
#
# Prints the raw JSON envelope {status, result | error, details} to stdout.
# Exit code: 0 on HTTP 2xx, 1 otherwise (the envelope is still printed).

BASE_URL="https://mcp.loore.ai/v1"

METHOD="${1:?Usage: loore.sh <method> ['<json body>']}"
BODY="${2:-}"
if [ -z "$BODY" ]; then
  BODY='{}'
fi

if [ -z "${LOORE_API_KEY:-}" ]; then
  echo "ERROR: LOORE_API_KEY is not set (create a key at app.loore.ai -> Settings -> API & MCP)" >&2
  exit 1
fi

# REST paths are kebab-case: ig_user_reels -> ig-user-reels
METHOD="${METHOD//_/-}"
if [[ ! "$METHOD" =~ ^[a-z0-9-]+$ ]]; then
  echo "ERROR: invalid method name: $METHOD" >&2
  exit 1
fi

echo "POST /v1/$METHOD" >&2

# The key goes to curl through a config on stdin, so it never shows up
# in the process list or in shell history.
HTTP_CODE_FILE="$(mktemp)"
trap 'rm -f "$HTTP_CODE_FILE"' EXIT

{
  printf 'header = "Authorization: Bearer %s"\n' "$LOORE_API_KEY"
  if [ -n "${LOORE_IDEMPOTENCY_KEY:-}" ]; then
    printf 'header = "Idempotency-Key: %s"\n' "$LOORE_IDEMPOTENCY_KEY"
  fi
} | curl -sS --config - \
  -X POST "$BASE_URL/$METHOD" \
  -H "Content-Type: application/json" \
  --data-binary "$BODY" \
  --max-time 300 \
  -w '\n%{http_code}' \
  | {
    RESPONSE="$(cat)"
    printf '%s\n' "${RESPONSE%$'\n'*}"
    printf '%s' "${RESPONSE##*$'\n'}" > "$HTTP_CODE_FILE"
  }

HTTP_CODE="$(cat "$HTTP_CODE_FILE")"
case "$HTTP_CODE" in
  2??) exit 0 ;;
  402) echo "HTTP 402: out of credits or plan quota -- stop and tell the user" >&2 ;;
  429) echo "HTTP 429: rate limited -- wait and retry with a growing delay" >&2 ;;
  401) echo "HTTP 401: key missing, revoked or malformed" >&2 ;;
  403) echo "HTTP 403: method is outside this key's scope or plan" >&2 ;;
  *) echo "HTTP $HTTP_CODE" >&2 ;;
esac
exit 1
