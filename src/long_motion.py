"""Motion graphics for the long-format series."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .long_preview import FONT_PATH, SIZE, YELLOW


def intro_frames(output: Path) -> None:
    title = ImageFont.truetype(str(FONT_PATH), 112)
    small = ImageFont.truetype(str(FONT_PATH), 26)
    for index in range(65):
        progress = min(1.0, index / 29)
        eased = 1 - (1 - progress) ** 3
        x = round(300 + eased * 190)
        opacity = min(1.0, index / 19)
        image = Image.new("RGB", SIZE, "#101319")
        draw = ImageDraw.Draw(image)
        for grid in range(32, 1280, 64):
            draw.line((grid, 0, grid, 720), fill="#1a202a")
        draw.rounded_rectangle((x - 318, 218, x - 40, 476), radius=17, fill="#262d38", outline="#818996", width=2)
        draw.polygon([(x - 291, 211), (x - 183, 211), (x - 158, 242), (x - 291, 242)], fill=YELLOW)
        draw.text((x - 181, 346), "13", font=title, fill=YELLOW, anchor="mm")
        draw.text((x + 22, 328), "ПАПКА №13", font=title,
                  fill=tuple(round(value * opacity) for value in (255, 249, 233)), anchor="lm")
        draw.text((x + 28, 428), "АРХИВНЫЕ РАССЛЕДОВАНИЯ", font=small, fill="#bdc6d4", anchor="lm")
        draw.rectangle((x + 28, 470, x + 28 + round(537 * eased), 473), fill=YELLOW)
        image.save(output / f"intro-{index:03}.png")


