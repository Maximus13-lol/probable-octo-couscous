"""Утренняя рубрика (08:00–09:30 МСК), чередуется по дням:
- «Этот день в истории тайн» — загадочное событие, случившееся в этот день (Wikipedia «On this day»), 25–35 с;
- «Правда или миф: 20 секунд» — популярное утверждение → доказательства → вердикт, ~20 с.

Вечерние слоты — обычные «Дела» (format = "case"). Формат выбирается по слоту из config/formats.json.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import requests

from . import gemini
from .trends import is_used, load_used_topics
from .utils import CONFIG_DIR, env, load_json, log

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
UA = {"User-Agent": "ShortsPipeline/1.0 (educational shorts; https://github.com) python-requests"}
DEFAULTS = {"slots": {"08:00-09:30": "morning"}, "morning_rotation": ["this_day", "myth20"]}

# параметры форматов: длина текста, число сцен, плашка
SPECS = {
    "case": None,  # обычный вечерний ролик — настройки по умолчанию
    "this_day": {"words": (60, 80), "relaxed": (50, 92), "seconds": "25–35", "scenes": (6, 9),
                 "label": "ЭТОТ ДЕНЬ В ИСТОРИИ",
                 "prompt": "Формат «Этот день в истории тайн»: первая фраза — хук-вопрос, сразу за ней дата "
                           "(«Это случилось {date} {year} года» — год словами в правильном падеже). Дальше — что "
                           "произошло и что в этом загадочного до сих пор. Без крючка в середине: ролик короткий."},
    "myth20": {"words": (40, 55), "relaxed": (34, 64), "seconds": "18–22", "scenes": (4, 6),
               "label": "ПРАВДА ИЛИ МИФ",
               "prompt": "Формат «Правда или миф за 20 секунд»: первая фраза — утверждение-вопрос «Правда ли, что…?»; "
                         "затем 2–3 коротких факта-доказательства; затем вердикт одним словом и коротким пояснением "
                         "(«Миф.», «Правда.», «Наполовину правда.»). Без крючка в середине: ролик очень короткий."},
}


def config() -> dict:
    c = dict(DEFAULTS)
    c.update(load_json(CONFIG_DIR / "formats.json", default={}) or {})
    return c


def today_msk() -> dt.date:
    if day := env("SLOT_DATE"):
        return dt.date.fromisoformat(day)
    return dt.datetime.now(ZoneInfo("Europe/Moscow")).date()


def current() -> str:
    """Формат ролика: FORMAT из окружения → по слоту → «case»."""
    forced = env("FORMAT")
    if forced:
        return forced
    kind = config()["slots"].get(env("SLOT") or "", "case")
    if kind == "morning":
        rot = config()["morning_rotation"]
        return rot[today_msk().toordinal() % len(rot)]
    return kind


def spec(fmt: str) -> dict | None:
    return SPECS.get(fmt)


# ------------------------------------------------------------------ темы
def _onthisday(day: dt.date) -> list[dict]:
    url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{day.month:02d}/{day.day:02d}"
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        out = []
        for e in r.json().get("events", []):
            pages = [p.get("titles", {}).get("normalized") or p.get("title") for p in e.get("pages", [])]
            out.append({"year": e.get("year"), "text": e.get("text", ""), "pages": [p for p in pages if p][:3]})
        log.info("«Этот день»: %d событий за %02d.%02d", len(out), day.day, day.month)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("Лента «On this day» недоступна: %s", exc)
        return []


def this_day_topic(exclude: list[str] | None = None) -> dict:
    day = today_msk()
    date_ru = f"{day.day} {MONTHS[day.month - 1]}"
    events = _onthisday(day)
    used = load_used_topics() + [{"topic": t} for t in (exclude or [])]
    if not events:
        raise RuntimeError("нет событий «в этот день» — утренний ролик «Этот день» не собрать")
    listing = "\n".join(f"{i}. {e['year']}: {e['text']} | статьи: {', '.join(e['pages'])}" for i, e in enumerate(events[:80]))
    used_list = "; ".join(u.get("topic", "") for u in used[-150:]) or "нет"

    def validate(obj):
        if not isinstance(obj, dict) or not isinstance(obj.get("index"), int) or not 0 <= obj["index"] < len(events[:80]):
            return "нужен index из списка"
        if is_used(obj.get("topic", ""), used):
            return "эта тема уже была"
        return None

    obj = gemini.generate_json(f"""Канал «Папка №13» — реальные тайны и загадочные случаи. Сегодня {date_ru}.
События, случившиеся в этот день (год: событие | статьи Википедии):
{listing}

Выбери ОДНО событие, в котором есть настоящая загадка: необъяснённое исчезновение, странная находка, нераскрытое
дело, необычное явление, тайна, которую до сих пор обсуждают. Не бери войны, теракты, катастрофы с жертвами,
политику, спорт. Уже использованные темы (нельзя повторять): {used_list}
Верни JSON: {{"index": <номер>, "topic": "<тема по-русски, конкретно>", "topic_en": "<topic in English>",
"wiki_title": "<точное название английской статьи из списка>", "hook": "<хук-вопрос по-русски, до 12 слов>"}}""",
                               temperature=0.4, validate=validate)
    ev = events[obj["index"]]
    return {"topic": obj["topic"], "topic_en": obj.get("topic_en") or ev["text"], "hook": obj.get("hook", ""),
            "source": "onthisday", "source_title": f"{ev['year']}: {ev['text']}",
            "wiki_title": obj.get("wiki_title") or (ev["pages"][0] if ev["pages"] else ""), "wiki_lang": "en",
            "date_ru": date_ru, "year": ev["year"], "metrics": {}, "reason": f"событие {date_ru} {ev['year']} года"}


def myth_topic(exclude: list[str] | None = None) -> dict:
    used = load_used_topics() + [{"topic": t} for t in (exclude or [])]
    used_list = "; ".join(u.get("topic", "") for u in used[-150:]) or "нет"

    def validate(obj):
        if not isinstance(obj, dict) or not obj.get("topic") or not obj.get("wiki_title"):
            return "нужны поля topic и wiki_title"
        if is_used(obj["topic"], used):
            return "эта тема уже была"
        return None

    obj = gemini.generate_json(f"""Канал «Папка №13» — реальные тайны и загадки. Рубрика «Правда или миф за 20 секунд».
Предложи ОДНО популярное утверждение СТРОГО из тематики канала: исторические загадки, легенды, проклятия,
таинственные места и находки, призраки, НЛО, тайные общества, загадочные артефакты (например: «в Бермудском
треугольнике пропадает больше кораблей», «гробницу Тутанхамона охраняло проклятие», «викинги носили рогатые
шлемы»). НЕ бери бытовые факты, животных, еду, здоровье, науку о повседневном. Нужен понятный проверенный ответ
и статья в Википедии. Без медицины, политики, религиозных споров и трагедий с жертвами. Уже были (нельзя повторять): {used_list}
Верни JSON: {{"topic": "<утверждение по-русски>", "topic_en": "<in English>", "wiki_title": "<точное название
английской статьи Википедии>", "verdict": "миф|правда|наполовину", "hook": "<«Правда ли, что …?» до 12 слов>"}}""",
                               temperature=0.9, validate=validate)
    return {"topic": obj["topic"], "topic_en": obj.get("topic_en", obj["topic"]), "hook": obj.get("hook", ""),
            "source": "myth", "source_title": obj["topic"], "wiki_title": obj["wiki_title"], "wiki_lang": "en",
            "verdict": obj.get("verdict", ""), "metrics": {}, "reason": "рубрика «Правда или миф»"}


def topic(fmt: str, exclude: list[str] | None = None) -> dict:
    return this_day_topic(exclude) if fmt == "this_day" else myth_topic(exclude)
