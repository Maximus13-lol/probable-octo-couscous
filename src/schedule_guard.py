"""Страж расписания: нужен ли ролик в этот запуск.

GitHub Actions не гарантирует запуск по cron — в часы нагрузки запуски задерживаются или пропадают
(так 11.11.2026 пропал запуск в 07:23 UTC). Поэтому у каждого слота есть резервные запуски, а этот
скрипт решает, публиковать ли сейчас:
  * до 15:00 UTC (18:00 МСК) норма дня — 1 ролик, после — 2;
  * сколько уже вышло сегодня — считаем по каналу YouTube (источник правды) и по publish_log.json.
Печатает run=true|false в $GITHUB_OUTPUT.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

from .utils import PUBLISH_LOG_FILE, env, load_json, log, setup_logging

PER_DAY = int(env("SHORTS_PER_DAY", "2"))
SECOND_SLOT_UTC_HOUR = 15


def target_now(now: dt.datetime) -> int:
    return PER_DAY if now.hour >= SECOND_SLOT_UTC_HOUR else min(1, PER_DAY)


def count_from_log(today: str) -> int:
    return sum(1 for e in (load_json(PUBLISH_LOG_FILE, default=[]) or []) if e.get("date") == today)


def count_from_youtube(today: str) -> int | None:
    try:
        from .upload import get_youtube_client
        yt = get_youtube_client()
        if yt is None:
            return None
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = yt.playlistItems().list(part="contentDetails,snippet", playlistId=uploads,
                                        maxResults=15).execute().get("items", [])
        n = 0
        for it in items:
            when = (it.get("contentDetails", {}).get("videoPublishedAt")
                    or it.get("snippet", {}).get("publishedAt") or "")
            if when.startswith(today):
                n += 1
        return n
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось посчитать ролики на канале (%s) — считаю по журналу", exc)
        return None


def decide(now: dt.datetime | None = None) -> tuple[bool, str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    target = target_now(now)
    by_log = count_from_log(today)
    by_yt = count_from_youtube(today)
    done = max(by_log, by_yt or 0)
    why = (f"сегодня ({today}) опубликовано {done} (канал: {by_yt if by_yt is not None else '—'}, "
           f"журнал: {by_log}), норма к {now:%H:%M} UTC — {target}")
    return done < target, why


def main() -> int:
    setup_logging()
    scheduled = os.environ.get("GUARD_SCHEDULED", "").lower() == "true" or \
        os.environ.get("GITHUB_EVENT_NAME", "schedule") == "schedule"
    if not scheduled:
        run, why = True, "ручной запуск — выполняется всегда"
    else:
        run, why = decide()
    print(("▶ Запускаю конвейер: " if run else "⏭ Пропуск: ") + why)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
