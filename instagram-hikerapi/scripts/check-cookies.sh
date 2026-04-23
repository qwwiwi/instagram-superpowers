#!/bin/bash
set -euo pipefail

# check-cookies.sh -- проверка валидности куков для каждой платформы

COBALT_SERVER="${COBALT_SERVER:?Set COBALT_SERVER=user@host (SSH target for your self-hosted Cobalt instance)}"

echo "=== Instagram ==="
ssh "$COBALT_SERVER" bash <<'REMOTE'
COOKIES=$(python3 -c 'import json; print(json.load(open("/opt/cobalt/cookies.json")).get("instagram",[""])[0])' 2>/dev/null)
if [ -z "$COOKIES" ]; then
  echo "NO COOKIES"
else
  curl -s "https://i.instagram.com/api/v1/accounts/current_user/" \
    -H "Cookie: $COOKIES" \
    -H "User-Agent: Instagram 385.0.0.47.74 Android" \
    -H "X-IG-App-ID: 567067343352427" \
  | python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("INVALID: parse error"); sys.exit()
if "user" in d:
    print(f"VALID: {d["user"]["username"]}")
else:
    print(f"INVALID: {d.get("message", "no response")}")'
fi
REMOTE

echo ""
echo "=== YouTube ==="
ssh "$COBALT_SERVER" '
YT_COOKIES=$(python3 -c "import json; c=json.load(open(\"/opt/cobalt/cookies.json\")); print(c.get(\"youtube\",[\"\"])[0])" 2>/dev/null)
if [ -z "$YT_COOKIES" ]; then
  echo "NO COOKIES"
else
  echo "CONFIGURED (validity checked on use)"
fi
' 2>/dev/null

echo ""
echo "=== Twitter ==="
ssh "$COBALT_SERVER" '
TW_COOKIES=$(python3 -c "import json; c=json.load(open(\"/opt/cobalt/cookies.json\")); print(c.get(\"twitter\",[\"\"])[0])" 2>/dev/null)
if [ -z "$TW_COOKIES" ]; then
  echo "NO COOKIES"
else
  echo "CONFIGURED (validity checked on use)"
fi
' 2>/dev/null
