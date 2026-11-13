"""Справка по теме из Википедии: история опирается на проверяемые факты, а статья идёт в «Источники»."""
from __future__ import annotations

import requests

from .utils import log

UA = {"User-Agent": "ShortsPipeline/1.0 (educational shorts; https://github.com) python-requests"}
API = "https://{lang}.wikipedia.org/w/api.php"


def _search(lang: str, query: str) -> dict | None:
    r = requests.get(API.format(lang=lang), headers=UA, timeout=20, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": query, "gsrlimit": 1,
        "prop": "extracts|info", "explaintext": 1, "exchars": 4000, "inprop": "url", "redirects": 1})
    r.raise_for_status()
    pages = list((r.json().get("query") or {}).get("pages", {}).values())
    if not pages or not pages[0].get("extract"):
        return None
    p = pages[0]
    return {"title": p["title"], "url": p.get("fullurl", ""), "extract": p["extract"].strip(), "lang": lang}


def lookup(choice: dict) -> dict | None:
    """Статья ru-Википедии по теме, иначе en-Википедии. None — если ничего подходящего."""
    for lang, q in (("ru", choice.get("topic", "")), ("en", choice.get("topic_en", ""))):
        if not q:
            continue
        try:
            art = _search(lang, q)
        except Exception as exc:  # noqa: BLE001
            log.warning("Википедия (%s) недоступна: %s", lang, exc)
            continue
        if art and len(art["extract"]) > 300:
            log.info("Справка: %s (%s-Википедия, %d символов)", art["title"], lang, len(art["extract"]))
            return art
    log.info("Справка из Википедии не найдена — история по общеизвестным фактам")
    return None


def sources(choice: dict, article: dict | None) -> list[str]:
    out = []
    if article:
        out.append(f"Википедия — «{article['title']}»: {article['url']}")
    if choice.get("source") == "reddit" and choice.get("source_url"):
        out.append(f"Обсуждение, с которого началась тема: {choice['source_url']}")
    return out
