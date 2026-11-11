"""Проверка доступа к YouTube без публикации: показывает канал, к которому подключены ключи.

    python -m src.yt_check
"""
from __future__ import annotations

import sys

from .upload import get_youtube_client
from .utils import env, setup_logging

HINTS = {
    "invalid_grant": "Refresh token недействителен или отозван. Получите новый (шаг «Получить постоянный токен»). "
                     "Если приложение в статусе Testing — переведите его в In production, иначе токен живёт 7 дней.",
    "invalid_client": "Неверный YT_CLIENT_ID или YT_CLIENT_SECRET (лишний пробел при копировании?).",
    "unauthorized_client": "Токен получен для другого клиента: Client ID/Secret в Playground и в секретах должны совпадать.",
    "accessNotConfigured": "В проекте Google Cloud не включён YouTube Data API v3 (APIs & Services → Library → Enable).",
    "insufficientPermissions": "Токен выдан без нужных разрешений — в Playground укажите обе области youtube.upload и youtube.readonly.",
    "youtubeSignupRequired": "У этого Google-аккаунта нет YouTube-канала — создайте канал или войдите в нужный аккаунт.",
}


def main() -> int:
    setup_logging()
    missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not env(k)]
    if missing:
        print("❌ Не заданы секреты:", ", ".join(missing))
        return 1
    try:
        yt = get_youtube_client()
        res = yt.channels().list(part="snippet,statistics,status", mine=True).execute()
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        print("❌ Ошибка доступа к YouTube:", text[:500])
        for key, hint in HINTS.items():
            if key in text:
                print("👉", hint)
        return 1
    items = res.get("items", [])
    if not items:
        print("❌ Ключи работают, но у аккаунта нет YouTube-канала.", HINTS["youtubeSignupRequired"])
        return 1
    for ch in items:
        sn, st = ch["snippet"], ch.get("statistics", {})
        print(f"✅ Подключено к каналу: «{sn['title']}» (https://youtube.com/channel/{ch['id']})")
        print(f"   подписчиков: {st.get('subscriberCount', '?')}, видео: {st.get('videoCount', '?')}")
    print("\nВсё готово к публикации: ролики будут выходить публичными (аудит Google не нужен с 02.11.2026).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
