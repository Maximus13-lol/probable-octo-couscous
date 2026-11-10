"""storyboard: разбивка истории на 8–12 сцен (текст + английский промпт картинки)."""
from __future__ import annotations

import re
from pathlib import Path

from . import gemini
from .demo import DEMO_STORY, DEMO_STORYBOARD
from .utils import log, save_json

MIN_SCENES, MAX_SCENES = 8, 12


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s.lower())


def _validate_for(story: str):
    def validate(obj) -> str | None:
        if not isinstance(obj, dict) or not isinstance(obj.get("scenes"), list):
            return "нужен объект с полем scenes"
        scenes = obj["scenes"]
        if not MIN_SCENES <= len(scenes) <= MAX_SCENES:
            return f"сцен {len(scenes)}, нужно {MIN_SCENES}–{MAX_SCENES}"
        for i, s in enumerate(scenes):
            if not s.get("text") or not s.get("prompt"):
                return f"у сцены {i} нет text или prompt"
            if re.search(r"[а-яё]", s["prompt"], re.I):
                return f"промпт сцены {i} должен быть на английском"
        if _norm("".join(s["text"] for s in scenes)) != _norm(story):
            return ("тексты сцен, склеенные подряд, должны дословно совпадать с историей — "
                    "ничего не добавляй, не удаляй и не переставляй")
        return None
    return validate


def _fallback(story: str, topic_en: str) -> dict:
    """Без Gemini: режем по предложениям на 8–12 сцен, промпты — общие по теме."""
    sents = [s for s in re.split(r"(?<=[.!?…»])\s+", story) if s.strip()]
    n = max(MIN_SCENES, min(MAX_SCENES, len(sents)))
    # если предложений больше, чем сцен, — склеиваем соседние
    groups: list[list[str]] = [[] for _ in range(min(n, len(sents)))]
    for i, s in enumerate(sents):
        groups[min(len(groups) - 1, i * len(groups) // len(sents))].append(s)
    shots = ["wide establishing shot", "dramatic close-up", "mysterious silhouette", "detailed interior",
             "eerie landscape", "old documents and photographs", "night scene", "dawn light"]
    scenes = [{"text": " ".join(g), "prompt": f"{shots[i % len(shots)]} illustrating {topic_en}"}
              for i, g in enumerate(groups)]
    return {"characters": [], "scenes": scenes}


def make_storyboard(story: dict, topic_en: str, run_dir: Path) -> dict:
    text = story["story"]
    if text == DEMO_STORY["story"]:
        board = DEMO_STORYBOARD
    elif gemini.available():
        prompt = f"""Ты раскадровщик вертикальных роликов. Разбей историю на {MIN_SCENES}–{MAX_SCENES} сцен.
История (на русском):
\"\"\"{text}\"\"\"

Правила:
- text каждой сцены — это ДОСЛОВНЫЙ последовательный фрагмент истории (1–2 предложения).
  Все text подряд должны дать исходную историю целиком, без изменений.
- Первая сцена — только хук-вопрос.
- prompt — описание картинки на английском (15–35 слов): что в кадре, ракурс, свет, эпоха, место.
  Без текста и надписей на картинке. Без кровавых и жестоких деталей.
- Если в истории есть повторяющиеся персонажи, опиши их один раз в characters
  (name — одно английское слово, description — внешность, одежда, возраст),
  и в prompt упоминай их по этому name, чтобы они выглядели одинаково во всех сценах.

Верни JSON:
{{"characters": [{{"name": "keeper", "description": "..."}}],
  "scenes": [{{"text": "...", "prompt": "..."}}]}}"""
        try:
            board = gemini.generate_json(prompt, temperature=0.6, validate=_validate_for(text), attempts=4)
        except Exception as exc:  # noqa: BLE001
            log.warning("storyboard: Gemini не справился (%s) — запасная разбивка", exc)
            board = _fallback(text, topic_en)
    else:
        board = _fallback(text, topic_en)

    for i, s in enumerate(board["scenes"]):
        s["index"] = i
        s["text"] = " ".join(s["text"].split())
    save_json(run_dir / "storyboard.json", board)
    log.info("Раскадровка: %d сцен", len(board["scenes"]))
    return board
