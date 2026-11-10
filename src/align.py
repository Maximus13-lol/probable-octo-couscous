"""Тайминги слов по готовой озвучке (forced alignment через faster-whisper, лицензия MIT).

Нужен для TTS, которые не отдают WordBoundary (Gemini TTS). Whisper распознаёт речь с таймингами слов,
затем распознанные слова сопоставляются со словами сценария (difflib); пропуски интерполируются.
"""
from __future__ import annotations

import difflib
import re

from .utils import ROOT, env, log

WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+")
_MODEL = None


def _n(s: str) -> str:
    s = s.replace("́", "").replace("ё", "е").replace("Ё", "Е").lower()
    return "".join(ch for ch in s if ch.isalnum())


def _model():
    global _MODEL  # noqa: PLW0603
    if _MODEL is None:
        from faster_whisper import WhisperModel
        size = env("WHISPER_MODEL", "small")
        root = env("WHISPER_DIR", str(ROOT / ".cache" / "whisper"))
        log.info("Загружаю faster-whisper «%s» (CPU, int8)…", size)
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8", download_root=root)
    return _MODEL


def _load_audio(path: str):
    """16 кГц моно float32 через ffmpeg — без зависимости от версии PyAV."""
    import subprocess

    import numpy as np
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0


def transcribe_words(audio_path: str, prompt: str = "") -> list[dict]:
    segments, _info = _model().transcribe(
        _load_audio(audio_path), language="ru", word_timestamps=True, beam_size=5, vad_filter=False,
        # initial_prompt НЕ передаём: если подсказка совпадает с речью, whisper на коротких фрагментах
        # «галлюцинирует» титрами («Редактор субтитров …») — проверено на практике.
        initial_prompt=prompt[:800] or None, condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            for piece in WORD_RE.findall(w.word):  # «Эйлеан-Мор» → два слова, как в сценарии
                words.append({"word": piece, "start": float(w.start), "end": float(w.end)})
    return words


def align_script(script_words: list[str], audio_path: str, total_dur: float) -> tuple[list[dict], float]:
    """Возвращает тайминги для КАЖДОГО слова сценария и долю слов, найденных в распознавании (0..1)."""
    rec = transcribe_words(audio_path)
    a = [_n(w) for w in script_words]
    b = [_n(w["word"]) for w in rec]
    times: list[tuple[float, float] | None] = [None] * len(a)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (rec[j1 + k]["start"], rec[j1 + k]["end"])
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            # одинаковое число слов, но распознано с ошибкой («Эйлеан» → «Эйлиан») — берём позицию
            for k in range(i2 - i1):
                if difflib.SequenceMatcher(None, a[i1 + k], b[j1 + k]).ratio() >= 0.5:
                    times[i1 + k] = (rec[j1 + k]["start"], rec[j1 + k]["end"])
    matched = sum(t is not None for t in times) / max(1, len(times))

    # интерполяция пропусков между известными соседями
    known = [i for i, t in enumerate(times) if t]
    out = []
    for i, t in enumerate(times):
        if t:
            out.append({"word": script_words[i], "start": t[0], "end": t[1]})
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        t0 = times[prev][1] if prev is not None else 0.0
        t1 = times[nxt][0] if nxt is not None else total_dur
        lo = prev if prev is not None else -1
        hi = nxt if nxt is not None else len(times)
        span = (t1 - t0) / (hi - lo)
        s = t0 + span * (i - lo - 1)
        out.append({"word": script_words[i], "start": s, "end": s + span})
    # монотонность
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["start"]:
            out[i]["start"] = out[i - 1]["start"]
        out[i]["end"] = max(out[i]["end"], out[i]["start"] + 0.05)
    log.info("Выравнивание: найдено %.0f%% слов сценария", matched * 100)
    return out, matched
