# Форматы файлов и параметры по умолчанию

Все файлы – JSON в UTF-8, время в секундах.

## words.json (`transcribe.py`)

```json
{
  "version": 1,
  "source": "/путь/к/take.mp4",
  "language": "russian",
  "duration": 13.0,
  "words": [{"text": "сегодня", "start": 0.8, "end": 1.15}]
}
```

`source` – видео, из которого сделан транскрипт: `plan_cut.py` берёт из него звук для проверки
тишины. Ключ Groq в файлы не пишется.

## phrases.json (`phrases.py`)

Список `phrases`: `id` (с 1), `start`/`end` (с воздухом), `core_start`/`core_end` (речь), `pause_before`,
`text`, `words`. Читаемая таблица – `phrases.md`.

## cut-plan.json (`plan_cut.py`)

| Поле | Смысл |
|---|---|
| `drop` | подтверждённые номера фраз (пусто на первом проходе) |
| `removals` | что вырезано: `start`, `end`, `kind` (`head`, `pause`, `drop`, `tail`), `phrases`, `note` |
| `keep` | оставленные куски исходника `[start, end]`; именно их рендерит `render_cut.py` |
| `candidates` | помеченные, но НЕ вырезанные: `phrase`, `kind` (`repeat`, `false_start`), `similar_to`, `similarity`, тексты |
| `speed` | ускорение после склейки |
| `notes` | паузы, которые оставлены, потому что звук там не тихий |
| `stats` | `kept_seconds`, `removed_seconds`, `output_seconds` |

`render_cut.py` читает из плана только `keep` и `speed`. Если нужно вырезать по времени то, чего нет в
таблице фраз, отредактируйте `keep` (куски по порядку, без пересечений, не короче 0,05 с) и перерендерите.
Слова вырезанных кусков в `words-cut.json` удаляются сами.

## words-cut.json и cut-map.json (`render_cut.py`)

`words-cut.json` – тот же формат, что `words.json`, но время на часах готового видео; слова
вырезанных кусков удалены. `cut-map.json` – `segments` (откуда и куда попал каждый кусок),
`seams` (время стыков на готовом видео), `expected_duration`.

## Параметры по умолчанию

| Параметр | Значение | Где |
|---|---|---|
| пауза, разделяющая фразы | 0,32 с | `phrases.py --min-pause` |
| самая короткая фраза | 0,6 с | `phrases.py --min-phrase` |
| после предлога/союза/частицы разрыв только от | 1,5 с | `phrases.py --hard-pause` |
| пауза, которую сжимаем | от 0,4 с | `plan_cut.py --min-pause` |
| зазор после сжатия | 0,25 с | `plan_cut.py --target-gap` |
| не-речь перед первым / после последнего звука речи | 0,06 с / 0,06 с (с сеткой кадров не больше 0,10 с) | `--head-air`, `--tail-air` |
| речь от первого кадра и до последнего | не дальше 0,10 с | `verify.py --max-lead`, `--max-trail` |
| повтор: сходство слов | от 0,75, ищем в 4 следующих фразах | `plan_cut.py` |
| фейд на стыке | 8 мс | `render_cut.py --fade-ms` |
| цель громкости | -14 LUFS, пик -1 dBTP | `render_cut.py` |
| формат | 1080x1920, 30 fps, H.264 + AAC 48 кГц, faststart | `render_cut.py` |
| лимит длительности при проверке | 90 с | `verify.py --max-duration` |
| допуск громкости при проверке | ±1,5 LU | `verify.py --lufs-tolerance` |
| самая длинная тишина при проверке | 0,8 с | `verify.py --max-silence` |
| субтитры | 78 px, 2–4 слова, центр блока на 0,78 высоты (`--y`), обводка 9 px и тень, поля 120 px | `captions.py`; способ `--backend auto\|ass\|png` |
