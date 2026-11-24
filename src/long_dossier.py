"""Подготовка документального выпуска по статье и материалам Wikimedia Commons."""
from __future__ import annotations

import html
import io
import json
import re
from pathlib import Path

import requests
from PIL import Image, ImageOps

from . import archive, facts, gemini, quality
from .long_pilot import SECTION_ORDER, validate_editorial
from .utils import log, retry

# Стартовые темы для канала. Когда они исчерпаны, поиск продолжает Википедия.
TOPICS = (
    "Antikythera mechanism", "Voynich manuscript", "Mary Celeste", "Vasa (ship)",
    "Sutton Hoo", "Terracotta Army", "Tunguska event", "Franklin's lost expedition",
    "Rosetta Stone", "Dead Sea Scrolls", "Dancing plague of 1518", "Oak Island mystery",
    "Phaistos Disc", "Roman dodecahedron", "Nazca lines", "Roanoke Colony",
)
SEARCHES = ("archaeological discoveries", "undeciphered manuscripts", "maritime historical mysteries",
            "unsolved historical events", "historical expeditions", "archaeological hoards")


@retry(tries=3, exceptions=(requests.RequestException,), label="Википедия")
def wiki(**params) -> dict:
    response = requests.get(facts.API.format(lang="en"), headers=facts.UA, timeout=35,
                            params={"action": "query", "format": "json", "formatversion": 2, **params})
    response.raise_for_status()
    return response.json()["query"]


def articles(used: set[str]):
    for title in TOPICS:
        if title not in used:
            yield title
    for search in SEARCHES:
        query = wiki(list="search", srsearch=search, srlimit=50)
        for item in query.get("search", []):
            if item["title"] not in used and not facts.OFF_TOPIC.search(item["title"]):
                yield item["title"]


def script(article: dict) -> dict:
    source = f"Источник: {article['title']}\n<source>\n{article['extract']}\n</source>"
    chapters = []
    section_tasks = {
        "context": ("Начни с конкретного эпизода и обозначь главную загадку.",
                    "Раскрой необходимый исторический контекст."),
        "event": ("Изложи, что произошло, в хронологическом порядке.",
                  "Покажи важные детали события и первые последствия."),
        "investigation": ("Опиши, какие свидетельства изучили и что они подтвердили.",
                          "Разбери версии и объясни, какие вопросы остались открытыми."),
        "outcome": ("Расскажи об установленных итогах и дальнейшей судьбе дела.",
                    "Заверши рассказ, отделив известное от нерешённого."),
    }

    def validate_chapter(obj):
        text = obj.get("text") if isinstance(obj, dict) else None
        count = len(text.split()) if isinstance(text, str) else 0
        if not minimum <= count <= maximum:
            return f"сейчас {count} слов, допустимо {minimum}–{maximum}"
        if quality.unsafe_topic(text):
            return "исключи графичные описания и небезопасные подробности"
        try:
            validate_editorial({"chapters": [{"text": c["text"]} for c in chapters] + [{"text": text}]})
        except ValueError as exc:
            return str(exc)
        return None

    for section in SECTION_ORDER:
        for task in section_tasks[section]:
            remaining = 7 - len(chapters)
            written = sum(len(chapter["text"].split()) for chapter in chapters)
            minimum = max(200, 1840 - written - 330 * remaining)
            maximum = min(330, 2200 - written - 200 * remaining)
            target = min(max(250, minimum), maximum)
            previous = "\n\n".join(chapter["text"] for chapter in chapters)
            chapter = gemini.generate_json(f"""Напиши только главу {len(chapters) + 1} из 8.
{task} Ориентир — {target} слов, допустимо {minimum}–{maximum}. Верни объект {{"text": "..."}}.
Документальный рассказ на русском. Используй только факты источника, пересказывай своими словами.
Отделяй версии от фактов. Числа и даты пиши словами. Без приветствия, анонсов и призывов.
Продолжай предыдущие главы, раскрывая новые детали без повтора.
{source}
<previous_chapters>
{previous}
</previous_chapters>""", temperature=0.35, validate=validate_chapter, attempts=4)
            chapters.append({"section": section, "text": chapter["text"].strip()})

    def validate_titles(obj):
        if not isinstance(obj, dict):
            return "нужен объект JSON"
        for key, limit in (("title", 95), ("headline", 28)):
            value = obj.get(key)
            if not isinstance(value, str) or not 4 <= len(value.strip()) <= limit:
                return f"нужен {key} длиной не более {limit} символов"
        if quality.unsafe_topic(f"{obj['title']} {obj['headline']}"):
            return "исключи графичные описания и небезопасные подробности"
        return None

    titles = gemini.generate_json(f"""Придумай русское название документального видео по источнику.
Верни объект {{"title": "до 95 символов", "headline": "до 28 символов, максимум три слова"}}.
Без домыслов и графичных деталей.
{source}""", temperature=0.35, validate=validate_titles, attempts=4)
    dossier = {"title": titles["title"].strip(), "headline": titles["headline"].strip(),
               "chapters": chapters}
    validate_editorial(dossier, require_sections=True)
    return dossier


def visuals(article: dict, folder: Path) -> list[dict]:
    candidates = archive.from_article(article)
    result, seen = [], set()
    for item in candidates:
        if item["file"] in seen or not item["page"].startswith("https://commons.wikimedia.org/"):
            continue
        if not item["url"] or not item["url"].startswith("https://upload.wikimedia.org/"):
            continue
        # The automated edit reuses images in a montage. Use public domain, CC0 or CC BY.
        if not re.fullmatch(r"(?:public domain(?: mark)?|pd|cc0(?:[ -]1.0)?|cc[ -]by[ -]\d\.\d)",
                            item["license"], re.I):
            continue
        filename = f"archive-{len(result) + 1:02d}.jpg"
        try:
            response = requests.get(item["url"], headers=archive.UA, timeout=25)
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((1600, 1000))
                image.save(folder / filename, quality=90)
        except (requests.RequestException, OSError) as exc:
            log.warning("Кадр Commons пропущен: %s", exc)
            continue
        seen.add(item["file"])
        result.append({"image": filename, "credit": html.unescape(item["artist"]),
                       "license": item["license"], "source_url": item["page"]})
        if len(result) == 20:
            break
    return result


def prepare(folder: Path, used: set[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "dossier.json"
    if path.is_file():
        return path
    seen = set(used)
    for title in articles(used):
        pages = wiki(titles=title, redirects=1, prop="extracts|info|pageprops", ppprop="disambiguation",
                     explaintext=1, inprop="url").get("pages", [])
        if not pages:
            continue
        page = pages[0]
        canonical = page["title"]
        if canonical in seen or "disambiguation" in page.get("pageprops", {}) or len(page.get("extract", "")) < 7000:
            continue
        seen.add(canonical)
        article = {"title": canonical, "url": page["fullurl"], "extract": page["extract"][:26000], "lang": "en"}
        frames = visuals(article, folder)
        if len(frames) < 3:
            continue
        log.info("Длинный выпуск: %s, кадров Commons: %s", canonical, len(frames))
        dossier = script(article)
        dossier["topic"] = canonical
        dossier["references"] = {"Статья": article["url"]}
        # Spread frames across chapters. Reuse them when the article has fewer frames than chapters.
        for index, chapter in enumerate(dossier["chapters"]):
            chapter.update(frames[index % len(frames)])
            chapter["visuals"] = frames[index + 8::8]
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return path
    raise RuntimeError("Не найден новый материал с достаточным числом кадров Wikimedia Commons")
