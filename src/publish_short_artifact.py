"""Publish a finished Short from an earlier interrupted release."""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ads, archive, cover, upload
from .utils import load_json, log, setup_logging


def publish(root: Path) -> None:
    (video,) = root.rglob("final.mp4")
    folder = video.parent
    summary = load_json(folder / "summary.json")
    error = summary.get("error", "")
    missed_window = error == "RuntimeError: Окно публикации Shorts закрыто"
    qa_rejected = error.startswith("QAError: ролик не прошёл контроль качества: ")
    if summary.get("upload") or summary.get("no_upload") or summary.get("test_ad") or not (missed_window or qa_rejected):
        raise RuntimeError("Артефакт не подходит для ручной публикации")
    story = load_json(folder / "story.json", default={}) or {}
    ad_info = load_json(folder / "ad_info.json", default=None)
    meta = load_json(folder / "metadata.json", default=None)
    if qa_rejected:
        photo_credits = archive.credits(load_json(folder / "archive_photos.json", default=[]))
        story = {**story, "case": summary.get("case"), "photo_credits": photo_credits}
        assembly = load_json(folder / "assembly.json")
        meta = upload.build_metadata(story, ad_info, assembly.get("music_credit"))
    if meta.get("privacy_status") != "public":
        raise RuntimeError("В артефакте отсутствуют настройки публичного ролика")
    run_id = summary["run_id"]
    for entry in load_json(upload.PUBLISH_LOG_FILE, default=[]):
        if entry.get("run_id") == run_id:
            log.info("Ролик уже опубликован: %s", entry["url"])
            return

    meta["publish_at"] = None
    result = upload.upload(video, meta, folder)
    today = dt.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    entry = {
        "date": today, "run_id": run_id, "video_id": result["video_id"],
        "url": result["url"], "title": meta["title"], "topic": summary.get("topic") or story["title"],
        "privacy": result["privacy"], "ad_campaign": (ad_info or {}).get("campaign"),
        "slot": None, "rubric": summary.get("rubric"), "case": summary.get("case"),
        "question": story.get("question"), "manual": True, "publish_at": None,
        "slot_date": today,
    }
    upload.append_publish_log(entry)
    if result["privacy"] != "public":
        raise RuntimeError(f"YouTube вернул статус {result['privacy']} для {result['url']}")
    if ad_info:
        ads.log_impression(ad_info, result["url"], meta["title"])
    thumbnail = folder / "cover.jpg"
    if thumbnail.is_file():
        log.info("Обложка: %s", cover.set_thumbnail(result["video_id"], thumbnail, wait_for_short=True))


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    publish(args.root)
