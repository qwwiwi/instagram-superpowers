# Instagram Superpowers

Два Claude Code скилла для Instagram-воронки: аналитика профилей/рилсов + автоматика DM и AI-агент-продажник в ChatPlace.

Собрано для тех, кто хочет повторить пайплайн из гайда [Как мы делаем виральные рилсы](https://guides.edgelab.su/guides/viral-reels-pipeline/).

## Что внутри

### 1. `instagram-hikerapi/` — разведка и скачивание
- Анализ любого публичного профиля (метрики, engagement rate)
- Топ-рилсы аккаунта за период
- Мониторинг списка конкурентов (watchlist)
- Скачивание рилсов/постов через Cobalt (self-hosted)
- Работает через **HikerAPI** — cloud API для Instagram данных

### 2. `chatplace-marketing-sales/` — автоворонка и продажник
- Кодовые слова в Instagram (Daши DM + публичный ответ под комментом в одном проходе)
- AI-агент-продажник в DM: global rules + topic rules + knowledge base
- Ведёт зрителя от комментария к продаже лендинга/воркшопа
- Работает через **ChatPlace MCP**

Для аналитики воронки (сквозные отчёты, конверсия, подписки) нужен отдельный скилл — он не включён в этот репо.

## Требования к API и цены

| Сервис      | Для чего                          | Цена                                        | Как подключить |
|-------------|-----------------------------------|---------------------------------------------|----------------|
| HikerAPI    | Данные Instagram (профили, рилсы) | ~$2–5/мес при умеренном объёме             | [hikerapi.com](https://hikerapi.com) — регистрация, получение access key |
| Cobalt      | Скачивание рилсов/постов          | Бесплатно: self-host свой инстанс          | [github.com/imputnet/cobalt](https://github.com/imputnet/cobalt) — поднимите на своём сервере |
| ChatPlace   | Instagram DM + AI-агент           | AI Pack — ~$50/мес                         | [chatplace.com](https://chatplace.com) — регистрация, подключение MCP |

**Обязательно:**
- HikerAPI — только для `instagram-hikerapi`
- ChatPlace MCP + AI Pack — только для `chatplace-marketing-sales`
- Cobalt self-hosted — опционально, нужен только если хотите скачивать медиа

## Установка

Скиллы Claude Code хранятся в `~/.claude/skills/`. Просто скопируйте туда нужные папки:

```bash
git clone https://github.com/qwwiwi/instagram-superpowers.git
cp -r instagram-superpowers/instagram-hikerapi ~/.claude/skills/
cp -r instagram-superpowers/chatplace-marketing-sales ~/.claude/skills/
```

Перезапустите Claude Code — скиллы появятся в списке доступных.

## Quick Start

1. Получите ключи (см. таблицу «Требования к API» выше).
2. Настройте `~/.secrets/` (см. «Конфигурация ключей» ниже).
3. Установите `COBALT_SERVER` в env, если будете скачивать рилсы:
   ```bash
   export COBALT_SERVER="user@your-server"   # SSH-таргет self-hosted Cobalt
   ```
4. В новой сессии Claude Code напишите: «проанализируй аккаунт @example» — скилл `instagram-hikerapi` подтянется сам.
5. Для ChatPlace-воронки: «создай в ChatPlace кодовое слово workshop» — подтянется `chatplace-marketing-sales`.

## Конфигурация ключей

Скиллы ожидают ключи в `~/.secrets/`:

```bash
mkdir -p ~/.secrets/hikerapi ~/.secrets/chatplace ~/.secrets/cobalt
echo "YOUR_HIKERAPI_KEY" > ~/.secrets/hikerapi/api-key
chmod 600 ~/.secrets/hikerapi/api-key

cat > ~/.secrets/chatplace/env <<'EOF'
export CHATPLACE_API_KEY="your_chatplace_api_key"
export CHATPLACE_BOT_ID="your_bot_id"
EOF
chmod 600 ~/.secrets/chatplace/env
```

Для Cobalt пропишите адрес своего инстанса в переменной окружения `COBALT_SERVER` (см. `instagram-hikerapi/scripts/download.sh`).

## Пайплайн на практике

Скиллы используются вместе так:

1. **Разведка** (`instagram-hikerapi`) — выбираете нишу, находите референсы рилсов конкурентов
2. **Сценарий и съёмка** — ваш процесс (гайд описывает вариант с Cobalt + транскрипция)
3. **Автоворонка** (`chatplace-marketing-sales`) — под каждый рилс настраиваете кодовое слово: DM с ссылкой + публичный ответ под комментом
4. **Продажа** (`chatplace-marketing-sales`, AI-агент) — в DM автоматически прогревает и ведёт к покупке

## Безопасность

- Все секреты — в `~/.secrets/` (не в репо, не в `~/.claude/skills/`)
- `.gitignore` исключает ключи и куки
- Скрипты читают ключи из файлов, не из хардкода

## Лицензия

MIT — используйте, форкайте, адаптируйте под свои задачи.

## Автор

Собрано в [EdgeLab](https://edgelab.su) — учим собирать AI-агентов за 3 дня без кода. [Воркшоп](https://edgelab.su/workshop/?utm_source=github&utm_medium=repo&utm_campaign=instagram-superpowers).
