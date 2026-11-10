"""Минимальный клиент Gemini API (REST, бесплатный ключ из Google AI Studio)."""
from __future__ import annotations

import json
import re

import requests

from .utils import env, log, retry

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]


class GeminiError(RuntimeError):
    pass


def available() -> bool:
    return bool(env("GEMINI_API_KEY"))


def _models() -> list[str]:
    custom = env("GEMINI_MODEL")
    return [custom] + [m for m in DEFAULT_MODELS if m != custom] if custom else list(DEFAULT_MODELS)


@retry(tries=4, base_delay=4, exceptions=(requests.RequestException, GeminiError), label="gemini")
def _call(model: str, prompt: str, temperature: float, as_json: bool) -> str:
    key = env("GEMINI_API_KEY")
    if not key:
        raise GeminiError("GEMINI_API_KEY не задан")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    resp = requests.post(API_URL.format(model=model), params={"key": key}, json=body, timeout=120)
    if resp.status_code == 404:
        # модели нет — пусть верхний уровень попробует следующую, без повторов
        raise ModelNotFound(model)
    if resp.status_code in (429, 500, 502, 503, 504):
        raise GeminiError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code != 200:
        raise ModelNotFound(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as exc:
        raise GeminiError(f"Пустой ответ Gemini: {json.dumps(data)[:300]}") from exc


class ModelNotFound(Exception):
    pass


def generate(prompt: str, temperature: float = 0.9, as_json: bool = False) -> str:
    last: Exception | None = None
    for model in _models():
        try:
            text = _call(model, prompt, temperature, as_json)
            log.debug("Gemini %s ответил %d символов", model, len(text))
            return text
        except ModelNotFound as exc:
            log.warning("Gemini: модель недоступна (%s), пробую следующую", exc)
            last = exc
    raise GeminiError(f"Ни одна модель Gemini не ответила: {last}")


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def generate_json(prompt: str, temperature: float = 0.8, validate=None, attempts: int = 3):
    """Запрашивает JSON; validate(obj) должен вернуть None или текст ошибки (тогда повтор)."""
    feedback = ""
    for i in range(1, attempts + 1):
        raw = generate(prompt + feedback, temperature=temperature, as_json=True)
        try:
            obj = _extract_json(raw)
        except json.JSONDecodeError as exc:
            log.warning("Gemini вернул невалидный JSON (попытка %d): %s", i, exc)
            feedback = "\n\nВАЖНО: верни строго валидный JSON без пояснений."
            continue
        problem = validate(obj) if validate else None
        if not problem:
            return obj
        log.warning("Ответ Gemini не прошёл проверку (попытка %d): %s", i, problem)
        feedback = f"\n\nПредыдущий ответ отклонён: {problem}. Исправь и верни JSON заново."
    raise GeminiError("Gemini не дал корректный ответ за отведённые попытки")
