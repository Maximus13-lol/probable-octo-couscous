"""Подготовка и отложенная публикация ежедневного длинного выпуска."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path

from .long_pilot import read_dossier, render, validate_editorial
from .long_schedule import MOSCOW, plan
from .utils import save_json, setup_logging


def select(root: Path, now: dt.datetime, *, publish_now: bool = False) -> dict | None:
    today = now.astimezone(MOSCOW).date()
    delivery = root / "delivery"
    if publish_now:
        if (delivery / f"{today.isoformat()}.json").exists():
            return None
        return {"date": today.isoformat(), "publish_at": None}

    start = dt.date(2026, 11, 24)
    count = max(1, (today - start).days + 2)
    for item in plan(start, count):
        when = dt.datetime.fromisoformat(item["local_time"])
        if not dt.timedelta(hours=7) <= when - now <= dt.timedelta(hours=29):
            continue
        day = when.date().isoformat()
        if not (delivery / f"{day}.json").exists():
            return {"date": day, "publish_at": item["publish_at"]}
    return None


def prepare(item: dict, output: Path) -> Path:
    from .long_dossier import prepare as prepare_dossier

    used = set()
    for path in Path("long/delivery").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if topic := data.get("topic"):
            used.add(topic)
    return prepare_dossier(output / "draft" / item["date"], used)


def deliver(item: dict, output: Path) -> dict:
    if os.environ.get("LONG_PUBLICATION_ENABLED") != "true":
        raise RuntimeError("Автопубликация длинных видео выключена")
    from . import cover, upload

    publish_at = item["publish_at"]
    if publish_at:
        when = dt.datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if when <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30):
            raise ValueError("До запланированного выхода осталось менее 30 минут")
    dossier = read_dossier(output / "draft" / item["date"] / "dossier.json")
    credits = {}
    for chapter in dossier["chapters"]:
        for frame in (chapter, *chapter.get("visuals", [])):
            credits[frame["source_url"]] = f"{frame['credit']} · {frame['license']} · {frame['source_url']}"
    sources = [*dossier.get("references", {}).values(), *credits.values()]
    description = "Папка №13 · Документальный разбор\n\nИсточники и архивные материалы\n" + "\n".join(sources)
    if len(description.encode("utf-8")) > 4900:
        raise ValueError("Атрибуция кадров не помещается в описание YouTube")
    description = description.replace("<", "‹").replace(">", "›")
    metadata = {
        "title": dossier["title"][:100],
        "description": description,
        "tags": ["история", "трукрайм", "Папка №13"],
        "category_id": "27", "language": "ru", "made_for_kids": False,
        "privacy_status": "private" if publish_at else "public", "publish_at": publish_at,
        "contains_synthetic_media": True, "paid_promotion": False,
    }
    result = upload.upload(output / "pilot.mp4", metadata, output)
    result["url"] = f'https://www.youtube.com/watch?v={result["video_id"]}'
    result.update(topic=dossier["topic"], publish_at=item["publish_at"], state="uploaded")
    save_json(output / "delivery.json", result)
    # Сохраняем ID сразу: после задержки обработки повторная загрузка создаст дубль.
    client = upload.get_youtube_client()
    if client is None:
        raise RuntimeError("YouTube API недоступен после загрузки")
    for attempt in range(31):
        response = client.videos().list(part="status", id=result["video_id"]).execute()
        items = response.get("items", [])
        status = items[0]["status"] if items else {}
        upload_status = status.get("uploadStatus")
        if upload_status in {"processed", "failed", "rejected", "deleted"}:
            break
        if attempt < 30:
            time.sleep(10)
    result["upload_status"] = upload_status
    result["rejection_reason"] = status.get("rejectionReason") or status.get("failureReason")
    result["confirmed_publish_at"] = status.get("publishAt")
    result["confirmed_privacy"] = status.get("privacyStatus")
    result["thumbnail"] = (
        cover.set_thumbnail(result["video_id"], output / "thumbnail.jpg")
        if upload_status == "processed" else "ожидает успешной обработки"
    )
    return result


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["select", "prepare", "render", "deliver"])
    parser.add_argument("item", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/long-release"))
    parser.add_argument("--publish-now", action="store_true")
    args = parser.parse_args()
    if args.mode == "select":
        print(json.dumps(select(args.item, dt.datetime.now(dt.timezone.utc), publish_now=args.publish_now)))
        return
    item = json.loads(args.item.read_text(encoding="utf-8"))
    if args.mode == "prepare":
        prepare(item, args.output)
    elif args.mode == "render":
        dossier = read_dossier(args.output / "draft" / item["date"] / "dossier.json")
        validate_editorial(dossier, require_sections=True)
        render(dossier, args.output)
    else:
        save_json(args.output / "delivery.json", deliver(item, args.output))


if __name__ == "__main__":
    main()
