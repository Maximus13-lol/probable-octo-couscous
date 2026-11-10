"""Субтитры из таймингов Azure (WordBoundary): фразы по 2–4 слова, крупный жирный шрифт с обводкой,
внизу по центру (выше нижней подписи Shorts), активное слово подсвечивается."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .utils import VIDEO_W, find_font, log

FONT_SIZE = 86
STROKE = 9
MAX_TEXT_W = 820           # не заезжаем на правую панель кнопок Shorts
CENTER_Y = 1240            # центр блока субтитров (нижняя подпись Shorts начинается ~1450)
SUB_ZONE = (1060, 1420)    # вертикальная зона субтитров — её избегает рекламный модуль
COLOR = (255, 255, 255)
HIGHLIGHT = (255, 214, 0)
STROKE_COLOR = (0, 0, 0)
MIN_WORDS, MAX_WORDS, MAX_CHARS = 2, 4, 20
BREAK_RE = re.compile(r"[.!?…,:;—–»)]$")
SENT_END_RE = re.compile(r"[.!?…]»?$")


@dataclass
class Word:
    text: str      # как в тексте сцены, с пунктуацией
    start: float
    end: float


def _n(s: str) -> str:
    s = s.replace("\u0301", "").replace("ё", "е").replace("Ё", "Е").lower()
    return "".join(ch for ch in s if ch.isalnum())


def align_words(scene_text: str, boundaries: list[dict], offset: float, scene_dur: float) -> list[Word]:
    """Сопоставляет слова текста сцены (с пунктуацией и «ё») с WordBoundary от Azure."""
    tokens = [t for t in scene_text.split() if _n(t)]
    out: list[Word] = []
    j = 0
    for tok in tokens:
        target = _n(tok)
        if j >= len(boundaries):
            out.append(Word(tok, -1, -1))
            continue
        start, end, acc = boundaries[j]["start"], boundaries[j]["end"], ""
        while j < len(boundaries) and len(acc) < len(target):
            acc += _n(boundaries[j]["word"])
            end = boundaries[j]["end"]
            j += 1
        out.append(Word(tok, start + offset, end + offset))
    # хвостовые токены без таймингов (расхождение токенизации) — равномерно до конца сцены
    missing = [w for w in out if w.start < 0]
    if missing:
        last_end = max((w.end for w in out if w.start >= 0), default=offset)
        span = max(0.2, (offset + scene_dur - last_end) / len(missing))
        for k, w in enumerate(missing):
            w.start, w.end = last_end + k * span, last_end + (k + 1) * span
    return out


def chunk_words(words: list[Word]) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        cur.append(w)
        chars = sum(len(x.text) for x in cur) + len(cur) - 1
        if len(cur) >= MAX_WORDS or chars >= MAX_CHARS or (BREAK_RE.search(w.text) and len(cur) >= MIN_WORDS) \
                or SENT_END_RE.search(w.text):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    # одиночное слово «прилипает» к соседу, если это не конец предложения и влезает
    merged: list[list[Word]] = []
    for c in chunks:
        if merged and len(c) == 1 and len(merged[-1]) < MAX_WORDS and not SENT_END_RE.search(merged[-1][-1].text):
            merged[-1] += c
        else:
            merged.append(c)
    return merged


_FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    if size not in _FONTS:
        _FONTS[size] = ImageFont.truetype(find_font(), size)
    return _FONTS[size]


def _layout(words: list[str], font) -> list[list[int]]:
    """Перенос строк: возвращает индексы слов по строкам."""
    lines, cur = [], []
    for i, w in enumerate(words):
        test = " ".join(words[k] for k in cur + [i])
        if cur and font.getlength(test) > MAX_TEXT_W:
            lines.append(cur)
            cur = [i]
        else:
            cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def render_chunk(words: list[str], active: int | None) -> np.ndarray:
    """RGBA-картинка фразы; active — индекс подсвеченного слова."""
    words = [w.upper() for w in words]
    size = FONT_SIZE
    font = _font(size)
    lines = _layout(words, font)
    while len(lines) > 2 and size > 60:  # не больше двух строк: уменьшаем шрифт
        size -= 8
        font = _font(size)
        lines = _layout(words, font)
    line_h = int(size * 1.12)
    pad = STROKE + 6
    w_img = VIDEO_W
    h_img = line_h * len(lines) + pad * 2
    img = Image.new("RGBA", (w_img, h_img), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    space = font.getlength(" ")
    for li, idxs in enumerate(lines):
        total = sum(font.getlength(words[k]) for k in idxs) + space * (len(idxs) - 1)
        x = (w_img - total) / 2
        y = pad + li * line_h
        for k in idxs:
            color = HIGHLIGHT if k == active else COLOR
            d.text((x, y), words[k], font=font, fill=color, stroke_width=STROKE, stroke_fill=STROKE_COLOR)
            x += font.getlength(words[k]) + space
    return np.array(img)


@dataclass
class SubEvent:
    image: np.ndarray
    start: float
    end: float


def build_events(chunks: list[list[Word]], total_dur: float) -> list[SubEvent]:
    events: list[SubEvent] = []
    for ci, chunk in enumerate(chunks):
        texts = [w.text for w in chunk]
        nxt = chunks[ci + 1][0].start if ci + 1 < len(chunks) else total_dur
        chunk_end = min(nxt, chunk[-1].end + 0.6)
        for wi, w in enumerate(chunk):
            start = w.start if wi else w.start
            end = chunk[wi + 1].start if wi + 1 < len(chunk) else chunk_end
            if end - start < 0.02:
                continue
            events.append(SubEvent(render_chunk(texts, wi), start, end))
    return events


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def write_srt(chunks: list[list[Word]], path: Path) -> None:
    lines = []
    for i, c in enumerate(chunks, 1):
        lines += [str(i), f"{_ts(c[0].start)} --> {_ts(c[-1].end)}", " ".join(w.text for w in c), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_subtitles(board: dict, tts: list[dict], starts: list[float], total_dur: float, run_dir: Path):
    words: list[Word] = []
    for scene, audio, t0 in zip(board["scenes"], tts, starts):
        words += align_words(scene.get("display_text") or scene["text"], audio["words"], t0, audio["duration"])
    chunks = chunk_words(words)
    write_srt(chunks, run_dir / "subtitles.srt")
    events = build_events(chunks, total_dur)
    log.info("Субтитры: %d фраз, %d кадров-подсветок", len(chunks), len(events))
    return events
