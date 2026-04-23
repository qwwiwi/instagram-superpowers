# Полный playbook: AI-агент-продажник

> **Пример цен ниже** (1990₽ / 2490₽ / 5000₽) — иллюстративные, подставьте свои. 3-дневный формат, 7-дневный возврат, «76% новичков» — тоже пример, замените под свой оффер и аудиторию.

Пошаговая инструкция для создания/обновления ChatPlace AI-агента.

Все операции на боте @your_instagram (BOT_ID из `~/.secrets/chatplace/env`).

## Предусловия

1. **Агент создан через UI.** Если `ai_agent_status` возвращает `"AI assistant not exists"` — стоп, попросите владельца открыть ChatPlace UI: бот → AI-агент → Создать.
2. **AI Pack оплачен.** Проверяется на publish. Если упадёт — Connect AI tools → AI agent → оплата (UI ChatPlace).
3. **API ключ загружен:**
   ```bash
   source ~/.secrets/chatplace/env
   BOT="YOUR_BOT_ID"
   API="https://mcp.chatplace.io/mcp"
   ```

## Шаг 1: Global rules

Global rules применяются ко ВСЕМ ответам агента. Здесь: тон, миссия, ссылки, запреты.

### Обязательные блоки в global rules

- **Роль и задача:** «AI-ассистент, отвечающий от имени владельца Instagram-аккаунта. Продаёт ваш оффер» (подставьте свой продукт)
- **Тон:** на «ты», кратко, без эмодзи (редко 👍🏻/💌), 1-3 предложения
- **Контекст продукта:** цены, формат, характеристики аудитории, условия возврата (подставьте под свой оффер — ниже пример)
- **Правила дат:** «НИКОГДА не называй конкретные даты. Только "3 дня", "3-дневный"»
- **Ссылки:** продающий лендинг с UTM, Telegram-канал, гайды. Внутренние ссылки (партнёрка, админка) — в отдельном запрещающем правиле
- **Логика продажи:** тёплый/холодный/гайды/возражения
- **Запреты:** не выдумывать цены, не обещать звонки от владельца, не обсуждать темы вне оффера, не отдавать внутренние ссылки

### Код

```bash
RULES=$(cat <<'EOF'
Ты – AI-ассистент, отвечающий от имени владельца Instagram @your_instagram в DM.

ГЛАВНАЯ ЗАДАЧА: продавать 3-дневный воркшоп «Запусти AI-агента за 3 дня». Каждый диалог веди к покупке или прогреву через канал.

ТОН:
– На «ты», кратко, 1-3 предложения
– Без эмодзи кроме редких 👍🏻/💌
– Без канцелярита, без «буду рад помочь»
– Тире короткое –

...(полный текст — см. ai-agent-current-config.md)
EOF
)

jq -n --arg r "$RULES" --arg b "$BOT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{
  name:"ai_agent_update_global_rules",
  arguments:{botId:$b,enabled:true,rules:$r}
}}' > /tmp/rules.json

curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' --data-binary @/tmp/rules.json
```

## Шаг 2: Settings

Рекомендуемые стартовые настройки:

```bash
jq -n --arg b "$BOT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{
  name:"ai_agent_update_settings",
  arguments:{
    botId: $b,
    answerOnMessageEnabled: true,
    answerOnCommentEnabled: false,
    engageCommentUserEnabled: false,
    generateButtons: true,
    autoRetrainOnDialogs: true,
    responseDelay: 45,
    responseDelayEnabled: true,
    silenceAfterHumanReply: 240,
    silenceAfterHumanReplyEnabled: true,
    automationInactivityPeriod: 60,
    automationInactivityPeriodEnabled: true
  }
}}'
```

**Почему именно так:**
- `responseDelay: 45` — задержка в 45 секунд имитирует живой ответ. Меньше 30 выглядит как бот.
- `silenceAfterHumanReply: 240` — если клиента ответил сам, агент молчит 4 часа. Не мешает живому диалогу.
- `answerOnCommentEnabled: false` — в первые 2-3 дня обкатываем только DM. Публичная ошибка дороже.
- `autoRetrainOnDialogs: true` — учится на реальных диалогах.

## Шаг 3: Topic rules

Типовой набор из 4 правил для продажника.

### 3.1 Тёплый лид

```
Когда клиент спрашивает про цену, оплату, как записаться, когда старт, что включено – он ТЁПЛЫЙ.

Действия:
1. Короткий ответ на конкретный вопрос
2. Ссылка: https://yourdomain.com/lp/?utm_source=instagram&utm_medium=chatplace&utm_campaign=agent
3. Дожим: «Первые 200 мест 1990₽, дальше 2490₽» ИЛИ «Возврат 7 дней»
```

### 3.2 Холодный интерес

```
Когда клиент пишет «привет», «расскажи», «интересно» – он ХОЛОДНЫЙ.

Действия:
1. Коротко: «Учу запускать AI-агентов за 3 дня. Без кода»
2. Канал: https://t.me/+YOUR_CHANNEL_INVITE – «Там бесплатные гайды»
3. НЕ дави продажей в первом сообщении
```

### 3.3 Бесплатные гайды

```
Когда клиент спрашивает про гайды, подборки, starter kit, материалы, клод.

Действия:
1. https://yourdomain.com/guides – «Бери что нужно, всё бесплатно»
2. Мягко: «За 3 дня с рабочим агентом – воркшоп: https://yourdomain.com/lp/?utm_source=instagram&utm_medium=chatplace&utm_campaign=agent»
```

### 3.4 Возражения

```
Типовые возражения:

«Дорого» → 1990₽ первые 200 + возврат 7 дней
«Я не кодер» → 76% новичков, визуальные инструменты
«Нет времени» → 3 дня вечером, записи остаются
«Не получится» → рабочий агент на выходе, возврат 7 дней
«Чем отличаетесь» → практика, не теория
«Когда старт» → поток скоро, детали на лендинге
```

### Код добавления

```bash
for TOPIC_FILE in topic_warm.txt topic_cold.txt topic_guides.txt topic_objection.txt; do
  TEXT=$(cat "$TOPIC_FILE")
  jq -n --arg b "$BOT" --arg i "$TEXT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{
    name:"ai_agent_topic_rules_add",
    arguments:{botId:$b,instructions:$i}
  }}' > /tmp/tr.json
  curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' --data-binary @/tmp/tr.json
done
```

Имя и описание topic rule генерирует ChatPlace автоматически на основе `instructions`.

## Шаг 4: Knowledge base (Q&A)

~30 пар вопрос-ответ покрывают типовые запросы.

### Категории

| Категория | Кол-во | Примеры вопросов |
|-----------|--------|------------------|
| Про воркшоп | 9 | Что за воркшоп? Сколько длится? Что я получу? |
| Цена и оплата | 6 | Сколько стоит? Как оплатить? Возврат? |
| Программа | 4 | Что внутри? Каких агентов? Подготовка? |
| После воркшопа | 3 | Поддержка? Сообщество? Пропуск дня? |
| Контент и канал | 3 | Где бесплатные? Что в канале клиента? Гайд для начинающих? |
| Возражения | 6 | Дорого? Не технарь? Нет времени? |

### Правила для ответов

- Короткий: 1-2 предложения + ссылка если уместно
- Цена всегда: «1990₽ первые 200 мест, потом 2490₽, полная 5000₽, возврат 7 дней»
- Ссылки с UTM по контексту (warm/guides/objection)
- НИКАКИХ конкретных дат старта
- Имя — только каноническое имя владельца. Whisper-опечатки (варианты) чистить grep'ом перед заливом

### Код (bulk)

```python
#!/usr/bin/env python3
import json, subprocess
from pathlib import Path

CPK = Path("/tmp/cpk.txt").read_text().strip()
BOT = "YOUR_BOT_ID"

KB = [
    ("Что за воркшоп?", "3-дневный..."),
    # ... ~30 entries
]

for q, a in KB:
    payload = {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
        "name":"ai_agent_knowledge_base_add",
        "arguments":{"botId":BOT,"question":q,"answer":a}
    }}
    subprocess.run(["curl","-sS","-X","POST","https://mcp.chatplace.io/mcp",
        "-H",f"Authorization: Bearer {CPK}",
        "-H","Content-Type: application/json",
        "--data-binary", json.dumps(payload)])
```

Полный список — [ai-agent-current-config.md](ai-agent-current-config.md).

## Шаг 5: Тестирование

Перед публикацией ОБЯЗАТЕЛЬНО прогнать 5 сценариев через `ai_agent_test_question`:

```bash
for Q in "Сколько стоит воркшоп?" "Привет! Расскажи чем занимаешься" "Где гайды?" "Это дорого" "Когда старт?"; do
  echo "Q: $Q"
  curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg b "$BOT" --arg q "$Q" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"ai_agent_test_question",arguments:{botId:$b,question:$q}}}')"
  echo
done
```

**Критерии приёмки:**
- Тёплый вопрос («сколько стоит») → цена + лендинг с UTM + дожим
- Холодный («привет, расскажи») → канал + мягкое упоминание воркшопа
- Гайды → yourdomain.com/guides + воркшоп
- Возражение → контр-аргумент + CTA
- Дата → НЕ называет конкретную дату, ссылается на лендинг

Если хоть один критерий проваливается — не публиковать. Правь topic rules / KB / global rules.

## Шаг 6: Драфт владельцу

Перед `ai_agent_publish` всегда присылать владельцу аккаунта:
- Global rules (главные тезисы)
- Список topic rules
- Количество Q&A в KB
- Settings (delay, silence, DM/comments on/off)
- **Реальные ответы агента на 5 тестовых вопросов** (копия вывода `ai_agent_test_question`)

Дождаться явного апрува. Без апрува — не жать publish.

## Шаг 7: Publish

```bash
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg b "$BOT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"ai_agent_publish",arguments:{botId:$b}}}')"
```

Успех: `statusName: "published"`. Агент начинает отвечать через 45с задержки после первого обращения клиента.

## Шаг 8: Обкатка (первые 2-3 дня)

### Ежедневный чек

```bash
# Пропущенные вопросы
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg b "$BOT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"ai_agent_knowledge_base_questions",arguments:{botId:$b}}}')"

# Аналитика
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg b "$BOT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"ai_agent_analytics",arguments:{botId:$b}}}')"
```

Вопросы без ответа → либо добавить Q&A через `ai_agent_knowledge_base_add_answer`, либо обновить topic rule.

### Открытие комментов

После 2-3 дней стабильной работы в DM:

```bash
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg b "$BOT" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{
    name:"ai_agent_update_settings",
    arguments:{botId:$b,answerOnCommentEnabled:true,engageCommentUserEnabled:true}
  }}')"
```

## Обновление существующего агента

Чаще всего правки:
- Добавить Q&A: `ai_agent_knowledge_base_add`
- Обновить Q&A: `ai_agent_knowledge_base_update` (только answer + rule, question неизменен)
- Удалить Q&A: `ai_agent_knowledge_base_delete` + создать заново с правильным вопросом
- Сменить тон: `ai_agent_update_global_rules` (новый текст, `enabled:true`)
- Новая ситуация: `ai_agent_topic_rules_add`
- Пауза: `ai_agent_unpublish` → правки → `ai_agent_publish`

## Limits и известные ограничения

- `ai_agent_knowledge_base_update` НЕ меняет question — только answer/rule. Для правки вопроса: delete + add.
- `ai_agent_knowledge_base_bulk_update` / `ai_agent_knowledge_base_bulk_delete` — максимум 50 entries за вызов.
- Topic rule name/description генерируется AI автоматически, их явно задать нельзя.
- Cloudflare защита: только `Authorization: Bearer`, ни `x-api-key`, ни отсутствие header не работают.

## Что НЕ настраивается через API

- Создание самого агента (только UI)
- Подключение AI Pack к тарифу (только UI/billing)
- Выбор роли агента (Personal Assistant / Support Agent / Sales Manager) — задаётся при создании в UI
- Story reply rules (есть поля, но не протестированы)
