# Полный playbook: создание кодового слова

Пошаговая инструкция для создания нового Instagram keyword в ChatPlace.

## Пример: слово `workshop` / `воркшоп`

### 0. Prerequisites

Сначала пройдите раздел «Что нужно до начала» в [SKILL.md](../SKILL.md): аккаунт с Instagram, API-ключ, файл `~/.secrets/chatplace/env`.

```bash
source ~/.secrets/chatplace/env   # $CHATPLACE_API_KEY и $CHATPLACE_BOT_ID
BOT="$CHATPLACE_BOT_ID"           # id из ответа bots_list, в файл не вписывайте
API="https://mcp.chatplace.io/mcp"
```

Запросы – только через `curl`: из Python `urllib` Cloudflare отвечает 403.

### 1. (ОПЦИОНАЛЬНО) Invite link

**Шаг нужен, только если воронка ведёт в ваш Telegram-канал. Без канала переходите сразу к шагу 2.** Тогда в шаге 3 в `buttonLink` стоит любая ваша ссылка (лендинг, гайд).

Если канал есть:
1. Telegram Bot API `createChatInviteLink` с `member_limit: 99999`.
2. Подставьте ссылку в `buttonLink` (шаг 3) или в карточку канала ([funnel-flow.md](funnel-flow.md)).
3. Если ведёте учёт источников подписки, сохраните пару `(invite_link → keyword)` в вашей таблице подписчиков, если она есть.

### 2. Tag

```bash
curl -sS -X POST "$API" \
  -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"tags_create","arguments":{"name":"kw_workshop","color":"#FF5733"}}}'
```

Запиши `tagId` из ответа — понадобится на шаге 4.

### 3. Automation через quick_setup

Схема `automations_quick_setup` (актуальная): триггер задаётся парой `triggerType` + `startMessages` (не `commentEquals` / `messageEquals`), тексты – полями `welcomeMessage`, `welcomeButton`, `messageWithLink`, `buttonText`, `buttonLink` (не `welcomeText` / `buttonUrl`).

| Поле | Обязательно | Что это |
|---|---|---|
| `botId` | да | id бота из `bots_list` |
| `triggerType` | да | `["messageContains","commentContains"]` – ловим и слово в DM, и слово в комментарии |
| `startMessages` | да | 7+ написаний слова (регистры и алфавиты) |
| `templateType` | да | `"base"` – оба сообщения создаются сразу в типе `Template`, кнопка со ссылкой работает |
| `welcomeMessage` | да | первое сообщение (гейт), без ссылки |
| `welcomeButton` | да | надпись на кнопке гейта |
| `messageWithLink` | да | второе сообщение – то, что открывается по кнопке гейта |
| `buttonText` | да | надпись на кнопке со ссылкой |
| `buttonLink` | да | ссылка кнопки |
| `additionalMaterialMessage` | нет | отложенное сообщение (например, карточка канала) |
| `additionalMaterialDelayType` | нет | единица задержки, например `"hour"` |
| `additionalMaterialDelayValue` | нет | число единиц, например `1` |

```bash
URL="https://yourdomain.com/guide"   # куда ведёт кнопка в оффере
jq -n --arg b "$BOT" --arg url "$URL" '{
  jsonrpc:"2.0",id:1,method:"tools/call",params:{
    name:"automations_quick_setup",
    arguments:{
      botId: $b,
      triggerType: ["messageContains","commentContains"],
      startMessages: ["workshop","Workshop","WORKSHOP","WorkShop","воркшоп","Воркшоп","ВОРКШОП"],
      templateType: "base",
      welcomeMessage: "Лови. Внутри – разбор по воркшопу. Жми «Забрать», пришлю.",
      welcomeButton: "Забрать",
      messageWithLink: "Держи материал. А следом расскажу, что делать дальше.",
      buttonText: "Открыть",
      buttonLink: $url,
      additionalMaterialMessage: "Пока пробуешь – загляни в мой канал.",
      additionalMaterialDelayType: "hour",
      additionalMaterialDelayValue: 1
    }
  }
}' > /tmp/automation.json
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' --data-binary @/tmp/automation.json
```

Три последних поля (`additionalMaterial*`) можно убрать – тогда отложенного сообщения не будет.

Запиши `automationId` и `stepId`/`messageId` из ответа. Дальше – вторая кнопка и отложенная карточка: [funnel-flow.md](funnel-flow.md).

### 4. Action setup — теггирование

```bash
curl -sS -X POST "$API" \
  -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
    "name":"automations_actions_setup",
    "arguments":{
      "messageId":"<MESSAGE_ID>",
      "addTags":["<TAG_ID>"]
    }
  }}'
```

### 5. Comment auto-reply rule

```bash
jq -n --arg b "$BOT" '{
  jsonrpc:"2.0",id:1,method:"tools/call",params:{
    name:"comments_create",
    arguments:{
      botId: $b,
      name: "WORKSHOP comment reply",
      type: "equals",
      startMessages: ["workshop","Workshop","WORKSHOP","WorkShop","воркшоп","Воркшоп","ВОРКШОП"],
      answers: ["Отправил в ЛС, смотри директ","Ответила в личку","Смотри сообщения, всё там","Кинул в директ","В ЛС всё расписал"]
    }
  }
}' > /tmp/comment.json
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' --data-binary @/tmp/comment.json
```

### 6. Тест

1. Под тестовым постом на @your_instagram написать комментарий: `workshop` (проверить lowercase)
2. Проверить: публичный ответ появился + в DM пришло welcome-сообщение с кнопкой
3. Повторить с `Workshop` и `воркшоп` — все должны ловиться
4. Нажать «Забрать», затем кнопку «Открыть» — ссылка должна открыться (если не открывается – см. гочу «Template против QuickAnswer» в [SKILL.md](../SKILL.md))
5. Если собрали отложенную карточку – через заданное время (в примере 1 час) проверить, что она пришла ровно один раз
6. (ОПЦИОНАЛЬНО) Если ведёте учёт источников подписки: через 1–2 часа проверить в вашей таблице подписчиков, если она есть, что источник записан верно

### 7. Актуализация

- (ОПЦИОНАЛЬНО) Занести keyword в ваш реестр:
  ```
  | workshop | kw_workshop | <automation_id> |
  ```
- Если нашли новую проблему – запишите её в свои заметки, чтобы не наступить дважды.

### Правка существующего слова

Перед правкой: сохраните текущее состояние (`automations_get_detail` в файл), внесите изменение, проверьте живым запросом (`automations_get_detail` + тест в директе). Если слов несколько – правьте по одному и проверяйте каждое. Подробности – раздел «перед правкой» в [SKILL.md](../SKILL.md).

## Чеклист «не забыл ли?»

Пункты с пометкой ОПЦИОНАЛЬНО нужны, только если у вас есть Telegram-канал или свой реестр.

- [ ] ОПЦИОНАЛЬНО: invite link создан с `member_limit: 99999`
- [ ] ОПЦИОНАЛЬНО: пара «ссылка → keyword» записана в вашу таблицу подписчиков, если она есть
- [ ] Tag создан
- [ ] Automation создана с `triggerType:["messageContains","commentContains"]` + 7+ case variants в `startMessages`
- [ ] Tag action навешен на первое сообщение
- [ ] **Comment auto-reply rule создан** (самое частое упущение!)
- [ ] Rule с теми же 7+ variants в `startMessages`
- [ ] 3+ варианта ответов в `answers` для ротации
- [ ] Без эмодзи и без ссылок в `answers`
- [ ] Проверено live: и коммент, и DM
- [ ] ОПЦИОНАЛЬНО: keyword добавлен в ваш реестр

## Частые ошибки

### Забыл comment rule
**Симптом:** DM приходит, но под комментом тишина. Зрители не понимают что происходит, не оставляют свои комменты.
**Решение:** всегда делать comment rule ВМЕСТЕ с automation. Без исключений.

### Только один регистр
**Симптом:** клиент пишет «Workshop» — ничего. «workshop» — работает.
**Причина:** `type:"equals"` case-sensitive.
**Решение:** 7 вариантов для каждого языка с первого раза.

### Кнопка не открывает ссылку
**Симптом:** кнопка нарисована серой пилюлей сбоку, по тапу ничего не открывается.
**Причина:** блок создан как `QuickAnswer`.
**Решение:** пересобрать блок с `formType: "template"`, см. гочу в [SKILL.md](../SKILL.md).

### Забыл `member_limit: 99999` (если есть канал)
**Симптом:** ссылка-приглашение истекает после первых подписчиков.
**Решение:** всегда при `createChatInviteLink` ставить лимит 99999.

### Обновил ссылку не везде (если ведёте свой реестр)
**Симптом:** реестр хранит старый URL, а кнопка в ChatPlace открывает новый.
**Решение:** при смене ссылки правьте одновременно кнопку в ChatPlace и свой реестр.
