---
name: video-assembly
description: Сборка вертикального видео 1080x1920 в MoviePy — озвучка Vosk TTS (запасные Gemini TTS / Azure), длительность сцены по озвучке, Ken Burns, crossfade 0.3 с, субтитры по таймингам слов (faster-whisper / Azure WordBoundary), лоу-фай музыка 12% с затуханием. Используй при правке монтажа, субтитров, звука или скорости рендера.
---

# video-assembly (`src/tts.py`, `src/subtitles.py`, `src/assemble.py`, `src/music_gen.py`)

## Озвучка (`tts.py`, `align.py`)
- Провайдер — `config/voice.json → provider` (по умолчанию `vosk`; запасные — gemini, azure, если есть ключи).
- **Vosk TTS** (Apache 2.0, локально, `vosk-model-tts-ru-0.9-multi`, голос `vosk.speaker = 3`): сцена синтезируется
  по `vosk_segments` (текст с «+»-ударениями, темп `speech_rate`, тишина `pause_after_ms`), → wav/mp3,
  тайминги — `align.align_script` по аудио сцены. Vosk падает на цифрах, латинице, «», …, – —
  всё это чистит `narration.vosk_clean`. Модель грузится ~45 с, синтез минуты речи ~25 с на 4 ядрах.
- **Gemini TTS** (`gemini-3.8-flash-tts`, Interactions API `POST /v1beta/interactions`, запасной путь — старый
  `generateContent`): вся история одним запросом (`narration.gemini_script`: сцены через `<short/long pause>`,
  манера — `speech_metadata.style`). Ответ — WAV 24 кГц base64.
- Тайминги: `align.align_script` — faster-whisper (small, int8, CPU) + сопоставление со сценарием (difflib),
  пропуски интерполируются. Совпадение < `min_alignment` (0.85) → новый дубль (до 3). Точность в тесте:
  медиана 0.04 с, рассинхрон субтитров в итоговом видео — медиана 0.004 с, без дрейфа.
- Нарезка: граница сцен — середина паузы между последним словом сцены и первым словом следующей →
  `audio/scene_XX.mp3`, `pause_after = 0` (пауза уже внутри аудио).
- 429: ждём `retryDelay`; дневной лимит → `QuotaExhausted`.
- **Azure** — по сценам, SSML, WordBoundary (см. narration-director).
- Без ключей и только с `--no-upload` — тихая заглушка.
- Выбор голоса: `python -m src.voice_test` / workflow «Voice test».

## Таймлайн (`assemble.timeline`)
- Сцена i звучит с `start_i`, длится ровно `duration_i` своей озвучки + пауза `pause_after` от narration-director (300–600 мс после хука, перед поворотом и развязкой, иначе 150–250 мс).
- Картинка сцены i появляется за 0.3 с до своей фразы (crossfade с предыдущей), хвост ролика 0.8 с.
- Ken Burns: плавный zoom 1.00↔1.08 (чётные — наезд, нечётные — отъезд) и лёгкий дрейф.
- Видеослой — одна функция кадра (Ken Burns + crossfade + субтитры на numpy): в ~2–3 раза быстрее CompositeVideoClip.

## Субтитры (`subtitles.py`)
- Слова `display_text` сцены (с пунктуацией и «ё») сопоставляются с таймингами слов (без учёта ударений и ё/е) → фразы 2–4 слова (разрыв на пунктуации, ≤20 символов).
- Жирный шрифт (Montserrat Black/ExtraBold или DejaVu Sans Bold), 86 px (уменьшается, если не влезает в 2 строки), чёрная обводка 9 px, ВЕРХНИЙ РЕГИСТР; текущее слово подсвечено жёлтым.
- Центр по горизонтали, по вертикали ~1240 px (выше нижней подписи Shorts), ширина ≤ 820 px (не залезает на кнопки справа). Зона `SUB_ZONE` запрещена для рекламы.
- Дополнительно пишется `subtitles.srt`.

## Музыка
- Случайный трек из `music/` (mp3/wav/ogg/m4a/flac), громкость `MUSIC_VOLUME=0.12`, fade-in 1 с, fade-out 2.5 с, зацикливание если короче.
- Если рядом лежит `<трек>.txt` — его текст идёт в описание как атрибуция.
- Если папка пуста — `music_gen.py` синтезирует спокойный лоу-фай (гарантированно без авторских прав).

## Кодирование
H.264 CRF 20, yuv420p, 30 fps, AAC 192k, `+faststart` (`X264_PRESET` — пресет x264).
