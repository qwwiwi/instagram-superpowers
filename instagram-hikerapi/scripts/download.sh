#!/bin/bash
set -euo pipefail

# download.sh -- скачивание медиа через Cobalt API на your server
# Использование: download.sh <URL> [audio|video|auto|transcribe]

URL="${1:?Usage: download.sh <URL> [audio|video|auto|transcribe]}"
MODE="${2:-auto}"

COBALT_SERVER="${COBALT_SERVER:?Set COBALT_SERVER=user@host (SSH target for your self-hosted Cobalt instance)}"
SECRETS_DIR="$HOME/.secrets/cobalt"
API_KEY=$(cat "$SECRETS_DIR/api-key" 2>/dev/null || echo "")

if [ -z "$API_KEY" ]; then
  echo "ERROR: API key not found at $SECRETS_DIR/api-key" >&2
  exit 1
fi

# Определяем downloadMode для Cobalt
case "$MODE" in
  audio|transcribe) DL_MODE="audio" ;;
  video) DL_MODE="auto" ;;
  *) DL_MODE="auto" ;;
esac

# Вызываем Cobalt API через SSH (API слушает только localhost)
echo "Requesting Cobalt API..." >&2
RESPONSE=$(ssh "$COBALT_SERVER" "curl -s -X POST 'http://127.0.0.1:9000/' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'Authorization: Api-Key $API_KEY' \
  -d '{\"url\":\"$URL\",\"downloadMode\":\"$DL_MODE\"}'")

STATUS=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")

if [ "$STATUS" = "error" ]; then
  ERROR=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('error',{}).get('code','unknown'))" 2>/dev/null || echo "unknown")
  echo "ERROR: Cobalt returned $ERROR" >&2
  echo "$RESPONSE" >&2
  exit 1
fi

# Получаем URL и имя файла
DL_URL=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('url',''))" 2>/dev/null)
FILENAME=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('filename','media.mp4'))" 2>/dev/null)

if [ -z "$DL_URL" ]; then
  echo "ERROR: No download URL in response" >&2
  exit 1
fi

# Скачиваем файл на your server
echo "Downloading $FILENAME..." >&2
ssh "$COBALT_SERVER" "curl -sL -o '/tmp/$FILENAME' '$DL_URL'"

# Копируем на Mac mini
mkdir -p /tmp/downloads
scp "$COBALT_SERVER:/tmp/$FILENAME" "/tmp/downloads/$FILENAME" 2>/dev/null
ssh "$COBALT_SERVER" "rm -f '/tmp/$FILENAME'" 2>/dev/null

LOCAL_PATH="/tmp/downloads/$FILENAME"
echo "Saved: $LOCAL_PATH" >&2

# Если режим transcribe -- запускаем Groq Whisper
if [ "$MODE" = "transcribe" ]; then
  echo "Transcribing..." >&2

  # Конвертируем в ogg если нужно
  AUDIO_PATH="$LOCAL_PATH"
  if [[ "$FILENAME" != *.ogg ]]; then
    AUDIO_PATH="/tmp/downloads/${FILENAME%.*}.ogg"
    ffmpeg -i "$LOCAL_PATH" -vn -acodec libopus "$AUDIO_PATH" -y 2>/dev/null
  fi

  # Проверяем размер -- Groq лимит 25MB
  FILE_SIZE=$(stat -f%z "$AUDIO_PATH" 2>/dev/null || stat --printf="%s" "$AUDIO_PATH" 2>/dev/null)
  if [ "$FILE_SIZE" -gt 25000000 ]; then
    echo "File >25MB, splitting..." >&2
    DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$AUDIO_PATH" 2>/dev/null | cut -d. -f1)
    CHUNK_DURATION=600  # 10 минут
    CHUNKS=0
    TRANSCRIPT=""
    for START in $(seq 0 $CHUNK_DURATION $DURATION); do
      CHUNK_PATH="/tmp/downloads/chunk_${CHUNKS}.ogg"
      ffmpeg -i "$AUDIO_PATH" -ss "$START" -t "$CHUNK_DURATION" -c copy "$CHUNK_PATH" -y 2>/dev/null
      CHUNK_TEXT=$(bash "${WHISPER_SCRIPT:?Set WHISPER_SCRIPT=/path/to/your/whisper.sh to use transcribe mode}" "$CHUNK_PATH" 2>/dev/null)
      TRANSCRIPT="$TRANSCRIPT$CHUNK_TEXT\n"
      rm -f "$CHUNK_PATH"
      CHUNKS=$((CHUNKS + 1))
    done
    echo -e "$TRANSCRIPT"
  else
    bash "${WHISPER_SCRIPT:?Set WHISPER_SCRIPT=/path/to/your/whisper.sh to use transcribe mode}" "$AUDIO_PATH"
  fi
else
  echo "$LOCAL_PATH"
fi
