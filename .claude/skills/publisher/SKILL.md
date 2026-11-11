---
name: publisher
description: Публикация Shorts на YouTube через Data API v3 (OAuth refresh token) — шаблоны названия, описания и хэштегов, отметка платной рекламы, лог публикаций. Используй при правке метаданных, ошибках загрузки или настройке OAuth.
---

# publisher (`src/upload.py`, шаблоны в `config/publish.json`)

## Метаданные (`build_metadata`)
- Название: `title_template` (по умолчанию `{title} #Shorts`), максимум 100 символов, `#Shorts` гарантирован.
- Описание: `description_template` с полями `{hook} {description} {ad_block} {hashtags} {music_credit}`; символы `<` `>` заменяются (YouTube их запрещает).
- Хэштеги: `base_hashtags` + хэштеги из истории, без дублей, до 12. Теги: `tags` + хэштеги, суммарно ≤ 450 символов.
- Если в ролике есть реклама: блок «Реклама. Рекламодатель: … erid: …» и ссылка рекламодателя ставятся **первыми строками** описания, `paidProductPlacementDetails.hasPaidProductPlacement = true`.
- `status.containsSyntheticMedia = true` (картинки и голос сгенерированы ИИ), `selfDeclaredMadeForKids = false`, `privacyStatus = public`.

## Загрузка (`upload`)
- OAuth: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` → `google.oauth2.credentials.Credentials`.
- Resumable upload по 8 МБ, повтор на 5xx/сетевых ошибках с экспоненциальной паузой.
- Если API не принял необязательные поля (`paidProductPlacementDetails`/`containsSyntheticMedia`) — повтор без них и громкое предупреждение: отметку платной рекламы тогда поставить вручную в Studio.
- Результат: `output/<run>/upload_result.json` + строка в `publish_log.json` (коммитится в репозиторий).
- Аудит Google не нужен: с 02.11.2026 видео из непроверенных API-проектов не делаются приватными. Если вернулся
  `private` — смотреть настройки доступа по умолчанию в YouTube Studio и `privacy_status`.

## Проверка без загрузки
```bash
python -m src.upload output/<run>   # показать итоговые метаданные
```
Refresh token: `python -m src.get_refresh_token --client-id ... --client-secret ...`.
