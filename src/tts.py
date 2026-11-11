"""Озвучка. Провайдеры (config/voice.json → provider):

* vosk — Vosk TTS (Apache 2.0), работает локально в GitHub Actions: без регистрации, ключей и лимитов.
  Ударения «+» из RUAccent, паузы и темп по фрагментам от narration-director; тайминги — faster-whisper.
* gemini — Gemini TTS (ключ GEMINI_API_KEY, бесплатный тариф). Вся история — одним запросом (ровный голос,
  экономия лимита), тайминги слов — faster-whisper (src/align.py), затем нарезка по сценам.
* azure — Azure AI Speech (AZURE_SPEECH_KEY, AZURE_SPEECH_REGION), SSML, тайминги из WordBoundary.
* auto — azure, если заданы его ключи, иначе gemini; второй — запасной.

Результат: audio/scene_XX.mp3 + тайминги слов в tts.json.
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import wave
import time
import urllib.parse
from pathlib import Path

import requests

from . import align
from .narration import ACUTE, gemini_script, voice_config
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


# ------------------------------------------------------------------ Gemini TTS
GEMINI_INTERACTIONS = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_LEGACY = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview"]


def gemini_available() -> bool:
    return bool(env("GEMINI_API_KEY"))


def _find_audio(obj):
    """Ищет base64-аудио в ответе (структура разная у Interactions API и generateContent)."""
    if isinstance(obj, dict):
        if obj.get("type") == "audio" and isinstance(obj.get("data"), str):
            return obj["data"], obj.get("mime_type") or obj.get("mimeType") or ""
        inline = obj.get("inlineData") or obj.get("inline_data")
        if isinstance(inline, dict) and str(inline.get("mimeType", inline.get("mime_type", ""))).startswith("audio"):
            return inline["data"], inline.get("mimeType") or inline.get("mime_type")
        found = None
        for v in obj.values():
            r = _find_audio(v)
            if r:
                found = r  # берём последний (финальный шаг ответа)
        return found
    if isinstance(obj, list):
        found = None
        for v in obj:
            r = _find_audio(v)
            if r:
                found = r
        return found
    return None


def _to_wav(raw: bytes, mime: str) -> bytes:
    if raw[:4] == b"RIFF":
        return raw
    m = re.search(r"rate=(\d+)", mime or "")
    rate = int(m.group(1)) if m else 24000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(raw)
    return buf.getvalue()


class QuotaExhausted(RuntimeError):
    pass


def _retry_delay(resp: requests.Response) -> float:
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', resp.text)
    if m:
        return float(m.group(1))
    return float(resp.headers.get("retry-after", 30))


def gemini_tts(text: str, style: str, voice: str, model: str) -> bytes:
    """Один запрос к Gemini TTS → WAV. Сначала Interactions API, при 400/404 — старый generateContent."""
    key = env("GEMINI_API_KEY")
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    bodies = [
        (GEMINI_INTERACTIONS, {
            "model": model,
            "input": [{"type": "user_input", "content": [{
                "type": "text", "text": text,
                "annotations": [{"type": "speech_metadata", "style": style}],
            }]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]},
        }),
        (GEMINI_LEGACY.format(model=model), {
            "contents": [{"parts": [{"text": f"{style}\nRead the following text aloud exactly as written:\n{text}"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
        }),
    ]
    last = ""
    for url, body in bodies:
        for attempt in range(1, 5):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=300)
            except requests.RequestException as exc:
                last = str(exc)
                time.sleep(5 * attempt)
                continue
            if r.status_code == 200:
                found = _find_audio(r.json())
                if not found:
                    last = f"в ответе нет аудио: {r.text[:200]}"
                    break
                data, mime = found
                return _to_wav(base64.b64decode(data), mime)
            if r.status_code == 429:
                if "PerDay" in r.text or "per day" in r.text.lower():
                    raise QuotaExhausted(f"{model}: дневной бесплатный лимит исчерпан")
                wait = min(120.0, _retry_delay(r) + 2)
                log.info("Gemini TTS: лимит запросов в минуту, жду %.0f с", wait)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(5 * attempt)
                continue
            if r.status_code in (401, 403):
                raise TtsFatal(f"Gemini TTS: доступ запрещён (HTTP {r.status_code}): {r.text[:200]}")
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            break  # 400/404 — пробуем другой формат запроса
    raise RuntimeError(f"Gemini TTS ({model}) не ответил: {last}")


def _cut(src: Path, start: float, end: float, out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                    "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "128k", str(out)], check=True)


def _synth_gemini(narr: list[dict], adir: Path) -> list[dict]:
    cfg = voice_config().get("gemini", {})
    voice = env("GEMINI_TTS_VOICE", cfg.get("voice", "Charon"))
    models = cfg.get("models") or DEFAULT_GEMINI_MODELS
    min_match = float(cfg.get("min_alignment", 0.85))
    script = gemini_script(narr)
    style = narr[0].get("tts_style") or ""
    # слова сценария с номером сцены
    words, owner = [], []
    for k, sc in enumerate(narr):
        for w in align.WORD_RE.findall(sc["display_text"]):
            words.append(w)
            owner.append(k)

    best = None
    full = adir / "full.wav"
    for attempt in range(1, 4):
        wav, used_model = None, None
        for model in models:
            try:
                wav = gemini_tts(script, style, voice, model)
                used_model = model
                break
            except (TtsFatal, QuotaExhausted):
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Gemini TTS %s: %s — пробую следующую модель", model, exc)
        if wav is None:
            raise RuntimeError("Gemini TTS: ни одна модель не ответила")
        tmp = adir / f"take_{attempt}.wav"
        tmp.write_bytes(wav)
        dur = audio_duration(tmp)
        timed, matched = align.align_script(words, str(tmp), dur)
        log.info("Gemini TTS дубль %d (%s, %s): %.1f с, совпадение с текстом %.0f%%",
                 attempt, used_model, voice, dur, matched * 100)
        if best is None or matched > best[2]:
            best = (tmp, timed, matched, dur, used_model)
        if matched >= min_match:
            break
        log.warning("Диктор отступил от текста (%.0f%% < %.0f%%) — переозвучиваю", matched * 100, min_match * 100)
    tmp, timed, matched, dur, used_model = best
    os.replace(tmp, full)
    if matched < 0.6:
        raise RuntimeError(f"Озвучка слишком расходится с текстом ({matched:.0%}) — субтитры будут неверны")

    # нарезка по сценам: граница — середина паузы между последним словом сцены и первым словом следующей
    n = len(narr)
    first = [owner.index(k) for k in range(n)]
    last = [len(owner) - 1 - owner[::-1].index(k) for k in range(n)]
    cuts = [0.0]
    for k in range(n - 1):
        a, b = timed[last[k]]["end"], timed[first[k + 1]]["start"]
        cuts.append(max(a + 0.02, (a + b) / 2) if b > a else a)
    cuts.append(dur)
    result = []
    for k, sc in enumerate(narr):
        s0, s1 = cuts[k], cuts[k + 1]
        path = adir / f"scene_{sc['index']:02d}.mp3"
        _cut(full, s0, s1, path)
        ws = [{"word": timed[i]["word"], "start": max(0.0, timed[i]["start"] - s0),
               "end": max(0.0, timed[i]["end"] - s0)} for i in range(first[k], last[k] + 1)]
        result.append({"index": sc["index"], "path": str(path), "duration": audio_duration(path), "words": ws,
                       "voice": f"gemini:{voice}", "model": used_model, "alignment": round(matched, 3),
                       "pause_after": 0.0})  # пауза уже внутри аудио сцены
    return result


# ------------------------------------------------------------------ Vosk TTS (локально)
_VOSK = None


def vosk_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("vosk_tts") is not None


def _vosk_synth():
    global _VOSK  # noqa: PLW0603
    if _VOSK is None:
        from vosk_tts import Model, Synth
        cfg = voice_config().get("vosk", {})
        name = cfg.get("model", "vosk-model-tts-ru-0.9-multi")
        log.info("Загружаю Vosk TTS (%s)…", name)
        _VOSK = Synth(Model(model_name=name))
    return _VOSK


def _silence(sec: float, rate: int):
    import numpy as np
    return np.zeros(int(sec * rate), dtype=np.int16)


def _synth_vosk(narr: list[dict], adir: Path) -> list[dict]:
    import numpy as np
    cfg = voice_config().get("vosk", {})
    speaker = int(env("VOSK_SPEAKER", str(cfg.get("speaker", 3))))
    base_rate = float(cfg.get("speech_rate", 1.0))
    synth = _vosk_synth()
    sr = int(synth.model.config["audio"].get("sample_rate", 22050))
    result = []
    for sc in narr:
        chunks = [_silence(0.05, sr)]
        for seg in sc.get("vosk_segments") or []:
            rate = base_rate * (1 + seg.get("rate", 0) / 100)
            audio = synth.synth_audio(seg["text"], speaker_id=speaker, speech_rate=rate)
            audio = np.asarray(audio)
            if audio.dtype != np.int16:
                audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            chunks.append(audio)
            if seg.get("pause_after_ms"):
                chunks.append(_silence(seg["pause_after_ms"] / 1000, sr))
        chunks.append(_silence(0.05, sr))
        wav_path = adir / f"scene_{sc['index']:02d}.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.concatenate(chunks).tobytes())
        path = adir / f"scene_{sc['index']:02d}.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "libmp3lame",
                        "-b:a", "128k", str(path)], check=True)
        dur = audio_duration(path)
        words = align.WORD_RE.findall(sc["display_text"])
        timed, matched = align.align_script(words, str(wav_path), dur)
        if matched < 0.6:
            log.warning("Сцена %d: распознано лишь %.0f%% слов — проверьте озвучку", sc["index"], matched * 100)
        result.append({"index": sc["index"], "path": str(path), "duration": dur, "words": timed,
                       "voice": f"vosk:{speaker}", "alignment": round(matched, 3),
                       "pause_after": sc.get("pause_after_ms", 200) / 1000})
        log.info("Озвучка сцены %d: %.2f с (Vosk, голос %d, слов узнано %.0f%%)",
                 sc["index"], dur, speaker, matched * 100)
    return result


def synth_cta(text: str, run_dir: Path) -> dict | None:
    """Озвучка концовки («лайк и подписка») тем же голосом Vosk. Без Vosk — концовка без голоса."""
    if not vosk_available():
        return None
    import numpy as np

    from .narration import vosk_clean
    try:
        cfg = voice_config().get("vosk", {})
        synth = _vosk_synth()
        sr = int(synth.model.config["audio"].get("sample_rate", 22050))
        audio = np.asarray(synth.synth_audio(vosk_clean(text), speaker_id=int(env("VOSK_SPEAKER", str(cfg.get("speaker", 3)))),
                                             speech_rate=float(cfg.get("speech_rate", 1.0))))
        if audio.dtype != np.int16:
            audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        path = run_dir / "audio" / "cta.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(audio.tobytes())
        dur = audio_duration(path)
        log.info("Озвучка концовки: %.2f с", dur)
        return {"path": str(path), "duration": dur}
    except Exception as exc:  # noqa: BLE001
        log.warning("Концовка без голоса: %s", exc)
        return None


# ------------------------------------------------------------------ Azure (по сценам)
def _synth_azure(narr: list[dict], adir: Path) -> list[dict]:
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
            return result
        except TtsFatal:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Голос %s не сработал (%s) — пробую запасной", voice, exc)
    raise RuntimeError(f"Azure: озвучка не удалась ни одним голосом: {last}")


# ------------------------------------------------------------------ шаг конвейера
def providers() -> list[str]:
    pref = voice_config().get("provider", "vosk")
    order = {"vosk": ["vosk", "gemini", "azure"], "gemini": ["gemini", "vosk", "azure"],
             "azure": ["azure", "vosk", "gemini"]}.get(pref, ["vosk", "azure", "gemini"])
    ok = {"azure": azure_available(), "gemini": gemini_available(), "vosk": vosk_available()}
    return [p for p in order if ok[p]]


def synthesize(narr: list[dict], run_dir: Path, allow_silent: bool = False) -> list[dict]:
    adir = run_dir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    provs = providers()
    if not provs:
        if not allow_silent:
            raise TtsFatal("Нет ключей озвучки: задайте GEMINI_API_KEY (Gemini TTS) или AZURE_SPEECH_KEY/REGION")
        log.warning("⚠ Нет ключей озвучки — ТИХАЯ ЗАГЛУШКА с оценочными таймингами (только для проверки монтажа!)")
        result = _silent_preview(narr, adir)
        save_json(run_dir / "tts.json", result)
        return result
    last: Exception | None = None
    for prov in provs:
        try:
            log.info("Озвучка: %s", prov)
            fn = {"gemini": _synth_gemini, "azure": _synth_azure, "vosk": _synth_vosk}[prov]
            result = fn(narr, adir)
            save_json(run_dir / "tts.json", result)
            return result
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Провайдер %s не сработал: %s", prov, exc)
    raise RuntimeError(f"Озвучка не удалась: {last}")
