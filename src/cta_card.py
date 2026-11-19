"""Современная концовка «подписка»: стеклянная карточка канала (как в приложении YouTube) с пружинной
анимацией. Аватар канала, название, строка «Новые дела каждый день», кнопка «Подписаться», которая
нажимается и превращается в «Вы подписаны ✓», мягкая волна от нажатия. Без клипарта и курсоров.

Карточка над субтитрами (верх кадра), фон под ней размывается — «glassmorphism».
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .utils import ROOT, VIDEO_W, find_font

SS = 2                       # суперсэмплинг статичных слоёв (сглаживание)
CARD_W, CARD_H = 860, 250
CARD_X = (VIDEO_W - CARD_W) // 2
CARD_Y = 300                 # верх карточки в кадре
RADIUS = 44
T_TAP = 1.25                 # момент «нажатия» кнопки
LOGO = ROOT / "assets" / "brand" / "logo.png"


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(), size)


def _spring(t: float, k: float = 9.0, damp: float = 5.5) -> float:
    """Пружина 0→1 с лёгким перелётом (как UI-анимации iOS/Android)."""
    if t <= 0:
        return 0.0
    return 1 - math.exp(-damp * t) * math.cos(k * t)


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


@lru_cache(maxsize=None)
def _card_static(name: str, subtitle: str) -> Image.Image:
    """Карточка без кнопки: полупрозрачная подложка, тонкая обводка, аватар, тексты."""
    w, h = CARD_W * SS, CARD_H * SS
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=RADIUS * SS, fill=(18, 18, 22, 150),
                        outline=(255, 255, 255, 60), width=2 * SS)
    # аватар-кружок
    av = 150 * SS
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA").resize((av, av), Image.LANCZOS)
    else:
        logo = Image.new("RGBA", (av, av), (200, 30, 30, 255))
    mask = Image.new("L", (av, av), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, av - 1, av - 1), fill=255)
    ax, ay = 40 * SS, (h - av) // 2
    ring = Image.new("RGBA", (av + 8 * SS, av + 8 * SS), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring.width - 1, ring.height - 1), fill=(255, 214, 0, 255))
    im.alpha_composite(ring, (ax - 4 * SS, ay - 4 * SS))
    im.paste(logo, (ax, ay), mask)
    tx = ax + av + 34 * SS
    d.text((tx, 70 * SS), name, font=_font(54 * SS), fill=(255, 255, 255, 255), anchor="lm")
    d.text((tx, 125 * SS), subtitle, font=_font(30 * SS), fill=(200, 200, 205, 255), anchor="lm")
    return im.resize((CARD_W, CARD_H), Image.LANCZOS)


@lru_cache(maxsize=None)
def _button(label: str, fill: tuple, check: bool) -> Image.Image:
    w, h = 400 * SS, 78 * SS
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=fill)
    f = _font(32 * SS)
    text = label
    if check:
        tw = d.textlength(text, font=f)
        cx0 = (w - tw) / 2 - 26 * SS
        cy = h / 2
        d.line([(cx0 - 16 * SS, cy), (cx0 - 6 * SS, cy + 10 * SS), (cx0 + 12 * SS, cy - 12 * SS)],
               fill=(255, 255, 255, 255), width=5 * SS, joint="curve")
        d.text((cx0 + 26 * SS, cy), text, font=f, fill=(255, 255, 255, 255), anchor="lm")
    else:
        d.text((w / 2, h / 2), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
    return im.resize((w // SS, h // SS), Image.LANCZOS)


def apply(frame: np.ndarray, t: float, cfg: dict) -> np.ndarray:
    """Кадр с карточкой в момент t (секунды от начала концовки)."""
    name = cfg.get("channel_name") or "Папка №13"
    subtitle = cfg.get("card_subtitle", "Новые дела каждый день")
    appear = _spring(t / 0.9)                       # въезд снизу с пружиной
    alpha = _ease(t / 0.25)
    if alpha <= 0:
        return frame
    base = Image.fromarray(frame).convert("RGBA")
    dy = int((1 - appear) * 140)
    x, y = CARD_X, CARD_Y + dy
    # «стекло»: размытый фон под карточкой
    box = (x, y, x + CARD_W, y + CARD_H)
    region = base.crop(box).filter(ImageFilter.GaussianBlur(18))
    mask = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=RADIUS, fill=int(255 * alpha))
    base.paste(region, box[:2], mask)
    card = _card_static(name, subtitle)
    if alpha < 1:
        card = card.copy()
        card.putalpha(Image.fromarray((np.asarray(card.getchannel("A")) * alpha).astype(np.uint8)))
    base.alpha_composite(card, (x, y))
    # кнопка: красная → нажатие (сжатие) → серая «Вы подписаны ✓»
    subscribed = t >= T_TAP
    btn = _button("Вы подписаны", (70, 70, 76, 255), True) if subscribed else _button("Подписаться", (229, 32, 39, 255), False)
    press = 1.0
    if T_TAP - 0.12 <= t < T_TAP + 0.25:
        k = (t - (T_TAP - 0.12)) / 0.37
        press = 1 - 0.08 * math.sin(math.pi * k)
    bw, bh = int(btn.width * press), int(btn.height * press)
    b = btn.resize((bw, bh), Image.LANCZOS) if press != 1.0 else btn
    if alpha < 1:
        b = b.copy()
        b.putalpha(Image.fromarray((np.asarray(b.getchannel("A")) * alpha).astype(np.uint8)))
    bx = x + CARD_W - 40 - btn.width + (btn.width - bw) // 2
    by = y + CARD_H + 26 + (btn.height - bh) // 2
    base.alpha_composite(b, (bx, by))
    # волна от нажатия
    k = (t - T_TAP) / 0.55
    if 0 <= k <= 1:
        ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
        cx, cy = bx + bw // 2, by + bh // 2
        r = 40 + 220 * _ease(k)
        ImageDraw.Draw(ov).ellipse((cx - r, cy - r * 0.45, cx + r, cy + r * 0.45),
                                   outline=(255, 255, 255, int(170 * (1 - k))), width=5)
        base.alpha_composite(ov)
    return np.asarray(base.convert("RGB"))
