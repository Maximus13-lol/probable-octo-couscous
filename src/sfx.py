"""Звуковой дизайн, синтезированный процедурно (без чужих записей — без авторских прав):
- ambient — тёмный эмбиент для тайн: низкий гул с медленным «дыханием», ветер, редкие колокольные ноты в миноре;
- whoosh — «вжух» на смене сцены;
- impact — низкий удар (хук в начале и финальная фраза).
Громкости — config/sfx.json.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .utils import CONFIG_DIR, load_json

SR = 44100
DEFAULTS = {"enabled": True, "ambient": True, "whoosh_volume": 0.32, "impact_volume": 0.55, "ambient_volume": 0.16}


def config() -> dict:
    c = dict(DEFAULTS)
    c.update(load_json(CONFIG_DIR / "sfx.json", default={}) or {})
    return c


def _write(path: Path, x: np.ndarray) -> Path:
    x = np.clip(x, -1, 1)
    st = np.stack([x, x], axis=1) if x.ndim == 1 else x
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 32767).astype(np.int16).tobytes())
    return path


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, cutoff, "low", fs=SR, output="sos"), x)


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(2, [lo, hi], "band", fs=SR, output="sos"), x)


def whoosh(path: Path, dur: float = 0.65, seed: int = 1) -> Path:
    """Шумовой «вжух»: полоса шума, которая проезжает снизу вверх, с плавной атакой и спадом."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    chunks = 24
    for i in range(chunks):  # центральная частота растёт от 300 до 4000 Гц
        a, b = i * n // chunks, (i + 1) * n // chunks
        fc = 300 * (4000 / 300) ** (i / (chunks - 1))
        seg = _bandpass(noise, fc * 0.6, min(fc * 1.6, SR / 2 - 100))[a:b]
        out[a:b] = seg
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    out = out * env
    return _write(path, 0.9 * out / (np.abs(out).max() + 1e-9))


def impact(path: Path, dur: float = 2.2, seed: int = 2) -> Path:
    """Низкий удар: синус, падающий 110→38 Гц, короткий шумовой «тук» и затухающий хвост."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    freq = 38 + 72 * np.exp(-t * 9)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 2.4)
    click = _lowpass(rng.standard_normal(n), 900) * np.exp(-t * 40) * 0.6
    tail = _lowpass(rng.standard_normal(n), 300) * np.exp(-t * 1.6) * 0.18
    out = body + click + tail
    out[: int(0.004 * SR)] *= np.linspace(0, 1, int(0.004 * SR))
    return _write(path, 0.95 * out / (np.abs(out).max() + 1e-9))


def ambient(path: Path, dur: float, seed: int = 3) -> Path:
    """Тёмный эмбиент: гул (ля минор, квинта, слегка расстроенные голоса), «дыхание» громкости, ветер и редкие
    колокольные ноты. Стерео за счёт разных задержек каналов."""
    rng = np.random.default_rng(seed)
    n = int((dur + 1) * SR)
    t = np.arange(n) / SR
    drone = np.zeros(n)
    for f, a in ((55.0, 0.5), (55.35, 0.4), (82.4, 0.25), (110.2, 0.12), (130.8, 0.08)):
        drone += a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    drone *= 0.75 + 0.25 * np.sin(2 * np.pi * t / 9.0)  # медленное «дыхание»
    wind = _lowpass(rng.standard_normal(n), 500) * (0.35 + 0.25 * np.sin(2 * np.pi * t / 13.0 + 1))
    bells = np.zeros(n)
    scale = [220.0, 261.6, 293.7, 329.6, 392.0, 440.0]  # ля минор (пентатоника)
    pos = rng.uniform(2.0, 4.0)
    while pos < dur - 1:
        f = rng.choice(scale)
        k = int(pos * SR)
        m = min(n - k, int(5 * SR))
        tt = np.arange(m) / SR
        tone = (np.sin(2 * np.pi * f * tt) + 0.4 * np.sin(2 * np.pi * 2.01 * f * tt)
                + 0.15 * np.sin(2 * np.pi * 3.02 * f * tt)) * np.exp(-tt * 1.1)
        bells[k:k + m] += 0.22 * tone
        pos += rng.uniform(5.0, 9.0)
    mono = drone / 1.4 + 0.5 * wind + bells
    mono = mono / (np.abs(mono).max() + 1e-9)
    d = int(0.013 * SR)
    left, right = mono, np.concatenate([np.zeros(d), mono[:-d]])
    fade = np.ones(n)
    fade[: SR] = np.linspace(0, 1, SR)
    fade[-2 * SR:] = np.linspace(1, 0, 2 * SR)
    st = np.stack([left * fade, right * fade], axis=1)[: int(dur * SR)]
    return _write(path, 0.85 * st)


def build(run_dir: Path, scene_starts: list[float], total: float, last_scene_start: float | None) -> dict:
    """Готовит файлы и возвращает план: [(путь, время, громкость)] + путь эмбиента."""
    cfg = config()
    if not cfg["enabled"]:
        return {"events": [], "ambient": None}
    d = run_dir / "sfx"
    wh, im = whoosh(d / "whoosh.wav"), impact(d / "impact.wav")
    events = [(str(im), 0.0, cfg["impact_volume"])]
    for t0 in scene_starts[1:]:
        events.append((str(wh), max(0.0, t0 - 0.35), cfg["whoosh_volume"]))
    if last_scene_start:
        events.append((str(im), max(0.0, last_scene_start - 0.1), cfg["impact_volume"] * 0.8))
    amb = str(ambient(d / "ambient.wav", total)) if cfg["ambient"] else None
    return {"events": events, "ambient": amb, "ambient_volume": cfg["ambient_volume"]}
