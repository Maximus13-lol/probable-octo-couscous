"""Страж расписания: какой слот публикации заполнять в этот запуск.

Слоты (время выхода по МСК) — config/schedule.json, по фазам (первая неделя — 3 ролика, потом — 2).
Сборка стартует за lead_minutes до слота; ролик загружается с отложенной публикацией (YouTube publishAt)
и выходит ровно в слот. Если слот уже прошёл (сбой, задержка) — ролик публикуется сразу.

Заполненные слоты — по publish_log.json (поле slot); защита от дублей — по числу загрузок на канале.
Пишет в $GITHUB_OUTPUT: run=true|false, slot=HH:MM, publish_at=ISO-UTC (пусто = публиковать сразу).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from .utils import CONFIG_DIR, PUBLISH_LOG_FILE, load_json, log, setup_logging

DEFAULT_CFG = {"timezone": "Europe/Moscow", "lead_minutes": 110,
               "phases": [{"slots": ["11:00", "19:00"]}]}


def cfg() -> dict:
    c = dict(DEFAULT_CFG)
    c.update(load_json(CONFIG_DIR / "schedule.json", default={}) or {})
    return c


def slots_for(day: dt.date, c: dict) -> list[str]:
    for ph in c["phases"]:
        until = ph.get("until")
        if until is None or day <= dt.date.fromisoformat(until):
            return list(ph["slots"])
    return []


def slot_dt(day: dt.date, hhmm: str, tz: ZoneInfo) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def filled_slots(day: dt.date, slots: list[str]) -> set[str]:
    """Слоты, уже занятые сегодня. Старые записи без поля slot занимают самые ранние слоты по порядку."""
    entries = [e for e in (load_json(PUBLISH_LOG_FILE, default=[]) or [])
               if (e.get("slot_date") or e.get("date")) == day.isoformat()]
    filled = {e["slot"] for e in entries if e.get("slot") in slots}
    legacy = sum(1 for e in entries if not e.get("slot"))
    for s in slots:
        if legacy <= 0:
            break
        if s not in filled:
            filled.add(s)
            legacy -= 1
    return filled


def uploads_today_on_channel(day: dt.date, tz: ZoneInfo) -> int | None:
    try:
        from .upload import get_youtube_client
        yt = get_youtube_client()
        if yt is None:
            return None
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = yt.playlistItems().list(part="snippet", playlistId=uploads, maxResults=20).execute().get("items", [])
        n = 0
        for it in items:
            added = it.get("snippet", {}).get("publishedAt", "")  # время добавления в загрузки = время загрузки
            if added:
                when = dt.datetime.fromisoformat(added.replace("Z", "+00:00")).astimezone(tz)
                n += when.date() == day
        return n
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось посчитать загрузки на канале (%s) — только по журналу", exc)
        return None


def decide(now: dt.datetime | None = None, channel_count=None) -> dict:
    c = cfg()
    tz = ZoneInfo(c["timezone"])
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    day = now.date()
    slots = slots_for(day, c)
    lead = dt.timedelta(minutes=int(c["lead_minutes"]))
    due = [s for s in slots if now >= slot_dt(day, s, tz) - lead]
    filled = filled_slots(day, slots)
    unfilled = [s for s in due if s not in filled]
    on_channel = uploads_today_on_channel(day, tz) if channel_count is None else channel_count
    info = {"run": False, "slot": "", "publish_at": "", "now": now.strftime("%Y-%m-%d %H:%M"),
            "slots": slots, "due": due, "filled": sorted(filled), "on_channel": on_channel}
    if not unfilled:
        info["why"] = "все слоты, которым пора, уже заняты"
        return info
    if on_channel is not None and on_channel >= len(due):
        info["why"] = f"на канале уже {on_channel} загрузок сегодня при {len(due)} слотах к этому времени"
        return info
    soon = [s for s in unfilled if slot_dt(day, s, tz) > now + dt.timedelta(minutes=10)]
    target = soon[0] if soon else unfilled[0]
    when = slot_dt(day, target, tz)
    info.update(run=True, slot=target)
    if when > now + dt.timedelta(minutes=15):
        info["publish_at"] = when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        info["why"] = f"готовлю ролик к слоту {target} (отложенная публикация)"
    else:
        info["why"] = f"слот {target} уже наступил — публикую сразу после сборки"
    return info


def main() -> int:
    setup_logging()
    scheduled = os.environ.get("GUARD_SCHEDULED", "").lower() == "true" or \
        os.environ.get("GITHUB_EVENT_NAME", "schedule") == "schedule"
    if not scheduled:
        info = {"run": True, "slot": "", "publish_at": "", "why": "ручной запуск — публикация сразу"}
    else:
        info = decide()
    print(("▶ Запуск: " if info["run"] else "⏭ Пропуск: ") + info["why"])
    print("  " + ", ".join(f"{k}={v}" for k, v in info.items() if k not in ("run", "why")))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if info['run'] else 'false'}\nslot={info['slot']}\npublish_at={info['publish_at']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
