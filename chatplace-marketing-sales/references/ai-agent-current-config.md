# AI-агент @your_instagram — live snapshot

Текущая конфигурация published-агента. Источник правды для восстановления / диффов.

Дата последнего апдейта: **2026-04-20**. После любого `ai_agent_update_*` / `ai_agent_topic_rules_*` / `ai_agent_knowledge_base_*` — обновить этот файл.

## Identity

| Поле | Значение |
|------|----------|
| Bot ID | `YOUR_BOT_ID` |
| Bot handle | @your_instagram (Instagram) |
| Agent ID | `YOUR_AGENT_ID` |
| Status | `published` |
| AI Pack | Подключён (tier: AI agent) |

## Settings (ai_agent_update_settings)

| Поле | Значение | Причина |
|------|----------|---------|
| answerOnMessageEnabled | `true` | DM отвечает сразу |
| answerOnCommentEnabled | `false` | комменты пока не трогаем, обкатываем DM |
| engageCommentUserEnabled | `false` | включить после обкатки комментов |
| generateButtons | `true` | кнопки к ответам помогают CTR |
| autoRetrainOnDialogs | `true` | обучение по реальным диалогам |
| responseDelay | `45` | секунд задержки |
| responseDelayEnabled | `true` | выглядит как живой |
| silenceAfterHumanReply | `240` | секунд молчания после ответа руками |
| silenceAfterHumanReplyEnabled | `true` | не перебивает клиента |

## Global rules (ai_agent_update_global_rules, enabled:true)

Полный текст — в `/tmp/global_rules.txt` (снэпшот). Ключевые принципы:

- Роль: AI-ассистент, отвечающий от имени владельца аккаунта @your_instagram.
- Миссия: продать 3-дневный воркшоп по AI-агентам.
- Обращение на «ты», кратко (1–3 предложения), без эмодзи (редко 👍🏻/💌), без канцелярита.
- **НИКОГДА** не называть даты старта. Только «3 дня», «3-дневный», «скоро».
- Ссылки:
  - Тёплый (воркшоп): `https://yourdomain.com/lp/?utm_source=instagram&utm_medium=chatplace&utm_campaign=agent`
  - Возражения: тот же лендинг, `utm_campaign=agent_objection`
  - Гайды: `https://yourdomain.com/lp/?utm_source=instagram&utm_medium=chatplace&utm_campaign=agent`
  - Холодный канал: `https://t.me/+YOUR_CHANNEL_INVITE` (your Telegram channel)
- **НЕ шли** `your-internal-url.example` (внутренняя ссылка).
- Запрещено: обещать сроки / результаты, давать конкретные даты, советы по здоровью / финансам / юриспруденции.
- Client name: каноническое имя владельца (задайте в global rules; все варианты Whisper-опечаток проверьте через grep перед заливом).

## Topic rules (4)

Полные ID и приоритеты — источник правды `ai_agent_topic_rules_list`. На момент апдейта:

| Приоритет | ID (prefix) | Название | Назначение |
|-----------|-------------|----------|------------|
| 1 | `TOPIC_RULE_UUID_WARM` | Тёплый лид | вопрос про воркшоп / курс / обучение → лендинг warm |
| 2 | `TOPIC_RULE_UUID_COLD` | Холодный интерес | «что делаешь», «про что канал» → Telegram-канал |
| 3 | `TOPIC_RULE_UUID_GUIDES` | Бесплатные гайды | «что-то бесплатное?» → yourdomain.com/guides |
| 4 | `TOPIC_RULE_UUID_OBJECTION` | Возражения | «дорого», «нет времени», «сомневаюсь» → objection link + аргументы |

## Knowledge base (30 Q&A)

Полный список ID — источник правды `ai_agent_knowledge_base_list`. Категории (кол-во):

| Категория | Кол-во | Примеры вопросов |
|-----------|--------|------------------|
| Воркшоп базовое | ~6 | «Что за воркшоп?», «Сколько длится?», «Что получу?» |
| Цена/оплата | ~4 | «Сколько стоит?», «Как оплатить?», «Есть рассрочка?» |
| Программа/формат | ~5 | «Какая программа?», «Онлайн или офлайн?», «Запись будет?» |
| После воркшопа | ~3 | «Что после?», «Есть клуб?», «Поддержка?» |
| Контент/канал | ~4 | «Что в канале владельца?» (каноническое имя — см. global rules), «Про что пишешь?» |
| Возражения | ~6 | «Дорого», «Нет времени», «Я новичок», «У меня есть ChatGPT» |
| Разное | ~2 | «Кто ты?», «С кем можно связаться?» |

**⚠ Namespace hygiene:** Whisper voice transcripts часто искажают имена. Перед заливом KB: `grep -iE 'variant1|variant2' *.txt` → заменить на каноническое имя клиента.

## Как перечитать живую конфигурацию

```bash
source ~/.secrets/chatplace/env
API="https://mcp.chatplace.io/mcp"
AGENT="YOUR_AGENT_ID"
BOT="YOUR_BOT_ID"

# Status + global rules + settings
for method in ai_agent_status ai_agent_get_global_rules ai_agent_get_settings; do
  echo "=== $method ==="
  curl -sS -X POST "$API" \
    -H "Authorization: Bearer $CHATPLACE_API_KEY" \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$method\",\"arguments\":{\"botId\":\"$BOT\"}}}"
done

# Topic rules
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"ai_agent_topic_rules_list\",\"arguments\":{\"botId\":\"$BOT\"}}}"

# KB
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"ai_agent_knowledge_base_list\",\"arguments\":{\"botId\":\"$BOT\",\"limit\":100}}}"
```

## История изменений

- **YYYY-MM-DD** — первый запуск. Агент создан через UI, настроены global rules + 4 topic rules + ~30 KB + settings. Опубликован после подключения AI Pack. Whisper-опечатки в именах почищены grep'ом перед заливом.
