# Полный playbook: создание кодового слова

Пошаговая инструкция для создания нового Instagram keyword в ChatPlace.

## Пример: слово `workshop` / `воркшоп`

### 0. Prerequisites

```bash
source ~/.secrets/chatplace/env
BOT="YOUR_BOT_ID"
API="https://mcp.chatplace.io/mcp"
```

### 1. Invite link

Ваш трекинг ссылок (invite link → источник подписки). Минимум 3 шага:
1. Telegram Bot API `createChatInviteLink` с `member_limit: 99999`
2. Сохранить пару `(invite_link → keyword)` в вашей БД
3. (Опционально) обновить карту на сервере-трекере, если используете

### 2. Tag

```bash
curl -sS -X POST "$API" \
  -H "Authorization: Bearer $CHATPLACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"tags_create","arguments":{"name":"kw_workshop","color":"#FF5733"}}}'
```

Запиши `tagId` из ответа — понадобится на шаге 4.

### 3. Automation через quick_setup

```bash
jq -n --arg b "$BOT" --arg url "https://t.me/+<INVITE_LINK>" '{
  jsonrpc:"2.0",id:1,method:"tools/call",params:{
    name:"automations_quick_setup",
    arguments:{
      botId: $b,
      name: "WORKSHOP keyword",
      commentEquals: ["workshop","Workshop","WORKSHOP","WorkShop","воркшоп","Воркшоп","ВОРКШОП"],
      messageEquals: ["workshop","Workshop","WORKSHOP","WorkShop","воркшоп","Воркшоп","ВОРКШОП"],
      welcomeText: "Привет! Держи ссылку на воркшоп",
      buttonText: "Открыть",
      buttonUrl: $url
    }
  }
}' > /tmp/automation.json
curl -sS -X POST "$API" -H "Authorization: Bearer $CHATPLACE_API_KEY" -H 'Content-Type: application/json' --data-binary @/tmp/automation.json
```

Запиши `automationId` и `stepId`/`messageId` из ответа.

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
4. Нажать кнопку «Открыть» — invite link должен увести в канал
5. Через 1-2 часа проверить Supabase `channel_subscribers` — источник подписки корректный

### 7. Актуализация

- Занести keyword в ваш реестр:
  ```
  | workshop | kw_workshop | <supabase_label> | <automation_id> |
  ```
- Записать в LEARNINGS если выявил новую проблему.

## Чеклист «не забыл ли?»

- [ ] Invite link создан с `member_limit: 99999`
- [ ] Ссылка в Supabase `invite_links_registry`
- [ ] Ссылка в вашем трекере источников (если используете)
- [ ] Tag создан
- [ ] Automation создана с 7+ case variants в `commentEquals` И `messageEquals`
- [ ] Tag action навешен на первое сообщение
- [ ] **Comment auto-reply rule создан** (самое частое упущение!)
- [ ] Rule с теми же 7+ variants в `startMessages`
- [ ] 3+ варианта ответов в `answers` для ротации
- [ ] Без эмодзи и без ссылок в `answers`
- [ ] Проверено live: и коммент, и DM
- [ ] Keyword добавлен в ваш реестр

## Частые ошибки

### Забыл comment rule
**Симптом:** DM приходит, но под комментом тишина. Зрители не понимают что происходит, не оставляют свои комменты.
**Решение:** всегда делать comment rule ВМЕСТЕ с automation. Без исключений.

### Только один регистр
**Симптом:** клиент пишет «Workshop» — ничего. «workshop» — работает.
**Причина:** `type:"equals"` case-sensitive.
**Решение:** 7 вариантов для каждого языка с первого раза.

### Забыл `member_limit: 99999`
**Симптом:** ссылка истекает после первых подписчиков.
**Решение:** всегда при `createChatInviteLink` ставить лимит 99999.

### Update ссылки в одном месте
**Симптом:** БД сказала старый URL, automation открывает новый, трекер не видит источник.
**Решение:** обновлять везде одновременно: ваша БД + ChatPlace automation button + трекер источников.
