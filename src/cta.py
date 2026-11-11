"""Концовка ролика: просьба поставить лайк и подписаться — голос + анимация поверх последнего кадра.

Сценарий анимации (t — секунды от начала концовки):
  0.0–0.35  затемнение кадра, заголовок «ПОНРАВИЛАСЬ ИСТОРИЯ?» выезжает сверху
  0.20–0.60 кнопка лайка «выпрыгивает» (ease-out-back)
  0.50–0.90 красная кнопка «ПОДПИСАТЬСЯ» выезжает снизу
  0.90–1.30 палец-курсор подъезжает к лайку → касание: волна, лайк становится синим и подпрыгивает
  1.50–1.90 палец подъезжает к подписке → касание: «ВЫ ПОДПИСАНЫ ✓», колокольчик качается
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .utils import CONFIG_DIR, ROOT, VIDEO_H, VIDEO_W, find_font, load_json

ASSETS = ROOT / "assets" / "cta"
DEFAULTS = {
    "enabled": True,
    "voice_text": "Понравилась история? Ставь лайк и подписывайся — впереди ещё больше тайн!",
    "headline": "ПОНРАВИЛАСЬ\nИСТОРИЯ?",
    "like_label": "ЛАЙК",
    "subscribe_label": "ПОДПИСАТЬСЯ",
    "subscribed_label": "ВЫ ПОДПИСАНЫ",
    "min_duration": 3.2,
    "dim": 0.45,
}

# раскладка (кадр 1080x1920; выше нижней подписи Shorts и левее колонки кнопок)
HEADLINE_Y = 600
LIKE_C = (540, 870)
LIKE_R = 115
SUB_C = (540, 1190)
SUB_W, SUB_H = 760, 140
HAND_START = (900, 1560)
T_LIKE_TAP, T_SUB_TAP = 1.30, 1.90


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(load_json(CONFIG_DIR / "cta.json", default={}) or {})
    return cfg


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ease_out(x: float) -> float:
    x = _clamp01(x)
    return 1 - (1 - x) ** 3


def _ease_out_back(x: float) -> float:
    x = _clamp01(x)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _ease_in_out(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3 - 2 * x)


@lru_cache(maxsize=None)
def _sprite(name: str, size: int) -> Image.Image | None:
    p = ASSETS / f"{name}.png"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGBA")
    k = size / max(im.size)
    return im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(), size)


def _paste_center(base: Image.Image, im: Image.Image, cx: float, cy: float, alpha: float = 1.0) -> None:
    if alpha <= 0:
        return
    if alpha < 1:
        im = im.copy()
        im.putalpha(Image.fromarray((np.asarray(im.getchannel("A")) * alpha).astype(np.uint8)))
    base.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))


def _text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, fill, stroke=6, anchor="mm") -> None:
    draw.text(xy, text, font=_font(size), fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, fill[3] if len(fill) > 3 else 255),
              anchor=anchor)


def _hand_pos(t: float) -> tuple[float, float, float]:
    """Позиция пальца (кончик) и прозрачность."""
    like_target = (LIKE_C[0] + 40, LIKE_C[1] + 60)
    sub_target = (SUB_C[0] + 150, SUB_C[1] + 55)
    if t < 0.9:
        return (*HAND_START, 0.0)
    if t < T_LIKE_TAP:
        k = _ease_in_out((t - 0.9) / (T_LIKE_TAP - 0.9))
        return (HAND_START[0] + (like_target[0] - HAND_START[0]) * k,
                HAND_START[1] + (like_target[1] - HAND_START[1]) * k, k)
    if t < 1.5:
        return (*like_target, 1.0)
    if t < T_SUB_TAP:
        k = _ease_in_out((t - 1.5) / (T_SUB_TAP - 1.5))
        return (like_target[0] + (sub_target[0] - like_target[0]) * k,
                like_target[1] + (sub_target[1] - like_target[1]) * k, 1.0)
    fade = 1 - _clamp01((t - 2.4) / 0.4)
    return (sub_target[0] + 20 * _clamp01((t - T_SUB_TAP) / 0.3),
            sub_target[1] + 30 * _clamp01((t - T_SUB_TAP) / 0.3), fade)


def render_overlay(t: float, cfg: dict | None = None) -> Image.Image:
    """RGBA-слой концовки 1080x1920 в момент t."""
    cfg = cfg or config()
    ov = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    dim = float(cfg["dim"]) * _ease_out(t / 0.35)
    ov.paste((0, 0, 0, int(255 * dim)), (0, 0, VIDEO_W, VIDEO_H))
    d = ImageDraw.Draw(ov)

    # заголовок
    k = _ease_out(t / 0.35)
    if k > 0:
        for i, line in enumerate(cfg["headline"].split("\n")):
            _text(d, (VIDEO_W / 2, HEADLINE_Y - 60 * (1 - k) + i * 92), line, 82, (255, 255, 255, int(255 * k)), stroke=9)

    # кнопка лайка
    pop = _ease_out_back((t - 0.2) / 0.4)
    if pop > 0:
        liked = t >= T_LIKE_TAP
        bounce = 1 + 0.18 * math.sin(math.pi * _clamp01((t - T_LIKE_TAP) / 0.35)) if liked else 1
        r = int(LIKE_R * pop * bounce)
        if r > 2:
            fill = (40, 120, 255, 255) if liked else (255, 255, 255, 235)
            d.ellipse([LIKE_C[0] - r, LIKE_C[1] - r, LIKE_C[0] + r, LIKE_C[1] + r], fill=fill,
                      outline=(0, 0, 0, 255), width=6)
            icon = _sprite("like", max(8, int(r * 1.15)))
            if icon is not None:
                _paste_center(ov, icon, LIKE_C[0], LIKE_C[1] - r * 0.03)
            else:
                _text(d, LIKE_C, "♥", int(r * 1.2), (255, 60, 80, 255), stroke=0)
            lbl_a = _clamp01((t - 0.45) / 0.25)
            if lbl_a > 0:
                _text(d, (LIKE_C[0], LIKE_C[1] + LIKE_R + 48), cfg["like_label"], 46,
                      (255, 255, 255, int(255 * lbl_a)), stroke=6)

    # кнопка подписки
    slide = _ease_out((t - 0.5) / 0.4)
    if slide > 0:
        subscribed = t >= T_SUB_TAP
        cy = SUB_C[1] + 260 * (1 - slide)
        press = 1 - 0.06 * math.sin(math.pi * _clamp01((t - T_SUB_TAP) / 0.25)) if subscribed else 1
        w, h = SUB_W * press, SUB_H * press
        box = [SUB_C[0] - w / 2, cy - h / 2, SUB_C[0] + w / 2, cy + h / 2]
        a = int(255 * _clamp01(slide * 1.5))
        fill = (90, 90, 90, a) if subscribed else (230, 30, 35, a)
        d.rounded_rectangle(box, radius=int(h / 2), fill=fill, outline=(0, 0, 0, a), width=6)
        label = cfg["subscribed_label"] if subscribed else cfg["subscribe_label"]
        _text(d, (SUB_C[0] - 50, cy), label, 52, (255, 255, 255, a), stroke=0)
        icon = _sprite("bell", 76)
        if icon is not None:
            angle = 0.0
            if subscribed:
                ring = _clamp01((t - T_SUB_TAP) / 0.8)
                angle = 22 * math.sin(ring * math.pi * 6) * (1 - ring)
            rotated = icon.rotate(angle, resample=Image.BICUBIC, expand=True)
            _paste_center(ov, rotated, box[2] - 70, cy, alpha=a / 255)

    # волны от касаний
    for tap, (cx, cy) in ((T_LIKE_TAP, LIKE_C), (T_SUB_TAP, (SUB_C[0] + 150, SUB_C[1]))):
        k = (t - tap) / 0.45
        if 0 <= k <= 1:
            rr = 60 + 200 * _ease_out(k)
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(255, 255, 255, int(220 * (1 - k))), width=8)

    # палец
    hx, hy, ha = _hand_pos(t)
    hand = _sprite("hand", 150)
    if hand is not None and ha > 0:
        press = 0.9 if any(0 <= t - tap < 0.12 for tap in (T_LIKE_TAP, T_SUB_TAP)) else 1.0
        h2 = hand.resize((int(hand.width * press), int(hand.height * press)), Image.LANCZOS)
        ov.alpha_composite(_with_alpha(h2, ha), (int(hx - h2.width * 0.45), int(hy - 10)))
    return ov


def _with_alpha(im: Image.Image, a: float) -> Image.Image:
    if a >= 1:
        return im
    im = im.copy()
    im.putalpha(Image.fromarray((np.asarray(im.getchannel("A")) * a).astype(np.uint8)))
    return im


def apply(frame: np.ndarray, t: float, cfg: dict | None = None) -> np.ndarray:
    base = Image.fromarray(frame).convert("RGBA")
    base.alpha_composite(render_overlay(t, cfg))
    return np.asarray(base.convert("RGB"))


def preview(path: Path, times=(0.15, 0.5, 1.0, 1.35, 1.95, 2.6)) -> Path:
    """Раскадровка концовки на сером фоне — для проверки."""
    tiles = []
    for t in times:
        bg = np.full((VIDEO_H, VIDEO_W, 3), 70, np.uint8)
        tiles.append(Image.fromarray(apply(bg, t)).resize((360, 640)))
    sheet = Image.new("RGB", (360 * len(tiles), 640))
    for i, im in enumerate(tiles):
        sheet.paste(im, (360 * i, 0))
    sheet.save(path)
    return path
