"""Минимальный клиент Gemini API (REST, бесплатный ключ из Google AI Studio)."""
from __future__ import annotations

import json
import re
import time

import requests

from .utils import env, log

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Запасной список, если получить перечень моделей не удалось. Google регулярно отключает старые модели
# (так, gemini-2.5-flash к ноябрю 2026 уже отвечает 404), поэтому основной путь — спросить у API.
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]
_DISCOVERED: list[str] | None = None
_DEAD: set[str] = set()
_SLOW: list[str] = []          # модели, которые в этом запуске не ответили вовремя — в конец очереди
CALL_TIMEOUT = (15, int(env("GEMINI_CALL_TIMEOUT", "90")))      # подключение, ожидание ответа (с)
GENERATE_BUDGET = int(env("GEMINI_BUDGET_SECONDS", "360"))       # максимум на один ответ Gemini


class GeminiError(RuntimeError):
    pass


class ModelNotFound(Exception):
    pass


def available() -> bool:
    """Есть ли хоть один поставщик текстов: Gemini (основной) или OpenRouter (запасной)."""
    from . import openrouter
    return bool(env("GEMINI_API_KEY")) or openrouter.available()


_GEMINI_DOWN = False  # Gemini уже не справился в этом запуске — дальше сразу запасной поставщик


def _rank(name: str) -> tuple | None:
    """Ключ сортировки текстовых flash-моделей: стабильные новые выше, затем lite, затем preview."""
    n = name.split("/")[-1]
    if not n.startswith("gemini") or any(x in n for x in ("tts", "image", "live", "audio", "embedding",
                                                           "vision", "thinking", "robotics", "computer", "native")):
        return None
    if "flash" not in n:
        return None
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    ver = float(m.group(1)) if m else (99.0 if "latest" in n else 0.0)
    lite = "lite" in n
    preview = "preview" in n or "exp" in n
    return (preview, lite, -ver, len(n))


def order_models(names: list[str]) -> list[str]:
    """Новейшая стабильная flash → её lite → алиасы latest → прочие стабильные → preview. Не больше 6."""
    ok = [n for n in names if _rank(n)]
    concrete = sorted((n for n in ok if "latest" not in n and "preview" not in n and "exp" not in n), key=_rank)
    flash = [n for n in concrete if "lite" not in n]
    lite = [n for n in concrete if "lite" in n]
    aliases = [n for n in ("gemini-flash-latest", "gemini-flash-lite-latest") if n in ok]
    preview = sorted((n for n in ok if "preview" in n or "exp" in n), key=_rank)
    out: list[str] = []
    for n in flash[:1] + lite[:1] + aliases + flash[1:2] + lite[1:2] + preview[:1]:
        if n not in out:
            out.append(n)
    return out[:6]


def discover_models() -> list[str]:
    global _DISCOVERED  # noqa: PLW0603
    if _DISCOVERED is not None:
        return _DISCOVERED
    try:
        r = requests.get(LIST_URL, params={"key": env("GEMINI_API_KEY"), "pageSize": 1000}, timeout=30)
        r.raise_for_status()
        names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
        _DISCOVERED = order_models(names) or list(FALLBACK_MODELS)
        log.info("Gemini: доступные модели по приоритету: %s", ", ".join(_DISCOVERED))
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось получить список моделей Gemini (%s) — запасной список", exc)
        _DISCOVERED = list(FALLBACK_MODELS)
    return _DISCOVERED


def _models() -> list[str]:
    custom = env("GEMINI_MODEL")
    models = discover_models()
    if custom:
        models = [custom] + [m for m in models if m != custom]
    alive = [m for m in models if m not in _DEAD]
    return [m for m in alive if m not in _SLOW] + [m for m in _SLOW if m in alive]


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
    resp = requests.post(API_URL.format(model=model), params={"key": key}, json=body, timeout=CALL_TIMEOUT)
    if resp.status_code == 404:
        raise ModelNotFound(model)
    if resp.status_code in (429, 500, 502, 503, 504):
        raise GeminiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    if resp.status_code != 200:
        raise ModelNotFound(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    except (KeyError, IndexError) as exc:
        raise GeminiError(f"Пустой ответ Gemini: {json.dumps(data)[:300]}") from exc


def generate(prompt: str, temperature: float = 0.9, as_json: bool = False) -> str:
    """Текст от LLM: сначала Gemini, при перегрузке/ошибке — бесплатные модели OpenRouter."""
    global _GEMINI_DOWN  # noqa: PLW0603
    from . import openrouter
    fallback = openrouter.available()
    if env("GEMINI_API_KEY") and not _GEMINI_DOWN:
        try:
            return _generate_gemini(prompt, temperature, as_json,
                                    budget=int(env("GEMINI_BUDGET_WITH_FALLBACK", "150")) if fallback else None)
        except GeminiError as exc:
            if not fallback:
                raise
            _GEMINI_DOWN = True
            log.warning("Gemini недоступен (%s) — до конца запуска пишу тексты через OpenRouter", str(exc)[:120])
    if not fallback:
        raise GeminiError("нет ни GEMINI_API_KEY, ни OPENROUTER_API_KEY")
    try:
        return openrouter.generate(prompt, temperature, as_json)
    except openrouter.OpenRouterError as exc:
        raise GeminiError(f"запасной OpenRouter тоже не ответил: {exc}") from exc


def _generate_gemini(prompt: str, temperature: float, as_json: bool, budget: int | None = None) -> str:
    """Пробует модели по очереди с общим лимитом времени GENERATE_BUDGET:
    404 → модель исключается; таймаут → модель уходит в конец очереди, сразу следующая;
    429/5xx → следующая модель, затем второй круг после паузы."""
    budget = budget or GENERATE_BUDGET
    deadline = time.monotonic() + budget
    last: Exception | None = None
    for _round in range(3):
        for model in _models():
            if time.monotonic() > deadline:
                raise GeminiError(f"Gemini не ответил за {budget} с (последняя ошибка: {last})")
            try:
                text = _call(model, prompt, temperature, as_json)
                log.debug("Gemini %s ответил %d символов", model, len(text))
                return text
            except ModelNotFound as exc:
                log.warning("Gemini: модель %s недоступна — исключаю", exc)
                _DEAD.add(model)
                last = exc
            except requests.Timeout as exc:
                log.warning("Gemini %s не ответил за %d с — перехожу к следующей модели", model, CALL_TIMEOUT[1])
                if model not in _SLOW:
                    _SLOW.append(model)
                last = exc
            except (requests.RequestException, GeminiError) as exc:
                log.warning("Gemini %s перегружен (%s) — пробую следующую модель", model, str(exc)[:120])
                last = exc
        pause = min(20 * (_round + 1), max(0, deadline - time.monotonic()))
        if pause > 0:
            time.sleep(pause)
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
