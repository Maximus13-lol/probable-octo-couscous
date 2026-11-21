"""Горизонтальное превью «Папки №13» для длинного выпуска."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .cover import YELLOW
from .utils import find_font

SIZE = (1280, 720)
MAX_BYTES = 2_000_000


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if not lines or draw.textlength(f"{lines[-1]} {word}", font=font) > width:
            lines.append(word)
        else:
            lines[-1] += f" {word}"
    return lines


def make_preview(source: Path, headline: str, output: Path) -> Path:
    """Сохраняет 16:9 JPEG. Для превью передаётся короткий редакторский заголовок."""
    if not headline.strip():
        raise ValueError("Нужен заголовок превью")
    with Image.open(source) as original:
        photo = ImageOps.fit(ImageOps.exif_transpose(original).convert("RGB"), SIZE)
    shade = Image.new("L", (SIZE[0], 1))
    shade.putdata([int(230 - 175 * x / (SIZE[0] - 1)) for x in range(SIZE[0])])
    photo = Image.composite(Image.new("RGB", SIZE, "#111520"), photo, shade.resize(SIZE))
    draw = ImageDraw.Draw(photo)
    font_path = find_font()
    brand = ImageFont.truetype(font_path, 36)
    draw.rounded_rectangle((64, 66, 372, 126), radius=8, fill="#a51e26")
    draw.text((82, 96), "ПАПКА №13", font=brand, fill="white", anchor="lm")
    for size in range(100, 43, -4):
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(draw, headline.upper(), font, 700)
        if len(lines) <= 3 and all(draw.textlength(line, font=font) <= 700 for line in lines):
            break
    else:
        raise ValueError("Сократите заголовок превью до трёх строк")
    y = 360 - (len(lines) - 1) * (size + 12) // 2
    for index, line in enumerate(lines):
        draw.text((64, y), line, font=font, fill=YELLOW if index == len(lines) - 1 else "white",
                  stroke_width=3, stroke_fill="#111520", anchor="lm")
        y += size + 12
    output.parent.mkdir(parents=True, exist_ok=True)
    for quality in (90, 80, 65):
        photo.save(output, "JPEG", quality=quality, optimize=True)
        if output.stat().st_size <= MAX_BYTES:
            return output
    raise ValueError("Превью превысило лимит размера")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("headline")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    make_preview(args.image, args.headline, args.output)


if __name__ == "__main__":
    main()
