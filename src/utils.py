"""Общие утилиты: пути, логирование, повторные попытки, JSON."""
from __future__ import annotations

import functools
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT / "music"
ADS_DIR = ROOT / "ads"
OUTPUT_DIR = ROOT / "output"
CONFIG_DIR = ROOT / "config"
USED_TOPICS_FILE = ROOT / "used_topics.json"
PUBLISH_LOG_FILE = ROOT / "publish_log.json"

VIDEO_W, VIDEO_H = 1080, 1920
FPS = 30

log = logging.getLogger("shorts")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # библиотеки слишком болтливы
    for noisy in ("urllib3", "googleapiclient.discovery_cache", "PIL", "httpx", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def retry(
    tries: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    exceptions: Iterable[type[BaseException]] = (Exception,),
    label: str | None = None,
) -> Callable:
    """Декоратор: повтор с экспоненциальной задержкой и джиттером."""
    exceptions = tuple(exceptions)

    def deco(fn: Callable) -> Callable:
        name = label or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, tries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    if attempt == tries:
                        log.error("%s: попытка %d/%d не удалась: %s", name, attempt, tries, exc)
                        raise
                    delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.8 + 0.4 * random.random())
                    log.warning("%s: попытка %d/%d не удалась (%s), повтор через %.1f с",
                                name, attempt, tries, exc, delay)
                    time.sleep(delay)
        return wrapper
    return deco


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        log.warning("Повреждён JSON %s: %s — использую значение по умолчанию", path, exc)
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def find_font(bold: bool = True) -> str:
    """Ищет жирный шрифт с кириллицей: assets/fonts → Montserrat → DejaVu."""
    candidates: list[Path] = []
    candidates += sorted((ROOT / "assets" / "fonts").glob("*.[ot]tf"))
    for base in ("/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")):
        b = Path(base)
        if b.exists():
            for pat in ("**/Montserrat-Black.*", "**/Montserrat-ExtraBold.*", "**/Montserrat-Bold.*"):
                candidates += sorted(b.glob(pat))
    candidates += [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise FileNotFoundError("Не найден жирный шрифт. Установите fonts-dejavu или положите .ttf в assets/fonts/")
