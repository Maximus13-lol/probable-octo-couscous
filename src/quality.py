"""Улучшения «не нейро-шаблон»: факты из Википедии, вывод и вопрос зрителю, рубрики, архивные фото,
оформление канала. Включаются в config/quality.json или для пробного ролика (PILOT=true)."""
from __future__ import annotations

import re

from .utils import CONFIG_DIR, PUBLISH_LOG_FILE, USED_TOPICS_FILE, env, load_json

DEFAULT_RUBRICS = [
    {"id": "mystery", "label": "ТАЙНА",
     "prompt": "Классическая история-загадка: хук, нарастание напряжения, развязка."},
    {"id": "versions", "label": "ТРИ ВЕРСИИ",
     "prompt": "После короткой завязки изложи три версии случившегося, по одному-два предложения, начиная словами "
               "«Первая версия», «Вторая версия», «Третья версия»."},
    {"id": "myth", "label": "ПРАВДА ИЛИ МИФ",
     "prompt": "Хук — вопрос «правда ли, что…». Раздели, что подтверждено документами и свидетелями, "
               "а что — легенды и домыслы. В выводе вердикт: правда, миф или наполовину."},
]


def config() -> dict:
    c = load_json(CONFIG_DIR / "quality.json", default={}) or {}
    c.setdefault("enabled", False)
    c.setdefault("features", {})
    c.setdefault("rubrics", DEFAULT_RUBRICS)
    c.setdefault("branding", {})
    return c


def pilot() -> bool:
    return (env("PILOT") or "").lower() == "true"


def on(feature: str) -> bool:
    c = config()
    if not (c["enabled"] or pilot()):
        return False
    return bool(c["features"].get(feature, True))


def rubric() -> dict | None:
    """Рубрика для следующего ролика — по кругу, чтобы ролики не шли одним шаблоном."""
    if not on("rubrics"):
        return None
    rubrics = config()["rubrics"] or DEFAULT_RUBRICS
    forced = env("RUBRIC")
    if forced:
        for r in rubrics:
            if r["id"] == forced:
                return r
    n = len(load_json(USED_TOPICS_FILE, default=[]) or [])
    return rubrics[n % len(rubrics)]


_CHANNEL: str | None = None


def channel_name() -> str:
    """Название канала для водяного знака: config/quality.json → branding.channel_name, иначе из YouTube API."""
    global _CHANNEL  # noqa: PLW0603
    if _CHANNEL is not None:
        return _CHANNEL
    name = (config()["branding"].get("channel_name") or "").strip()
    if not name:
        try:
            from .upload import get_youtube_client
            yt = get_youtube_client()
            if yt:
                name = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        except Exception:  # noqa: BLE001
            name = ""
    _CHANNEL = name
    return name


def overlays(story: dict) -> dict | None:
    """Что рисовать поверх ролика: водяной знак канала и плашку рубрики в первые секунды."""
    if not on("branding"):
        return None
    label = story.get("rubric") or ""
    if story.get("case"):
        label = f"ДЕЛО №{story['case']}" + (f" · {label}" if label else "")
    return {"channel": channel_name(), "rubric": label}


def case_number() -> int | None:
    """Номер «дела» для сериала «Папка №13»: последний выданный номер + 1 (нумерация с 1, с запуска сериала)."""
    if not on("series"):
        return None
    log = load_json(PUBLISH_LOG_FILE, default=[]) or []
    return max((int(e["case"]) for e in log if e.get("case") and not e.get("deleted")), default=0) + 1


_CHILD = re.compile(r"\b(реб[её]н|дет(и|ей|ям|ск)|девочк|мальчик|школьни|подрост|младен|малыш|child|girl|boy|kid|teen)", re.I)
_ADULT = re.compile(r"(муж|жен[аыуе]\b|любов|роман|свадьб|секс|интим|beloved|husband|wife|marri|romanc|sex)", re.I)
_HARM = re.compile(r"(убий|убит|убил|погиб|насил|изнасил|пыт|самоуб|расчлен|каннибал|теракт|казн|murder|kill|rape|abuse|suicide|tortur)", re.I)


def unsafe_topic(text: str) -> str | None:
    """Сюжеты, которые YouTube режет почти до нуля показов: дети во «взрослом» контексте или рядом с насилием,
    подробности убийств и насилия. Возвращает причину или None."""
    t = text or ""
    if _CHILD.search(t) and (_ADULT.search(t) or _HARM.search(t)):
        return "ребёнок во «взрослом» контексте или рядом с насилием"
    if len(_HARM.findall(t)) >= 3:
        return "много насилия и смертей в тексте"
    return None
