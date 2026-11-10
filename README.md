# Автоматический конвейер YouTube Shorts (тайны и загадки)

Раз в день GitHub Actions сам находит трендовую тему, пишет историю на русском, рисует картинки,
озвучивает, накладывает субтитры и музыку, (по желанию) вставляет рекламный баннер и публикует
вертикальное видео 1080×1920 ~60 с на YouTube. Используются **только бесплатные сервисы**.

```
trend-scout → story-writer → storyboard → картинки → озвучка → субтитры → сборка → ad-inserter → publisher
```

| Шаг | Модуль | Сервис |
|---|---|---|
| Поиск темы | `src/trends.py` | YouTube Data API, Reddit JSON, Google Trends (pytrends), Gemini |
| История 130–160 слов | `src/story.py` | Gemini API (бесплатный ключ) |
| Раскадровка 8–12 сцен | `src/storyboard.py` | Gemini API |
| Картинки | `src/images.py`, `src/style.py` | Pollinations.ai (без ключа) |
| Озвучка + тайминги слов | `src/tts.py` | edge-tts, `ru-RU-DmitryNeural` |
| Субтитры | `src/subtitles.py` | из таймингов edge-tts |
| Сборка | `src/assemble.py` | MoviePy + FFmpeg, музыка из `music/` |
| Реклама (выкл.) | `src/ads.py` | FFmpeg overlay |
| Публикация | `src/upload.py` | YouTube Data API v3 (OAuth) |

Описание каждого шага для Claude Code — в `.claude/skills/<имя>/SKILL.md`.

## Структура

```
src/            код конвейера (main.py — точка входа)
config/         publish.json — шаблоны названия, описания, хэштегов
music/          ваши лоу-фай треки (mp3/wav/ogg/m4a)
ads/            config.json (кампании), баннеры, log.json (показы)
output/         результаты запусков (в git не попадают)
used_topics.json, publish_log.json, trends_quota.json — состояние, коммитится ботом
.github/workflows/daily.yml — расписание
```

---

## 1. Быстрый локальный тест (без ключей)

Нужны Python 3.11+ и FFmpeg.

```bash
pip install -r requirements.txt
python -m src.main --no-upload
```

Без `GEMINI_API_KEY` конвейер работает в демо-режиме (готовая история про маяк Фланнан),
но картинки, озвучка, субтитры, музыка и сборка — настоящие. Видео: `output/<дата_время>/final.mp4`.

Флаги:

| Флаг | Что делает |
|---|---|
| `--no-upload` | всё, кроме загрузки на YouTube |
| `--test-ad` | вставить тестовую рекламу (подразумевает `--no-upload`) |
| `--ad-campaign ID` | принудительно взять конкретную кампанию |
| `--topic "..."` | задать тему вручную, без trend-scout |
| `--mark-used` | записать тему в `used_topics.json` даже при `--no-upload` |
| `-v` | подробный лог |

---

## 2. Ключи и секреты — пошагово

Все секреты добавляются в репозиторий: **GitHub → Settings → Secrets and variables → Actions → New repository secret**.

| Секрет | Обязателен | Для чего |
|---|---|---|
| `GEMINI_API_KEY` | да | выбор темы, история, раскадровка |
| `YT_CLIENT_ID` | да | OAuth-клиент Google |
| `YT_CLIENT_SECRET` | да | OAuth-клиент Google |
| `YT_REFRESH_TOKEN` | да | бессрочный доступ к каналу |
| `YT_API_KEY` | нет | поиск трендов, если OAuth не задан |
| `POLLINATIONS_TOKEN` | нет | бесплатный токен Pollinations — мягче лимиты |

### 2.1. GEMINI_API_KEY (бесплатно)

1. Откройте <https://aistudio.google.com/apikey> и войдите в Google-аккаунт.
2. Нажмите **Create API key** → выберите или создайте проект → скопируйте ключ.
3. Сохраните его как секрет `GEMINI_API_KEY`.

Бесплатного тарифа хватает с запасом: конвейер делает 3–10 запросов в день.
Модель по умолчанию — `gemini-2.5-flash` (если её нет, пробуются `gemini-flash-latest` и `gemini-2.0-flash`;
свою можно задать переменной `GEMINI_MODEL`).

### 2.2. Проект Google Cloud и YouTube Data API v3

1. Откройте <https://console.cloud.google.com/> → вверху выбор проекта → **New Project** → имя, например `shorts-bot` → **Create**.
2. **APIs & Services → Library** → найдите **YouTube Data API v3** → **Enable**.
3. **APIs & Services → OAuth consent screen** (или **Google Auth Platform**):
   - User type: **External** → **Create**;
   - App name, support email, developer email — любые ваши;
   - **Scopes / Data access → Add or remove scopes**: добавьте
     `https://www.googleapis.com/auth/youtube.upload` и `https://www.googleapis.com/auth/youtube.readonly`;
   - **Test users / Audience**: добавьте Google-аккаунт, которому принадлежит канал.
   - ⚠️ **Важно:** нажмите **Publish app** (статус **In production**). В статусе *Testing* refresh token
     перестаёт работать через 7 дней. Проходить проверку Google для личного использования не обязательно —
     при входе просто появится предупреждение «Google hasn't verified this app».
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app** → **Create**;
   - скопируйте **Client ID** → секрет `YT_CLIENT_ID`, **Client secret** → секрет `YT_CLIENT_SECRET`.
5. *(необязательно)* **Create credentials → API key** → секрет `YT_API_KEY`.

### 2.3. YT_REFRESH_TOKEN

Делается один раз на своём компьютере (нужен браузер):

```bash
git clone <этот репозиторий> && cd <папка>
pip install google-auth-oauthlib
python -m src.get_refresh_token --client-id "ВАШ_CLIENT_ID" --client-secret "ВАШ_CLIENT_SECRET"
```

1. Откроется браузер — войдите в аккаунт **с тем каналом**, куда нужно публиковать (если каналов несколько — выберите нужный).
2. Предупреждение «Google hasn't verified this app» → **Advanced / Дополнительно → Go to … (unsafe) / Перейти**.
3. Разрешите доступ (загрузка видео и просмотр аккаунта YouTube).
4. В консоли появится `YT_REFRESH_TOKEN` — сохраните его как секрет.

Если порт 8080 занят — добавьте `--port 8090`.

### 2.4. ⚠️ Аудит YouTube API (иначе видео будут «Доступ ограничен»)

Google ограничивает **непроверенные** API-проекты (созданные после 28.07.2020): видео, загруженные через
`videos.insert`, принудительно становятся **приватными**. Чтобы публиковать сразу `public`:

1. Заполните форму **YouTube API Services — Audit and Quota Extension Form**
   (<https://support.google.com/youtube/contact/yt_api_form>), выберите аудит соответствия (compliance audit).
2. Опишите использование: «личный канал, автоматическая загрузка собственных роликов раз в день».
3. Пока аудит не пройден, конвейер всё равно загрузит ролик (приватно) и напишет предупреждение в лог —
   его можно открыть вручную в YouTube Studio.

Квота: 10 000 единиц в сутки. Загрузка = 1600, поиск трендов = 100 × до 10 запросов, статистика — копейки. Укладываемся.

### 2.5. POLLINATIONS_TOKEN (необязательно)

Без токена Pollinations работает, но ограничивает анонимов (≈ 1 картинка в 45 с с одного IP, ~576×1024).
Конвейер сам ждёт сколько нужно, поэтому шаг картинок может занять до ~10 минут.
Бесплатный токен: зарегистрируйтесь на <https://enter.pollinations.ai> → скопируйте ключ → секрет `POLLINATIONS_TOKEN`.

---

## 3. Музыка

1. Откройте <https://pixabay.com/music/> → поиск `lofi` / `lo-fi chill` / `ambient`.
2. Скачайте 5–15 спокойных треков (MP3) и положите в папку `music/`, закоммитьте.
3. *(необязательно)* рядом с треком положите `<имя_трека>.txt` с атрибуцией — она попадёт в описание.

Каждый раз выбирается случайный трек, громкость ~12% (`MUSIC_VOLUME`), плавное затухание в конце.
Pixabay Content License разрешает бесплатное использование без указания авторства; на всякий случай
избегайте треков с пометкой о регистрации в Content ID. Если папка пуста — конвейер **синтезирует**
спокойный лоу-фай трек сам (`src/music_gen.py`), он гарантированно без авторских прав.

---

## 4. Расписание GitHub Actions

Файл `.github/workflows/daily.yml`:

- **автоматически** каждый день в 07:23 UTC (10:23 МСК) — полный прогон с публикацией;
- **вручную**: вкладка **Actions → Daily YouTube Short → Run workflow**. По умолчанию галочка
  «Не загружать на YouTube» включена — это безопасный тестовый прогон. Можно включить тестовую рекламу и задать тему.

Что получаете:

- артефакт `short-N`: `final.mp4`, картинки сцен, `story.json`, `storyboard.json`, `metadata.json`, `subtitles.srt`, `summary.json`;
- артефакт `trends-report-N`: `trends_report.json` (тема, источник, метрики, причина выбора, топ-15 кандидатов);
- после публикации бот коммитит `used_topics.json`, `publish_log.json`, `ads/log.json` (темы не повторяются,
  а коммиты не дают GitHub отключить расписание за неактивность).

Если пуш бота падает с 403: **Settings → Actions → General → Workflow permissions → Read and write permissions**.

Время запуска меняется строкой `cron` (время в UTC).

---

## 5. Реклама (по умолчанию выключена)

Конфиг — `ads/config.json`:

```json
{
  "enabled": false,
  "opacity": 0.9,
  "fade": 0.5,
  "max_banner_width": 720,
  "max_banner_height": 300,
  "label": "Реклама",
  "campaigns": [
    {
      "id": "romashka-20261110",
      "enabled": true,
      "advertiser": "Ромашка",
      "legal": "ООО «Ромашка», ИНН 0000000000",
      "erid": "2Vtzqxxxxxx",
      "banner": "ads/romashka-20261110.png",
      "url": "https://example.com/?utm_source=shorts",
      "start_date": "2026-11-10",
      "end_date": "2026-12-10",
      "position": "auto",
      "show_at": 20,
      "duration": 5
    }
  ]
}
```

| Поле | Значение |
|---|---|
| `position` | `auto` (ищет самую «пустую» область в верхней половине), `top-left`, `top-center`, `top-right`, `upper-middle` или `{"x": 100, "y": 300}` |
| `show_at` | секунда появления (не раньше 3.5 с — хук не трогаем; пусто = 40% ролика) |
| `duration` | 4–6 секунд |
| `legal`, `erid` | данные для маркировки рекламы (ФЗ «О рекламе»: «Реклама», рекламодатель, токен ОРД) |

### Как включить рекламу

1. Добавьте кампанию (баннер копируется в `ads/`):
   ```bash
   python -m src.ads add --banner ~/Downloads/banner.png --advertiser "Ромашка" \
       --url "https://example.com" --start 2026-11-10 --end 2026-12-10 \
       --legal "ООО «Ромашка», ИНН 0000000000" --erid "2Vtzq..."
   ```
2. Проверьте на последнем готовом видео:
   ```bash
   python -m src.ads test --campaign romashka-20261110
   ```
   Посмотрите `output/ad_test_*/ad_preview.jpg` и `video_ad.mp4`.
3. Включите: `python -m src.ads enable` (или `"enabled": true` в конфиге), закоммитьте `ads/`.

Можно просто прислать баннер Claude Code в чат — он сделает шаги 1–2 сам (скилл `ad-inserter`).

### Что гарантирует модуль

- Баннер только в безопасной зоне Shorts: верх кадра под шапкой (y 210–900), не перекрывает субтитры,
  правую колонку кнопок и нижнюю подпись; даже ручные координаты зажимаются в эту зону.
- Непрозрачность 90%, плавное появление и исчезновение, показ 4–6 с, не в первые 3 секунды.
- Плашка «Реклама» на баннере, в описании первой строкой «Реклама. Рекламодатель: … erid: …» и ссылка.
- В YouTube ставится отметка о платной рекламе (`paidProductPlacementDetails.hasPaidProductPlacement`).
  Если API её не принял — в логе будет предупреждение, поставьте галочку в YouTube Studio вручную.
- Кампании с истёкшей датой пропускаются; при нескольких активных — ротация (меньше показов → раньше).
- Каждый опубликованный показ пишется в `ads/log.json`. Отчёт рекламодателю: `python -m src.ads report --campaign ID`.
- Прочие команды: `python -m src.ads list`, `python -m src.ads disable`.

---

## 6. Настройки через переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | модель Gemini |
| `TTS_VOICE` | `ru-RU-DmitryNeural` | голос edge-tts |
| `TTS_FALLBACK_VOICES` | `ru-RU-SvetlanaNeural` | запасные голоса через запятую |
| `TTS_RATE` | `+12%` | темп речи |
| `IMAGE_SEED` | `424242` | seed картинок (единый стиль) |
| `POLLINATIONS_MODEL` | `flux` | модель картинок |
| `MUSIC_VOLUME` | `0.12` | громкость музыки |
| `MAX_YT_QUERIES` | `10` | лимит поисковых запросов YouTube в сутки |
| `X264_PRESET` | `medium` | скорость/качество кодирования |

Шаблоны названия и описания — `config/publish.json`.

## 7. Если что-то пошло не так

- **Тема повторяется** — проверьте, что бот коммитит `used_topics.json` (права на запись, см. п. 4).
- **edge-tts: No audio was received** — временный сбой сервиса Microsoft; конвейер повторяет запрос
  и при необходимости переключается на запасной голос.
- **Видео загрузилось приватным** — проект API не прошёл аудит (п. 2.4).
- **invalid_grant** при загрузке — refresh token отозван или истёк (приложение в статусе Testing, п. 2.2) — получите новый.
- **Reddit / Google Trends недоступны** — источник пропускается, тема выбирается из оставшихся.
- Подробности каждого запуска — `output/<run>/summary.json` (время шагов, ошибка с трассировкой).
