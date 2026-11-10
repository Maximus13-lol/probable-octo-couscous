"""story-writer: история 130–160 слов на русском с хуком-вопросом и развязкой."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import gemini
from .demo import DEMO_STORY
from .trends import load_used_topics
from .utils import USED_TOPICS_FILE, log, save_json

MIN_WORDS, MAX_WORDS = 130, 160
WORD_RE = re.compile(r"[\wЁё]+(?:[-’'][\wЁё]+)*", re.U)


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _validate(obj) -> str | None:
    if not isinstance(obj, dict):
        return "ожидался объект"
    for key in ("title", "story", "description", "hashtags"):
        if not obj.get(key):
            return f"нет поля {key}"
    story = obj["story"].strip()
    n = count_words(story)
    if not MIN_WORDS <= n <= MAX_WORDS:
        return f"в истории {n} слов, нужно {MIN_WORDS}–{MAX_WORDS}"
    first = re.split(r"(?<=[.!?…])\s+", story, maxsplit=1)[0]
    if not first.endswith("?"):
        return "первое предложение должно быть хуком-вопросом и заканчиваться знаком «?»"
    if re.search(r"\d", story):
        return "в тексте есть цифры — напишите все числа и годы словами в правильном падеже"
    latin = re.findall(r"[A-Za-z]{2,}", story)
    if latin:
        return f"уберите латиницу (диктор её не прочтёт): {', '.join(sorted(set(latin))[:5])}"
    abbr = re.findall(r"\b[А-ЯЁA-Z]{2,}\b", story)
    if abbr:
        return f"уберите аббревиатуры (их плохо читает диктор): {', '.join(sorted(set(abbr)))}"
    if re.search(r"\b(т\.\s?е|т\.\s?д|т\.\s?п|др|г|гг|км|см)\.", story):
        return "уберите сокращения (т. е., г., км и т. п.) — пишите словами"
    long_sent = [s for s in re.split(r"(?<=[.!?…])\s+", story) if count_words(s) > 18]
    if long_sent:
        return f"слишком длинное предложение для слуха (>18 слов): «{long_sent[0][:60]}…»"
    if count_words(first) > 16:
        return "хук-вопрос слишком длинный (максимум 16 слов, он должен звучать за 3 секунды)"
    return None


def write_story(choice: dict, run_dir: Path) -> dict:
    if gemini.available():
        prompt = f"""Напиши сценарий для YouTube Shorts (60 секунд озвучки) на русском языке.
Тема: {choice['topic']}
{('Идея хука: ' + choice['hook']) if choice.get('hook') else ''}

Требования к истории (поле story):
- {MIN_WORDS}–{MAX_WORDS} слов, ровно один сплошной текст без заголовков и ремарок.
- Первое предложение — короткий хук-вопрос (до 12 слов), загадка, которая цепляет за 3 секунды.
- Дальше — напряжённое повествование короткими предложениями, конкретные детали, даты, места.
- В конце — развязка: ответ или самая правдоподобная версия, плюс последняя цепляющая фраза.
- Только факты и общепринятые версии, без выдумок о реальных людях; без жестокости.
- ВСЕ числа, включая годы и даты, пиши словами в правильном падеже («в тысяча девятисотом году»):
  диктор не умеет читать цифры. Без эмодзи, без хэштегов в тексте. Только кириллица, без латиницы.
- Пиши для слуха: короткие фразы (до 12–14 слов), простые слова, без причастных и деепричастных оборотов,
  без скобок, кавычек-цитат на полстраницы, аббревиатур и сокращений (не «США», а «Америка»; не «т. е.»).
- Используй букву «ё» везде, где она нужна.

Верни JSON:
{{"title": "<цепляющее название до 60 символов, без хэштегов>",
  "hook": "<первое предложение истории>",
  "story": "<текст истории>",
  "description": "<1-2 предложения для описания видео>",
  "hashtags": ["#тег1", "#тег2", "#тег3", "#тег4", "#тег5"]}}"""
        try:
            data = gemini.generate_json(prompt, temperature=0.9, validate=_validate, attempts=4)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"story-writer: Gemini не написал историю: {exc}") from exc
    else:
        log.warning("GEMINI_API_KEY не задан — использую демо-историю")
        data = dict(DEMO_STORY)

    data["story"] = " ".join(data["story"].split())
    data["topic"] = choice["topic"]
    data["word_count"] = count_words(data["story"])
    save_json(run_dir / "story.json", data)
    log.info("История: «%s», %d слов", data["title"], data["word_count"])
    return data


def mark_topic_used(choice: dict, story: dict) -> None:
    used = load_used_topics()
    used.append({
        "topic": choice["topic"],
        "topic_en": choice.get("topic_en", ""),
        "source_title": choice.get("source_title", ""),
        "title": story.get("title", ""),
        "date": dt.date.today().isoformat(),
    })
    save_json(USED_TOPICS_FILE, used)
