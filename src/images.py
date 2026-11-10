"""Генерация картинок сцен через Pollinations.ai (бесплатно, без ключа)."""
from __future__ import annotations

import io
import re
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageOps

from . import style
from .utils import VIDEO_H, VIDEO_W, env, log, retry

BASE = "https://image.pollinations.ai/prompt/"


class ImageError(RuntimeError):
    pass


@retry(tries=5, base_delay=5, max_delay=90, exceptions=(requests.RequestException, ImageError), label="pollinations")
def _download(prompt: str, seed: int) -> Image.Image:
    params = {
        "width": VIDEO_W, "height": VIDEO_H, "seed": seed, "model": style.MODEL,
        "nologo": "true", "enhance": "false", "safe": "true", "referrer": "shorts-pipeline",
    }
    headers = {"User-Agent": "shorts-pipeline/1.0"}
    token = env("POLLINATIONS_TOKEN")  # необязательно: бесплатный токен снимает часть лимитов
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = BASE + urllib.parse.quote(prompt, safe="")
    for _ in range(12):  # бесплатный лимит: «1 картинка / N секунд с IP» — ждём столько, сколько просят
        r = requests.get(url, params=params, headers=headers, timeout=180)
        if r.status_code not in (402, 429):
            break
        m = re.search(r"[Rr]etry after (\d+)", r.text)
        wait = int(m.group(1)) + 2 if m else int(r.headers.get("retry-after", 30))
        log.info("Pollinations: лимит бесплатного тарифа, жду %d с", wait)
        time.sleep(min(wait, 120))
    if r.status_code != 200:
        raise ImageError(f"HTTP {r.status_code}: {r.text[:200]}")
    if not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 10_000:
        raise ImageError(f"ответ не похож на картинку ({r.headers.get('content-type')}, {len(r.content)} байт)")
    img = Image.open(io.BytesIO(r.content))
    img.load()
    return img.convert("RGB")


WATERMARK_CROP = float(env("POLLINATIONS_CROP", "0.06"))  # доля снизу, где бесплатный тариф ставит логотип


def fit_vertical(img: Image.Image) -> Image.Image:
    """Срезает полосу с логотипом внизу, затем обрезка по центру и масштабирование до 1080x1920."""
    if WATERMARK_CROP > 0:
        img = img.crop((0, 0, img.width, int(img.height * (1 - WATERMARK_CROP))))
    return ImageOps.fit(img, (VIDEO_W, VIDEO_H), method=Image.LANCZOS, centering=(0.5, 0.5))


def generate_images(board: dict, run_dir: Path) -> list[Path]:
    img_dir = run_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    characters = board.get("characters", [])
    paths: list[Path | None] = []
    for scene in board["scenes"]:
        i = scene["index"]
        path = img_dir / f"scene_{i:02d}.jpg"
        full_prompt = style.build_prompt(scene["prompt"], characters)
        scene["full_prompt"] = full_prompt
        if path.exists() and path.stat().st_size > 10_000:
            paths.append(path)
            continue
        try:
            img = _download(full_prompt, style.SEED)
            fit_vertical(img).save(path, quality=93)
            log.info("Картинка %d/%d готова (%dx%d → %dx%d)", i + 1, len(board["scenes"]),
                     img.width, img.height, VIDEO_W, VIDEO_H)
            paths.append(path)
        except Exception as exc:  # noqa: BLE001
            log.error("Сцена %d: картинка не получена (%s)", i, exc)
            paths.append(None)
        time.sleep(float(env("POLLINATIONS_DELAY", "3")))  # пауза между запросами (лимит анонимного тарифа)

    ok = [p for p in paths if p]
    if len(ok) < max(1, len(paths) // 2):
        raise RuntimeError(f"Получено только {len(ok)} из {len(paths)} картинок — останавливаю конвейер")
    # пропуски заполняем соседними кадрами, чтобы видео не развалилось
    result: list[Path] = []
    for i, p in enumerate(paths):
        if p is None:
            p = next((paths[j] for j in list(range(i - 1, -1, -1)) + list(range(i + 1, len(paths))) if paths[j]), ok[0])
            log.warning("Сцена %d использует картинку соседней сцены: %s", i, p.name)
        result.append(p)
    return result
