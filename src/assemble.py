"""video-assembly: сцена длится ровно столько, сколько её озвучка; Ken Burns; crossfade 0.3 с;
субтитры; фоновая лоу-фай музыка ~12% с затуханием."""
from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, afx
from PIL import Image

from . import cta, music_gen
from .subtitles import CENTER_Y, build_subtitles
from .utils import FPS, MUSIC_DIR, VIDEO_H, VIDEO_W, env, log, save_json

CROSSFADE = 0.3
PAUSE = 0.2         # пауза между сценами по умолчанию (обычно её задаёт narration-director)
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

    brand_layers = _brand_layers(brand) if brand else []

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


def _text_layer(text: str, size: int, fill, bg=None, pad=(18, 10), opacity: float = 1.0):
    from PIL import Image, ImageDraw, ImageFont
    from .utils import find_font
    font = ImageFont.truetype(find_font(), size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), text, font=font, stroke_width=3)
    w, h = r - l + 2 * pad[0], b - t + 2 * pad[1]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bg:
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=14, fill=bg)
    d.text((pad[0] - l, pad[1] - t), text, font=font, fill=fill, stroke_width=0 if bg else 3, stroke_fill="black")
    arr = np.asarray(img).astype(np.float32)
    return arr[:, :, :3], arr[:, :, 3:4] / 255.0 * opacity


def _brand_layers(brand: dict) -> list:
    """Водяной знак канала (весь ролик) и плашка рубрики (0,3–3,3 с)."""
    layers = []
    if brand.get("channel"):
        rgb, al = _text_layer(brand["channel"], 34, (255, 255, 255, 255), opacity=0.55)
        layers.append((0.0, 1e9, (110, 40, rgb, al)))
    if brand.get("rubric"):
        rgb, al = _text_layer(brand["rubric"], 46, (255, 255, 255, 255), bg=(200, 30, 30, 235))
        layers.append((0.3, 3.3, (170, (VIDEO_W - rgb.shape[1]) // 2, rgb, al)))
    return layers


def timeline(tts: list[dict]) -> tuple[list[float], float]:
    starts, t = [], 0.0
    for a in tts:
        starts.append(t)
        t += a["duration"] + a.get("pause_after", PAUSE)  # паузу после сцены задаёт narration-director
    total = t - tts[-1].get("pause_after", PAUSE) + TAIL
    return starts, total


def assemble(board: dict, images: list[Path], tts: list[dict], run_dir: Path, out_name: str = "video.mp4",
             cta_audio: dict | None = None, brand: dict | None = None) -> Path:
    starts, total = timeline(tts)
    n = len(tts)
    story_end = total
    # --- концовка «лайк и подписка»: после последней фразы, поверх последнего кадра
    cta_cfg = cta.config()
    cta_start = None
    if cta_cfg.get("enabled"):
        cta_start = total - TAIL + 0.45
        voice_len = (cta_audio or {}).get("duration", 0.0)
        total = cta_start + max(float(cta_cfg["min_duration"]), voice_len + 0.25 + 0.6)
    log.info("Длительность ролика: %.1f с (история %.1f с + концовка), сцен: %d", total, story_end, n)

    # --- видео-слой: одна функция кадра (Ken Burns + crossfade + субтитры) — в разы быстрее,
    # чем CompositeVideoClip из сотни клипов с полнокадровым альфа-смешиванием
    spans = []  # (begin, end, frame_fn)
    for i, (img, t0) in enumerate(zip(images, starts)):
        begin = t0 - CROSSFADE if i > 0 else 0.0
        end = starts[i + 1] if i + 1 < n else total
        spans.append((begin, end, ken_burns_fn(img, end - begin, i)))

    subs = []
    for ev in build_subtitles(board, tts, starts, cta_start or total, run_dir):
        rgba = ev.image
        h = rgba.shape[0]
        ys = CENTER_Y - h // 2
        alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
        subs.append((ev.start, ev.end, ys, rgba[:, :, :3].astype(np.float32), alpha))

    brand_layers = _brand_layers(brand) if brand else []

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
        for t0, t1, (y0, x0, rgb, al) in brand_layers:
            if t0 <= t < t1 and (cta_start is None or t < cta_start):
                fade = min(1.0, (t - t0) / 0.25, (t1 - t) / 0.25) if t1 < 1e8 else 1.0
                out = out.copy() if not out.flags.writeable else out
                h, w = rgb.shape[:2]
                region = out[y0:y0 + h, x0:x0 + w].astype(np.float32)
                a = al * fade
                out[y0:y0 + h, x0:x0 + w] = (region * (1 - a) + rgb * a).astype(np.uint8)
        if cta_start is not None and t >= cta_start:
            out = cta.apply(out, t - cta_start, cta_cfg)
        return out

    video = VideoClip(frame, duration=total)

    # --- звук: озвучка + музыка
    voice = [AudioFileClip(a["path"]).with_start(t0) for a, t0 in zip(tts, starts)]
    if cta_start is not None and cta_audio and cta_audio.get("path"):
        voice.append(AudioFileClip(cta_audio["path"]).with_start(cta_start + 0.25))
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
        "cta_start": round(cta_start, 2) if cta_start is not None else None,
        "music": music_path.name,
        "music_credit": credit_file.read_text(encoding="utf-8").strip() if credit_file.exists() else None,
    })
    log.info("Видео собрано: %s (%.1f МБ)", out, out.stat().st_size / 1e6)
    return out
