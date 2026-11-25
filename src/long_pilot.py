"""Сборка черновика длинного выпуска из редакторского досье. Загрузки на YouTube нет."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .long_preview import SIZE, make_preview
from .utils import find_font


def dossier_image(root: Path, relative_path: str) -> Path:
    image = (root / relative_path).resolve()
    if not image.is_relative_to(root.resolve()) or not image.is_file():
        raise ValueError("Изображение должно находиться рядом с досье или в его подпапке")
    return image


def archive_path(root: Path, visual: dict) -> Path:
    for key in ("image", "credit", "license", "source_url"):
        if not isinstance(visual.get(key), str) or not visual[key].strip():
            raise ValueError(f"Архивный кадр: заполните {key}")
    url = urlsplit(visual["source_url"])
    if url.scheme != "https" or not url.hostname:
        raise ValueError("Архивный кадр: укажите HTTPS-адрес источника")
    return dossier_image(root, visual["image"])


def archive_count(dossier: dict) -> int:
    visuals = [visual for chapter in dossier["chapters"] for visual in [chapter, *chapter.get("visuals", [])]]
    paths = {visual["image_path"] for visual in visuals if "image_path" in visual}
    return len({hashlib.sha256(path.read_bytes()).digest() for path in paths})


def read_dossier(path: Path) -> dict:
    dossier = json.loads(path.read_text(encoding="utf-8"))
    for key in ("title", "headline"):
        if not isinstance(dossier.get(key), str) or not dossier[key].strip():
            raise ValueError(f"Досье: заполните {key}")
    chapters = dossier.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("Досье: нужна хотя бы одна глава")
    if "opening" in dossier and (not isinstance(dossier["opening"], str) or not dossier["opening"].strip()):
        raise ValueError("Досье: opening должен содержать текст анонса")
    for chapter in chapters:
        for key in ("text", "image", "credit", "license", "source_url"):
            if not isinstance(chapter.get(key), str) or not chapter[key].strip():
                raise ValueError(f"Глава: заполните {key}")
        chapter["image_path"] = archive_path(path.parent, chapter)
        visuals = chapter.get("visuals", [])
        if not isinstance(visuals, list):
            raise ValueError("Глава: visuals должен быть списком")
        for visual in visuals:
            if "prompt" in visual:
                if not isinstance(visual["prompt"], str) or not visual["prompt"].strip():
                    raise ValueError("Укажите описание иллюстрации")
            else:
                visual["image_path"] = archive_path(path.parent, visual)
    if "preview_image" in dossier:
        subject = dossier["preview_image"]
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("Досье: preview_image должен содержать путь к изображению")
        dossier["preview_path"] = dossier_image(path.parent, subject)
    return dossier


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *args], check=True)


def render_frame(chapter: dict, path: Path, backdrop: Path) -> None:
    with Image.open(chapter["image_path"]) as original:
        original = ImageOps.exif_transpose(original).convert("RGB")
        background = ImageOps.fit(original, SIZE).filter(ImageFilter.GaussianBlur(26))
        ImageEnhance.Brightness(background).enhance(0.32).save(backdrop)
        image = ImageOps.contain(original, (1200, 620))
    frame = Image.new("RGBA", SIZE)
    frame.paste(image, ((1280 - image.width) // 2, (660 - image.height) // 2))
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype(find_font(), 26)
    label = f'{chapter["credit"]} · {chapter["license"]}'
    while draw.textlength(label, font=font) > 1180:
        label = label[:-2].rstrip("…") + "…"
    draw.text((48, 677), label, fill="white", font=font, anchor="lm")
    frame.save(path)


CHANNEL_NAME = re.compile(
    r"\bпапка[\s,.:;—–-]*(?:номер[\s,.:;—–-]*|№\s*)(?:13|тринадцать)\b", re.I
)


def spoken_channel_name(text: str) -> str:
    return CHANNEL_NAME.sub("Папка номер тринадцать", text)


def narration_scenes(chapters: list[dict]) -> list[dict]:
    scenes = []
    for chapter in chapters:
        visuals = [chapter, *chapter.get("visuals", [])]
        shot = 0
        words = re.findall(r"\S*Папка номер тринадцать\S*|\S+", spoken_channel_name(chapter["text"]))
        if len(visuals) > len(words):
            raise ValueError("В главе больше кадров, чем слов для их озвучки")
        while words:
            remaining_shots = max(1, len(visuals) - shot)
            max_words = (len(words) + remaining_shots - 1) // remaining_shots
            limit = min(max_words, 32)
            if limit < len(words):
                limit = next((i for i in range(limit, 15, -1) if re.search(r"[.!?…,:;]$", words[i - 1])), limit)
            visual = visuals[shot % len(visuals)]
            scenes.append({**chapter, **visual, "text": " ".join(words[:limit]), "index": len(scenes) + 1})
            shot += 1
            words = words[limit:]
    return scenes


def render_intro(output: Path) -> Path | None:
    source = Path(__file__).resolve().parents[1] / "assets" / "long-intro.mp4"
    if not source.is_file():
        return None
    clip = output / "intro.mp4"
    ffmpeg("-i", str(source), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4",
           "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=25",
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           *speech_tempo, "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(clip))
    return clip


def resolve_illustrations(scenes: list[dict], output: Path) -> None:
    from .images import _download

    for scene in scenes:
        if "prompt" not in scene:
            continue
        prompt = (scene["prompt"] + ", documentary editorial illustration, cinematic horizontal framing, "
                  "main subject in the center, no text, no logos")
        seed = int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:4], "big") % (2**31)
        path = output / f"illustration-{seed}.jpg"
        if not path.exists():
            _download(prompt, seed).save(path, quality=92)
        scene.update(image_path=path, credit="Сгенерированная иллюстрация",
                     license="Визуальная реконструкция", source_url="https://pollinations.ai/")


SECTION_ORDER = ("context", "event", "investigation", "outcome")


def validate_editorial(dossier: dict, *, require_sections: bool = False) -> None:
    chapters = dossier["chapters"]
    sections = [chapter.get("section") for chapter in chapters]
    if require_sections or any(section is not None for section in sections):
        if any(section not in SECTION_ORDER for section in sections):
            raise ValueError("Укажите section для каждой главы: context, event, investigation или outcome")
        distinct = list(dict.fromkeys(sections))
        ranks = [SECTION_ORDER.index(section) for section in sections]
        if tuple(distinct) != SECTION_ORDER or ranks != sorted(ranks):
            raise ValueError("Главы должны идти по порядку: контекст → событие → расследование → итог")
    texts = [("приветствие", dossier["opening"])] if dossier.get("opening") else []
    texts.extend((f"глава {index}", chapter["text"]) for index, chapter in enumerate(chapters, 1))
    seen = {}
    for label, text in texts:
        words = re.findall(r"[\w]+", text.casefold().replace("ё", "е"))
        if len(words) < 5:
            continue
        width = min(12, len(words))
        for index in range(len(words) - width + 1):
            excerpt = tuple(words[index:index + width])
            previous = seen.get(excerpt)
            if previous is not None:
                previous_label, previous_index = previous
                if previous_label != label or index - previous_index >= width:
                    raise ValueError(f"Повтор между фрагментами «{previous_label}» и «{label}»: {' '.join(excerpt)}")
            else:
                seen[excerpt] = (label, index)


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
        validate_editorial(dossier)
        from . import narration, tts

        if opening := dossier.get("opening"):
            chapters = [{**chapters[0], "text": opening, "visuals": []}, *chapters]
        chapters = narration_scenes(chapters)
        resolve_illustrations(chapters, output)
        board = {"scenes": [{"index": scene["index"], "text": scene["text"]} for scene in chapters]}
        voices = tts.synthesize(narration.direct(board, output), output, vosk_only=True)
    speech_tempo = [] if smoke else ["-af", "atempo=1.06"]
    intro = render_intro(output)
    clips = [intro.name] if intro else []
    for index, (chapter, voice) in enumerate(zip(chapters, voices, strict=True), start=1):
        frame = output / f"frame-{index:03d}.png"
        clip = output / f"clip-{index:03d}.mp4"
        backdrop = output / f"backdrop-{index:03d}.jpg"
        render_frame(chapter, frame, backdrop)
        direction = 1 if index % 2 else -1
        motion = ("[0:v]scale=1344:756,crop=1280:720:x='32-12*sin(t/8)':y=18[bg];"
                  f"[bg][1:v]overlay=x='{direction}*16*sin(t/7)':y='5*sin(t/8)'[v]")
        ffmpeg("-loop", "1", "-framerate", "25", "-i", str(backdrop),
               "-loop", "1", "-framerate", "25", "-i", str(frame), "-i", voice["path"],
               "-filter_complex", motion, "-map", "[v]", "-map", "2:a:0", "-c:v", "libx264",
               "-preset", "fast", "-pix_fmt", "yuv420p",
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

