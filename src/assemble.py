"""video-assembly: сцена длится ровно столько, сколько её озвучка; Ken Burns; crossfade 0.3 с;
субтитры; фоновая лоу-фай музыка ~12% с затуханием."""
from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, afx
from PIL import Image

from . import music_gen
from .subtitles import CENTER_Y, build_subtitles
from .utils import FPS, MUSIC_DIR, VIDEO_H, VIDEO_W, env, log, save_json

CROSSFADE = 0.3
PAUSE = 0.12        # короткий вдох между сценами
TAIL = 0.8          # хвост после последней фразы
MUSIC_VOLUME = float(env("MUSIC_VOLUME", "0.12"))
MUSIC_FADE_OUT = 2.5
ZOOM = 0.08         # Ken Burns: 1.00 → 1.08


def ken_burns_fn(path: Path, duration: float, index: int):
    img = Image.open(path).convert("RGB")
    if img.size != (VIDEO_W, VIDEO_H):
        img = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)
    zoom_in = index % 2 == 0
    # лёгкий дрейф: чётные сцены — к центру, нечётные — с небольшим сдвигом
    rng = random.Random(index)
    dx, dy = rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25)

    def frame(t: float) -> np.ndarray:
        p = min(1.0, max(0.0, t / max(duration, 1e-3)))
        p = p * p * (3 - 2 * p)  # плавный старт/финиш
        z = 1 + ZOOM * (p if zoom_in else 1 - p)
        w, h = VIDEO_W / z, VIDEO_H / z
        cx = VIDEO_W / 2 + dx * (VIDEO_W - w) * p
        cy = VIDEO_H / 2 + dy * (VIDEO_H - h) * p
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        return np.array(img.resize((VIDEO_W, VIDEO_H), Image.BILINEAR, box=box))

    return frame


def pick_music(run_dir: Path, duration: float) -> Path:
    tracks = [p for p in MUSIC_DIR.glob("*") if p.is_file() and p.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a", ".flac")]
    if tracks:
        t = random.choice(tracks)
        log.info("Музыка: %s", t.name)
        return t
    log.warning("Папка music/ пуста — генерирую процедурный лоу-фай трек (без авторских прав)")
    return music_gen.generate(run_dir / "music_generated.wav", duration=duration + 2, seed=random.randint(1, 10_000))


def timeline(tts: list[dict]) -> tuple[list[float], float]:
    starts, t = [], 0.0
    for a in tts:
        starts.append(t)
        t += a["duration"] + PAUSE
    total = t - PAUSE + TAIL
    return starts, total


def assemble(board: dict, images: list[Path], tts: list[dict], run_dir: Path, out_name: str = "video.mp4") -> Path:
    starts, total = timeline(tts)
    n = len(tts)
    log.info("Длительность ролика: %.1f с, сцен: %d", total, n)

    # --- видео-слой: одна функция кадра (Ken Burns + crossfade + субтитры) — в разы быстрее,
    # чем CompositeVideoClip из сотни клипов с полнокадровым альфа-смешиванием
    spans = []  # (begin, end, frame_fn)
    for i, (img, t0) in enumerate(zip(images, starts)):
        begin = t0 - CROSSFADE if i > 0 else 0.0
        end = starts[i + 1] if i + 1 < n else total
        spans.append((begin, end, ken_burns_fn(img, end - begin, i)))

    subs = []
    for ev in build_subtitles(board, tts, starts, total, run_dir):
        rgba = ev.image
        h = rgba.shape[0]
        ys = CENTER_Y - h // 2
        alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
        subs.append((ev.start, ev.end, ys, rgba[:, :, :3].astype(np.float32), alpha))

    def frame(t: float) -> np.ndarray:
        active = [(b, e, f) for b, e, f in spans if b <= t < e] or [spans[-1]]
        b, e, f = active[-1]
        out = f(t - b)
        if len(active) > 1:  # crossfade с предыдущей сценой
            pb, pe, pf = active[-2]
            a = min(1.0, max(0.0, (t - b) / CROSSFADE))
            out = (pf(t - pb).astype(np.float32) * (1 - a) + out.astype(np.float32) * a).astype(np.uint8)
        for s0, s1, ys, rgb, al in subs:
            if s0 <= t < s1:
                out = out.copy() if not out.flags.writeable else out
                region = out[ys:ys + rgb.shape[0]].astype(np.float32)
                out[ys:ys + rgb.shape[0]] = (region * (1 - al) + rgb * al).astype(np.uint8)
                break
        return out

    video = VideoClip(frame, duration=total)

    # --- звук: озвучка + музыка
    voice = [AudioFileClip(a["path"]).with_start(t0) for a, t0 in zip(tts, starts)]
    music_path = pick_music(run_dir, total)
    music = AudioFileClip(str(music_path))
    if music.duration < total:
        music = music.with_effects([afx.AudioLoop(duration=total)])
    else:
        offset = random.uniform(0, max(0.0, min(30.0, music.duration - total - 1)))
        music = music.subclipped(offset, offset + total)
    music = music.with_effects([afx.MultiplyVolume(MUSIC_VOLUME), afx.AudioFadeIn(1.0),
                                afx.AudioFadeOut(MUSIC_FADE_OUT)])
    audio = CompositeAudioClip([music] + voice).with_duration(total)
    video = video.with_audio(audio)

    out = run_dir / out_name
    video.write_videofile(
        str(out), fps=FPS, codec="libx264", audio_codec="aac", audio_bitrate="192k",
        preset=env("X264_PRESET", "medium"), threads=os.cpu_count() or 2,
        ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        logger=None,
    )
    for c in voice + [music]:
        c.close()
    credit_file = music_path.with_suffix(".txt")
    save_json(run_dir / "assembly.json", {
        "duration": round(total, 2), "scene_starts": [round(x, 3) for x in starts],
        "music": music_path.name,
        "music_credit": credit_file.read_text(encoding="utf-8").strip() if credit_file.exists() else None,
    })
    log.info("Видео собрано: %s (%.1f МБ)", out, out.stat().st_size / 1e6)
    return out
