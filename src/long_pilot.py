"""Сборка черновика длинного выпуска из редакторского досье. Загрузки на YouTube нет."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .long_preview import SIZE, make_preview
from .utils import find_font


def dossier_image(root: Path, relative_path: str) -> Path:
    image = (root / relative_path).resolve()
    if not image.is_relative_to(root.resolve()) or not image.is_file():
        raise ValueError("Изображение должно находиться рядом с досье или в его подпапке")
    return image


def read_dossier(path: Path) -> dict:
    dossier = json.loads(path.read_text(encoding="utf-8"))
    for key in ("title", "headline"):
        if not isinstance(dossier.get(key), str) or not dossier[key].strip():
            raise ValueError(f"Досье: заполните {key}")
    chapters = dossier.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("Досье: нужна хотя бы одна глава")
    for chapter in chapters:
        for key in ("text", "image", "credit", "license", "source_url"):
            if not isinstance(chapter.get(key), str) or not chapter[key].strip():
                raise ValueError(f"Глава: заполните {key}")
        url = urlsplit(chapter["source_url"])
        if url.scheme != "https" or not url.hostname:
            raise ValueError("Глава: укажите HTTPS-адрес источника")
        chapter["image_path"] = dossier_image(path.parent, chapter["image"])
    if "preview_image" in dossier:
        subject = dossier["preview_image"]
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("Досье: preview_image должен содержать путь к изображению")
        dossier["preview_path"] = dossier_image(path.parent, subject)
    return dossier


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *args], check=True)


def render_frame(chapter: dict, path: Path) -> None:
    with Image.open(chapter["image_path"]) as original:
        image = ImageOps.contain(ImageOps.exif_transpose(original).convert("RGB"), (1280, 660))
    frame = Image.new("RGB", SIZE, "#111520")
    frame.paste(image, ((1280 - image.width) // 2, (660 - image.height) // 2))
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype(find_font(), 26)
    label = f'{chapter["credit"]} · {chapter["license"]}'
    while draw.textlength(label, font=font) > 1180:
        label = label[:-2].rstrip("…") + "…"
    draw.text((48, 690), label, fill="white", font=font, anchor="lm")
    frame.save(path)


def render(dossier: dict, output: Path, *, smoke: bool = False) -> Path:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    chapters = dossier["chapters"]
    make_preview(chapters[0]["image_path"], dossier["headline"], output / "thumbnail.jpg",
                 subject=dossier.get("preview_path"))
    if smoke:
        audio = output / "test-tone.wav"
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio))
        voices = [{"path": str(audio)} for chapter in chapters]
    else:
        from . import narration, tts

        board = {"scenes": [
            {"index": index, "text": chapter["text"]}
            for index, chapter in enumerate(chapters, start=1)
        ]}
        voices = tts.synthesize(narration.direct(board, output), output, author_only=True)
    clips = []
    for index, (chapter, voice) in enumerate(zip(chapters, voices, strict=True), start=1):
        frame = output / f"frame-{index:03d}.png"
        clip = output / f"clip-{index:03d}.mp4"
        render_frame(chapter, frame)
        ffmpeg("-loop", "1", "-framerate", "25", "-i", str(frame), "-i", voice["path"],
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(clip))
        clips.append(clip.name)
    concat = output / "clips.txt"
    concat.write_text("".join(f"file '{name}'\n" for name in clips), encoding="utf-8")
    video = output / "pilot.mp4"
    ffmpeg("-f", "concat", "-safe", "1", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(video))
    (output / "sources.json").write_text(
        json.dumps([{key: chapter[key] for key in ("source_url", "credit", "license")} for chapter in chapters],
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--smoke", action="store_true", help="Только технический тест с синусом вместо речи")
    args = parser.parse_args()
    render(read_dossier(args.dossier), args.output, smoke=args.smoke)


if __name__ == "__main__":
    main()
