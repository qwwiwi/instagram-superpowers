# Скелеты раскладок

Готовые заготовки, чтобы геометрию не придумывать заново. Все числа – из
`montage-reglament.md` (кадр 1080 × 1920, 30 к/с). Тексты в примерах условные: в
ролике на экране только слова речи студента (правило 4.2).

Два пути сборки, выбирается один на ролик:

- **Путь А (по умолчанию): одна композиция HyperFrames.** Видео спикера, сцены и
  субтитры – одна HTML-страница с остановленной GSAP-шкалой; `hyperframes render`
  выдаёт готовый mp4 со звуком. Субтитры – HTML, поэтому не нужны ни libass, ни Pillow,
  ни `drawtext`.
- **Путь Б: склейка ffmpeg.** Сцены отрисованы заранее отдельными роликами (тем же
  HyperFrames или как неподвижные PNG с движением средствами ffmpeg), ffmpeg кадрирует
  спикера, собирает сплит, склеивает планы и накладывает субтитры-PNG.

## 1. Зоны в числах

| Что | Координаты |
|---|---|
| Шов СПЛИТА | y 960 |
| Полоса карточек СПЛИТА | x 120–960, y 140–750 |
| Субтитр в СПЛИТЕ | низ блока y 944, строка 80 px (1 строка: y 864–944; 2 строки: y 784–944) |
| Макет ВИЗУАЛА | x 60–1020, y 130–1280 |
| Субтитр в ВИЗУАЛЕ | верх блока y 1320, строка 80 px, низ ≤ 1460 |
| «Два визуала» | верх y 115–820, разделитель y 836–840, низ y 860–1280 |
| Субтитр СПИКЕРА «грудь» | верх блока y 1180, низ ≤ 1340 |
| Субтитр СПИКЕРА «над головой» | низ блока = верх волос − 24 |
| Срез спикера в СПЛИТЕ | строки исходника `[y0, y0 + 960]`, `y0` по правилу 1.3 (`head_guide.py --top … --chin …`) |
| Не трогать | y 0–115, y 1520–1920, x 950–1080 при y 1080–1720 |

## 2. Путь А: композиция HyperFrames

### 2.1. Папка проекта

```
build/
  index.html        композиция
  speaker.mp4       копия reel-cut.mp4 (этап 1), без изменений
  speech.wav        звук reel-cut.mp4: ffmpeg -i reel-cut.mp4 -vn -c:a pcm_s16le speech.wav
  fonts/            файлы шрифтов (правило 5.4), подключаются через @font-face
```

Пересобирая проект, копируй `speaker.mp4` и `speech.wav` заново каждый раз (не по
размеру файла: новый рендер этапа 1 бывает того же размера).

### 2.2. Правила, без которых рендер ломается

- Корень: `data-composition-id="main"`, `data-width="1080"`, `data-height="1920"`,
  `data-duration` = длина `reel-cut.mp4`.
- Всё, что появляется на время, – элемент с `class="clip"`, `data-start`,
  `data-duration`.
- `<video>` с `data-start` нельзя класть внутрь другого элемента с `data-start`
  (линтер: `video_nested_in_timed_element`; кадры видео разъедутся с окном показа).
  Обёртка, которая обрезает видео (срез СПЛИТА), – без `data-start`.
- Видео спикера – `muted`; звук идёт отдельным `<audio>` на весь ролик. Для каждого плана
  со спикером – свой `<video>` с `data-media-start` = `data-start` (резка уже сделана на
  этапе 1, время ролика и время файла совпадают).
- Шкала GSAP создаётся остановленной (`{ paused: true }`) и регистрируется в
  `window.__timelines["main"]`. Время – третьим аргументом (`tl.to(el, vars, 1.5)`).
- Двигать только `x`, `y`, `scale`, `rotation`, `opacity` (не `left` / `top`).
- Колбэков GSAP нет (при перемотке они не срабатывают): набор текста – буквы-`span`,
  которые включаются `tl.set`; счётчик – стопка значений, которые переключаются `tl.set`.
- Проверка перед рендером: `hyperframes lint build` – 0 ошибок.

### 2.3. Скелет `index.html`

```html
<!doctype html>
<html lang="ru">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1080, height=1920" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
<style>
  /* Fonts: local files only (rule 5.4). */
  @font-face { font-family: "Display"; src: url("fonts/display.ttf"); font-weight: 700; }
  @font-face { font-family: "Ui"; src: url("fonts/ui.ttf"); }
  @font-face { font-family: "Mono"; src: url("fonts/mono.ttf"); }
  :root {
    --bg-dark: #111418; --grid-dark: #1F242B;
    --bg-accent: #0B7285; --bg-light: #EEF1F4; --grid-light: #D9DEE5;
    --card: #FFFFFF; --card-dark: #0B0D10; --ink: #111111; --muted: #5B6270;
    --ink-on-dark: #FFFFFF; --muted-on-dark: #9AA3B2;
    --accent: #0B7285; --accent-on-dark: #66D9E8; --signal: #E8590C; --signal-text: #C2410C;
    --mark: #D3EEF2;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1080px; height: 1920px; overflow: hidden; background: var(--bg-dark); }
  #root { position: relative; width: 1080px; height: 1920px; font-family: "Ui", sans-serif; }

  /* Speaker half of a split: untimed wrapper that crops the take (rule 1.3). */
  .speaker-half { position: absolute; left: 0; top: 960px; width: 1080px; height: 960px; overflow: hidden; z-index: 1; }
  .speaker-half video { position: absolute; left: 0; width: 1080px; height: 1920px; } /* top: -y0 */
  .speaker-full { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; z-index: 1; }

  /* Scene backgrounds with a 120 px grid. */
  .bg-dark   { background-color: var(--bg-dark);
               background-image: linear-gradient(var(--grid-dark) 2px, transparent 2px),
                                 linear-gradient(90deg, var(--grid-dark) 2px, transparent 2px);
               background-size: 120px 120px; }
  .bg-light  { background-color: var(--bg-light);
               background-image: linear-gradient(var(--grid-light) 2px, transparent 2px),
                                 linear-gradient(90deg, var(--grid-light) 2px, transparent 2px);
               background-size: 120px 120px; }
  .bg-accent { background-color: var(--bg-accent);
               background-image: linear-gradient(rgba(255,255,255,.12) 2px, transparent 2px),
                                 linear-gradient(90deg, rgba(255,255,255,.12) 2px, transparent 2px);
               background-size: 120px 120px; }

  /* Scene frames: split = top half, visual = whole frame. */
  .scene-split  { position: absolute; left: 0; top: 0; width: 1080px; height: 960px; z-index: 2; }
  .scene-visual { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; z-index: 2; }

  /* Card (rule 6.4). */
  .card { position: absolute; background: var(--card); border: 4px solid var(--ink); border-radius: 24px;
          box-shadow: 0 8px 0 var(--ink); padding: 32px; color: var(--ink); }
  .bg-dark .card { box-shadow: 0 8px 0 #000; }
  .card-dark { background: var(--card-dark); border-color: #3A414C; box-shadow: 0 8px 0 #000; color: var(--ink-on-dark);
               font-family: "Mono", monospace; }
  .card h3 { font: 600 44px "Ui", sans-serif; }
  .row { height: 112px; margin-top: 20px; border-radius: 16px; background: #EEF1F4;
         display: flex; align-items: center; gap: 24px; padding: 0 28px; font: 500 36px "Ui", sans-serif; }
  .mono { font: 500 44px "Mono", monospace; }
  .box { width: 64px; height: 64px; flex: none; border: 4px solid var(--ink); border-radius: 12px; }
  .box.on { background: var(--accent); }   /* tick state: switch the class layer with tl.set on a twin element */

  /* Captions (rules 7.1-7.5). Lines are explicit, never auto-wrapped. */
  .cap { position: absolute; left: 120px; width: 840px; z-index: 5; text-align: center;
         font: 700 60px "Display", sans-serif; letter-spacing: 0.04em; text-transform: uppercase; }
  .cap .line { height: 80px; line-height: 80px; white-space: nowrap; }
  .cap .w { opacity: 0; }
  .cap-split  { bottom: 976px; }   /* bottom edge at y 944 */
  .cap-visual { top: 1320px; }
  .cap-chest  { top: 1180px; }
  /* above the head: set style="bottom: <1920 - (hair_top - 24)>px" on the block */
  .on-light { color: var(--ink); }
  .on-dark  { color: var(--ink-on-dark); }
  .on-speaker .w { display: inline-block; background: rgba(255,255,255,.92); color: var(--ink);
                   border-radius: 14px; padding: 0 18px; margin: 0 6px; }
  .key { color: var(--accent); }
  .on-dark .key { color: var(--accent-on-dark); }
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="19" data-width="1080" data-height="1920">

  <!-- Speaker: one <video> per speaker plan. Split plans sit in an untimed cropping wrapper. -->
  <div class="speaker-half">
    <video id="spk-1" class="clip" src="speaker.mp4" muted playsinline
           data-start="0" data-duration="3" data-media-start="0" style="top:-550px"></video>
    <!-- next split plan: same top (one y0 per reel) -->
  </div>
  <video id="spk-2" class="clip speaker-full" src="speaker.mp4" muted playsinline
         data-start="3" data-duration="2.5" data-media-start="3"></video>

  <!-- Plan 1: split scene (visual half) -->
  <div id="sc-1" class="clip scene-split bg-dark" data-start="0" data-duration="3">
    <div class="card" style="left:120px; top:140px; width:840px; height:560px">
      <h3>ЗАГОЛОВОК КАРТОЧКИ</h3>
      <div class="row" id="sc1-r1">Первая строка</div>
      <div class="row" id="sc1-r2" style="opacity:0">Вторая строка</div>
    </div>
  </div>

  <!-- Caption groups: one block per group, never across a cut -->
  <div id="cap-1" class="clip cap cap-split on-dark" data-start="0" data-duration="1.1">
    <div class="line"><span class="w" id="w1">ПЕРВОЕ</span></div>
    <div class="line"><span class="w" id="w2">СЛОВО</span> <span class="w key" id="w3">ТРИ</span></div>
  </div>

  <audio id="speech" src="speech.wav" data-start="0" data-duration="19" data-volume="1"></audio>
</div>
<script>
  const tl = gsap.timeline({ paused: true });
  // Caption words: solid at t=0 for the hook, otherwise 55 % -> 100 % in 0.25 s at the word start.
  function word(id, t, hook) {
    if (hook) { tl.set(id, { opacity: 1 }, 0); return; }
    tl.set(id, { opacity: 0.55 }, t);
    tl.to(id, { opacity: 1, duration: 0.25, ease: "none" }, t);
  }
  word("#w1", 0, true);
  word("#w2", 0.62);
  word("#w3", 0.9);
  // Scene states (rule 2.5): a row slides in on its word.
  tl.fromTo("#sc1-r2", { opacity: 0, y: 60 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out" }, 0.5);
  window.__timelines = window.__timelines || {};
  window.__timelines["main"] = tl;
</script>
</body>
</html>
```

Высота блока субтитра растёт от опорной линии: у `.cap-split` якорь – низ (y 944),
вторая строка встаёт выше первой; у `.cap-visual` и `.cap-chest` якорь – верх. Блок «над
головой» – `style="bottom: …px"` с числом `1920 − (верх волос − 24)` из `head_guide.py`.

### 2.4. Движения (правило 9.2) в GSAP

```js
// Pop: 0.7 -> 1.06 -> 1.0 with fade, 0.22 s.
tl.fromTo("#badge", { opacity: 0, scale: 0.7 }, { opacity: 1, scale: 1.06, duration: 0.14, ease: "power2.out" }, t);
tl.to("#badge", { scale: 1, duration: 0.08 }, t + 0.14);

// Slide in from below by 120 px, 0.3 s.
tl.fromTo("#row3", { opacity: 0, y: 120 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out" }, t);

// Cascade: items 0.12 s apart.
["#i1", "#i2", "#i3"].forEach((id, k) => tl.fromTo(id, { opacity: 0, scale: 0.7 }, { opacity: 1, scale: 1, duration: 0.2 }, t + k * 0.12));

// Blur-in of one element (never on frame 0).
tl.fromTo("#panel", { opacity: 0, filter: "blur(12px)" }, { opacity: 1, filter: "blur(0px)", duration: 0.3 }, t);

// Typing: one <span> per character, switched on at 12 chars/s.
// HTML: <span class="field"><span class="ch">С</span><span class="ch">О</span>...</span>
document.querySelectorAll("#field .ch").forEach((ch, k) => tl.set(ch, { opacity: 1 }, t + k / 12));

// Counter: stacked values, one visible at a time (no callbacks).
// HTML: <div class="counter"><span class="v">0</span><span class="v">12</span>...<span class="v">48</span></div>
const vals = document.querySelectorAll("#counter .v");
vals.forEach((v, k) => { tl.set(v, { opacity: 1 }, t + k * 0.1); if (k < vals.length - 1) tl.set(v, { opacity: 0 }, t + (k + 1) * 0.1); });

// Progress bar: scaleX from the left edge (CSS: transform-origin: left center).
tl.fromTo("#bar", { scaleX: 0 }, { scaleX: 1, duration: 0.8, ease: "none" }, t);

// Pointer: travels 0.5 s, click ring 0.2 s, button pressed.
tl.to("#pointer", { x: 260, y: -40, duration: 0.5, ease: "power1.inOut" }, t);
tl.fromTo("#ring", { opacity: 0.9, scale: 0.4 }, { opacity: 0, scale: 1.4, duration: 0.2 }, t + 0.5);
tl.to("#send", { scale: 0.94, duration: 0.08 }, t + 0.5);
tl.to("#send", { scale: 1, duration: 0.08 }, t + 0.58);

// Scroll a long card in steps of 0.4 s (inner element moves, the card stays).
tl.to("#doc", { y: -180, duration: 0.4, ease: "power2.inOut" }, t);
tl.to("#doc", { y: -360, duration: 0.4, ease: "power2.inOut" }, t + 0.9);
```

Состояние-переключатель (тумблер, кнопка «Подписаться» → «Вы подписаны»): два слоя
друг над другом, `tl.set` гасит один и включает другой в момент слова.

### 2.5. Сцены: заготовки

Каждая заготовка – внутренность `.scene-split` (полоса y 140–750) или `.scene-visual`
(полоса y 130–1280). Размеры шрифтов не меньше правила 5.2.

```html
<!-- Terminal (split): 3-5 lines of 44 px mono, key line 56 px -->
<div class="card card-dark" style="left:120px; top:140px; width:840px; height:580px">
  <div style="font:500 32px 'Ui'; color:var(--muted-on-dark)">терминал</div>
  <div class="mono" id="t-l1" style="margin-top:28px">$ команда один</div>
  <div class="mono" id="t-l2" style="opacity:0">$ команда два</div>
  <div class="mono" id="t-l3" style="opacity:0; font-size:56px; color:var(--accent-on-dark)">готово</div>
</div>

<!-- Checklist (visual): main card + second element (progress) under it -->
<div class="card" style="left:60px; top:130px; width:960px; height:760px">
  <h3>ПОСЛЕ ВСТРЕЧИ</h3>
  <div class="row" id="c-1"><span class="box" id="c-1-box"></span>Первый пункт</div>
  <div class="row" id="c-2" style="opacity:0"><span class="box" id="c-2-box"></span>Второй пункт</div>
  <div class="row" id="c-3" style="opacity:0"><span class="box" id="c-3-box"></span>Третий пункт</div>
</div>
<div class="card" style="left:60px; top:950px; width:960px; height:300px">
  <div style="font:600 40px 'Ui'">готово <span id="done-n">0</span> из 3</div>
  <div style="margin-top:40px; height:56px; border-radius:28px; background:#EEF1F4; overflow:hidden">
    <div id="bar" style="height:100%; width:100%; background:var(--accent); transform-origin:left center; transform:scaleX(0)"></div>
  </div>
</div>

<!-- Comment field with the code word (split, call to action, rule 10.2) -->
<div class="card" style="left:120px; top:140px; width:840px; height:560px">
  <h3>Комментарий</h3>
  <div style="display:flex; align-items:center; gap:24px; margin-top:36px">
    <div style="width:72px; height:72px; border-radius:50%; background:var(--accent)"></div>
    <div id="field" style="flex:1; height:150px; border:4px solid var(--ink); border-radius:16px; display:flex;
         align-items:center; padding:0 24px; font:700 110px 'Display'; letter-spacing:.04em; color:var(--accent)">
      <span class="ch" style="opacity:0">С</span><span class="ch" style="opacity:0">Л</span><span class="ch" style="opacity:0">О</span>
      <span class="ch" style="opacity:0">В</span><span class="ch" style="opacity:0">О</span>
    </div>
  </div>
  <div id="send" style="margin:40px 0 0 auto; width:340px; height:96px; border-radius:48px; background:var(--accent);
       color:#fff; font:600 40px 'Ui'; display:flex; align-items:center; justify-content:center">Отправить</div>
</div>
```

### 2.6. Рендер и проверка файла

```bash
hyperframes lint build                               # 0 errors
hyperframes render build -o reel.mp4 -q delivery     # mp4 1080x1920, 30 fps, with the audio track
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate -of compact reel.mp4
python3 $S/verify.py reel.mp4                        # duration, size, fps, loudness, first frame
```

Длина `reel.mp4` равна длине `reel-cut.mp4` (±1 кадр), звук – тот же (рендер его не
меняет). Рендер долгий (минуты): запускай его на переднем плане, не отрывая от терминала.

## 3. Путь Б: склейка ffmpeg

Сцены отрисованы отдельными файлами: `scene-01.mp4` (1080 × 960, для СПЛИТА),
`scene-02.mp4` (1080 × 1920, для ВИЗУАЛА) и т. д., каждая длиной ровно в свой план.
Каждый план собирается отдельным файлом, затем планы склеиваются.

Сложные графы ffmpeg кладутся в файл и передаются через `-filter_complex_script`
(квадратные скобки и кавычки не проходят через оболочку и через проверки агента).

Планы собираются **без звука**, а звук в конце берётся из `reel-cut.mp4` целиком: так
он остаётся тем, что проверил этап 1, и на стыках не набегают лишние доли секунды (у
каждого отдельно закодированного куска AAC длиннее на ~0,02 с).

### 3.1. План СПЛИТ

```bash
# plan 1: 0.0-3.0 s of reel-cut.mp4, y0 = 550
cat > split-01.graph <<'EOF'
[0:v]trim=start=0:end=3,setpts=PTS-STARTPTS,crop=1080:960:0:550,setsar=1[spk];
[1:v]scale=1080:960,setsar=1,fps=30[vis];
[vis][spk]vstack=inputs=2,format=yuv420p[v]
EOF
ffmpeg -y -i reel-cut.mp4 -i scene-01.mp4 -filter_complex_script split-01.graph \
  -map "[v]" -an -r 30 -c:v libx264 -crf 18 plan-01.mp4
```

### 3.2. План СПИКЕР и план ВИЗУАЛ

```bash
# speaker 3.0-5.5 s: the whole frame, no zoom
ffmpeg -y -ss 3 -t 2.5 -i reel-cut.mp4 -an -r 30 -c:v libx264 -crf 18 plan-02.mp4

# visual: the scene clip as it is (1080x1920, exactly the plan length)
ffmpeg -y -i scene-03.mp4 -vf "scale=1080:1920,setsar=1,fps=30,format=yuv420p" -an -c:v libx264 -crf 18 plan-03.mp4
```

### 3.3. Склейка планов и звук

```bash
printf "file '%s'\n" plan-01.mp4 plan-02.mp4 plan-03.mp4 > plans.txt
ffmpeg -y -f concat -safe 0 -i plans.txt -c copy video-only.mp4
ffmpeg -y -i video-only.mp4 -i reel-cut.mp4 -map 0:v -map 1:a -c:v copy -c:a copy -shortest reel-nocap.mp4
```

Все планы – одного размера, частоты и кодека; сумма длин планов равна длине
`reel-cut.mp4` (сверь `ffprobe`, допуск – один кадр).

### 3.4. Субтитры поверх

Субтитры этапа 2 – прописные, накопительные, у каждого плана своё место (раздел 7
регламента), поэтому `captions.py` этапа 1 здесь не подходит. Два способа:

- Отрисовать субтитры той же композицией HyperFrames с прозрачным фоном
  (`hyperframes render build-captions --format mov` – ProRes 4444 с альфой) и наложить:

```bash
cat > caps.graph <<'EOF'
[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]
EOF
ffmpeg -y -i reel-nocap.mp4 -i captions.mov -filter_complex_script caps.graph \
  -map "[v]" -map 0:a -c:v libx264 -crf 18 -c:a copy reel.mp4
```

- Или нарисовать каждое состояние субтитра в PNG 1080 × 1920 (Pillow) и наложить списком
  concat, как это делает `captions_png.py` этапа 1 (одна картинка на состояние, пустая
  картинка в паузах).

## 4. Проверка раскладки до рендера целиком

Перед полным рендером – кадры в ключевые моменты, чтобы не ждать минуты ради одной
ошибки геометрии:

```bash
hyperframes render build -o probe.mp4 -q draft --start 0 --end 6
python3 $S/frames_sheet.py probe.mp4 --out probe-accept --tail 1
```

Посмотри лист: шов, место субтитра, лицо, поля. Потом полный рендер и приёмка
(`montage-acceptance.md`).
