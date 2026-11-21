"""Select and schedule one approved long episode. Requires explicit publication opt-in."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from .long_pilot import read_dossier, render
from .long_schedule import plan


def select(root: Path, now: dt.datetime) -> dict | None:
    queue = root / "queue.json"
    if not queue.is_file():
        return None
    config = json.loads(queue.read_text(encoding="utf-8"))
    dossiers = config["dossiers"]
    if len(dossiers) < 7:
        raise ValueError("Для первой недели требуется семь утверждённых досье")
    schedule = plan(dt.date.fromisoformat(config["start"]), len(dossiers))
    for entry, dossier in zip(schedule, dossiers, strict=True):
        if (root / "delivery" / f'{entry["episode"]:04d}.json').exists():
            continue
        deadline = dt.datetime.fromisoformat(entry["publish_at"].replace("Z", "+00:00"))
        if not now + dt.timedelta(hours=7) < deadline <= now + dt.timedelta(days=8):
            continue
        path = (root / dossier).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Досье за пределами каталога или отсутствует: {dossier}")
        return {**entry, "dossier": str(path)}
    return None


def deliver(item: dict, output: Path) -> dict:
    if os.environ.get("LONG_PUBLICATION_ENABLED") != "true":
        raise RuntimeError("Автопубликация длинных видео выключена")
    from . import cover, upload

    when = dt.datetime.fromisoformat(item["publish_at"].replace("Z", "+00:00"))
    if when <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30):
        raise ValueError("До запланированного выхода осталось менее 30 минут")
    dossier = read_dossier(Path(item["dossier"]))
    sources = list(dict.fromkeys(c["source_url"] for c in dossier["chapters"]))
    sources.extend(dossier.get("references", {}).values())
    metadata = {
        "title": dossier["title"][:100],
        "description": ("Папка №13 · Документальный разбор\n\nИсточники и архивные материалы\n"
                        + "\n".join(dict.fromkeys(sources)))[:4900],
        "tags": ["история", "трукрайм", "Папка №13"],
        "category_id": "27", "language": "ru", "made_for_kids": False,
        "privacy_status": "private", "publish_at": item["publish_at"],
        "contains_synthetic_media": True, "paid_promotion": False,
    }
    result = upload.upload(output / "pilot.mp4", metadata, output)
    result["url"] = f'https://www.youtube.com/watch?v={result["video_id"]}'
    result["thumbnail"] = cover.set_thumbnail(result["video_id"], output / "thumbnail.jpg")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["select", "render", "deliver"])
    parser.add_argument("item", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/long-release"))
    args = parser.parse_args()
    if args.mode == "select":
        print(json.dumps(select(args.item, dt.datetime.now(dt.timezone.utc))))
        return
    item = json.loads(args.item.read_text(encoding="utf-8"))
    if args.mode == "render":
        render(read_dossier(Path(item["dossier"])), args.output)
    else:
        result = deliver(item, args.output)
        (args.output / "delivery.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
