# Скелеты раскладок

Готовые раскладки, чтобы геометрию не придумывать заново. Все числа – из
`montage-reglament.md` (кадр 1080 × 1920, 30 к/с). Тексты в примерах условные (общий
инструмент командной строки, файлы `README.md`, `config.yaml`): в ролике на экране только
слова речи студента и нейтральный хром интерфейса – кнопки окна, знак приглашения, курсор,
значки, служебные надписи; команды и имена файлов – только сказанные (единое правило 4.2).

Каждая сцена в скелетах уже детализирована по разделу 13 регламента: фон с фактурой,
карточка с рамкой и жёсткой тенью, хром своего типа, строки «значок + жирное название +
состояние справа», акцент на кромке, микродвижение при удержании, пункты по одному на
своих словах. Пустых заготовок – мест под будущие пункты, серых полос, `$` без команды,
пустых полей – в скелетах нет и добавлять их нельзя (правило 4.9).

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
| Полоса карточек СПЛИТА | x 120–960, y 140–750 вместе с тенью (карточка от y 140 высотой 602 px + тень 8 px) |
| Колонка кнопок в ВИЗУАЛЕ | в y 1080–1280 содержимое не правее x 940 |
| Субтитр в СПЛИТЕ | низ блока y 944, строка 80 px (1 строка: y 864–944; 2 строки: y 784–944) |
| Макет ВИЗУАЛА | x 60–1020, y 140–1280 |
| Субтитр в ВИЗУАЛЕ | верх блока y 1320, строка 80 px, низ ≤ 1480 |
| «Два визуала» | верх y 140–820, разделитель y 836–840, низ y 860–1280 |
| Субтитр СПИКЕРА «грудь» | верх блока y 1180, низ ≤ 1352 (строки плашек с шагом 92 px) |
| Субтитр СПИКЕРА «над головой» | низ блока = верх волос − 24 (самый высокий верх волос за план); 2 строки плашек – 172 px |
| Срез спикера в СПЛИТЕ | строки исходника `[y0, y0 + 960]`, `y0` и масштаб по правилу 1.3 (`head_guide.py --top … --chin …`); голова целиком: верх волос y ≥ 1000, рот и подбородок y ≤ 1520; проверка среза – `head_guide.py --top … --chin … --y0 … [--scale …]` (код 1 – FAIL) |
| Спикер уменьшен (крупный план) | масштаб `s` 0,55–1,0: видео `1080·s × 1920·s`, по центру (`left = 540·(1 − s)`), `top = −y0`; боковые поля – копия того же кадра, размытие ≥ 30 px, яркость −30…−50 % |
| Заголовок-хук на кадре 0,00 | ≥ 72 px, 2–6 слов, строка над карточкой y 140–330 (1–2 строки); виден и непрозрачен с кадра 0 (правило 3.7); карточка под ним – не ближе 20 px к низу заголовка (одна строка – с y 260, две – с y 350) |
| Не трогать | y 0–140 (верхняя служебная зона, одна граница везде), y 1520–1920, x 950–1080 при y 1080–1720 |

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
- Двигать только `x`, `y`, `scale`, `rotation`, `opacity` (не `left` / `top`). У элемента,
  который двигает GSAP, в CSS нет `transform` (линтер: `gsap_css_transform_conflict`):
  начальное состояние ставится `tl.set(el, { scaleX: 0 }, 0)`.
- `fromTo` ставит своё начальное состояние сразу, с начала шкалы. Если начальное
  состояние видимое (кольцо клика с непрозрачностью 0,9), элемент будет на экране с
  первого кадра плана. Для таких – `tl.set(el, {...}, t)` и затем `tl.to(el, {...}, t)`.
- Смена состояния панели (статус → результат) – в один кадр: старое гасится и новое
  включается в одно и то же время, двигается только положение нового; иначе на стыке
  состояний будет пустой кадр или текст поверх текста.
- Шрифты: файлы в `fonts/` через `@font-face`; без файлов – обычные стеки системных
  шрифтов (`font-family: "Menlo", "Courier New", monospace`). `src: local(...)` в
  `@font-face` ненадёжен: моноширинный шрифт так «упал» в шрифт с засечками.
- Колбэков GSAP нет (при перемотке они не срабатывают): набор текста – буквы-`span`,
  которые включаются `tl.set`; счётчик – стопка значений, которые переключаются `tl.set`.
- Проверка перед рендером: `hyperframes lint build` – 0 ошибок. Предупреждения
  `nested_structure_needs_subcomposition` (на каждую сцену и группу субтитра с вложенными
  `div`) и `composition_file_too_large` рендер не ломают; дробить композицию на
  подкомпозиции ради них не нужно.
- Кнопка, по которой кликает курсор, на 60 px шире своей надписи с каждой стороны: клик и
  кольцо – в этом поле, не на буквах (правило 9.2). Курсор стоит до клика под кнопкой или
  сбоку и подъезжает коротким путём, не пересекая другие надписи.
- Надпись в пилюле или кнопке не касается рамки: ширина пилюли – ширина текста плюс не
  меньше 24 px с каждой стороны; не помещается – шрифт меньше (не ниже 32 px) или короче
  текст.

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
    --ink-on-dark: #FFFFFF; --muted-on-dark: #9AA3B2; --muted-on-accent: #E3F4F7; /* rule 6.5: 4.9:1 on the accent */
    --accent: #0B7285; --accent-on-dark: #66D9E8; --signal: #E8590C; --signal-text: #C2410C;
    --mark: #D3EEF2;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1080px; height: 1920px; overflow: hidden; background: var(--bg-dark); }
  #root { position: relative; width: 1080px; height: 1920px; font-family: "Ui", sans-serif; }

  /* Speaker half of a split: untimed wrapper that crops the take (rule 1.3: the whole head, hair >= y 1000, mouth and chin <= y 1520). */
  .speaker-half { position: absolute; left: 0; top: 960px; width: 1080px; height: 960px; overflow: hidden; z-index: 1; }
  .speaker-half video { position: absolute; left: 0; width: 1080px; height: 1920px; } /* top: -y0 */
  /* Close-up scaled to s (0.55-1.0, rule 1.3; below 0.85 tell the student the take is too close): the speaker is
     1080*s x 1920*s, centred; the side gaps show a blurred, darkened copy of the same frame (never a flat bar). */
  .speaker-half video.spk-fill { filter: blur(30px) brightness(0.6); }
  .speaker-full { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; z-index: 1; }

  /* Scene backgrounds with texture (rule 13.1): never one flat colour. */
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

  /* Hook title (rule 3.7): readable on frame 0.00, >= 72 px, no fade/pop/typing from 0. */
  .hook-title { position: absolute; left: 120px; top: 150px; width: 840px; text-align: center;
                font: 700 72px/90px "Display", sans-serif; letter-spacing: .04em; text-transform: uppercase;
                color: var(--ink-on-dark); }

  /* Card (rules 6.4, 13.5): 4 px border, hard 8 px shadow, radius 24. */
  .card { position: absolute; background: var(--card); border: 4px solid var(--ink); border-radius: 24px;
          box-shadow: 0 8px 0 var(--ink); padding: 0 32px 32px; color: var(--ink); }
  .bg-dark .card { box-shadow: 0 8px 0 #000; }
  .card-dark { background: var(--card-dark); border-color: #3A414C; box-shadow: 0 8px 0 #000; color: var(--ink-on-dark); }
  /* Card header 70-90 px: icon + title, divider (rule 13.2); 48 px = 1.5 x the 32 px body (rule 13.4). */
  .head { height: 84px; display: flex; align-items: center; gap: 18px; border-bottom: 3px solid #E3E7EC;
          font: 700 48px "Ui", sans-serif; }   /* two weights per card: 400 and 700 */
  .card-dark .head { border-color: #262C35; }
  .ico { width: 52px; height: 52px; flex: none; border-radius: 14px; background: var(--mark);
         display: flex; align-items: center; justify-content: center; }
  .ico svg { width: 30px; height: 30px; fill: none; stroke: var(--ink); stroke-width: 2.5; }
  /* Row = icon + bold name + state on the right (rule 13.2). */
  .row { position: absolute; left: 32px; right: 32px; height: 100px; border-radius: 16px; background: #EEF1F4;
         display: flex; align-items: center; gap: 20px; padding: 0 24px; font: 400 32px "Ui", sans-serif; }
  .row b { font-weight: 700; }
  .row .state { margin-left: auto; }
  .mono { font: 500 40px "Mono", monospace; }
  .box { width: 64px; height: 64px; flex: none; border: 4px solid var(--ink); border-radius: 12px; }
  .box.on { background: var(--accent); }   /* tick state: switch the class layer with tl.set on a twin element */
  /* Accent pill; .edge puts it over the TOP edge of the card, inside its width (rule 13.5). Rotation (up to 6 deg)
     is set with tl.set, not in CSS. With the 30 px overhang and the tilt a card that carries an .edge badge
     starts at y >= 200, so the badge stays below y 140 and inside x 120-960 (rule 1.7). */
  .pill { height: 60px; padding: 0 26px; border-radius: 30px; background: var(--accent); color: #fff;
          display: inline-flex; align-items: center; font: 700 32px "Ui", sans-serif; letter-spacing: .06em;
          text-transform: uppercase; white-space: nowrap; }
  .edge { position: absolute; top: -30px; right: 24px; z-index: 3; border: 4px solid var(--ink); }
  .caret { display: inline-block; width: 6px; height: 1em; margin-left: 6px; background: currentColor; vertical-align: -0.1em; }

  /* Captions (rules 7.1-7.5, 7.8). Lines are explicit, never auto-wrapped, centred on x 540. */
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
  .on-speaker .line { height: 92px; }   /* 80 px plate + 12 px: plates of two lines do not touch */
  .key { color: var(--accent); }
  .on-dark .key { color: var(--accent-on-dark); }
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="19" data-width="1080" data-height="1920">

  <!-- Speaker: one <video> per speaker plan. Split plans sit in an untimed cropping wrapper; one y0 and one scale per reel. -->
  <div class="speaker-half">
    <video id="spk-1" class="clip" src="speaker.mp4" muted playsinline
           data-start="0" data-duration="3" data-media-start="0" style="top:-550px"></video>
    <!-- Close-up at s = 0.9 (head_guide.py prints s and y0): blurred fill first, then the scaled speaker.
    <video id="spk-1-fill" class="clip spk-fill" src="speaker.mp4" muted playsinline
           data-start="0" data-duration="3" data-media-start="0" style="top:-560px"></video>
    <video id="spk-1" class="clip" src="speaker.mp4" muted playsinline data-start="0" data-duration="3"
           data-media-start="0" style="left:54px; width:972px; height:1728px; top:-530px"></video> -->
  </div>
  <video id="spk-2" class="clip speaker-full" src="speaker.mp4" muted playsinline
         data-start="3" data-duration="2.5" data-media-start="3"></video>

  <!-- Plan 1: split scene with the hook title. The title says THREE files, so frame 0.00 shows exactly three rows
       (rule 14.9): the number is on screen from the first frame; the hook motion is state changes, not new rows. -->
  <div id="sc-1" class="clip scene-split bg-dark" data-start="0" data-duration="3">
    <div class="hook-title">ТРИ ФАЙЛА</div>   <!-- one line: y 150-240; two lines end at y 330, then the card starts at y 350 -->
    <div class="card" id="sc1-card" style="left:120px; top:270px; width:840px; height:472px">
      <div class="head">
        <span class="ico"><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg></span>ПРОЕКТ
      </div>
      <div class="row" id="sc1-row1" style="top:108px">
        <span class="ico"><svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/></svg></span><b>README.md</b>
        <span class="state pill" id="sc1-st1">ГОТОВ</span>
      </div>
      <div class="row" id="sc1-row2" style="top:224px">
        <span class="ico"><svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/></svg></span><b>config.yaml</b>
        <span class="state" style="color:var(--muted)">правка<span class="caret" id="sc1-caret"></span></span>
      </div>
      <div class="row" id="sc1-row3" style="top:340px">
        <span class="ico"><svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/></svg></span><b>notes.md</b>
        <span class="state pill" id="sc1-st3">НОВЫЙ</span>
      </div>
      <div class="pill edge" id="sc1-badge">3 ФАЙЛА</div>
    </div>
  </div>

  <!-- Caption groups: one block per group, never across a cut; no line made of one short word (rule 7.8) -->
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
  // Hook title: no tween before frame 0 - it is simply there. An accent may move it only later.
  // Scene states (rule 2.5): all three rows are there on frame 0 (the title says three); the third row's state
  // pops on its word, the badge lands on the card edge.
  tl.set("#sc1-st3", { opacity: 0, scale: 0.7 }, 0);
  tl.to("#sc1-st3", { opacity: 1, scale: 1, duration: 0.2, ease: "back.out(2)" }, 0.5);
  tl.set("#sc1-badge", { opacity: 0, scale: 0.7, rotation: -4 }, 0);
  tl.to("#sc1-badge", { opacity: 1, scale: 1, duration: 0.2, ease: "back.out(2)" }, 1.2);
  // Micro-motion while held (rule 13.6): caret blinks every 0.5 s, the status pill pulses.
  tl.to("#sc1-caret", { opacity: 0, duration: 0.01, repeat: 5, yoyo: true, repeatDelay: 0.5 }, 0);
  tl.to("#sc1-st1", { scale: 1.05, duration: 0.5, repeat: 3, yoyo: true, ease: "sine.inOut" }, 1.0);
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

// Progress bar: scaleX from the left edge (CSS: transform-origin: left center, no CSS transform).
tl.set("#bar", { scaleX: 0 }, 0);
tl.to("#bar", { scaleX: 1, duration: 0.8, ease: "none" }, t);

// Pointer: travels 0.5 s, click ring 0.2 s, button pressed.
tl.to("#pointer", { x: 260, y: -40, duration: 0.5, ease: "power1.inOut" }, t);
tl.set("#ring", { opacity: 0.9, scale: 0.4 }, t + 0.5);   // not fromTo: it would show the ring from frame 0
tl.to("#ring", { opacity: 0, scale: 1.4, duration: 0.2 }, t + 0.5);
tl.to("#send", { scale: 0.94, duration: 0.08 }, t + 0.5);
tl.to("#send", { scale: 1, duration: 0.08 }, t + 0.58);

// Scroll a long card in steps of 0.4 s (inner element moves, the card stays).
tl.to("#doc", { y: -180, duration: 0.4, ease: "power2.inOut" }, t);
tl.to("#doc", { y: -360, duration: 0.4, ease: "power2.inOut" }, t + 0.9);

// Micro-motion while a card is held (rule 13.6): something moves at least every 1.5 s.
// Caret at the end of a real line: blinks every 0.5 s for `hold` seconds.
tl.to("#caret", { opacity: 0, duration: 0.01, repeat: Math.floor(hold / 0.5), yoyo: true, repeatDelay: 0.5 }, t);
// Status pill pulses; spinner turns; push-in +3 %/s only for a card that has room in its band.
tl.to("#status", { scale: 1.05, duration: 0.5, repeat: Math.floor(hold / 0.5) - 1, yoyo: true, ease: "sine.inOut" }, t);
tl.to("#spinner", { rotation: 360 * hold, duration: hold, ease: "none" }, t);
tl.to("#small-card", { scale: 1 + 0.03 * hold, duration: hold, ease: "none" }, t);
// State story "waiting -> working -> done" in 0.5 s steps: stacked labels switched with tl.set.
["#st-wait", "#st-work", "#st-done"].forEach((id, k, all) => {
  tl.set(id, { opacity: 1 }, t + k * 0.5);
  if (k < all.length - 1) tl.set(id, { opacity: 0 }, t + (k + 1) * 0.5);
});

// List rows one by one with no pre-drawn slots (rule 4.9): with k of N rows on screen the block stays centred,
// older rows move apart when the next one arrives. Rows are absolutely positioned at their final places.
const STEP = 186, N = 3, rows = ["#c-1", "#c-2", "#c-3"], at = [t, t + 1.1, t + 2.0];
rows.forEach((id, k) => {
  const y = (N - k - 1) * STEP / 2;
  if (k === 0) { tl.set(id, { opacity: 1, y }, 0); return; }          // the first row is there on the cut
  tl.set(id, { opacity: 0, y: y + 60 }, 0);
  tl.to(id, { opacity: 1, y, duration: 0.22, ease: "back.out(1.6)" }, at[k]);
  rows.slice(0, k).forEach(prev => tl.to(prev, { y, duration: 0.25, ease: "power2.out" }, at[k]));
});
```

Состояние-переключатель (тумблер, кнопка «Подписаться» → «Вы подписаны»): два слоя
друг над другом, `tl.set` гасит один и включает другой в момент слова.

### 2.5. Сцены: заготовки

Каждая заготовка – внутренность `.scene-split` (полоса y 140–750) или `.scene-visual`
(полоса y 140–1280), классы – из скелета 2.3. Размеры смыслового текста не меньше правила
5.2; хром и фактура – правило 13.2, 13.4. В каждой: фон с фактурой (класс сцены), карточка с
рамкой и тенью, хром своего типа, акцент на кромке (`.edge`), микродвижение при удержании
(раздел 2.4), будущие строки до своего слова скрыты, а не нарисованы пустыми.

```html
<!-- Terminal (split, rule 13.2): mono caps label + divider, lines "marker + verb + path", key command,
     texture output 18 px, status line at the bottom, final status pill on the edge.
     On the cut two real commands are already there (rule 4.3); nothing stands for a line before it is typed. -->
<div class="card card-dark" style="left:120px; top:200px; width:840px; height:542px; font-family:'Mono', monospace">
  <div style="height:64px; display:flex; align-items:center; border-bottom:2px solid #262C35;
       font:500 28px 'Mono'; letter-spacing:.06em; color:var(--muted-on-dark)">TERMINAL · ~/project</div>
  <div class="mono" style="margin-top:20px"><span style="color:var(--accent-on-dark)">›</span> read <b>README.md</b></div>
  <div style="font:400 18px/28px 'Mono'; opacity:.45">  # project · setup · usage</div>   <!-- texture: not read -->
  <div class="mono" style="margin-top:12px"><span style="color:var(--accent-on-dark)">›</span> edit <b>config.yaml</b></div>
  <div class="mono" id="t-key" style="margin-top:12px; font-size:48px"><span style="color:var(--accent-on-dark)">$</span> tool run --check</div>
  <div id="t-out" style="font:400 18px/28px 'Mono'; opacity:.45">  ok  README.md<br>  ok  config.yaml</div>
  <div style="position:absolute; left:32px; right:32px; bottom:24px; height:56px; display:flex; align-items:center; gap:16px;
       border-top:2px solid #262C35; font:500 32px 'Ui'; color:var(--muted-on-dark)">
    <svg id="t-spin" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="#66D9E8" stroke-width="3"><path d="M12 3a9 9 0 1 0 9 9"/></svg>
    <span>работает</span><span id="t-time" style="margin-left:auto; font-family:'Mono'">0:04</span>
  </div>
  <div class="pill edge" id="t-done">ГОТОВО</div>
</div>

<!-- Checklist (visual): header with icon, rows "icon + bold name + state", progress panel with a number under it.
     A VISUAL card starts at x 60, so rows start at 56 px inside: text from x 120 (rule 1.7).
     Rows are placed at their final tops; the GSAP block "rows one by one" (2.4) keeps them centred while fewer
     than three are on screen - no empty slots for the future rows (rule 4.9). -->
<div class="card" style="left:60px; top:140px; width:960px; height:750px; padding:0 56px 32px">
  <div class="head"><span class="ico"><svg viewBox="0 0 24 24"><path d="M5 12l4 4 10-10"/></svg></span>ПОСЛЕ ВСТРЕЧИ</div>
  <div class="row" id="c-1" style="top:140px; left:56px; right:56px; height:160px">
    <span class="ico"><svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/></svg></span><b>README.md</b>
    <span class="state box" id="c-1-box"></span>
  </div>
  <div class="row" id="c-2" style="top:326px; left:56px; right:56px; height:160px">
    <span class="ico"><svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/></svg></span><b>config.yaml</b>
    <span class="state box" id="c-2-box"></span>
  </div>
  <div class="row" id="c-3" style="top:512px; left:56px; right:56px; height:160px">
    <span class="ico"><svg viewBox="0 0 24 24"><path d="M8 5l10 7-10 7z"/></svg></span><b>tool run</b>
    <span class="state box" id="c-3-box"></span>
  </div>
</div>
<div class="card" style="left:60px; top:950px; width:880px; height:300px; padding:32px 60px">
  <div style="font:700 48px 'Ui'">готово <span id="done-n">0</span> из 3</div>
  <div style="margin-top:40px; height:56px; border-radius:28px; background:#EEF1F4; overflow:hidden">
    <div id="bar" style="height:100%; width:100%; background:var(--accent); transform-origin:left center"></div>
  </div>
  <div class="pill edge" id="steps">3 ШАГА</div>
</div>

<!-- Comment field with the code word (split, call to action, rules 10.2, 10.7).
     Before the code word: preview of this reel (hook title + like/comment icons), avatar, button.
     The field appears together with its first letter (pop 0.2 s): no empty field, no hint text (rule 4.9).
     Geometry inside the split band y 140-750 (measured in a browser): card y 200-742 (+8 px shadow = 750);
     header 204-288; preview row 312-368; field 396-546; button 582-678. The edge badge is short ("КОД"), so it
     sits right of the header title and does not touch it (rule 14.4). -->
<div class="card" style="left:120px; top:200px; width:840px; height:542px">
  <div class="head"><span class="ico"><svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg></span>Комментарий</div>
  <div style="display:flex; align-items:center; gap:20px; margin-top:24px; font:400 32px 'Ui'">
    <b>ТРИ ФАЙЛА</b>
    <svg viewBox="0 0 24 24" width="56" height="56" fill="none" stroke="#111" stroke-width="2.5" style="margin-left:auto"><path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.5-7 10-7 10z"/></svg>
    <svg id="ico-comment" viewBox="0 0 24 24" width="56" height="56" fill="none" stroke="#111" stroke-width="2.5"><path d="M4 5h16v11H9l-5 4z"/></svg>
  </div>
  <div style="display:flex; align-items:center; gap:24px; margin-top:28px">
    <div style="width:72px; height:72px; border-radius:50%; background:var(--accent)"></div>
    <div id="field" style="flex:1; height:150px; border:4px solid var(--ink); border-radius:16px; display:flex; opacity:0;
         align-items:center; padding:0 24px; font:700 110px 'Display'; letter-spacing:.04em; color:var(--accent); background:#FFFFFF">
      <span class="ch" style="opacity:0">С</span><span class="ch" style="opacity:0">Л</span><span class="ch" style="opacity:0">О</span>
      <span class="ch" style="opacity:0">В</span><span class="ch" style="opacity:0">О</span><span class="caret" id="field-caret"></span>
    </div>
  </div>
  <div id="send" style="margin:36px 0 0 auto; width:340px; height:96px; border-radius:48px; background:var(--accent);
       color:#fff; font:700 40px 'Ui'; display:flex; align-items:center; justify-content:center">Отправить</div>
  <div class="pill edge" id="cta-pill">КОД</div>
</div>
```

```js
// Terminal: the key line and its output arrive on their words, the spinner turns, "ГОТОВО" lands on the edge.
// The timer ticks like the counter in 2.4 (stacked values switched with tl.set).
tl.set(["#t-key", "#t-out", "#t-done"], { opacity: 0 }, 0);
tl.set("#t-key", { opacity: 1 }, t1);                       // then type it: one <span> per char at 40-70 chars/s
tl.to("#t-out", { opacity: .45, duration: 0.2 }, t1 + 0.6);
tl.to("#t-spin", { rotation: 720, duration: 2, ease: "none" }, t0);
tl.set("#t-done", { opacity: 0, scale: 0.7, rotation: -4 }, 0);
tl.to("#t-done", { opacity: 1, scale: 1, duration: 0.2, ease: "back.out(2)" }, t2);

// Comment: the comment icon lights up on the word "comments"; the field pops in with the first letter.
tl.to("#ico-comment", { scale: 1.15, duration: 0.15, yoyo: true, repeat: 1 }, tWord);
tl.set("#field", { opacity: 1, scale: 0.9 }, tCode);
tl.to("#field", { scale: 1, duration: 0.2, ease: "back.out(2)" }, tCode);
document.querySelectorAll("#field .ch").forEach((ch, k) => tl.set(ch, { opacity: 1 }, tCode + k / 12));
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

Крупный план, который `head_guide.py` уменьшил (правило 1.3, шаг 5): голова не режется,
спикер в масштабе `s` стоит по центру, боковые поля – тот же кадр, размытый и затемнённый.
Перед сборкой проверь срез: `head_guide.py --top … --chin … --y0 530 --scale 0.9` – без FAIL.

```bash
# plan 1, close-up at s = 0.9 (972 x 1728, centred at x 54), y0 = 530 in the scaled rows
cat > split-01.graph <<'EOF'
[0:v]trim=start=0:end=3,setpts=PTS-STARTPTS,split=2[a][b];
[a]crop=1080:960:0:589,gblur=sigma=30,colorchannelmixer=rr=0.6:gg=0.6:bb=0.6[fill];
[b]scale=972:1728,crop=972:960:0:530[head];
[fill][head]overlay=54:0,setsar=1[spk];
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
  -map "[v]" -map 0:a -c:v libx264 -crf 18 -c:a copy -movflags +faststart reel.mp4
```

Без `-movflags +faststart` ffmpeg пишет `moov` в конец файла, и `verify.py` такой файл отклонит.
Ключ нужен в той команде, что пишет итоговый `reel.mp4`.

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

Посмотри лист: шов, место субтитра, лицо, поля; кадр 0,00 – заголовок-хук уже читается,
вставка рядом заполнена (правило 3.7); голова целиком – верх волос не выше y 1000, рот и
подбородок не ниже y 1520 (правило 1.3); ни одной заготовки (правило 4.9); у каждой вставки слои, хром и
движение при удержании (раздел 13). Потом полный рендер и приёмка (`montage-acceptance.md`).

Дешевле пробного рендера – снимки отдельных моментов без кодирования видео:

```bash
hyperframes snapshot build --at 0,2.9,6.6,9.1,37.35 --no-end -o probe-shots
```

Кадры в любых точках шкалы (стыки, клики, смены состояний) и общий лист `contact-sheet.jpg`.
Видео спикера на снимках тоже есть. Удобно до первого рендера пройти по одному моменту на
каждую сцену и по каждому клику курсора: так ловятся значки на тексте, курсор на надписи,
пустые карточки и заготовки.
