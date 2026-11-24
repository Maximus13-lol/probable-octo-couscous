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
    def validate(obj):
        if not isinstance(obj, dict):
            return "верни объект JSON"
        for key, limit in (("title", 95), ("headline", 28)):
            text = obj.get(key)
            if not isinstance(text, str) or not 4 <= len(text.strip()) <= limit:
                return f"нужен {key} длиной не более {limit} символов"
        chapters = obj.get("chapters")
        if not isinstance(chapters, list) or len(chapters) != 8:
            return "нужны восемь глав, по две на каждый раздел"
        for chapter, section in zip(chapters, [s for s in SECTION_ORDER for _ in range(2)], strict=True):
            if not isinstance(chapter, dict) or chapter.get("section") != section:
                return "в каждой главе укажи section из задания и соблюдай порядок"
            text = chapter.get("text")
            if not isinstance(text, str) or not 200 <= len(text.split()) <= 330:
                return "в каждой главе должно быть от 200 до 330 слов связного текста"
        if not 1840 <= sum(len(c["text"].split()) for c in chapters) <= 2200:
            return "в восьми главах вместе должно быть 1840–2200 слов"
        body = " ".join([obj["title"], obj["headline"], *(c["text"] for c in chapters)])
        if quality.unsafe_topic(body):
            return "исключи графичные описания и небезопасные подробности"
        try:
            validate_editorial(obj, require_sections=True)
        except ValueError as exc:
            return str(exc)
        return None

    return gemini.generate_json(f"""Напиши документальный сценарий на русском по источнику ниже.
Используй только факты из источника, явно отделяй гипотезы от фактов. Пересказывай своими словами.
Без приветствия, представления канала, призывов подписаться и повторов. Начни прямо с события.
Восемь глав по 235–265 слов, по две на каждый раздел: context, event, investigation, outcome.
Каждая глава раскрывает новые подробности. Числа и даты пиши словами в нужном падеже.
Сократи текст обложки до трёх коротких слов и 28 символов. Заголовок видео — до 95 символов.
Формат: {{"title": "...", "headline": "...", "chapters": [{{"section":"context","text":"..."}}]}}.
Источник: {article['title']}
<source>
{article['extract']}
</source>""", temperature=0.35, validate=validate, attempts=4)


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
