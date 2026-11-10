"""Озвучка Azure AI Speech (тариф F0) по SSML от narration-director, с таймингами слов (WordBoundary).

Секреты: AZURE_SPEECH_KEY, AZURE_SPEECH_REGION. Голос — config/voice.json.
Каждая сцена озвучивается отдельно: audio/scene_XX.mp3 + тайминги слов в tts.json.
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

from .narration import ACUTE, voice_config
from .utils import env, log, retry, save_json

TICKS = 10_000_000  # Azure отдаёт смещения в 100-нс тиках
RETRIABLE = {"ConnectionFailure", "ServiceTimeout", "ServiceError", "TooManyRequests", "ServiceUnavailable"}
FATAL = {"AuthenticationFailure", "Forbidden"}


class SsmlRejected(Exception):
    pass


class TtsFatal(RuntimeError):
    pass


def azure_available() -> bool:
    return bool(env("AZURE_SPEECH_KEY") and env("AZURE_SPEECH_REGION"))


@retry(tries=3, base_delay=3, exceptions=(requests.RequestException,), label="azure.voices")
def list_voices() -> list[dict]:
    region, key = env("AZURE_SPEECH_REGION"), env("AZURE_SPEECH_KEY")
    r = requests.get(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
                     headers={"Ocp-Apim-Subscription-Key": key}, timeout=30)
    r.raise_for_status()
    return r.json()


def voice_styles(voice: str) -> list[str]:
    try:
        for v in list_voices():
            if v.get("ShortName") == voice:
                return v.get("StyleList") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось получить список голосов Azure: %s", exc)
    return []


def _speech_config():
    import azure.cognitiveservices.speech as sdk
    cfg = sdk.SpeechConfig(subscription=env("AZURE_SPEECH_KEY"), region=env("AZURE_SPEECH_REGION"))
    cfg.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3)
    cfg.set_property(sdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary, "false")
    cfg.set_property(sdk.PropertyId.SpeechServiceResponse_RequestPunctuationBoundary, "false")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:  # SDK сам не читает переменные прокси
        u = urllib.parse.urlparse(proxy)
        if u.hostname and u.port:
            cfg.set_proxy(u.hostname, u.port)
    return cfg


def _synth_once(ssml: str, out: Path) -> list[dict]:
    import azure.cognitiveservices.speech as sdk
    synth = sdk.SpeechSynthesizer(speech_config=_speech_config(), audio_config=None)
    words: list[dict] = []

    def on_word(evt):
        if evt.boundary_type == sdk.SpeechSynthesisBoundaryType.Word:
            start = evt.audio_offset / TICKS
            words.append({"word": evt.text.replace(ACUTE, ""), "start": start,
                          "end": start + evt.duration.total_seconds()})

    synth.synthesis_word_boundary.connect(on_word)
    res = synth.speak_ssml_async(ssml).get()
    if res.reason == sdk.ResultReason.SynthesizingAudioCompleted:
        if not res.audio_data or not words:
            raise RuntimeError("Azure вернул пустое аудио")
        out.write_bytes(res.audio_data)
        return words
    det = res.cancellation_details
    code = str(det.error_code).split(".")[-1]
    msg = f"{code}: {det.error_details}"
    if code in FATAL:
        raise TtsFatal(f"Azure: доступ запрещён ({msg}) — проверьте AZURE_SPEECH_KEY / AZURE_SPEECH_REGION")
    if code == "BadRequest" or "SSML" in (det.error_details or ""):
        raise SsmlRejected(msg)
    if code in RETRIABLE or code == "RuntimeError":
        raise ConnectionError(msg)
    raise RuntimeError(msg)


def synth_scene(ssml: str, plain_ssml: str, out: Path) -> tuple[list[dict], bool]:
    """Возвращает (слова, использован ли запасной SSML)."""
    used_plain = False
    current = ssml
    for attempt in range(1, 6):
        try:
            return _synth_once(current, out), used_plain
        except SsmlRejected as exc:
            if used_plain:
                raise RuntimeError(f"Azure отверг даже простой SSML: {exc}") from exc
            log.warning("Azure отверг SSML (%s) — повтор с простой разметкой (текст + паузы)", exc)
            current, used_plain = plain_ssml, True
        except TtsFatal:
            raise
        except Exception as exc:  # noqa: BLE001
            if attempt == 5:
                raise
            delay = min(30, 2 ** attempt)
            log.warning("Azure TTS, попытка %d: %s — повтор через %d с", attempt, exc, delay)
            time.sleep(delay)
    raise RuntimeError("Azure TTS: попытки исчерпаны")


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _with_voice(ssml: str, old: str, new: str) -> str:
    return ssml.replace(f'<voice name="{old}">', f'<voice name="{new}">')


# ------------------------------------------------------------------ заглушка без ключа
def _silent_preview(narr: list[dict], adir: Path) -> list[dict]:
    """Только для локальной проверки монтажа без ключа Azure: тишина + оценочные тайминги слов."""
    import re
    out = []
    for sc in narr:
        words = re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", sc["display_text"])
        t, wl = 0.05, []
        for w in words:
            d = 0.12 + 0.055 * len(w)
            wl.append({"word": w, "start": t, "end": t + d})
            t += d + 0.06
        dur = t + 0.1
        path = adir / f"scene_{sc['index']:02d}.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                        "-t", f"{dur:.3f}", "-b:a", "64k", str(path)], check=True)
        out.append({"index": sc["index"], "path": str(path), "duration": audio_duration(path), "words": wl,
                    "voice": "SILENT-PREVIEW", "plain_ssml_used": False,
                    "pause_after": sc.get("pause_after_ms", 200) / 1000})
    return out


# ------------------------------------------------------------------ шаг конвейера
def synthesize(narr: list[dict], run_dir: Path, allow_silent: bool = False) -> list[dict]:
    adir = run_dir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    if not azure_available():
        if not allow_silent:
            raise TtsFatal("Не заданы AZURE_SPEECH_KEY / AZURE_SPEECH_REGION")
        log.warning("⚠ Нет ключа Azure — ТИХАЯ ЗАГЛУШКА с оценочными таймингами (только для проверки монтажа!)")
        result = _silent_preview(narr, adir)
        save_json(run_dir / "tts.json", result)
        return result

    cfg = voice_config()
    voices = [cfg["voice"]] + [v for v in cfg.get("fallback_voices", []) if v != cfg["voice"]]
    last: Exception | None = None
    for voice in voices:
        try:
            result = []
            for sc in narr:
                path = adir / f"scene_{sc['index']:02d}.mp3"
                ssml = _with_voice(sc["ssml"], cfg["voice"], voice)
                plain = _with_voice(sc["plain_ssml"], cfg["voice"], voice)
                words, used_plain = synth_scene(ssml, plain, path)
                dur = audio_duration(path)
                result.append({"index": sc["index"], "path": str(path), "duration": dur, "words": words,
                               "voice": voice, "plain_ssml_used": used_plain,
                               "pause_after": sc.get("pause_after_ms", 200) / 1000})
                log.info("Озвучка сцены %d: %.2f с, %d слов%s", sc["index"], dur, len(words),
                         " (простой SSML)" if used_plain else "")
                time.sleep(0.5)  # F0: не больше 20 запросов в минуту
            save_json(run_dir / "tts.json", result)
            return result
        except TtsFatal:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Голос %s не сработал (%s) — пробую запасной", voice, exc)
    raise RuntimeError(f"Озвучка не удалась ни одним голосом: {last}")
