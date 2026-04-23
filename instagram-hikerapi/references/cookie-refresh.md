# Регламент обновления куков

## Общие принципы

- Куки протухают: Instagram ~30-90 дней, YouTube ~180 дней, Twitter ~365 дней
- При ошибке `fetch.empty` или `login_required` -- первым делом обновить куки
- Купленные аккаунты: куки живут часы/дни, нужно залогиниться заново
- Свои аккаунты: стабильнее, но тоже протухают

## Хранение секретов

### accounts.json -- креды аккаунтов

Путь: `~/.secrets/cobalt/accounts.json`

```json
{
  "instagram": {
    "login": "username",
    "password": "password",
    "totp_secret": "BASE32_TOTP_SECRET",
    "user_id": "numeric_user_id",
    "source": "bought|own|technical",
    "purchased_at": "2026-03-28",
    "notes": "account marketplace purchase"
  },
  "youtube": {
    "login": "email@gmail.com",
    "notes": "manual Cookie-Editor export from owner's browser"
  },
  "twitter": {
    "login": "username",
    "notes": "manual Cookie-Editor export from owner's browser"
  }
}
```

### cookies.json -- рабочие куки

Путь: `~/.secrets/cobalt/cookies.json`

Формат Cobalt v10+, см. SKILL.md.

### api-key -- ключ API Cobalt

Путь: `~/.secrets/cobalt/api-key`
Содержимое: UUID строка (одна строка, без переводов).

## Instagram -- полный регламент логина

### Предусловия
- Chromium запущен с CDP на порту 9222
- В accounts.json есть login, password, totp_secret

### Шаг 1: Запуск Chromium
```bash
# start headless Chromium with CDP on port 9222 — see github.com/imputnet/cobalt or use puppeteer/playwright
chromium --remote-debugging-port=9222 --user-data-dir=/tmp/chromium-profile &
```

### Шаг 2: Навигация на страницу логина
```bash
# navigate via any CDP client (chrome-remote-interface, playwright, etc.) — example below uses raw websockets
```
Подождать 3 секунды для загрузки.

### Шаг 3: Ввод логина и пароля через CDP

Instagram использует React -- прямое изменение `.value` не работает.
Использовать CDP Input.dispatchKeyEvent (эмуляция клавиатуры):

```python
import asyncio, json, base64, websockets

async def main():
    # Получить WS URL
    import urllib.request
    targets = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json').read())
    ws_url = [t for t in targets if t['type'] == 'page'][0]['webSocketDebuggerUrl']

    async with websockets.connect(ws_url) as ws:
        msg_id = 0
        async def send_cmd(method, params=None):
            nonlocal msg_id
            msg_id += 1
            cmd = {"id": msg_id, "method": method}
            if params: cmd["params"] = params
            await ws.send(json.dumps(cmd))
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == msg_id:
                    return resp.get("result", {})

        async def type_text(text):
            for char in text:
                await send_cmd("Input.dispatchKeyEvent", {"type": "char", "text": char})
                await asyncio.sleep(0.03)

        async def click(x, y):
            await send_cmd("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1
            })
            await asyncio.sleep(0.05)
            await send_cmd("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1
            })

        # Найти поле username через DOM
        result = await send_cmd("Runtime.evaluate", {
            "expression": """
                const inp = document.querySelector('input[name="username"]');
                const rect = inp.getBoundingClientRect();
                JSON.stringify({x: rect.x + rect.width/2, y: rect.y + rect.height/2});
            """
        })
        pos = json.loads(result['result']['value'])
        await click(pos['x'], pos['y'])
        await asyncio.sleep(0.5)
        await type_text(LOGIN)

        # Tab к паролю
        await send_cmd("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9
        })
        await send_cmd("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9
        })
        await asyncio.sleep(0.3)
        await type_text(PASSWORD)

        # Нажать кнопку "Войти" через JS (div[role="button"])
        await send_cmd("Runtime.evaluate", {
            "expression": """
                const btns = document.querySelectorAll('div[role="button"]');
                for (const b of btns) {
                    if (b.textContent.trim() === 'Войти' || b.textContent.trim() === 'Log in') {
                        b.click(); break;
                    }
                }
            """
        })

asyncio.run(main())
```

**Важно:**
- Кнопка логина -- `div[role="button"]`, НЕ `<button type="submit">`
- React-формы НЕ реагируют на `.value = ...` -- только CDP keystroke events
- Playwright `connectOverCDP` зависает -- использовать чистый websockets

### Шаг 4: 2FA -- ввод TOTP кода

После успешного логина Instagram перенаправит на `/accounts/login/two_factor`.

Генерация TOTP:
```python
import hmac, hashlib, struct, time, base64

def generate_totp(secret):
    key = base64.b32decode(secret)
    counter = int(time.time()) // 30
    msg = struct.pack('>Q', counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack('>I', h[offset:offset+4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)
```

Ввести код:
1. Найти `input[name="verificationCode"]` через DOM
2. Кликнуть по его координатам
3. Ввести 6-значный TOTP через `Input.dispatchKeyEvent`
4. Найти кнопку «Подтвердить» (тег `BUTTON`) и кликнуть по координатам

**TOTP живёт 30 секунд.** Генерировать непосредственно перед вводом.

### Шаг 5: Извлечение куков

После успешной 2FA -- URL сменится на `/accounts/onetap/` или `/`.

```python
result = await send_cmd("Network.getCookies", {"urls": ["https://www.instagram.com"]})
cookies = result.get("cookies", [])
important = {}
for c in cookies:
    if c["name"] in ("sessionid", "csrftoken", "mid", "ds_user_id", "ig_did"):
        important[c["name"]] = c["value"]
```

### Шаг 6: Сохранение и деплой

```python
import json

# Сформировать cookie-строку для Cobalt
cookie_str = "; ".join(f"{k}={v}" for k, v in important.items())

# Прочитать существующий cookies.json
with open("~/.secrets/cobalt/cookies.json") as f:
    cobalt_cookies = json.load(f)

cobalt_cookies["instagram"] = [cookie_str]

# Сохранить
with open("~/.secrets/cobalt/cookies.json", "w") as f:
    json.dump(cobalt_cookies, f, indent=2)
```

Деплой на сервер:
```bash
bash ~/.claude/skills/instagram-hikerapi/scripts/deploy-cookies.sh
```

## YouTube -- обновление куков

YouTube не поддерживает программный логин (CAPTCHA, device verification).
Единственный путь -- ручной экспорт куков из браузера владельца.

1. Установите расширение Cookie-Editor в Chrome
2. Зайдите на youtube.com (должен быть залогинен)
3. Cookie-Editor → Export → Header String
4. Сохраните полученную строку в cookies.json:
```json
{
  "youtube": ["<cookie-строка>"]
}
```
5. Деплой: `bash ~/.claude/skills/instagram-hikerapi/scripts/deploy-cookies.sh`

## Twitter -- обновление куков

Аналогично YouTube:
1. Экспорт из Chrome через Cookie-Editor (как для YouTube)
2. Нужны: `auth_token` и `ct0`
3. Формат: `"twitter": ["auth_token=xxx; ct0=yyy"]`
4. Деплой

## Проверка валидности куков

### Instagram
```bash
ssh $YOUR_SERVER_SSH 'curl -s "https://i.instagram.com/api/v1/accounts/current_user/" \
  -H "Cookie: $(cat /opt/cobalt/cookies.json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"instagram\"][0])")" \
  -H "User-Agent: Instagram 385.0.0.47.74 Android" \
  -H "X-IG-App-ID: 567067343352427" | python3 -c "
import json,sys
d=json.load(sys.stdin)
if \"user\" in d: print(f\"VALID: {d[\"user\"][\"username\"]}\")
else: print(f\"INVALID: {d.get(\"message\",\"unknown\")}\")"'
```

## Покупка аккаунтов (если нужен новый)

### Процесс
1. Купить аккаунт на маркетплейсе (формат: login:password:2FA|cookies)
2. Ссылка на скачивание приходит на ваш email
3. Скачать файл, распарсить: `login:password:2FA_SECRET|user_agent|device_ids|cookies|flags`
4. Купленные куки обычно мертвы -- нужно залогиниться заново (шаги 1-6 выше)
5. Сохранить креды в `~/.secrets/cobalt/accounts.json`

### Парсинг файла с аккаунтом
Формат: `login:password:2FA|UserAgent|DeviceIDs|Cookies|Flags`
- Разделитель полей: `|`
- Разделитель login/pass/2fa: `:`
- 2FA -- Base32 TOTP-секрет
- Cookies -- заголовки через `;` (обычно уже мертвы)

### Предупреждения
- Купленные куки протухают за часы -- всегда логинься заново
- Аккаунт может быть забанен в любой момент
- Для стабильности лучше свой технический аккаунт
