"""Publish a QA-approved scheduled Short or restore its YouTube receipt."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from . import ads, cover, upload
from .utils import load_json, log, setup_logging


def publish(root: Path) -> dict:
    (manifest,) = root.rglob("release.json")
    folder = manifest.parent
    entry = load_json(manifest)
    if (entry["slot"], entry["slot_date"]) != (os.environ["SLOT"], os.environ["SLOT_DATE"]):
        raise RuntimeError("Резервная копия относится к другому слоту")
    entries = load_json(upload.PUBLISH_LOG_FILE, default=[]) or []
    previous = next((item for item in entries
                     if item.get("slot") == entry["slot"] and item.get("slot_date") == entry["slot_date"]), None)
    if previous:
        log.info("Слот уже заполнен: %s", previous["url"])
        return previous

    meta = load_json(folder / "metadata.json")
    when = dt.datetime.fromisoformat(os.environ["PUBLISH_AT"])
    meta["publish_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when > dt.datetime.now(dt.timezone.utc) else None
    result = load_json(folder / "upload_result.json", default=None)
    if result is None:
        result = upload.upload(folder / "final.mp4", meta, folder)
    else:
        log.info("Восстановлена запись о загрузке: %s", result["url"])
    expected_privacy = "private" if result.get("publish_at") else "public"
    if result["privacy"] != expected_privacy:
        status = upload.get_youtube_client().videos().list(part="status", id=result["video_id"]).execute()["items"][0]["status"]
        if status["privacyStatus"] != "public":
            raise RuntimeError(f"YouTube не подтвердил публикацию {result['url']}")
        result.update(privacy="public", publish_at=None)
    entry.update(date=dt.date.today().isoformat(), video_id=result["video_id"], url=result["url"],
                 privacy=result["privacy"], publish_at=result.get("publish_at"))
    upload.append_publish_log(entry)

    thumbnail = folder / "cover.jpg"
    if thumbnail.is_file():
        log.info("Обложка: %s", cover.set_thumbnail(result["video_id"], thumbnail, wait_for_short=True))
    ad_info = load_json(folder / "ad_info.json", default=None)
    if ad_info:
        ads.log_impression(ad_info, result["url"], meta["title"])
    return entry


if __name__ == "__main__":
    setup_logging()
    publish(Path(sys.argv[1]))
