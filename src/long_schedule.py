"""План выхода длинных выпусков. Команда печатает расписание и не обращается к YouTube."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def _stable_index(key: str, size: int) -> int:
    return int.from_bytes(hashlib.sha256(key.encode()).digest(), "big") % size


def plan(start: dt.date, count: int) -> list[dict]:
    """Ежедневный старт на семь дней, затем интервалы в два или три дня."""
    if count < 1:
        raise ValueError("Нужен хотя бы один выпуск")
    result = []
    day = start
    previous_tick = None
    for number in range(1, count + 1):
        key = f"{start.isoformat()}|{number}"
        if number > 1:
            interval = 1 if number <= 7 else 2 + _stable_index(f"{key}|gap", 2)
            day += dt.timedelta(days=interval)
        tick = _stable_index(f"{key}|time", 19)
        if tick == previous_tick:
            tick = (tick + 1) % 19
        previous_tick = tick
        when = dt.datetime.combine(day, dt.time(15, 10), MOSCOW) + dt.timedelta(minutes=tick * 5)
        result.append({"episode": number, "local_time": when.isoformat(),
                       "publish_at": when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=dt.date.fromisoformat, help="Дата первого длинного выпуска: ГГГГ-ММ-ДД")
    parser.add_argument("--count", type=int, default=14)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count должен быть положительным")
    print(json.dumps(plan(args.start, args.count), indent=2))


if __name__ == "__main__":
    main()
