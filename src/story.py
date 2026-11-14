"""story-writer: история 130–160 слов на русском с хуком-вопросом и развязкой."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import facts, gemini, quality
from .demo import DEMO_STORY
from .trends import load_used_topics
from .uniqueness import stems
from .utils import USED_TOPICS_FILE, log, save_json

MIN_WORDS, MAX_WORDS = 130, 160
WORD_RE = re.compile(r"[\wЁё]+(?:[-’'][\wЁё]+)*", re.U)


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


RELAXED_MIN_WORDS, RELAXED_MAX_WORDS = 100, 180


def _validate_relaxed(obj) -> str | None:
    """Допустимый минимум, если Gemini перегружен и идеальный вариант не получается:
    ролик выйдет чуть короче, но выйдет. Цифры/латиницу дочистит vosk_clean."""
    if not isinstance(obj, dict) or not obj.get("story") or not obj.get("title"):
        return "нет полей story/title"
    story = obj["story"].strip()
    n = count_words(story)
    if not RELAXED_MIN_WORDS <= n <= RELAXED_MAX_WORDS:
        return f"{n} слов"
    head = " ".join(story.split()[:28])
    if "?" not in head:
        return "нет вопроса в начале"
    if any(count_words(x) > 26 for x in re.split(r"(?<=[.!?…])\s+", story)):
        return "очень длинное предложение"
    return None


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


def _extras(choice: dict) -> tuple[str, dict]:
    """Доп. требования к истории: справка-факты, рубрика, вывод от лица канала и вопрос зрителю."""
    parts, meta = [], {}
    if quality.on("facts"):
        art = facts.lookup(choice)
        meta["article"] = art
        meta["sources"] = facts.sources(choice, art)
        if art:
            parts.append("СПРАВКА (статья «" + art["title"] + "»). Опирайся на факты из неё: имена, даты, места. "
                         "Не выдумывай деталей, которых нет в справке или в общеизвестных источниках. Если справка "
                         "НЕ про эту тему — не используй её и верни reference_used=false:\n\"\"\"" +
                         art["extract"][:3500] + "\"\"\"")
    rub = quality.rubric()
    if rub:
        meta["rubric"] = rub
        parts.append(f"Формат ролика — рубрика «{rub['label']}»: {rub['prompt']}")
    if quality.on("verdict"):
        meta["verdict"] = True
        parts.append("Концовка обязательна в таком виде: предпоследнее предложение — честный вывод от лица автора "
                     "канала («Я думаю…», «Мне кажется самой правдоподобной…»), с коротким объяснением почему; "
                     "ПОСЛЕДНЕЕ предложение — вопрос зрителю («А вы как думаете…?», «Какая версия ближе вам?»), "
                     "заканчивается знаком «?». Верни этот вопрос также в поле question.")
    return ("\n\n" + "\n\n".join(parts)) if parts else "", meta


def write_story(choice: dict, run_dir: Path) -> dict:
    extra, meta = _extras(choice) if gemini.available() else ("", {})
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
- Используй букву «ё» везде, где она нужна.{extra}

Верни JSON:
{{"title": "<цепляющее название до 60 символов, без хэштегов>",
  "hook": "<первое предложение истории>",
  "story": "<текст истории>",
  "description": "<1-2 предложения для описания видео>",
  "hashtags": ["#тег1", "#тег2", "#тег3", "#тег4", "#тег5"]{', "question": "<вопрос зрителю из конца истории>"' if meta.get("verdict") else ''}{', "reference_used": true' if meta.get("article") else ''}}}"""
        acceptable: list[dict] = []

        def validate(obj):
            problem = _validate(obj)
            if not problem and meta.get("verdict"):
                last = re.split(r"(?<=[.!?…])\s+", obj["story"].strip())[-1]
                if not last.endswith("?"):
                    problem = "последнее предложение должно быть вопросом зрителю и заканчиваться знаком «?»"
            if problem and _validate_relaxed(obj) is None:
                acceptable.append(obj)  # запасной вариант на случай, если идеальный не получится
            return problem

        try:
            data = gemini.generate_json(prompt, temperature=0.9, validate=validate, attempts=4)
        except Exception as exc:  # noqa: BLE001
            if not acceptable:
                raise RuntimeError(f"story-writer: Gemini не написал историю: {exc}") from exc
            data = max(acceptable, key=lambda o: -abs(count_words(o["story"]) - (MIN_WORDS + MAX_WORDS) / 2))
            data.setdefault("description", data.get("hook", ""))
            data.setdefault("hashtags", ["#тайна", "#загадка", "#мистика"])
            log.warning("Идеальная история не получилась (%s) — беру допустимый вариант: %d слов",
                        str(exc)[:80], count_words(data["story"]))
    else:
        log.warning("GEMINI_API_KEY не задан — использую демо-историю")
        data = dict(DEMO_STORY)

    data["story"] = " ".join(data["story"].split())
    if meta.get("sources"):
        src = list(meta["sources"])
        if meta.get("article") and data.get("reference_used") is False:
            src = [x for x in src if not x.startswith("Википедия")]  # справка оказалась не про эту тему
        data["sources"] = src
    if meta.get("article") and data.get("reference_used") is not False:
        a = meta["article"]
        data["article"] = {"title": a["title"], "lang": a["lang"], "url": a["url"]}
    if meta.get("rubric"):
        data["rubric"] = meta["rubric"]["label"]
    if meta.get("verdict") and not data.get("question"):
        last = re.split(r"(?<=[.!?…])\s+", data["story"])[-1]
        data["question"] = last if last.endswith("?") else ""
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
        "summary": story.get("description", "") or story.get("hook", ""),
        "keywords": sorted(stems(story.get("story", ""))),
        "date": dt.date.today().isoformat(),
    })
    save_json(USED_TOPICS_FILE, used)
