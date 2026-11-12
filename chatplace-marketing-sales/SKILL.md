---
name: chatplace-marketing-sales
description: "Создание элементов воронки в ChatPlace: кодовые слова (Instagram keywords) с DM-автоматизацией и публичным ответом под комментом; опционально – AI-агент-продажник (нужен платный AI-пакет ChatPlace). Use when: (1) adding new Instagram keyword that must trigger DM + public comment reply, (2) editing existing keyword automations or comment rules, (3) troubleshooting ChatPlace setup (Cloudflare 403, buttons that do not open links), (4) optionally: configuring the ChatPlace AI sales agent (global rules, topic rules, knowledge base). NOT for analytics/reports (out of scope — keep in your own tracking system)."
---

# ChatPlace Marketing & Sales — создание элементов воронки

Инструмент для создания/обновления двух типов сущностей в ChatPlace:
1. **Кодовые слова** (Instagram keywords) — автоматика DM + публичный ответ под комментом. Это основное, с него и начинайте.
2. **AI-агент-продажник** (ОПЦИОНАЛЬНО) — отвечает в DM за владельца. Требует платного AI-пакета ChatPlace; для кодовых слов не нужен.

> **Note:** этот скилл покрывает только создание сущностей (keywords + AI-агент). Аналитика, реестры ссылок, сквозные отчёты — вне скоупа, делайте своим инструментом.

---

## Что нужно до начала

1. **Аккаунт ChatPlace с подключённым Instagram.** Бот Instagram-аккаунта должен быть создан и подключён в кабинете ChatPlace. Для кодовых слов AI-пакет не нужен. Какой тариф ChatPlace требуется для доступа к API, проверьте на сайте ChatPlace: в этом скилле это не зафиксировано.
2. **API-ключ ChatPlace.** Получите API-ключ по актуальной инструкции ChatPlace. Расположение раздела выдачи ключей в кабинете этим скиллом не подтверждено. Ключ сохраните сразу и никому не показывайте.
3. **Файл с ключами** `~/.secrets/chatplace/env`:

   ```bash
   mkdir -p ~/.secrets/chatplace
   cat > ~/.secrets/chatplace/env <<'EOF'
   export CHATPLACE_API_KEY="<ВАШ_КЛЮЧ>"
   export CHATPLACE_BOT_ID="<ID_БОТА>"
   EOF
   chmod 600 ~/.secrets/chatplace/env
   ```

4. **Bot id** – вызов `bots_list`:

   ```bash
   source ~/.secrets/chatplace/env
   curl -sS -X POST "https://mcp.chatplace.io/mcp" \
     -H "Authorization: Bearer $CHATPLACE_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"bots_list","arguments":{}}}'
   ```

   В ответе найдите бота своего Instagram и впишите его id в `CHATPLACE_BOT_ID`. Если ответ пришёл HTML-страницей с 403 – см. Troubleshooting.
5. **`curl` и `jq`** в терминале.

**Не нужно для кодовых слов, делается по желанию:** Telegram-канал со ссылкой-приглашением и учёт источников подписки в своей таблице; AI-агент (требует платного AI-пакета).

---

## Главные правила (выучить до кода)

### Правило 1: кодовое слово = 4 элемента за один проход
1. **Automation** (`automations_quick_setup`) — DM-воронка: welcome + URL-кнопка
2. **Comment auto-reply rule** (`comments_create`) — публичный ответ под комментом
3. **Case variants** — минимум 7 вариантов суммарно (не на каждый язык): ALL_CAPS, Capitalized, lowercase, CamelCase + кириллица. Правило автоответа с `type: "equals"` чувствительно к регистру, поэтому тот же набор нужен и в нём.
4. **Оба языка** (если переводимо): EN + RU входят в те же 7 вариантов. Пример: workshop, Workshop, WORKSHOP, WorkShop, воркшоп, Воркшоп, ВОРКШОП.

Почему сразу 4: без comment rule под постом молчание (теряется соцдоказательство). Без case variants триггер не ловит «Workshop» с большой.

### Правило 2: перед правкой – бэкап, потом проверка

Относится ко всему, что уже работает у живых людей: тексты, кнопки, ссылки, написания слова, включение и выключение воронок и правил автоответа.

1. **Бэкап.** Прочитайте текущее состояние и сохраните в файл: список (tools группы `automations_*` / `comments_*` на чтение) и `automations_get_detail` для воронки. Файл кладите вне репозитория, например `~/chatplace-backups/<дата>-<слово>.json`.
2. **Правка.** Меняйте только то, что нужно, остальное не трогайте.
3. **Проверка живым запросом.** После правки снова вызовите `automations_get_detail` (для текстов и кнопок) и откройте ссылку или сделайте тест в директе. Код ответа 200 от API не доказывает, что человек увидит то, что вы задумали.
4. **Массовые правки (2+ слова) – по одному.** Правка, проверка, следующее слово. Перед массовой правкой выпишите список слов, которые затронете, новое значение и то, что не трогаете.

---

## Гоча: Template против QuickAnswer

Кнопка выглядит одинаково, но работает по-разному – от типа формы блока-сообщения.

| Тип формы | Как рисуется кнопка | Что по тапу |
|---|---|---|
| `Template` | внутри пузыря сообщения, во всю ширину | ссылка открывается, переход работает |
| `QuickAnswer` | сбоку, серой пилюлей-чипом | боту уходит текст кнопки, наружу **ничего не открывается** |

- **QuickAnswer не используем.** В каждом `automations_messages_create` всегда передавайте `formType: "template"`:

  ```json
  {"stepId": "<STEP_ID>", "actionType": "message", "formType": "template", "text": "<ТЕКСТ>"}
  ```

- **Тип нельзя изменить после создания:** `automations_messages_set_form_type` отвечает `You can not change type`. Чинится только пересборкой: новый блок с `formType: "template"`, перевесить кнопки, переподключить входящие связи, старый удалить.
- **Кнопку нельзя вешать на action-блок** (`actionType: 3`). API примет, `automations_get_detail` покажет, а человек не увидит ничего: у служебных блоков нет пузыря.
- **Воронку с кнопками начинайте с `automations_quick_setup`** (`templateType: "base"`). `automations_create` заводит первый блок в типе `QuickAnswer`, а удалить первый блок API не даёт, так что исправить это нельзя. Quick_setup создаёт первое и второе сообщения сразу в `Template`; ветки достраивайте вручную, передавая `formType: "template"`.
- **После quick_setup снимите `<p>`-обёртки с текстов** сообщений (`<p>…</p>` вокруг `text`): оставьте чистый текст.

Дальнейшая сборка воронки – [references/funnel-flow.md](references/funnel-flow.md).

---

## Connection

- **MCP endpoint:** `https://mcp.chatplace.io/mcp`
- **Auth:** `Authorization: Bearer $CHATPLACE_API_KEY` (НЕ `x-api-key` – Cloudflare 403)
- **Клиент – только `curl`.** Запросы из Python `urllib` получают от Cloudflare 403, даже с верным ключом.
- **Файл ключей:** `~/.secrets/chatplace/env` (`CHATPLACE_API_KEY`, `CHATPLACE_BOT_ID`)
- **Bot id** берите из ответа `bots_list` (или из `CHATPLACE_BOT_ID`), в скилл и в скрипты его не вписывайте.

```bash
source ~/.secrets/chatplace/env   # $CHATPLACE_API_KEY, $CHATPLACE_BOT_ID
```

---

## Workflow 1: создание кодового слова

Полный playbook — [references/keyword-workflow.md](references/keyword-workflow.md). Короткая версия:

1. **(ОПЦИОНАЛЬНО) Invite link** – нужен, только если воронка ведёт в ваш Telegram-канал: Telegram Bot API `createChatInviteLink`; запись в реестр источников – тоже по желанию. Без канала пропустите шаг.
2. **Tag** (`tags_create`, name=`kw_<keyword>`) для сегментации.
3. **Automation** (`automations_quick_setup`): botId, `triggerType:["messageContains","commentContains"]`, `startMessages` = минимум 7 вариантов суммарно, `templateType:"base"`, `welcomeMessage` / `welcomeButton` / `messageWithLink` / `buttonText` / `buttonLink` (+ опционально, только если нужен отложенный переход в Telegram-канал: `additionalMaterialMessage` / `additionalMaterialDelayType` / `additionalMaterialDelayValue`). По умолчанию воронка кончается оффером. Что делать дальше – [references/funnel-flow.md](references/funnel-flow.md).
4. **Action** (`automations_actions_setup`) — добавить тег на первое сообщение.
5. **Comment auto-reply rule** (`comments_create`): type=equals, startMessages=тот же набор (минимум 7 суммарно), answers=3+ ротации без эмодзи и ссылок.
6. **Тест** — вручную под постом и в DM.

Существующие keywords можно вести в своём реестре (таблица / spreadsheet) – по желанию; сами автоматизации всегда можно перечитать из ChatPlace tools группы `automations_*`.

---

## Workflow 2: AI-агент-продажник (ОПЦИОНАЛЬНО)

> **Эта часть необязательна и требует платного AI-пакета ChatPlace.** Для кодовых слов она не нужна: можно целиком пропустить всё ниже до раздела Troubleshooting.

Полный playbook — [references/ai-agent-workflow.md](references/ai-agent-workflow.md).

### Предусловия

- **Агент создаётся только через UI.** Все `ai_agent_*` падают с `"AI assistant not exists"`, пока агент не создан в кабинете: ChatPlace → бот → AI-агент → Создать.
- **AI-пакет должен быть оплачен.** Публикация падает с `"You have no active ChatPlace AI package"`: Connect AI tools → AI agent → оплата.

### Правила AI-агента

- **`generateButtons` – это quick-reply варианты ответов пользователя, а не URL-кнопки.** Включённый `generateButtons: true` кнопок-ссылок не создаёт. В `ai_agent_knowledge_base_add` / `_update` поля `buttons` нет. Все ссылки агент отправляет текстом в DM, «вынести URL в кнопку» через API нельзя.
- **Global rules не должны прописывать ОДИН URL на все случаи.** Если в global rules жёстко стоит один шаблон (например `?utm_campaign=agent`), агент переписывает на него суффиксы из knowledge base, и атрибуция теряется: `agent_warm`, `agent_guides`, `agent_objection` сливаются в `agent`. Правильно: перечислите в global rules 3+ контекстных URL с явными суффиксами, в блоке логики продажи укажите, какой суффикс для какого сегмента, и добавьте запрет «НЕ упрощай и НЕ обрезай utm_campaign». Проверьте через `ai_agent_test_question` на трёх вопросах (тёплый / гайды / возражение): `utm_campaign` в ответе должен совпасть с сегментом.

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
- **Внутренние ссылки** (админка, партнёрка) клиентам не отдавать: запретите это в global rules отдельной строкой.
- **Холодный канал:** `https://t.me/+YOUR_CHANNEL_INVITE` (ваш Telegram-канал, если он есть).
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
| Cloudflare 403 HTML | Неверный заголовок или не `curl` | Только `Authorization: Bearer`, запросы только через `curl` (не `urllib`) |
| Кнопка есть, но ссылка не открывается | Блок создан как `QuickAnswer` | Пересобрать блок с `formType: "template"` (см. гочу выше) |
| Кнопка есть в `get_detail`, но человек её не видит | Кнопка на action-блоке | Перенести на блок `actionType: "message"` |
| Агент не ловит опечатки «Cloude/Клоде» | equals case-sensitive | Варианты в KB как вопросы ИЛИ Message Recognition (UI) |
| Под комментом тишина | Нет comment auto-reply rule | `comments_create` |

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

- [references/keyword-workflow.md](references/keyword-workflow.md) – пошаговое создание кодового слова
- [references/funnel-flow.md](references/funnel-flow.md) – воронка после quick_setup: гейт, оффер, отложенная карточка
- [references/ai-agent-workflow.md](references/ai-agent-workflow.md) – AI-агент (опционально, платный AI-пакет)
- [references/ai-agent-current-config.md](references/ai-agent-current-config.md) – пример конфигурации AI-агента (опционально)
