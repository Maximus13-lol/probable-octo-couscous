"""Удаление ролика с канала (по просьбе владельца).

    python -m src.yt_delete VIDEO_ID

Нужна область youtube (или youtube.force-ssl) — с токеном только youtube.upload YouTube ответит 403;
тогда ролик удаляется вручную: Studio → Контент → ⋮ → Удалить навсегда.
"""
from __future__ import annotations

import sys

from .upload import get_youtube_client
from .utils import PUBLISH_LOG_FILE, load_json, save_json, setup_logging


def main(argv: list[str]) -> int:
    setup_logging()
    if not argv:
        print("Укажите VIDEO_ID")
        return 1
    vid = argv[0].strip()
    yt = get_youtube_client()
    try:
        yt.videos().delete(id=vid).execute()
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "403" in text or "insufficient" in text.lower():
            print(f"❌ Нет прав на удаление ({text[:160]}). Удалите вручную: Studio → Контент → ⋮ → Удалить навсегда.")
        else:
            print("❌ Не удалось удалить:", text[:300])
        return 1
    log = load_json(PUBLISH_LOG_FILE, default=[]) or []
    for e in log:
        if e.get("video_id") == vid:
            e["deleted"] = True
    save_json(PUBLISH_LOG_FILE, log)
    print(f"✅ Ролик {vid} удалён")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
