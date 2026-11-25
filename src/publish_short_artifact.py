"""Publish a finished Short that missed its publication window."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cover, upload
from .utils import load_json, log, setup_logging


def publish(root: Path) -> None:
    (video,) = root.rglob("final.mp4")
    folder = video.parent
    summary = load_json(folder / "summary.json")
    if summary.get("error") != "RuntimeError: Окно публикации Shorts закрыто" or summary.get("upload"):
        raise RuntimeError("Артефакт должен содержать готовый ролик, пропустивший окно публикации")
    meta = load_json(folder / "metadata.json")
    if meta.get("privacy_status") != "public":
        raise RuntimeError("В артефакте отсутствуют настройки публичного ролика")
    run_id = summary["run_id"]
    for entry in load_json(upload.PUBLISH_LOG_FILE, default=[]):
        if entry.get("run_id") == run_id:
            log.info("Ролик уже опубликован: %s", entry["url"])
            return

    meta["publish_at"] = None
    story = load_json(folder / "story.json", default={}) or {}
    result = upload.upload(video, meta, folder)
    today = dt.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    entry = {
        "date": today, "run_id": run_id, "video_id": result["video_id"],
        "url": result["url"], "title": meta["title"], "topic": summary["topic"],
        "privacy": result["privacy"], "ad_campaign": summary.get("ad"),
        "slot": None, "rubric": summary.get("rubric"), "case": summary.get("case"),
        "question": story.get("question"), "manual": True, "publish_at": None,
        "slot_date": today,
    }
    upload.append_publish_log(entry)
    if result["privacy"] != "public":
        raise RuntimeError(f"YouTube вернул статус {result['privacy']} для {result['url']}")
    thumbnail = folder / "cover.jpg"
    if thumbnail.is_file():
        log.info("Обложка: %s", cover.set_thumbnail(result["video_id"], thumbnail, wait_for_short=True))


if __name__ == "__main__":
    setup_logging()
    publish(Path(sys.argv[1]))
