"""Страж расписания: какой слот публикации заполнять в этот запуск.

Слоты (время выхода по МСК) — config/schedule.json.
Сборка стартует за lead_minutes до слота; ролик загружается с отложенной публикацией (YouTube publishAt)
и выходит в слот. Пропущенные слоты повторяются до публикации.

Заполненные слоты определяются по publish_log.json (поле slot).
Пишет в $GITHUB_OUTPUT: run, slot, slot_date, publish_at, deadline.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from .utils import CONFIG_DIR, PUBLISH_LOG_FILE, load_json, log, setup_logging

DEFAULT_CFG = {"timezone": "Europe/Moscow", "lead_minutes": 170,
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
    """Время выхода слота. «19:00» — фиксированное; «18:00-19:30» — окно: каждый день своё случайное время
    (шаг 5 мин), одинаковое для всех запусков этого дня (зерно = дата + окно), чтобы страж не путался."""
    if "-" in hhmm:
        import hashlib
        a, b = hhmm.split("-")
        ha, ma = map(int, a.split(":"))
        hb, mb = map(int, b.split(":"))
        start, end = ha * 60 + ma, hb * 60 + mb
        steps = max(0, (end - start) // 5)
        r = int(hashlib.sha256(f"{day.isoformat()}|{hhmm}".encode()).hexdigest(), 16) % (steps + 1)
        minutes = start + r * 5
        return dt.datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=tz)
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def _is_manual(e: dict) -> bool:
    """Внеплановый ролик (ручной запуск): у новых записей есть slot_date, но нет slot. Слоты не занимает."""
    return bool(e.get("manual")) or ("slot_date" in e and not e.get("slot"))


def filled_slots(day: dt.date, slots: list[str], entries: list[dict] | None = None) -> set[str]:
    """Слоты, уже занятые сегодня. Старые записи (до поля slot_date) без slot занимают самые ранние слоты."""
    if entries is None:
        entries = load_json(PUBLISH_LOG_FILE, default=[]) or []
    entries = [e for e in entries if (e.get("slot_date") or e.get("date")) == day.isoformat()
               and not _is_manual(e)]
    filled = {e["slot"] for e in entries if e.get("slot") in slots}
    legacy = sum(1 for e in entries if not e.get("slot"))
    for s in slots:
        if legacy <= 0:
            break
        if s not in filled:
            filled.add(s)
            legacy -= 1
    return filled


def window_end(day: dt.date, slot: str, tz: ZoneInfo) -> dt.datetime:
    if "-" in slot:
        return slot_dt(day, slot.split("-", 1)[1], tz)
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tz)


def decide(now: dt.datetime | None = None) -> dict:
    c = cfg()
    tz = ZoneInfo(c["timezone"])
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    day = now.date()
    lead = dt.timedelta(minutes=int(c["lead_minutes"]))
    entries = load_json(PUBLISH_LOG_FILE, default=[]) or []
    retry_from = dt.date.fromisoformat(c["retry_from"]) if c.get("retry_from") else None
    candidate = min(day, retry_from) if retry_from else day
    target = None
    while candidate <= day:
        slots = slots_for(candidate, c)
        retry_enabled = retry_from is not None and candidate >= retry_from
        due = [s for s in slots if slot_dt(candidate, s, tz) - lead <= now
               and (retry_enabled or now < window_end(candidate, s, tz))]
        filled = filled_slots(candidate, slots, entries)
        target = next((s for s in due if s not in filled), None)
        if target is not None:
            day = candidate
            break
        candidate += dt.timedelta(days=1)
    info = {"run": False, "slot": "", "slot_date": "", "publish_at": "", "deadline": "",
            "now": now.isoformat(), "slots": slots, "due": due, "filled": sorted(filled)}
    if target is None and now.hour >= 20:
        # По возможности готовим завтрашние публикации заранее.
        day += dt.timedelta(days=1)
        slots = slots_for(day, c)
        filled = filled_slots(day, slots, entries)
        target = next((s for s in slots if s not in filled), None)
        info.update(slots=slots, due=slots, filled=sorted(filled))
    if target is None and not due:
        return {**info, "why": "сейчас нет открытого окна сборки"}
    if target is None:
        return {**info, "why": "открытые слоты уже заполнены"}
    when = slot_dt(day, target, tz)
    return {**info, "run": True, "slot": target, "slot_date": day.isoformat(),
            "publish_at": when.astimezone(dt.timezone.utc).isoformat(),
            "deadline": window_end(day, target, tz).astimezone(dt.timezone.utc).isoformat(),
            "why": f"готовлю ролик к слоту {target}"}


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
            f.write(f"run={'true' if info['run'] else 'false'}\n")
            for key in ("slot", "slot_date", "publish_at", "deadline"):
                f.write(f"{key}={info.get(key, '')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
