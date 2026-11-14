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


def _parse(pages: list[dict]) -> list[dict]:
    out = []
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
    return out


IINFO = {"prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1080}


def search(query: str, limit: int = 10) -> list[dict]:
    r = requests.get(API, headers=UA, timeout=25, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6, "gsrlimit": limit, **IINFO})
    r.raise_for_status()
    pages = list((r.json().get("query") or {}).get("pages", {}).values())
    out = _parse(pages)
    log.info("Commons «%s»: найдено %d, подходит по лицензии и размеру %d", query, len(pages), len(out))
    return out


def _commons_files(titles: list[str]) -> list[dict]:
    """Метаданные файлов Commons (лицензия, автор, ссылка) пачками по 50; отсутствующие на Commons отсеиваются."""
    out = []
    titles = list(dict.fromkeys(titles))
    for i in range(0, len(titles), 50):
        r = requests.get(API, headers=UA, timeout=30, params={
            "action": "query", "format": "json", "titles": "|".join(titles[i:i + 50]), **IINFO})
        r.raise_for_status()
        out += _parse([p for p in (r.json().get("query") or {}).get("pages", {}).values() if "missing" not in p])
    return out


_SKIP_FILE = re.compile(r"(icon|logo|flag|coat[_ ]of[_ ]arms|commons-|wikidata|symbol|signature|locator|blank)", re.I)


def _wiki_page(lang: str, title: str) -> dict:
    """Иллюстрации статьи, её Wikidata-элемент и название английской версии."""
    r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=25, params={
        "action": "query", "format": "json", "titles": title, "redirects": 1,
        "prop": "images|pageprops|langlinks", "imlimit": 50, "lllang": "en", "ppprop": "wikibase_item"})
    r.raise_for_status()
    pg = next(iter((r.json().get("query") or {}).get("pages", {}).values()), {})
    files = [im["title"] for im in pg.get("images", [])
             if re.search(r"\.(jpe?g|png)$", im["title"], re.I) and not _SKIP_FILE.search(im["title"])]
    return {"files": ["File:" + f.split(":", 1)[1] for f in files],
            "qid": (pg.get("pageprops") or {}).get("wikibase_item"),
            "en": next((ll.get("*") for ll in pg.get("langlinks", []) if ll.get("lang") == "en"), None)}


def _commons_category(qid: str | None) -> list[str]:
    """Файлы из категории Commons, привязанной к теме в Wikidata (свойство P373)."""
    if not qid:
        return []
    r = requests.get("https://www.wikidata.org/w/api.php", headers=UA, timeout=25, params={
        "action": "wbgetentities", "format": "json", "ids": qid, "props": "claims"})
    r.raise_for_status()
    claims = (r.json().get("entities", {}).get(qid, {}) or {}).get("claims", {})
    cat = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("P373", [])
                if c.get("mainsnak", {}).get("datavalue")), None)
    if not cat:
        return []
    r = requests.get(API, headers=UA, timeout=25, params={
        "action": "query", "format": "json", "list": "categorymembers", "cmtitle": f"Category:{cat}",
        "cmtype": "file", "cmlimit": 40})
    r.raise_for_status()
    return [m["title"] for m in (r.json().get("query") or {}).get("categorymembers", [])
            if re.search(r"\.(jpe?g|png)$", m["title"], re.I) and not _SKIP_FILE.search(m["title"])]


def from_article(article: dict | None) -> list[dict]:
    """Самые точные кандидаты: иллюстрации статьи-справки (ru и её en-версии) и категория Commons этой темы.
    Файлы, загруженные только в Википедию по «добросовестному использованию», на Commons отсутствуют и отсеиваются."""
    if not article or not article.get("title"):
        return []
    try:
        info = _wiki_page(article.get("lang", "ru"), article["title"])
        files = list(info["files"])
        if info["en"] and article.get("lang") != "en":
            files += _wiki_page("en", info["en"])["files"]
        try:
            files += _commons_category(info["qid"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Категория Commons не получена: %s", exc)
        out = [{**c, "from_article": True} for c in _commons_files(files)] if files else []
        log.info("Статья «%s»%s: файлов %d, со свободной лицензией на Commons %d", article["title"],
                 f" (+ en «{info['en']}»)" if info["en"] else "", len(set(files)), len(out))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("Иллюстрации статьи не получены: %s", exc)
        return []


def _queries(story: dict, topic_en: str) -> list[str]:
    obj = gemini.generate_json(f"""Нужны НАСТОЯЩИЕ архивные фотографии для ролика (Wikimedia Commons).
Тема: {topic_en}
История: {story['story']}

Придумай 2–4 коротких поисковых запроса на английском (2–5 слов) — конкретные места, люди, здания, предметы,
документы из истории, как они названы в энциклопедиях. Без общих слов вроде "mystery", "ghost".
Если в истории нет реальных конкретных объектов — верни пустой список.
Верни JSON: {{"queries": ["..."]}}""", temperature=0.3)
    qs = obj if isinstance(obj, list) else (obj.get("queries") or [])  # модель иногда отдаёт просто список
    return [q for q in qs if isinstance(q, str) and q.strip()][:4]


def _pick(board: dict, cands: list[dict], max_photos: int) -> list[dict]:
    scenes = "\n".join(f"{s['index']}: {s['text']}" for s in board["scenes"])
    listing = "\n".join(f"{i}: {'[иллюстрация из статьи Википедии об этой истории] ' if c.get('from_article') else ''}"
                        f"{c['file']} | {c['description']} | {c['date']}" for i, c in enumerate(cands))
    obj = gemini.generate_json(f"""Сцены ролика:
{scenes}

Кандидаты — файлы Wikimedia Commons (номер: имя | описание | дата):
{listing}

Выбери от 3 до {max_photos} фото, которые изображают реальное место, человека, предмет, документ или событие
именно из этой истории (не похожее, не «по мотивам» и не просто той же эпохи). Иллюстрации из статьи об этой
истории почти всегда подходят — бери их в первую очередь. Для каждого фото — номер сцены, текст которой
лучше всего с ним совпадает (не сцена 0; фото распредели по ролику, не ставь два в соседние сцены),
и короткую подпись по-русски (до 45 символов, например «Маяк на острове Эйлеан-Мор, 1900 год»).
Каждая сцена — не больше одного фото. Если подходящих нет совсем — верни пустой список.
Верни JSON: {{"photos": [{{"candidate": 0, "scene": 3, "caption": "..."}}]}}""", temperature=0.2)
    out, used_scenes = [], set()
    n_scenes = len(board["scenes"])
    photos = obj if isinstance(obj, list) else (obj.get("photos") or [])  # модель иногда отдаёт просто список
    for p in photos:
        if not isinstance(p, dict):
            continue
        try:
            ci, si = int(p["candidate"]), int(p["scene"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= ci < len(cands) and 0 < si < n_scenes and si not in used_scenes:
            used_scenes.add(si)
            out.append({**cands[ci], "scene": si, "caption": str(p.get("caption", ""))[:45]})
    return out[:max_photos]


def _autofill(board: dict, cands: list[dict], picks: list[dict], want: int) -> list[dict]:
    """Если модель выбрала меньше трёх фото, добавляет иллюстрации статьи по теме в свободные сцены
    равномерно по ролику (они заведомо про эту историю). Подпись — из описания файла, если оно короткое."""
    article = [c for c in cands if c.get("from_article") and c["file"] not in {p["file"] for p in picks}]
    if len(picks) >= want or not article:
        return picks
    n = len(board["scenes"])
    taken = {p["scene"] for p in picks}
    free = [i for i in range(2, n - 1) if i not in taken and i - 1 not in taken and i + 1 not in taken]
    step = max(1, len(free) // max(1, want - len(picks)))
    for c, si in zip(article, free[::step]):
        if len(picks) >= want:
            break
        picks.append({**c, "scene": si, "caption": ""})
        log.info("Архив: фото из статьи %s добавлено в сцену %d без подбора моделью", c["file"], si)
    return picks


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
        for c in from_article(story.get("article")):  # сначала иллюстрации статьи-справки
            if c["file"] not in seen:
                seen.add(c["file"])
                cands.append(c)
        for q in queries:
            for c in search(q):
                if c["file"] not in seen:
                    seen.add(c["file"])
                    cands.append(c)
        log.info("Архив: запросы %s → %d подходящих по лицензии фото", queries, len(cands))
        if not cands:
            return []
        try:
            picks = _pick(board, cands[:40], max_photos)
        except Exception as exc:  # noqa: BLE001
            log.warning("Модель не выбрала фото (%s) — беру иллюстрации статьи", exc)
            picks = []
        picks = _autofill(board, cands, picks, min(3, max_photos))
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
