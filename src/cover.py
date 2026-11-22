"""Вертикальная обложка Shorts из итогового названия и кадра ролика."""
from __future__ import annotations

import re
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .utils import VIDEO_H, VIDEO_W, find_font, log

YELLOW = (255, 214, 0)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(), size)


def _lines(title: str) -> list[str]:
    title = re.sub(r"\s*#\w+", "", title).strip().rstrip(".").upper()
    return textwrap.wrap(title, width=min(28, max(12, (len(title) + 2) // 3))) or ["ПАПКА №13"]


def make_cover(title: str, images: list[Path], run_dir: Path, rubric: str = "",
               photo_scene: int | None = None) -> Path:
    """1080x1920 JPEG: кадр (архивное фото, если есть, иначе первая сцена), затемнение, название в 2–4 строки."""
    src = images[photo_scene] if photo_scene is not None and photo_scene < len(images) else images[0]
    im = ImageOps.fit(Image.open(src).convert("RGB"), (VIDEO_W, VIDEO_H), method=Image.LANCZOS)
    im = ImageEnhance.Brightness(im).enhance(0.62)
    shade = Image.new("L", (1, VIDEO_H))
    for y in range(VIDEO_H):  # плавное затемнение к центру, где стоит текст
        shade.putpixel((0, y), int(150 * max(0.0, 1 - abs(y - 860) / 700)))
    im.paste(Image.new("RGB", im.size, (0, 0, 0)), (0, 0), shade.resize(im.size))
    d = ImageDraw.Draw(im)
    lines = _lines(title)
    size = 128 if max(len(x) for x in lines) <= 11 else 108
    f = _font(size)
    while max(d.textlength(line, font=f) for line in lines) > VIDEO_W - 144 and size > 36:
        size -= 2
        f = _font(size)
    step = int(size * 1.12)
    y0 = 860 - step * len(lines) // 2
    for i, line in enumerate(lines):
        color = YELLOW if i == len(lines) - 1 else (255, 255, 255)  # последнюю строку — акцентом
        d.text((VIDEO_W / 2, y0 + i * step), line, font=f, fill=color, anchor="mm", stroke_width=10, stroke_fill="black")
    if rubric:
        fb = _font(50)
        w = d.textlength(rubric, font=fb)
        top = y0 - step // 2 - 120
        d.rounded_rectangle(((VIDEO_W - w) / 2 - 26, top, (VIDEO_W + w) / 2 + 26, top + 78), radius=16, fill=(200, 30, 30))
        d.text((VIDEO_W / 2, top + 39), rubric, font=fb, fill="white", anchor="mm")
    out = run_dir / "cover.jpg"
    im.save(out, quality=88, optimize=True)
    log.info("Обложка: %s (%d КБ)", out.name, out.stat().st_size // 1024)
    return out


def set_thumbnail(video_id: str, cover: Path, *, wait_for_short: bool = False) -> str:
    """Пробует поставить обложку через API. Возвращает «ok» или причину отказа (без исключений)."""
    try:
        from googleapiclient.http import MediaFileUpload

        from .upload import get_youtube_client
        yt = get_youtube_client()
        if yt is None:
            return "нет ключей YouTube"
        if wait_for_short:
            for attempt in range(13):
                response = yt.videos().list(part="processingDetails,status", id=video_id).execute()
                items = response.get("items", [])
                details = items[0].get("processingDetails", {}) if items else {}
                processing = details.get("processingStatus")
                if processing == "succeeded":
                    break
                if processing in {"failed", "terminated"}:
                    raise RuntimeError(f"YouTube завершил обработку Shorts со статусом {processing}")
                if attempt < 12:
                    time.sleep(5)
            else:
                raise RuntimeError("YouTube ещё обрабатывает Shorts, обложка сохранена в cover.jpg")
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(cover), mimetype="image/jpeg")).execute()
        log.info("Обложка установлена для %s", video_id)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        why = ("канал не подтверждён по телефону — свои обложки недоступны (youtube.com/verify)"
               if "forbidden" in text.lower() or "403" in text else text[:200])
        log.warning("Обложку поставить не удалось: %s", why)
        return why
