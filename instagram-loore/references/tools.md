# Инструменты Loore для Instagram

Только то, что нужно этому скиллу. Полный список (там же Reddit, TikTok, YouTube, X, Threads,
Telegram, рекламные библиотеки) – https://docs.loore.ai/mcp/tools

Цены в кредитах взяты из описаний инструментов в контракте API на 07.11.2026 и могут
меняться – перед крупной цепочкой проверяй бесплатным `estimate_credit_cost`.

## Аккаунт

| Инструмент | REST | Когда | Ключевые аргументы |
|---|---|---|---|
| `ig_profile` | `/v1/ig-profile` | быстрый профиль: id, подписчики, био, категория, приватность | `username` или `user_id` |
| `ig_analytics` | `/v1/ig-analytics` | полная аналитика: ER, микс контента, окна публикаций, хэштеги, `metrics.topRankedReels`, топ каруселей | `handle`; `depth`: `quick` (24 рилса, 4 кр.) или `full` (до 100 рилсов, 13 кр.) |
| `ig_analytics_read` | `GET /v1/ig-analytics/{id}` | перечитать сохранённый анализ без повторной оплаты | `id` |
| `analyses_list` | `/v1/analyses-list` | история анализов пользователя | – |
| `ig_user_reels` | `/v1/ig-user-reels` | все рилсы аккаунта строками с метриками для отбора | `username` / `user_id`, `limit` 1–50, `cursor`, `include_caption` |
| `ig_user_posts` | `/v1/ig-user-posts` | посты и карусели ленты | те же |
| `ig_user_stories` | `/v1/ig-user-stories` | активные сторис с id для скачивания | `username` / `user_id` |
| `ig_highlights` | `/v1/ig-highlights` | хайлайты | `username` |
| `ig_followers` / `ig_following` / `ig_followers_sample` | | подписчики, подписки, выборка | аккаунт |

`ig_profile` возвращает `id` – передавай его как `user_id` в следующие вызовы, это дешевле.

## Один пост или рилс

| Инструмент | REST | Когда | Ключевые аргументы |
|---|---|---|---|
| `ig_media` | `/v1/ig-media` | метрики, звук, вся подпись до 2200 символов | `media_url_or_code` |
| `analyze_reel` | `/v1/analyze-reel` | транскрипт с таймкодами, подпись, метрики, выброс к медиане автора; на `deep` – кадры hook / body / cta | `url`; `depth`: `quick` (4 кр.) или `deep` (по умолчанию, 6 кр.) |
| `video_transcribe` | `/v1/video-transcribe` | каждое слово с таймингом, паузы, слова-паразиты, файлы `.json` и `.srt`; до 10 минут, 3 кр. | `url`, `language` (`ru`) |
| `reel_storyboard` | `/v1/reel-storyboard` | кадры с таймкодами, смены планов | `url`, `frames` 3–100 (по умолчанию 10); до 24 кадров – 1,5 кр. |
| `reel_reference_compare` | `/v1/reel-reference-compare` | 2–5 рилсов рядом: хуки, структура, визуал, доказательства, качество комментариев + журнал фактов E1..En; 2 кр. за рилс | `urls`, `frames` 3–24 |
| `ig_media_comments` | `/v1/ig-media-comments` | комментарии верхнего уровня | `media_url_or_code`, `limit`, `cursor` |
| `ig_comment_replies` | `/v1/ig-comment-replies` | ответы под комментарием | `comment_id` + пост |
| `ig_post_engagement` | `/v1/ig-post-engagement` | до 20 лайкнувших и топ-ветки комментариев | `post_url_or_code` |
| `ig_resolve` | `/v1/ig-resolve` | что за ссылка и её канонический id | `url` |

## Watchlist конкурентов (хранится в Loore, не локально)

| Инструмент | REST | Что делает | Цена |
|---|---|---|---|
| `tracking_add` | `/v1/tracking-add` | взять аккаунт под наблюдение; `list_only: true` – только в список, без сбора | 9 кр. со сбором, бесплатно с `list_only` |
| `tracking_add_batch` | `/v1/tracking-add-batch` | до 500 аккаунтов одним вызовом, возвращает `jobId` | 9 кр. за реально добавленный |
| `tracking_batch_status` | `/v1/tracking-batch-status` | статус пакетного импорта | – |
| `tracking_list` | `/v1/tracking-list` | список, слоты тарифа, когда собирался | бесплатно |
| `tracking_refresh` | `/v1/tracking-refresh` | собрать свежие публикации одного аккаунта сейчас | 1 кр. (первый сбор после `list_only` – 9) |
| `tracking_feed` | `/v1/tracking-feed` | общая лента; `filter`: `new`, `views`, `anomalous` (только выбросы, по `ratioToMedian`); `kind`: `reel` / `post` | бесплатно |
| `tracking_pause` / `tracking_resume` | | остановить и вернуть обновление, слот сохраняется | бесплатно |
| `tracking_remove` | `/v1/tracking-remove` | убрать аккаунт и освободить слот | бесплатно |
| `tracking_categories` / `tracking_set_category` | | папки (например «конкуренты») | бесплатно |

Ночное автообновление по умолчанию выключено – включить его может только владелец, оно списывает
кредиты каждый день. Без него свежие данные – только через `tracking_refresh`.

## Ниша и поиск

| Инструмент | REST | Когда |
|---|---|---|
| `miner_search` | `/v1/miner-search` | фоновый скан ниши: `niche_query` (3–100 символов), `max_profiles` 1–300 → `run_id`; потом опрос тем же инструментом с `run_id` |
| `miner_insights` | `/v1/miner-insights` | после скана: конкуренты, топ рилсов с `authorMedianPlays`, выбросы, часы публикаций (UTC); бесплатно |
| `miner_track` | `/v1/miner-track` | поставить найденного конкурента на ежедневное отслеживание выбросов |
| `miner_fired_reels` / `miner_fired_posts` | | что «выстрелило» у отслеживаемых, свежие первыми |
| `ig_search` | `/v1/ig-search` | `kind`: `accounts`, `reels`, `hashtags`, `places` + `query` |
| `ig_hashtag_media` | `/v1/ig-hashtag-media` | публикации под хэштегом; `rank`: `top`, `recent`, `reels` |
| `ig_location_media` | `/v1/ig-location-media` | публикации по месту (id из `ig_search kind=places`) |
| `ig_music_trends` / `ig_audio` / `ig_audio_media` | | трендовые звуки и кто их использует |

## Медиа

| Инструмент | REST | Когда |
|---|---|---|
| `ig_download` | `/v1/ig-download` | файл поста, рилса, сторис или слайда карусели (`index`); в ответе `downloadUrl` без ключа на 15 минут – сразу `curl -o`; 0,5 кр. за файл |
| `download_instagram_media` | `/v1/download-instagram-media` | то же для поста/рилса, только первый слайд |
| `download_instagram_story` | `/v1/download-instagram-story` | сторис |

## Сервисные

| Инструмент | REST | Когда |
|---|---|---|
| `get_credit_balance` | `/v1/billing-balance` | остаток кредитов; бесплатно |
| `estimate_credit_cost` | `/v1/billing-estimate` | цена действия без запуска; бесплатно |
| `ig_batch_quote` / `ig_batch_create` / `ig_batch_status` / `ig_batch_result` | | одно чтение (`work`: `profiles`, `reels`, `posts`, `media`, `comments`, `reel_funnel`) по списку до 1000 объектов фоновой задачей |
| `spend_authorization` | `/v1/spend-authorization` | бюджет на цепочку вызовов на 24 часа после согласия пользователя |
| `skill_list` / `skill_run` / `skill_run_status` | | готовые пайплайны Loore; `skill_list` бесплатен и показывает рецепт, входы и `card.creditsMax` |
| `ig_audit_render` / `report_render` | | брендированный отчёт-файл (HTML) по готовому анализу |
| `library_save_item` / `library_list` | | отложить профиль, рилс, хэштег в библиотеку пользователя |

## Поля, о которых важно помнить

- `likesHidden: true` – автор скрыл лайки, цифра лайков – заглушка; исключай строку из ER и
  рейтинга по лайкам (просмотры настоящие).
- У каруселей и фото сервис не получает просмотры, отправки и сохранения – это не ошибка чтения.
- `isPinned: true` – закреп (обычно старый хит), `isTrial: true` – пробный рилс для
  неподписчиков; обе строки не учитывай в медиане автора.
- Курсоры страниц живут 6 часов.
