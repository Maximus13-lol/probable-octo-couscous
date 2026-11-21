"""Кинематографическое превью 1280×720 для длинных выпусков «Папки №13»."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .cover import YELLOW

SIZE = (1280, 720)
MAX_BYTES = 2_000_000
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Oswald.ttf"
DARK = "#101319"


def _font(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), size)
    font.set_variation_by_axes([weight])
    return font


def _photo(path: Path) -> Image.Image:
    with Image.open(path) as original:
        return ImageOps.exif_transpose(original).convert("RGB")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if not lines or draw.textlength(f"{lines[-1]} {word}", font=font) > width:
            lines.append(word)
        else:
            lines[-1] += f" {word}"
    return lines


def _backdrop(source: Path, subject: Path | None) -> Image.Image:
    photo = _photo(source)
    if subject is None:
        background = ImageOps.fit(photo, SIZE, Image.Resampling.LANCZOS, centering=(0.62, 0.38))
    else:
        background = ImageOps.fit(photo, SIZE, Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(3))
        background = ImageEnhance.Brightness(background).enhance(0.58)
        portrait = ImageOps.fit(_photo(subject), (650, 720), Image.Resampling.LANCZOS, centering=(0.5, 0.19))
        portrait = ImageEnhance.Contrast(portrait).enhance(1.12)
        portrait = ImageEnhance.Color(portrait).enhance(0.87)
        blend = Image.new("L", (650, 1))
        blend.putdata([min(255, round(x * 255 / 180)) for x in range(650)])
        background.paste(portrait, (630, 0), blend.resize(portrait.size))
    shade = Image.new("L", (SIZE[0], 1))
    shade.putdata([round(240 * (1 - x / (SIZE[0] - 1)) ** 2.25) for x in range(SIZE[0])])
    return Image.composite(Image.new("RGB", SIZE, DARK), background, shade.resize(SIZE))


def make_preview(source: Path, headline: str, output: Path, *, subject: Path | None = None) -> Path:
    """Сохраняет JPEG. Subject — необязательный редакторский выбор главного кадра."""
    if not headline.strip():
        raise ValueError("Нужен заголовок превью")
    image = _backdrop(source, subject)
    draw = ImageDraw.Draw(image)
    brand = _font(32, 600)
    draw.rectangle((63, 67, 71, 113), fill=YELLOW)
    draw.text((88, 90), "ПАПКА №13", font=brand, fill="white", anchor="lm", stroke_width=1, stroke_fill=DARK)
    width = 625 if subject else 700
    for size in range(146, 55, -2):
        font = _font(size)
        lines = _wrap(draw, headline.upper(), font, width)
        if len(lines) <= 3 and all(draw.textlength(line, font=font) <= width for line in lines):
            if len(lines) <= 2 or size <= 102:
                break
    else:
        raise ValueError("Сократите заголовок превью до трёх строк")
    step = round(size * 1.08)
    y = 365 - (len(lines) - 1) * step // 2
    for index, line in enumerate(lines):
        draw.text((61, y), line, font=font, fill=YELLOW if index == len(lines) - 1 else "#fffaf1",
                  stroke_width=3, stroke_fill=DARK, anchor="lm")
        y += step
    output.parent.mkdir(parents=True, exist_ok=True)
    for quality in (93, 85, 72):
        image.save(output, "JPEG", quality=quality, optimize=True)
        if output.stat().st_size <= MAX_BYTES:
            return output
    raise ValueError("Превью превысило лимит размера")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("headline")
    parser.add_argument("output", type=Path)
    parser.add_argument("--subject", type=Path)
    args = parser.parse_args()
    make_preview(args.image, args.headline, args.output, subject=args.subject)


if __name__ == "__main__":
    main()
