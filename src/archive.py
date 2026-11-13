"""Настоящие архивные фото из Wikimedia Commons (общественное достояние / CC0 / CC BY / CC BY-SA).

Gemini придумывает короткие поисковые запросы, Commons отдаёт кандидатов с лицензиями, Gemini выбирает
до max_photos фото, которые ТОЧНО показывают реальное место, человека или предмет из истории, и сцену для
каждого. Фото кладётся вместо ИИ-картинки этой сцены: по центру, на размытом фоне, с подписью
«АРХИВНОЕ ФОТО». Авторство и лицензия попадают в описание ролика.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import gemini
from .utils import VIDEO_H, VIDEO_W, find_font, log, save_json

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "ShortsPipeline/1.0 (educational shorts; https://github.com) python-requests"}
OK_LICENSE = re.compile(r"^(public domain|pd\b|cc0|cc[ -]by(?:[ -]sa)?(?:[ -]\d\.\d)?)", re.I)


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def search(query: str, limit: int = 10) -> list[dict]:
    r = requests.get(API, headers=UA, timeout=25, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1080})
    r.raise_for_status()
    out = []
    pages = list((r.json().get("query") or {}).get("pages", {}).values())
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        lic = _plain(md.get("LicenseShortName", {}).get("value", ""))
        if not OK_LICENSE.search(lic) or ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if min(ii.get("width", 0), ii.get("height", 0)) < 400:
            continue
        out.append({
            "file": p["title"], "page": ii.get("descriptionurl", ""), "url": ii.get("thumburl") or ii.get("url"),
            "license": lic, "artist": _plain(md.get("Artist", {}).get("value", ""))[:80] or "неизвестен",
            "description": _plain(md.get("ImageDescription", {}).get("value", ""))[:220],
            "date": _plain(md.get("DateTimeOriginal", {}).get("value", ""))[:40],
        })
    log.info("Commons «%s»: найдено %d, подходит по лицензии и размеру %d", query, len(pages), len(out))
    return out


def _queries(story: dict, topic_en: str) -> list[str]:
    obj = gemini.generate_json(f"""Нужны НАСТОЯЩИЕ архивные фотографии для ролика (Wikimedia Commons).
Тема: {topic_en}
История: {story['story']}

Придумай 2–4 коротких поисковых запроса на английском (2–5 слов) — конкретные места, люди, здания, предметы,
документы из истории, как они названы в энциклопедиях. Без общих слов вроде "mystery", "ghost".
Если в истории нет реальных конкретных объектов — верни пустой список.
Верни JSON: {{"queries": ["..."]}}""", temperature=0.3)
    return [q for q in (obj.get("queries") or []) if isinstance(q, str) and q.strip()][:4]


def _pick(board: dict, cands: list[dict], max_photos: int) -> list[dict]:
    scenes = "\n".join(f"{s['index']}: {s['text']}" for s in board["scenes"])
    listing = "\n".join(f"{i}: {c['file']} | {c['description']} | {c['date']}" for i, c in enumerate(cands))
    obj = gemini.generate_json(f"""Сцены ролика:
{scenes}

Кандидаты — файлы Wikimedia Commons (номер: имя | описание | дата):
{listing}

Выбери не больше {max_photos} фото, которые ТОЧНО изображают реальное место, человека, предмет или документ
именно из этой истории (не похожее, не «по мотивам»). Для каждого — номер сцены, к которой оно подходит
(не сцена 0), и короткую подпись по-русски (до 45 символов, например «Маяк на острове Эйлеан-Мор»).
Каждая сцена — не больше одного фото. Если подходящих нет — верни пустой список.
Верни JSON: {{"photos": [{{"candidate": 0, "scene": 3, "caption": "..."}}]}}""", temperature=0.2)
    out, used_scenes = [], set()
    n_scenes = len(board["scenes"])
    for p in obj.get("photos") or []:
        try:
            ci, si = int(p["candidate"]), int(p["scene"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= ci < len(cands) and 0 < si < n_scenes and si not in used_scenes:
            used_scenes.add(si)
            out.append({**cands[ci], "scene": si, "caption": str(p.get("caption", ""))[:45]})
    return out[:max_photos]


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(), size)


def render(photo: Image.Image, caption: str) -> Image.Image:
    """Фото целиком по центру на размытом затемнённом фоне + плашка «АРХИВНОЕ ФОТО» и подпись сверху."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    bg = ImageOps.fit(photo, (VIDEO_W, VIDEO_H), method=Image.LANCZOS)
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(40))).enhance(0.45)
    box_w, box_h = VIDEO_W - 80, 800   # фото выше блока субтитров (центр субтитров ~1240)
    fg = ImageOps.contain(photo, (box_w, box_h), method=Image.LANCZOS)
    x, y = (VIDEO_W - fg.width) // 2, 330 + (box_h - fg.height) // 2
    frame = Image.new("RGB", (fg.width + 16, fg.height + 16), (235, 230, 220))  # «паспарту» как у отпечатка
    bg.paste(frame, (x - 8, y - 8))
    bg.paste(fg, (x, y))
    d = ImageDraw.Draw(bg)
    label = "АРХИВНОЕ ФОТО"
    f1 = _font(38)
    w1 = d.textlength(label, font=f1)
    d.rounded_rectangle(((VIDEO_W - w1) / 2 - 22, 170, (VIDEO_W + w1) / 2 + 22, 228), radius=12, fill=(200, 30, 30))
    d.text((VIDEO_W / 2, 199), label, font=f1, fill="white", anchor="mm")
    if caption:
        d.text((VIDEO_W / 2, 280), caption, font=_font(44), fill="white", anchor="mm",
               stroke_width=3, stroke_fill="black")
    return bg


def place_photos(board: dict, story: dict, topic_en: str, run_dir: Path, max_photos: int = 2) -> list[dict]:
    """Кладёт архивные фото в images/scene_XX.jpg (генерация ИИ-картинок эти сцены пропустит).
    Возвращает список использованных фото с авторством. Любая ошибка — просто без архивных фото."""
    try:
        queries = _queries(story, topic_en)
        cands, seen = [], set()
        for q in queries:
            for c in search(q):
                if c["file"] not in seen:
                    seen.add(c["file"])
                    cands.append(c)
        log.info("Архив: запросы %s → %d подходящих по лицензии фото", queries, len(cands))
        if not cands:
            return []
        picks = _pick(board, cands[:30], max_photos)
        img_dir = run_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        used = []
        for p in picks:
            try:
                r = requests.get(p["url"], headers=UA, timeout=60)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content))
                img.load()
                render(img, p["caption"]).save(img_dir / f"scene_{p['scene']:02d}.jpg", quality=93)
                used.append(p)
                log.info("Архивное фото в сцене %d: %s (%s, %s)", p["scene"], p["file"], p["artist"], p["license"])
            except Exception as exc:  # noqa: BLE001
                log.warning("Фото %s не загрузилось: %s", p["file"], exc)
        save_json(run_dir / "archive_photos.json", used)
        return used
    except Exception as exc:  # noqa: BLE001
        log.warning("Архивные фото пропущены: %s", exc)
        return []


def credits(used: list[dict]) -> list[str]:
    return [f"{p['caption'] or p['file']}: {p['artist']}, {p['license']}, Wikimedia Commons — {p['page']}"
            for p in used]
