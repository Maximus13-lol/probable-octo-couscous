"""Комментарий канала с вопросом зрителю под каждым роликом (вопрос больше не звучит в озвучке —
он снижал удержание). Комментировать можно только публичный ролик, поэтому комментарий ставится
отдельным шагом каждого запуска daily.yml — под роликами, чьё время публикации уже наступило.

Закрепить комментарий через API YouTube нельзя — при желании закрепите вручную (⋮ → «Закрепить»).
"""
from __future__ import annotations

import datetime as dt

from .utils import PUBLISH_LOG_FILE, load_json, log, save_json

TEMPLATE = "💬 {q}\nПишите свою версию в комментариях 👇"


def _due(e: dict, now: dt.datetime) -> bool:
    if not e.get("question") or e.get("commented") or e.get("deleted") or not e.get("video_id"):
        return False
    at = e.get("publish_at")
    if at:
        try:
            return dt.datetime.fromisoformat(at.replace("Z", "+00:00")) <= now
        except ValueError:
            return False
    return e.get("privacy") == "public"


def post_due() -> int:
    from .upload import get_youtube_client
    data = load_json(PUBLISH_LOG_FILE, default=[]) or []
    now = dt.datetime.now(dt.timezone.utc)
    todo = [e for e in data if _due(e, now)]
    if not todo:
        log.info("Комментарии: нечего публиковать")
        return 0
    yt = get_youtube_client()
    if yt is None:
        log.warning("Комментарии: нет ключа YouTube")
        return 0
    done = 0
    for e in todo:
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": e["video_id"],
                "topLevelComment": {"snippet": {"textOriginal": TEMPLATE.format(q=e["question"])}}}}).execute()
            e["commented"] = True
            done += 1
            log.info("Комментарий с вопросом опубликован: %s", e["url"])
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if "insufficient" in text and "scope" in text or "insufficientpermissions" in text:
                log.warning("::warning::Комментарии: у токена нет права youtube.force-ssl — получите токен заново "
                            "со списком прав из src/get_refresh_token.py")
                break  # без права бесполезно пробовать остальные; попробуем снова после обновления токена
            log.warning("Комментарий под %s не опубликован: %s", e.get("url"), text[:300])
            if "commentsdisabled" in text or "forbidden" in text:
                e["commented"] = "error"  # не повторять бесконечно: комментарии под роликом отключены
    if done or any(e.get("commented") == "error" for e in todo):
        save_json(PUBLISH_LOG_FILE, data)
    return done


if __name__ == "__main__":
    post_due()
