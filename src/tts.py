"""Озвучка edge-tts (ru-RU-DmitryNeural) с таймингами слов (WordBoundary). По сцене — отдельный файл."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import edge_tts

from .utils import env, log, save_json

VOICE = env("TTS_VOICE", "ru-RU-DmitryNeural")
FALLBACK_VOICES = [v for v in env("TTS_FALLBACK_VOICES", "ru-RU-SvetlanaNeural").split(",") if v]
RATE = env("TTS_RATE", "+12%")  # 130–160 слов ≈ 55–65 с
PITCH = env("TTS_PITCH", "-2Hz")
TICKS = 10_000_000  # edge-tts отдаёт время в 100-нс тиках


def _patch_ssl() -> None:
    """edge-tts использует только certifi; если окружение задаёт свой CA (корп. прокси) — добавляем его."""
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not ca or not Path(ca).exists():
        return
    try:
        from edge_tts import communicate
        communicate._SSL_CTX.load_verify_locations(ca)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.debug("Не удалось добавить CA в edge-tts: %s", exc)


_patch_ssl()


async def _synth(text: str, voice: str, out: Path) -> list[dict]:
    comm = edge_tts.Communicate(text, voice, rate=RATE, pitch=PITCH, boundary="WordBoundary")
    words: list[dict] = []
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "word": chunk["text"],
                    "start": chunk["offset"] / TICKS,
                    "end": (chunk["offset"] + chunk["duration"]) / TICKS,
                })
    if out.stat().st_size < 1000 or not words:
        raise RuntimeError("edge-tts вернул пустое аудио")
    return words


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def synth_scene(text: str, out: Path) -> tuple[list[dict], str]:
    """Озвучивает текст; повторы с паузами, затем запасные голоса. Возвращает (слова, голос)."""
    last: Exception | None = None
    for voice in [VOICE] + [v for v in FALLBACK_VOICES if v != VOICE]:
        for attempt in range(1, 5):
            try:
                return asyncio.run(_synth(text, voice, out)), voice
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("edge-tts %s, попытка %d: %s", voice, attempt, exc)
                asyncio.run(asyncio.sleep(2 * attempt))
        log.warning("Голос %s недоступен — пробую запасной", voice)
    raise RuntimeError(f"Озвучка не удалась: {last}")


def synthesize(board: dict, run_dir: Path) -> list[dict]:
    """Для каждой сцены: mp3, длительность и тайминги слов. Голос фиксируется по первой сцене."""
    global VOICE  # noqa: PLW0603 — если основной голос недоступен, весь ролик одним голосом
    adir = run_dir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    result = []
    for scene in board["scenes"]:
        i = scene["index"]
        path = adir / f"scene_{i:02d}.mp3"
        words, voice = synth_scene(scene["text"], path)
        if voice != VOICE:
            log.warning("Дальше весь ролик озвучивается голосом %s", voice)
            VOICE = voice
            if i > 0:  # переозвучить предыдущие сцены тем же голосом
                return synthesize(board, run_dir)
        dur = audio_duration(path)
        result.append({"index": i, "path": str(path), "duration": dur, "words": words, "voice": voice})
        log.info("Озвучка сцены %d: %.2f с, %d слов", i, dur, len(words))
    save_json(run_dir / "tts.json", result)
    return result
