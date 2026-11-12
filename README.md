# Instagram Superpowers

Три Claude Code скилла для Instagram-воронки: аналитика профилей и рилсов через Loore AI, автоматика DM и AI-агент-продажник в ChatPlace, монтаж рилса с говорящей головой на ffmpeg и Groq.

Собрано для тех, кто хочет повторить пайплайн из гайда [Как мы делаем виральные рилсы](https://guides.edgelab.su/guides/viral-reels-pipeline/).

## Что внутри

### 1. `instagram-loore/` – разведка, разбор и скачивание
- Аналитика любого публичного профиля: метрики, engagement rate, окна публикаций, хэштеги
- Залетевшие рилсы аккаунта – относительно медианы самого автора
- Мониторинг списка конкурентов (watchlist) с лентой выбросов
- Хуки, транскрипты с таймкодами, раскадровка рилса, сравнение 2–5 рилсов
- Поиск того, что вирусится в нише и по хэштегам
- Скачивание рилсов, постов и сторис файлом
- Работает через **[Loore AI](https://loore.ai)** – источник данных Instagram для AI-агентов: REST API, CLI и MCP по одному ключу. Документация: [docs.loore.ai](https://docs.loore.ai)

### 2. `chatplace-marketing-sales/` – автоворонка и продажник
- Кодовые слова в Instagram (Daши DM + публичный ответ под комментом в одном проходе)
- AI-агент-продажник в DM: global rules + topic rules + knowledge base
- Ведёт зрителя от комментария к продаже лендинга/воркшопа
- Работает через **ChatPlace MCP**

Для аналитики воронки (сквозные отчёты, конверсия, подписки) нужен отдельный скилл – он не включён в этот репо.

### 3. `reels-ffmpeg-montage/` – монтаж рилса без Edits
- Сырой дубль одного говорящего человека превращается в готовый рилс: вырезаются паузы, повторы и оговорки, громкость около -14 LUFS, 1080x1920, 30 fps, субтитры с подсветкой слова
- Транскрипт со словами и таймкодами – через Groq по вашему ключу, всё остальное считается локально на ffmpeg
- Повторы и оговорки агент только предлагает, вырезается лишь то, что вы подтвердили; готовый файл и кадры на стыках агент проверяет и показывает вам
- В отличие от монтажа через приложение Edits, ему не нужны ни Edits, ни его автоматизация – только ffmpeg (с libass для субтитров), Python 3.10+ и ключ Groq
- Скилл ничего не публикует: готовый mp4 вы выкладываете руками в Instagram или импортируете руками в приложение Edits

## Требования к API и цены

| Сервис      | Для чего                          | Цена                                        | Как подключить |
|-------------|-----------------------------------|---------------------------------------------|----------------|
| Loore AI    | Данные Instagram: профили, рилсы, транскрипты, конкуренты, скачивание | Тарифы в кредитах | [loore.ai](https://loore.ai) – регистрация, ключ в кабинете: Настройки → API и MCP. Документация: [docs.loore.ai](https://docs.loore.ai) |
| ChatPlace   | Instagram DM + AI-агент           | $20/мес                                     | [chatplace.io](https://chatplace.io/?r=019d12f0-45f9-713a-87c0-e6ad9d10ebad) — регистрация, подключение MCP |
| Groq        | Распознавание речи со словами и таймкодами для монтажа | По тарифам Groq | Ключ создаётся в консоли Groq, кладётся в `GROQ_API_KEY` или `~/.secrets/groq/env` |

**Обязательно:**
- Loore AI – только для `instagram-loore`
- ChatPlace MCP + AI Pack – только для `chatplace-marketing-sales`
- ffmpeg и ключ Groq – только для `reels-ffmpeg-montage` (ключ создаётся в консоли Groq, тарифы – у Groq; подробности в `reels-ffmpeg-montage/SKILL.md`)

## Установка

Скиллы Claude Code хранятся в `~/.claude/skills/`. Просто скопируйте туда нужные папки:

```bash
git clone https://github.com/qwwiwi/instagram-superpowers.git
cp -r instagram-superpowers/instagram-loore ~/.claude/skills/
cp -r instagram-superpowers/chatplace-marketing-sales ~/.claude/skills/
cp -r instagram-superpowers/reels-ffmpeg-montage ~/.claude/skills/
```

Перезапустите Claude Code – скиллы появятся в списке доступных.

## Подключение Loore

1. Зарегистрируйтесь на [loore.ai](https://loore.ai) и создайте ключ в кабинете: **Настройки → API и MCP**. Ключ показывается один раз – сохраните его сразу.
2. Положите ключ в переменную окружения (не в команды и не в репо):
   ```bash
   export LOORE_API_KEY='<ВАШ_КЛЮЧ>'
   ```
3. Подключите MCP-сервер.

   Claude Code:
   ```bash
   claude mcp add --transport http loore https://mcp.loore.ai/mcp \
     --header "Authorization: Bearer $LOORE_API_KEY"
   ```
   Проверка – команда `/mcp` в сессии.

   Codex:
   ```bash
   codex mcp add loore --url https://mcp.loore.ai/mcp --bearer-token-env-var LOORE_API_KEY
   ```
   Проверка – `codex mcp list`.

   Другие клиенты (Cursor, Claude Desktop, OpenClaw, Hermes) – в [документации Loore](https://docs.loore.ai).
4. Без MCP скилл работает через REST: `instagram-loore/scripts/loore.sh` берёт ключ из `LOORE_API_KEY`.

Подробно – `instagram-loore/references/connect.md`.

## Quick Start

1. Получите ключи (см. таблицу «Требования к API» выше).
2. Подключите Loore (см. «Подключение Loore») и настройте ChatPlace (см. «Конфигурация ключей ChatPlace»).
3. В новой сессии Claude Code напишите: «проанализируй аккаунт @example» – скилл `instagram-loore` подтянется сам.
4. Для ChatPlace-воронки: «создай в ChatPlace кодовое слово workshop» – подтянется `chatplace-marketing-sales`.

## Конфигурация ключей ChatPlace

Скилл ожидает ключи в `~/.secrets/`:

```bash
mkdir -p ~/.secrets/chatplace
cat > ~/.secrets/chatplace/env <<'EOF'
export CHATPLACE_API_KEY="your_chatplace_api_key"
export CHATPLACE_BOT_ID="your_bot_id"
EOF
chmod 600 ~/.secrets/chatplace/env
```

## Пайплайн на практике

Скиллы используются вместе так:

1. **Разведка** (`instagram-loore`) – выбираете нишу, находите залетевшие рилсы конкурентов, вытаскиваете хуки и транскрипты
2. **Сценарий и съёмка** – ваш процесс: адаптируете паттерн (хук, структура, темп), а не копируете чужой рилс
3. **Монтаж** (`reels-ffmpeg-montage`) – из сырого дубля: «смонтируй рилс из take.mp4». Агент показывает таблицу фраз и список вырезаний, вы подтверждаете, получаете mp4 с субтитрами и выкладываете его руками
4. **Автоворонка** (`chatplace-marketing-sales`) – под каждый рилс настраиваете кодовое слово: DM с ссылкой + публичный ответ под комментом
5. **Продажа** (`chatplace-marketing-sales`, AI-агент) – в DM автоматически прогревает и ведёт к покупке

## Безопасность

- Ключ Loore – только в переменной окружения или хранилище секретов клиента, не в репо и не в `~/.claude/skills/`
- Секреты ChatPlace – в `~/.secrets/`
- Ключ Groq – в переменной `GROQ_API_KEY` или в `~/.secrets/groq/env`; скрипты монтажа не печатают его и не пишут в выходные файлы
- `.gitignore` исключает ключи
- Скрипты читают ключи из окружения и файлов, не из хардкода, и не печатают их

## Лицензия

MIT – используйте, форкайте, адаптируйте под свои задачи.

## Автор

Собрано в [EdgeLab](https://edgelab.su) – учим собирать AI-агентов за 3 дня без кода. [Воркшоп](https://edgelab.su/workshop/?utm_source=github&utm_medium=repo&utm_campaign=instagram-superpowers).
