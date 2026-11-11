---
name: trend-scout
description: Выбор трендовой темы для ежедневного Shorts в нише тайн/загадок/необъяснимых случаев. Используй, когда нужно найти, проверить или отладить выбор темы, источники трендов (YouTube, Reddit, Google Trends), trends_report.json или used_topics.json.
---

# trend-scout (`src/trends.py`)

Первый шаг конвейера. Находит кандидатов и выбирает одну тему с самым сильным хуком.

## Источники
| Источник | Как | Метрика «скорости» |
|---|---|---|
| YouTube Data API v3 | `search.list` (`videoDuration=short`, `order=viewCount`, `publishedAfter`=7 дней) по запросам из `YT_QUERIES`, затем `videos.list` за статистикой | просмотры / часы с публикации |
| Reddit JSON (без ключа) | `/r/{UnresolvedMysteries,HighStrangeness,todayilearned}/top.json?t=week` | очки / часы с публикации |
| Google Trends (pytrends) | растущие связанные запросы к «загадка/тайна/мистика», geo=RU | рост в % |

- Лимит YouTube: не больше `MAX_YT_QUERIES` (по умолчанию 10) поисковых запросов в сутки — счётчик в `trends_quota.json`. Один `search.list` = 100 единиц квоты из 10 000.
- Любой источник может упасть — он пропускается, конвейер продолжает работу.
- Скорость нормируется внутри источника и умножается на вес `SOURCE_WEIGHT`; в Gemini уходит топ-15 (не больше 7 от одного источника).

## Уникальность (`src/uniqueness.py`)
- После выбора Gemini проверяет смысловой повтор (`gemini_duplicate`): тот же случай/явление/объект/человек → тема
  в `report.rejected`, выбирается другая (до 3 раз).
- Нет новых тем в трендах → `gemini_fresh_topic` предлагает новую реальную загадку (тоже проверяется).
- В `main.py` готовая история сравнивается с прошлыми по основам слов (Жаккар, порог 0.14: разные загадки ≤0.08,
  пересказ ≈0.19) — похожая история отклоняется, тема исключается, цикл до 3 раз.
- `used_topics.json` хранит `topic, topic_en, title, summary, keywords, date`.
- Reddit с серверов GitHub отвечает 403 на JSON — запасной путь RSS-лента (`_reddit_rss`).

## Выбор
`choose_with_gemini()` отдаёт Gemini кандидатов + список `used_topics.json`; ответ проверяется (индекс в диапазоне, тема не повтор — нечёткое сравнение `difflib` ≥ 0.82). Если Gemini недоступен — берётся лидер по скорости, если кандидатов нет — тема из `EVERGREEN`.

## Отчёт
`output/<run>/trends_report.json`: `sources` (сколько пришло из каждого), `candidates` (топ-15 с метриками и score), `chosen` (тема, источник, метрики, причина). Уходит в артефакты Actions отдельным архивом `trends-report-N`.

## Как проверить
```bash
python -c "from src.utils import setup_logging; setup_logging(); from src.trends import fetch_reddit; print(len(fetch_reddit()))"
python -m src.main --no-upload --topic "Перевал Дятлова"   # обойти trend-scout
```
