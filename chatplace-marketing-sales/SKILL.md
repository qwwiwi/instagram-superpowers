---
name: chatplace-marketing-sales
description: "Создание новых элементов воронки в ChatPlace: кодовые слова с DM-автоматизацией и публичным ответом под комментом, AI-агент-продажник с knowledge base + topic rules + global rules. Use when: (1) adding new Instagram keyword that must trigger DM + public comment reply, (2) configuring or updating the ChatPlace AI sales agent (your account), (3) writing/editing global rules or topic rules for the AI agent, (4) adding Q&A entries to knowledge base, (5) testing agent answers before publishing, (6) troubleshooting ChatPlace setup (API limits, AI Pack subscription errors). NOT for analytics/reports (out of scope — keep in your own tracking system)."
---

# ChatPlace Marketing & Sales — создание элементов воронки

Инструмент для создания/обновления двух типов сущностей в ChatPlace:
1. **Кодовые слова** (Instagram keywords) — автоматика DM + публичный ответ под комментом
2. **AI-агент-продажник** — единая сущность, отвечает в DM за клиента, ведёт к продаже воркшопа

> **Note:** этот скилл покрывает только создание сущностей (keywords + AI-агент). Аналитика, реестры ссылок, сквозные отчёты — вне скоупа, делайте своим инструментом.

---

## Главные правила (выучить до кода)

### Правило 1: кодовое слово = 4 элемента за один проход
1. **Automation** (`automations_quick_setup`) — DM-воронка: welcome + URL-кнопка
2. **Comment auto-reply rule** (`comments_create`) — публичный ответ под комментом
3. **Case variants** — минимум 7 форм: ALL_CAPS, Capitalized, lowercase, CamelCase + кириллица. equals case-sensitive.
4. **Оба языка** (если переводимо): EN + RU. Пример: workshop, Workshop, WORKSHOP, WorkShop, воркшоп, Воркшоп, ВОРКШОП.

Почему сразу 4: без comment rule под постом молчание (теряется соцдоказательство). Без case variants триггер не ловит «Workshop» с большой.

### Правило 2: AI-агент создаётся ТОЛЬКО через UI
Все `ai_agent_*` падают с `"AI assistant not exists"` пока агент не создан через UI ChatPlace. Увидел — попроси владельца аккаунта: ChatPlace → бот → AI-агент → Создать.

### Правило 3: AI Pack должен быть оплачен
Публикация падает с `"You have no active ChatPlace AI package"` без AI-пакета. Попроси владельца: Connect AI tools → AI agent → оплата.

### Правило 4: без Whisper-опечаток в продакшне
Перед заливом текстов (KB, шаблоны, rules) — grep на все Whisper-варианты имени клиента и заменять на каноническое. Voice transcripts often misspell names.

---

## Connection

- **MCP endpoint:** `https://mcp.chatplace.io/mcp`
- **Auth:** `Authorization: Bearer $CHATPLACE_API_KEY` (НЕ x-api-key — Cloudflare 403)
- **Key path:** `~/.secrets/chatplace/env`
- **Bot (@your_instagram Instagram):** `YOUR_BOT_ID`

```bash
source ~/.secrets/chatplace/env
```

---

## Workflow 1: создание кодового слова

Полный playbook — [references/keyword-workflow.md](references/keyword-workflow.md). Короткая версия:

1. **Invite link** — Telegram Bot API `createChatInviteLink` + сохранить в свой реестр источников.
2. **Tag** (`tags_create`, name=`kw_<keyword>`) для сегментации.
3. **Automation** (`automations_quick_setup`): botId, commentEquals=7+ variants, messageEquals=те же, welcome + URL-кнопка.
4. **Action** (`automations_actions_setup`) — добавить тег на первое сообщение.
5. **Comment auto-reply rule** (`comments_create`): type=equals, startMessages=те же 7+, answers=3+ ротации без эмодзи и ссылок.
6. **Тест** — вручную под постом и в DM.

Существующие keywords держите в своём реестре (БД / spreadsheet).

---

## Workflow 2: AI-агент-продажник

Полный playbook — [references/ai-agent-workflow.md](references/ai-agent-workflow.md).

### Архитектура

| Компонент | API | Назначение |
|-----------|-----|------------|
| Global rules | `ai_agent_update_global_rules` | Тон, миссия, ссылки, запреты — на ВСЕ ответы |
| Topic rules | `ai_agent_topic_rules_add` | Ситуационные блоки |
| Knowledge base | `ai_agent_knowledge_base_add` | Q&A пары |
| Settings | `ai_agent_update_settings` | Задержка, silence, DM/comments on/off, auto-retrain |
| Publish | `ai_agent_publish` | Активация (требует AI Pack) |

### Порядок настройки

1. `ai_agent_status` — проверить что создан.
2. `ai_agent_update_global_rules` с `enabled:true`.
3. `ai_agent_update_settings` — стартовый набор:
   - answerOnMessageEnabled: true
   - answerOnCommentEnabled: false (комменты — после обкатки)
   - generateButtons: true
   - autoRetrainOnDialogs: true
   - responseDelay: 45 + responseDelayEnabled: true
   - silenceAfterHumanReply: 240 + silenceAfterHumanReplyEnabled: true
4. 4 topic rules через `ai_agent_topic_rules_add`: Тёплый лид / Холодный интерес / Бесплатные гайды / Возражения.
5. 30 Q&A через `ai_agent_knowledge_base_add` пачкой.
6. Тест на 5 сценариях через `ai_agent_test_question`.
7. Драфт владельцу на апрув (с примерами реальных ответов).
8. `ai_agent_publish` после явного апрува владельца.

### Правила контента (Evergreen)

- **НИКОГДА не называй конкретные даты старта.** Пиши «3 дня», «3-дневный», «за 3 дня».
- **ВСЕГДА UTM:** `https://yourdomain.com/lp/?utm_source=instagram&utm_medium=chatplace&utm_campaign=agent`. Суффиксы: `agent_warm`, `agent_guides`, `agent_objection`.
- **НЕ шли `your-internal-url.example`** — внутренняя ссылка.
- **Холодный канал:** `https://t.me/+YOUR_CHANNEL_INVITE` (your Telegram channel; track in your subscriber DB).
- **Гайды:** `https://yourdomain.com/guides` — можно клиентам.
- **Тон:** на «ты», кратко, без эмодзи (редко 👍🏻/💌), без канцелярита, 1-3 предложения.

### Обкатка

- 2-3 дня: `ai_agent_knowledge_base_questions` (пропущенные вопросы) + `ai_agent_analytics`.
- Когда стабильно — включать `answerOnCommentEnabled` + `engageCommentUserEnabled`.
- Плохой ответ → topic rule (приоритет) ИЛИ Q&A в KB ИЛИ global rules (жёстче).

---

## Troubleshooting

| Симптом | Причина | Решение |
|---------|---------|---------|
| `AI assistant not exists` | Не создан в UI | Владелец → UI → Создать |
| `You have no active ChatPlace AI package` | Нет AI Pack | Владелец → Connect AI tools → оплата |
| Cloudflare 403 HTML | Неверный auth | Только `Authorization: Bearer` |
| Агент не ловит опечатки «Cloude/Клоде» | equals case-sensitive | Варианты в KB как вопросы ИЛИ Message Recognition (UI) |
| Под комментом тишина | Нет comment auto-reply rule | `comments_create` |
| Whisper misspelling of name | Upload without proofreading | Grep for name variants → replace with canonical |

---

## API Discovery

```bash
source ~/.secrets/chatplace/env
curl -s -X POST "https://mcp.chatplace.io/mcp" \
  -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Группы (~120 tools): `ai_agent_*`, `automations_*`, `comments_*`, `bots_*`, `chats_*`, `mailings_*`, `products_*`, `ref_links_*`, `tags_*`, `variables_*`, `virale_*`, `media_*`.

### Документация
- AI Agent: https://intercom.help/chatplace/en/articles/11869889-ai-agent
- Text Assistant: https://intercom.help/chatplace/en/articles/7016813
- Flow Enhancements: https://intercom.help/chatplace/en/articles/10989531
- Neural Network Queries: https://intercom.help/chatplace/en/articles/10894004
- Automation Creation: https://intercom.help/chatplace/en/articles/10003893

---

## Files

- [references/keyword-workflow.md](references/keyword-workflow.md)
- [references/ai-agent-workflow.md](references/ai-agent-workflow.md)
- [references/ai-agent-current-config.md](references/ai-agent-current-config.md)
