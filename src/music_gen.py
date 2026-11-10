"""Запасной генератор спокойной лоу-фай музыки (если папка music/ пуста).

Музыка синтезируется процедурно (аккорды + бас + мягкий хэт + треск винила),
поэтому она гарантированно без авторских прав. Для лучшего звучания положите
в music/ треки с Pixabay Music (см. README)."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100


def _note(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x: np.ndarray, alpha: float) -> np.ndarray:
    # однополюсный ФНЧ, векторно через scipy недоступен — простой цикл по блокам
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += alpha * (x[i] - acc)
        y[i] = acc
    return y


def generate(path: Path, duration: float = 70.0, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    bpm = 72
    beat = 60 / bpm
    bar = beat * 4
    n = int(SR * duration)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # Am9 – Fmaj7 – Cmaj7 – G6, по такту на аккорд
    prog = [[57, 60, 64, 67, 71], [53, 57, 60, 64, 69], [48, 55, 59, 64, 67], [55, 59, 62, 64, 71]]
    bass = [45, 41, 36, 43]
    n_bars = int(np.ceil(duration / bar))
    for b in range(n_bars):
        s, e = int(b * bar * SR), min(n, int((b + 1) * bar * SR))
        if s >= n:
            break
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 0.4) * np.exp(-tt / (bar * 1.4))
        chord = prog[b % 4]
        for m in chord:
            f = _note(m) * (1 + rng.normal(0, 0.0008))
            out[s:e] += 0.06 * env * (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt))
        fb = _note(bass[b % 4])
        benv = np.exp(-tt / 1.2)
        out[s:e] += 0.18 * benv * np.sin(2 * np.pi * fb * tt)

    # мягкий «кик» на 1 и 3, хэт на восьмых
    for k in range(int(duration / beat)):
        s = int(k * beat * SR)
        if k % 2 == 0:
            L = int(0.25 * SR)
            tt = np.arange(min(L, n - s)) / SR
            out[s:s + len(tt)] += 0.22 * np.exp(-tt * 18) * np.sin(2 * np.pi * (55 + 60 * np.exp(-tt * 30)) * tt)
        for half in (0, 0.5):
            hs = int((k + half) * beat * SR + rng.normal(0, 0.006) * SR)  # «свинг»/неровность
            L = int(0.05 * SR)
            if 0 <= hs < n - L:
                out[hs:hs + L] += 0.025 * rng.normal(0, 1, L) * np.exp(-np.arange(L) / (0.012 * SR))

    # треск винила и шум ленты
    crackle = np.zeros(n)
    idx = rng.integers(0, n, int(duration * 6))
    crackle[idx] = rng.normal(0, 0.25, len(idx))
    out += crackle + rng.normal(0, 0.004, n)

    out = _lowpass(out, 0.18)  # «тёплый» глухой звук
    # лёгкое «плавание» громкости (как старая плёнка)
    out *= 0.9 + 0.1 * np.sin(2 * np.pi * 0.11 * t)
    out /= np.max(np.abs(out)) + 1e-9
    out *= 0.8
    stereo = np.stack([out, np.roll(out, int(0.012 * SR))], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


if __name__ == "__main__":
    import sys
    print(generate(Path(sys.argv[1] if len(sys.argv) > 1 else "lofi_generated.wav")))
