"""Озвучка голосом автора канала через Qwen3-TTS (Apache 2.0). Запускается в ОТДЕЛЬНОМ окружении
(~/qwen-venv: torch CPU + qwen-tts + faster-whisper), чтобы не конфликтовать с зависимостями конвейера.

    ~/qwen-venv/bin/python tools/qwen_worker.py job.json

job.json: {"model", "ref_audio", "ref_text", "language", "min_recall", "items": [{"id", "text", "out"}]}
Каждую фразу озвучивает с проверкой: распознаёт результат (faster-whisper) и сверяет слова с текстом.
Порядок попыток: образец с интонацией (ICL) ×2 → только тембр (x-vector) ×2; берётся лучший результат.
В stdout — по строке JSON на фразу: {"id", "out", "recall", "mode", "attempts"}.
"""
from __future__ import annotations

import json
import re
import sys
import time

import numpy as np
import soundfile as sf
import torch

torch.set_num_threads(max(1, torch.get_num_threads()))


def norm_words(s: str) -> list[str]:
    s = s.lower().replace("ё", "е").replace("́", "")
    return re.findall(r"[a-zа-я0-9]+", s)


NUM_STEMS = ("ноль", "один", "одна", "одно", "одн", "два", "две", "двух", "двум", "три", "трех", "трем", "четыр",
             "пят", "шест", "сем", "восем", "девят", "десят", "одиннад", "двенад", "тринад", "сорок", "девяност",
             "сто", "двест", "двухсот", "трист", "трехсот", "четырест", "пятьсот", "пятисот", "шестьсот", "шестисот",
             "семьсот", "семисот", "восемьсот", "восьмисот", "девятьсот", "девятисот", "тысяч", "миллион",
             "перв", "втор", "трет", "четверт", "сотн", "полтор")


def _is_num(w: str) -> bool:
    return w.isdigit() or w.startswith(NUM_STEMS)


def recall(expected: str, heard: str) -> tuple[float, bool]:
    """Доля слов текста, найденных в распознанном (по порядку), и есть ли начало фразы.
    Распознавание пишет числа цифрами — если в услышанном есть цифры, числительные текста не считаем."""
    exp, got = norm_words(expected), norm_words(heard)
    if any(ch.isdigit() for ch in heard):
        exp = [w for w in exp if not _is_num(w)]
        got = [w for w in got if not w.isdigit()]
    if not exp:
        return 1.0, True
    j, hit = 0, 0
    for w in exp:
        k = j
        while k < len(got) and not (got[k] == w or (len(w) > 4 and got[k][:4] == w[:4])):
            k += 1
        if k < len(got):
            hit += 1
            j = k + 1
    head = exp[:2]
    head_ok = all(any(g == w or (len(w) > 4 and g[:4] == w[:4]) for g in got[:5]) for w in head)
    return hit / len(exp), head_ok


def main() -> None:
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    from faster_whisper import WhisperModel
    from qwen_tts import Qwen3TTSModel
    import librosa

    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(job["model"], device_map="cpu", dtype=torch.float32)
    asr = WhisperModel("small", device="cpu", compute_type="int8", download_root=job.get("whisper_dir"))
    print(json.dumps({"event": "loaded", "sec": round(time.time() - t0, 1)}), flush=True)
    prompts = {}

    def prompt(mode):
        if mode not in prompts:
            kw = dict(ref_audio=job["ref_audio"])
            if mode == "icl":
                kw.update(ref_text=job["ref_text"], x_vector_only_mode=False)
            else:
                kw.update(x_vector_only_mode=True)
            prompts[mode] = model.create_voice_clone_prompt(**kw)
        return prompts[mode]

    min_recall = float(job.get("min_recall", 0.85))
    for it in job["items"]:
        text = it["text"]
        best = None
        attempts = 0
        for mode, seed in (("icl", 1), ("icl", 7), ("xvec", 1), ("xvec", 7)):
            attempts += 1
            torch.manual_seed(seed)
            wavs, sr = model.generate_voice_clone(text=text, language=job.get("language", "Russian"),
                                                  voice_clone_prompt=prompt(mode),
                                                  max_new_tokens=int(len(text) * 1.6) + 40)
            w = np.asarray(wavs[0], dtype=np.float32)
            dur = len(w) / sr
            w16 = librosa.resample(w, orig_sr=sr, target_sr=16000)
            segs, _ = asr.transcribe(w16, language="ru", beam_size=1)
            heard = " ".join(s.text for s in segs)
            rec, head_ok = recall(text, heard)
            cps = len(text) / max(dur, 0.1)  # символов в секунду: обрезка/затягивание
            score = rec - (0 if head_ok else 0.3) - (0 if 7 <= cps <= 24 else 0.3)
            if best is None or score > best[0]:
                best = (score, w, sr, mode, rec, heard)
            if rec >= min_recall and head_ok and 7 <= cps <= 24:
                break
        score, w, sr, mode, rec, heard = best
        sf.write(it["out"], w, sr)
        print(json.dumps({"id": it["id"], "out": it["out"], "recall": round(rec, 3), "mode": mode,
                          "attempts": attempts, "ok": score >= min_recall, "heard": heard[:200]},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
