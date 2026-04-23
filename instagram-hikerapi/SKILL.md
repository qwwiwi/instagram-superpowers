---
name: instagram-hikerapi
description: >
  Полный инструмент для работы с Instagram: аналитика аккаунтов через HikerAPI SaaS
  + скачивание рилсов/постов через Cobalt API.
  Используй когда: (1) скачать рилс/видео/фото из Instagram, (2) найти залетевшие рилсы аккаунта,
  (3) проанализировать конкурента (views, likes, comments, engagement), (4) управлять watchlist
  аккаунтов для мониторинга, (5) скачать + транскрибировать видео, (6) поиск по хэштегам, локациям.
  Триггеры: «скачай рилс», «скачай из инсты», «проанализируй аккаунт», «залетевшие рилсы»,
  «топ рилсы», «watchlist», «отследить аккаунт», «конкуренты инста», «engagement»,
  ссылки на instagram.com/reel/, instagram.com/p/, instagram.com/stories/.
  НЕ для: YouTube, Twitter, массового спама, автолайков, автоподписок.
---

# Instagram SuperPower

## Инструменты

| Инструмент | Назначение | Стоимость |
|-----------|-----------|-----------|
| **HikerAPI** | Аналитика: профили, рилсы, комменты, подписчики, хэштеги, поиск | $0.001/запрос (Standard) |
| **Cobalt** | Скачивание видео/фото с CDN Instagram | Бесплатно (self-hosted) |
| **Groq Whisper** | Транскрипция скачанных видео | Бесплатно |

## Документация и ссылки

| Ресурс | URL |
|--------|-----|
| HikerAPI документация | https://hiker-doc.readthedocs.io/en/latest/ |
| HikerAPI Swagger | https://api.instagrapi.com/docs |
| HikerAPI Redoc | https://api.instagrapi.com/redoc |
| HikerAPI дашборд | https://hikerapi.com |
| HikerAPI Python SDK | `pip install hikerapi` |
| Cobalt GitHub | https://github.com/imputnet/cobalt |
| instagrapi GitHub | https://github.com/subzeroid/instagrapi |

## Маппинг путей

### Mac mini (координатор / агенты)

| Что | Путь |
|-----|------|
| Скрипт скачивания (Cobalt) | `~/.claude/skills/instagram-hikerapi/scripts/download.sh` |
| Скрипт аналитики (HikerAPI) | `~/.claude/skills/instagram-hikerapi/scripts/analyze.sh` |
| Скрипт watchlist | `~/.claude/skills/instagram-hikerapi/scripts/watchlist.sh` |
| Скрипт деплоя куков | `~/.claude/skills/instagram-hikerapi/scripts/deploy-cookies.sh` |
| Скрипт проверки куков | `~/.claude/skills/instagram-hikerapi/scripts/check-cookies.sh` |
| HikerAPI ключ | `~/.secrets/hikerapi/api-key` |
| Instagram креды (для Cobalt) | `~/.secrets/cobalt/accounts.json` |
| Instagram куки (для Cobalt) | `~/.secrets/cobalt/cookies.json` |
| Cobalt API ключ | `~/.secrets/cobalt/api-key` |
| Watchlist | `~/.secrets/cobalt/watchlist.json` |
| Скачанные файлы | `/tmp/downloads/` |

### your server (`YOUR_SERVER_IP`, SSH: `ssh $YOUR_SERVER_SSH`)

| Что | Путь |
|-----|------|
| Cobalt Docker Compose | `/opt/cobalt/docker-compose.yml` |
| Cobalt куки (рабочая копия) | `/opt/cobalt/cookies.json` |
| Cobalt API ключи | `/opt/cobalt/keys/api-keys.json` |
| Docker контейнер | `cobalt-api` |
| Cobalt API endpoint | `http://127.0.0.1:9000/` (только localhost) |

### Как всё связано

```
HikerAPI (облако)                    your server (YOUR_SERVER_IP)
─────────────────                    ────────────────────────────
api.hikerapi.com  ◀── curl ──  Mac mini / your server
  аналитика, данные                  Cobalt API (localhost:9000)
  $0.001/запрос                        скачивание медиа (бесплатно)

Mac mini
  analyze.sh  → HikerAPI (напрямую, без SSH)
  download.sh → SSH → your server → Cobalt → Instagram CDN
  watchlist.sh → analyze.sh + download.sh
```

## HikerAPI -- Аутентификация

```bash
# ВАЖНО: используй api.instagrapi.com (без Cloudflare) вместо api.hikerapi.com
# api.hikerapi.com блокирует urllib/requests из скриптов
# Ключ хранится в:
cat ~/.secrets/hikerapi/api-key

# Использование в curl:
curl -s "https://api.instagrapi.com/ENDPOINT" \
  -H "accept: application/json" \
  -H "x-access-key: $(cat ~/.secrets/hikerapi/api-key)"

# Или как GET-параметр:
curl -s "https://api.instagrapi.com/ENDPOINT?access_key=$(cat ~/.secrets/hikerapi/api-key)"
```

**Баланс:** `GET /sys/balance` -- показывает оставшиеся запросы и сумму.

## HikerAPI -- Основные эндпоинты

### Пользователи

| Эндпоинт | Описание | Запросов |
|----------|---------|---------|
| `GET /v2/user/by/username?username=X` | Профиль по username | 1 |
| `GET /v2/user/by/id?id=X` | Профиль по ID (быстрее) | 1 |
| `GET /v2/user/clips?user_id=X` | Рилсы пользователя (12 шт/страница) | 1 |
| `GET /gql/user/clips?user_id=X` | Рилсы через GraphQL | 1 |
| `GET /v1/user/medias/chunk?user_id=X` | Все посты (с пагинацией) | 1 |
| `GET /v2/user/stories?user_id=X` | Сторис | 1 |
| `GET /v2/user/followers?user_id=X` | Подписчики (с пагинацией) | 1 |
| `GET /v2/user/following?user_id=X` | Подписки | 1 |
| `GET /v1/user/highlights?user_id=X` | Хайлайты | 1 |

### Медиа (посты/рилсы)

| Эндпоинт | Описание | Запросов |
|----------|---------|---------|
| `GET /v2/media/info/by/code?code=X` | Инфо по коду поста | 1 |
| `GET /v2/media/info/by/url?url=X` | Инфо по URL | 1 |
| `GET /v2/media/comments?media_id=X` | Комментарии (15/стр) | 1 |
| `GET /v2/media/likers?media_id=X` | Кто лайкнул | 1 |
| `GET /v1/media/download/video?media_pk=X` | Скачать видео | 1 |
| `GET /v1/media/download/photo?media_pk=X` | Скачать фото | 1 |

### Поиск

| Эндпоинт | Описание | Запросов |
|----------|---------|---------|
| `GET /v2/fbsearch/topsearch?query=X` | Глобальный поиск | 1 |
| `GET /v2/fbsearch/reels?query=X` | Поиск рилсов | 1 |
| `GET /v2/fbsearch/accounts?query=X` | Поиск аккаунтов | 1 |
| `GET /v2/fbsearch/places?query=X` | Поиск мест | 1 |

### Хэштеги

| Эндпоинт | Описание | Запросов |
|----------|---------|---------|
| `GET /v2/hashtag/by/name?name=X` | Инфо о хэштеге | 1 |
| `GET /v2/hashtag/medias/top?hashtag_id=X` | Топ посты по хэштегу | 1 |
| `GET /v2/hashtag/medias/recent?hashtag_id=X` | Свежие посты по хэштегу | 1 |

### Данные в ответах

Каждый media-объект содержит:
- `play_count` / `view_count` -- просмотры
- `like_count` -- лайки
- `comment_count` -- комментарии
- `taken_at` -- timestamp публикации
- `code` -- shortcode для URL (`instagram.com/reel/{code}/`)
- `caption.text` -- текст подписи
- `user.username` -- автор

## Быстрый старт

### Профиль аккаунта
```bash
HIKER_KEY=$(cat ~/.secrets/hikerapi/api-key)
curl -s "https://api.instagrapi.com/v2/user/by/username?username=example_account" \
  -H "x-access-key: $HIKER_KEY" | python3 -m json.tool
```

### Рилсы аккаунта (топ залетевших)
```bash
HIKER_KEY=$(cat ~/.secrets/hikerapi/api-key)
# 1. Получить user_id
USER_ID=$(curl -s "https://api.instagrapi.com/v2/user/by/username?username=example_account" \
  -H "x-access-key: $HIKER_KEY" | python3 -c "import json,sys; print(json.load(sys.stdin)['user']['pk'])")

# 2. Получить рилсы
curl -s "https://api.instagrapi.com/v2/user/clips?user_id=$USER_ID" \
  -H "x-access-key: $HIKER_KEY" | python3 -c "
import json, sys
d = json.load(sys.stdin)
items = d.get('response',{}).get('items',[])
for item in sorted(items, key=lambda x: x['media'].get('play_count',0), reverse=True)[:5]:
    m = item['media']
    print(f'{m.get(\"play_count\",0):>10,} views | {m.get(\"like_count\",0):>6,} likes | https://instagram.com/reel/{m[\"code\"]}/')
"
```

### Скачать рилс (через Cobalt)
```bash
bash ~/.claude/skills/instagram-hikerapi/scripts/download.sh "https://www.instagram.com/reel/REEL_CODE/"
```

### Скачать + транскрибировать (нужен свой Whisper-скрипт, см. ниже)
```bash
WHISPER_SCRIPT=/path/to/your/whisper.sh \
  bash ~/.claude/skills/instagram-hikerapi/scripts/download.sh "https://www.instagram.com/reel/REEL_CODE/" transcribe
```

### Проверить баланс HikerAPI
```bash
curl -s "https://api.instagrapi.com/sys/balance" \
  -H "x-access-key: $(cat ~/.secrets/hikerapi/api-key)"
# {"requests":N,"rate":8,"currency":"USD","amount":X.XX}
```

## Типичные сценарии

### 1. «Покажи залетевшие рилсы конкурента»
```bash
bash scripts/analyze.sh <username> 5 14
```
Скрипт: HikerAPI → профиль → рилсы → сортировка по engagement → результат.

### 2. «Скачай этот рилс и расскажи о чём он»
```bash
bash scripts/download.sh "<url>" transcribe
```
Cobalt скачивает → Groq транскрибирует → агент анализирует.

### 3. «Добавь в мониторинг»
```bash
bash scripts/watchlist.sh add <username>
bash scripts/watchlist.sh scan 3 14
```

### 4. «Полный разбор конкурента»
1. `analyze.sh <username> 5 14` → топ-5 рилсов + цифры
2. `download.sh <url> transcribe` для каждого из топ-3
3. Агент разбирает: хук → боль → решение → CTA

### 5. «Что вирусится по хэштегу»
```bash
HIKER_KEY=$(cat ~/.secrets/hikerapi/api-key)
# Получить hashtag_id
curl -s "https://api.instagrapi.com/v2/hashtag/by/name?name=aitools" -H "x-access-key: $HIKER_KEY"
# Топ-посты
curl -s "https://api.instagrapi.com/v2/hashtag/medias/top?hashtag_id=ID" -H "x-access-key: $HIKER_KEY"
```

## Пагинация

Многие эндпоинты возвращают `next_page_id`. Для следующей страницы:
```bash
curl -s "https://api.instagrapi.com/v2/user/clips?user_id=123&page_id=NEXT_PAGE_ID" \
  -H "x-access-key: $HIKER_KEY"
```

Рилсы: ~12 штук на страницу. Для 50 рилсов нужно ~4 запроса.

## Rate Limits

### HikerAPI
- Лимит: **8 запросов/секунду** (тариф Standard)
- Без риска бана Instagram -- HikerAPI использует свой пул аккаунтов и прокси
- Баланс проверяется командой выше (`/sys/balance`)

### Cobalt (скачивание)
- Rate limits Instagram: **20 скачиваний/час** на 1 аккаунт
- Пауза между запросами: **минимум 3 секунды**
- Пакетное скачивание (>5): пауза **10-15 секунд**
- Строго последовательно, никогда параллельно

### Оптимальная стратегия
- **Аналитика** (HikerAPI): без ограничений, до 8 req/sec
- **Скачивание** (Cobalt): 20/час, последовательно
- HikerAPI тоже умеет скачивать (`/v1/media/download/*`), но тратит платные запросы -- используй Cobalt

## Обслуживание

### HikerAPI
```bash
# Баланс
curl -s "https://api.instagrapi.com/sys/balance" -H "x-access-key: $(cat ~/.secrets/hikerapi/api-key)"

# Ключ в дашборде: https://hikerapi.com → Tokens
```

### Cobalt
```bash
# Статус
ssh $YOUR_SERVER_SSH "docker ps --filter name=cobalt-api --format '{{.Status}}'"

# Логи
ssh $YOUR_SERVER_SSH "docker logs cobalt-api --tail 20"

# Перезапуск
ssh $YOUR_SERVER_SSH "cd /opt/cobalt && docker compose restart cobalt-api"
```

## Обновление куков Instagram (для Cobalt)

Подробный регламент: `references/cookie-refresh.md`

1. Креды: `~/.secrets/cobalt/accounts.json`
2. Chromium CDP: `bash scripts/chromium-launch.sh start`
3. Логин через `Input.dispatchKeyEvent` (посимвольно)
4. Кнопка: `div[role="button"]` (не `<button type="submit">`)
5. 2FA: TOTP → `input[name="verificationCode"]`
6. Куки: `Network.getCookies` → `.instagram.com` → `cookies.json`
7. Деплой: `bash scripts/deploy-cookies.sh`

## Безопасность

- HikerAPI ключ: `~/.secrets/hikerapi/api-key` (chmod 600)
- Cobalt: ТОЛЬКО `127.0.0.1:9000` на your server
- Доступ к your server: через Tailscale SSH
- Куки/креды: ТОЛЬКО `~/.secrets/cobalt/` (chmod 600)
- Запрещено: логировать ключи/куки, выводить в чат, коммитить в git

## Требования для запуска

1. HikerAPI ключ в `~/.secrets/hikerapi/api-key`
2. Для скачивания: self-hosted Cobalt + SSH-доступ + Cobalt API ключ (env `COBALT_SERVER=user@host`)
3. Для транскрипции (опционально): свой Whisper-скрипт (env `WHISPER_SCRIPT=/path/to/whisper.sh`)
4. Скилл в `~/.claude/skills/instagram-hikerapi/`
