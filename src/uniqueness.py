"""Уникальность роликов: тематика общая (тайны), но истории не должны повторяться и быть похожими.

Три уровня защиты:
  1. Текстовое сходство темы со списком used_topics.json (trends.is_used).
  2. Смысловая проверка Gemini: не тот же ли это случай / явление / человек, что в прошлых роликах.
  3. Сходство готовой истории с прошлыми по ключевым словам (коэффициент Жаккара по основам слов).
"""
from __future__ import annotations

import re

from . import gemini
from .utils import log

# Частые слова любой мистической истории — не делают истории похожими
STOP = set("""
который которая которое которые этого этому этот эта это эти того тому тот та те такой такая такое такие
когда тогда потом после перед через почти совсем очень может могут могли можно нельзя только также тоже
всего всех всем всеми всегда никто ничего никогда нигде никак сразу снова опять вдруг будто словно
человек люди людей года годах годы время время времени день дней ночью ночь утром вечером место месте
история истории тайна тайны загадка загадки загадку необъяснимо странно странный странная странные
нашли найти найдено исчез исчезли исчезла исчезновение пропал пропали пропала остался остались осталась
сегодня спустя позже однажды никого ответ ответа версия версии правда случилось произошло было были была
своей своих своим свой своего самый самая самое самые один одна одно двух трое троих тысяча""".split())


def stems(text: str) -> set[str]:
    words = re.findall(r"[а-яёa-z]+", text.lower().replace("ё", "е"))
    return {w[:6] for w in words if len(w) >= 5 and w not in STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


SIMILARITY_THRESHOLD = 0.14  # калибровка: разные загадки ≤0.08, пересказ той же истории ≈0.19


def similar_story(story_text: str, used: list[dict], threshold: float = SIMILARITY_THRESHOLD):
    """Возвращает (запись, коэффициент) самой похожей прошлой истории, если сходство выше порога."""
    mine = stems(story_text)
    best, best_score = None, 0.0
    for item in used:
        kw = set(item.get("keywords") or [])
        if not kw:
            continue
        score = jaccard(mine, kw)
        if score > best_score:
            best, best_score = item, score
    if best is not None and best_score >= threshold:
        return best, round(best_score, 3)
    return None


def _past_list(used: list[dict], limit: int = 250) -> str:
    lines = []
    for i, u in enumerate(used[-limit:]):
        summary = u.get("summary") or u.get("title") or ""
        lines.append(f"{i}. {u.get('topic', '')} — {summary[:140]}")
    return "\n".join(lines)


def gemini_duplicate(topic: str, hook: str, used: list[dict]) -> str | None:
    """Gemini: тот же ли это случай/явление/человек, что в одном из прошлых роликов. Возвращает его тему или None."""
    if not used or not gemini.available():
        return None
    past = used[-250:]
    prompt = f"""Канал публикует короткие истории о тайнах. Темы могут быть из одной области, но один и тот же
случай, явление, место, человек или артефакт нельзя показывать дважды — даже под другим названием или с другой стороны.

Новая тема: «{topic}». {('Хук: ' + hook) if hook else ''}

Прошлые ролики:
{_past_list(past)}

Совпадает ли новая тема по сути с каким-то прошлым роликом (тот же случай / явление / объект / человек)?
Верни JSON: {{"duplicate": true/false, "index": <номер прошлого ролика или null>, "reason": "<кратко>"}}"""
    try:
        res = gemini.generate_json(prompt, temperature=0.0, attempts=2,
                                   validate=lambda o: None if isinstance(o, dict) and "duplicate" in o
                                   else "нужно поле duplicate")
    except Exception as exc:  # noqa: BLE001
        log.warning("Смысловая проверка на повтор не удалась (%s) — полагаюсь на текстовую", exc)
        return None
    if res.get("duplicate"):
        idx = res.get("index")
        ref = past[idx]["topic"] if isinstance(idx, int) and 0 <= idx < len(past) else "один из прошлых роликов"
        log.info("Gemini: тема «%s» повторяет «%s» (%s)", topic, ref, res.get("reason", ""))
        return ref
    return None


def gemini_fresh_topic(used: list[dict], exclude: list[str]) -> dict | None:
    """Если тренды не дали подходящей темы — Gemini предлагает новую реальную загадку, которой ещё не было."""
    if not gemini.available():
        return None
    banned = "; ".join([u.get("topic", "") for u in used[-250:]] + exclude) or "нет"
    prompt = f"""Предложи ОДНУ реальную загадочную историю для 60-секундного ролика на русском:
тайны, необъяснимые случаи, исчезновения, странные находки, артефакты, природные аномалии.
Нельзя (уже были или отклонены): {banned}
Не бери тот же случай под другим названием. Без жестокости, политики и реальных жертв-детей.
Верни JSON: {{"topic": "<тема по-русски, конкретно>", "topic_en": "<in English>",
"hook": "<хук-вопрос до 12 слов>", "reason": "<почему зацепит>"}}"""
    try:
        res = gemini.generate_json(prompt, temperature=1.0, attempts=3,
                                   validate=lambda o: None if isinstance(o, dict) and o.get("topic") else "нет topic")
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini не предложил свежую тему: %s", exc)
        return None
    return {"topic": res["topic"], "topic_en": res.get("topic_en", res["topic"]), "hook": res.get("hook", ""),
            "source": "gemini_fresh", "metrics": {},
            "reason": "свежая тема от Gemini (из трендов не нашлось новой) — " + res.get("reason", "")}
