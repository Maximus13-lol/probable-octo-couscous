"""Запасной поставщик текстов: бесплатные модели OpenRouter (`:free`).

Включается автоматически, если Gemini перегружен или не отвечает. Ключ — секрет OPENROUTER_API_KEY
(регистрация на openrouter.ai, карта не нужна). Лимиты бесплатных моделей: 20 запросов в минуту,
50 в день без пополнения баланса — запасному пути этого хватает.

Набор бесплатных моделей меняется, поэтому список берётся у OpenRouter при каждом запуске,
а модели сортируются по приоритету (крупные универсальные — выше, крошечные и «кодовые» — исключены).
"""
from __future__ import annotations

import re
import time

import requests

from .utils import env, log

API = "https://openrouter.ai/api/v1"
# Предпочтения по подстроке id (выше — лучше для русских художественных текстов)
PREFERRED = [
    "deepseek", "qwen3", "gemma-4-31b", "nemotron-3-ultra", "ernie-5.5", "llama-4", "mistral",
    "nemotron-3.5-super", "gemma-4-26b", "glm", "kimi",
]
EXCLUDE = re.compile(r"(code|coder|vl\b|-vl-|vision|audio|omni|lfm|1\.7b|2\.6b|3b|nano|tiny|small|mini)", re.I)
_MODELS: list[str] | None = None
_DEAD: set[str] = set()


class OpenRouterError(RuntimeError):
    pass


def available() -> bool:
    return bool(env("OPENROUTER_API_KEY"))


def free_models() -> list[str]:
    global _MODELS  # noqa: PLW0603
    if _MODELS is not None:
        return _MODELS
    custom = [m.strip() for m in (env("OPENROUTER_MODELS") or "").split(",") if m.strip()]
    try:
        r = requests.get(f"{API}/models", timeout=30)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", []) if m["id"].endswith(":free")]
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenRouter: не удалось получить список моделей (%s)", exc)
        ids = []
    usable = [i for i in ids if not EXCLUDE.search(i.split("/")[-1])]

    def rank(i: str) -> int:
        for k, p in enumerate(PREFERRED):
            if p in i:
                return k
        return len(PREFERRED)

    ordered = sorted(usable, key=rank)
    _MODELS = (custom + [m for m in ordered if m not in custom])[:5]
    log.info("OpenRouter: бесплатные модели по приоритету: %s", ", ".join(_MODELS) or "нет")
    return _MODELS


def _call(model: str, prompt: str, temperature: float, as_json: bool) -> str:
    body = {"model": model, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]}
    if as_json:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {env('OPENROUTER_API_KEY')}",
               "HTTP-Referer": "https://github.com", "X-Title": "shorts-pipeline"}
    r = requests.post(f"{API}/chat/completions", headers=headers, json=body, timeout=(15, 120))
    if r.status_code == 400 and as_json and "response_format" in r.text:
        body.pop("response_format")  # модель не умеет JSON-режим — попросим JSON текстом (промпт уже просит)
        r = requests.post(f"{API}/chat/completions", headers=headers, json=body, timeout=(15, 120))
    if r.status_code in (401, 403):
        raise OpenRouterError(f"доступ запрещён (HTTP {r.status_code}) — проверьте OPENROUTER_API_KEY")
    if r.status_code == 429:
        raise OpenRouterError("лимит бесплатных запросов (429)")
    if r.status_code == 404:
        _DEAD.add(model)
        raise OpenRouterError(f"модель {model} недоступна (404)")
    if r.status_code != 200:
        raise OpenRouterError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("error"):
        raise OpenRouterError(str(data["error"])[:200])
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise OpenRouterError(f"пустой ответ: {str(data)[:200]}") from exc
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # рассуждения reasoning-моделей
    if not text:
        raise OpenRouterError("пустой ответ")
    return text


def generate(prompt: str, temperature: float = 0.9, as_json: bool = False) -> str:
    if not available():
        raise OpenRouterError("OPENROUTER_API_KEY не задан")
    last: Exception | None = None
    for _round in range(2):
        for model in [m for m in free_models() if m not in _DEAD]:
            try:
                text = _call(model, prompt, temperature, as_json)
                log.info("OpenRouter %s ответил (%d символов)", model, len(text))
                return text
            except (OpenRouterError, requests.RequestException) as exc:
                last = exc
                log.warning("OpenRouter %s: %s", model, str(exc)[:150])
                if "доступ запрещён" in str(exc):
                    raise
        time.sleep(20)
    raise OpenRouterError(f"ни одна бесплатная модель OpenRouter не ответила: {last}")
