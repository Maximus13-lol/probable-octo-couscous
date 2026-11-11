"""publisher: шаблоны названия/описания/хэштегов и загрузка в YouTube (Data API v3, OAuth refresh token)."""
from __future__ import annotations

import datetime as dt
import json
import random
import time
from pathlib import Path

from .utils import CONFIG_DIR, PUBLISH_LOG_FILE, env, load_json, log, save_json

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_TITLE = 100
MAX_DESCRIPTION = 4900


def _oauth_credentials():
    cid, secret, refresh = env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return None
    from google.oauth2.credentials import Credentials
    return Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret, token_uri=TOKEN_URI)


def get_youtube_client(readonly: bool = False):
    from googleapiclient.discovery import build
    creds = _oauth_credentials()
    if creds:
        return build("youtube", "v3", credentials=creds, cache_discovery=False)
    if readonly and env("YT_API_KEY"):
        return build("youtube", "v3", developerKey=env("YT_API_KEY"), cache_discovery=False)
    return None


# ------------------------------------------------------------------ шаблоны
def templates() -> dict:
    return load_json(CONFIG_DIR / "publish.json", default={}) or {}


def _hashtags(story: dict, tpl: dict) -> list[str]:
    tags, seen = [], set()
    for t in tpl.get("base_hashtags", []) + list(story.get("hashtags", [])):
        t = "#" + "".join(ch for ch in t.lstrip("#") if ch.isalnum() or ch == "_")
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    return tags[:12]


def build_metadata(story: dict, ad_info: dict | None, music_credit: str | None = None) -> dict:
    from .ads import description_block
    tpl = templates()
    title_tpl = tpl.get("title_template", "{title} #Shorts")
    base = story["title"].strip().rstrip(".")
    title = title_tpl.format(title=base)
    if len(title) > MAX_TITLE:
        room = MAX_TITLE - len(title_tpl.format(title=""))
        title = title_tpl.format(title=base[: room - 1].rstrip() + "…")
    if "#shorts" not in title.lower():
        title = (title[: MAX_TITLE - 8].rstrip() + " #Shorts")
    hashtags = _hashtags(story, tpl)
    ad_block = description_block(ad_info)
    desc = tpl.get("description_template", "{hook}\n\n{description}\n\n{ad_block}\n\n{hashtags}").format(
        hook=story.get("hook", ""), description=story.get("description", ""), ad_block=ad_block,
        hashtags=" ".join(hashtags), music_credit=music_credit or tpl.get("music_credit_default", ""),
    )
    if ad_block:
        # ссылка рекламодателя и пометка «Реклама» — первыми строками, чтобы были видны без «ещё»
        desc = ad_block + "\n\n" + desc.replace(ad_block, "").strip()
    while "\n\n\n" in desc:
        desc = desc.replace("\n\n\n", "\n\n")
    desc = desc.replace("<", "‹").replace(">", "›")[:MAX_DESCRIPTION]  # YouTube запрещает < >
    tags = list(dict.fromkeys(tpl.get("tags", []) + [h.lstrip("#") for h in hashtags]))
    while len(",".join(tags)) > 450:
        tags.pop()
    return {
        "title": title, "description": desc.strip(), "tags": tags,
        "category_id": tpl.get("category_id", "24"),
        "privacy_status": tpl.get("privacy_status", "public"),
        "language": tpl.get("language", "ru"),
        "made_for_kids": bool(tpl.get("made_for_kids", False)),
        "contains_synthetic_media": bool(tpl.get("contains_synthetic_media", True)),
        "paid_promotion": bool(ad_info and not ad_info.get("test")),
    }


# ------------------------------------------------------------------ загрузка
def _body(meta: dict, with_optional: bool) -> tuple[dict, str]:
    body = {
        "snippet": {
            "title": meta["title"], "description": meta["description"], "tags": meta["tags"],
            "categoryId": meta["category_id"], "defaultLanguage": meta["language"],
            "defaultAudioLanguage": meta["language"],
        },
        "status": {
            "privacyStatus": meta["privacy_status"],
            "selfDeclaredMadeForKids": meta["made_for_kids"],
            "embeddable": True,
        },
    }
    if meta.get("publish_at"):
        # отложенная публикация: YouTube требует privacyStatus=private, в publishAt видео станет публичным
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = meta["publish_at"]
    parts = ["snippet", "status"]
    if with_optional:
        body["status"]["containsSyntheticMedia"] = meta["contains_synthetic_media"]
        if meta["paid_promotion"]:
            body["paidProductPlacementDetails"] = {"hasPaidProductPlacement": True}
            parts.append("paidProductPlacementDetails")
    return body, ",".join(parts)


def upload(video: Path, meta: dict, run_dir: Path) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    yt = get_youtube_client()
    if yt is None:
        raise RuntimeError("Нет YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN — загрузка невозможна")

    for with_optional in (True, False):
        body, parts = _body(meta, with_optional)
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part=parts, body=body, media_body=media, notifySubscribers=True)
        response, attempt = None, 0
        try:
            while response is None:
                try:
                    status, response = req.next_chunk()
                    if status:
                        log.info("Загрузка: %d%%", int(status.progress() * 100))
                except HttpError as exc:
                    if exc.resp.status not in RETRIABLE_STATUS or attempt >= 6:
                        raise
                    attempt += 1
                    delay = min(64, 2 ** attempt) * (0.8 + 0.4 * random.random())
                    log.warning("Загрузка: HTTP %s, повтор через %.0f с", exc.resp.status, delay)
                    time.sleep(delay)
                except (ConnectionError, TimeoutError, OSError) as exc:
                    if attempt >= 6:
                        raise
                    attempt += 1
                    log.warning("Загрузка: сеть (%s), повтор", exc)
                    time.sleep(min(64, 2 ** attempt))
            break
        except HttpError as exc:
            text = exc.content.decode("utf-8", "ignore") if isinstance(exc.content, bytes) else str(exc)
            if with_optional and exc.resp.status == 400 and (
                    "paidProductPlacement" in text or "containsSyntheticMedia" in text):
                log.warning("API не принял необязательные поля (%s) — повтор без них; "
                            "отметку о платной рекламе поставьте вручную в YouTube Studio!", text[:200])
                continue
            raise

    vid = response["id"]
    result = {
        "video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "title": meta["title"],
        "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "privacy": response.get("status", {}).get("privacyStatus"),
        "upload_status": response.get("status", {}).get("uploadStatus"),
        "paid_promotion_flag": bool(response.get("paidProductPlacementDetails", {}).get("hasPaidProductPlacement")),
    }
    save_json(run_dir / "upload_result.json", result)
    log.info("Опубликовано: %s (%s)", result["url"], result["privacy"])
    result["publish_at"] = response.get("status", {}).get("publishAt") or meta.get("publish_at")
    if meta.get("publish_at"):
        log.info("Ролик запланирован: станет публичным в %s (UTC)", result["publish_at"])
    elif result["privacy"] != meta["privacy_status"]:
        log.warning("YouTube выставил статус %s вместо %s — проверьте настройки доступа по умолчанию "
                    "в YouTube Studio (Настройки → Загрузка видео).", result["privacy"], meta["privacy_status"])
    return result


def append_publish_log(entry: dict) -> None:
    data = load_json(PUBLISH_LOG_FILE, default=[]) or []
    data.append(entry)
    save_json(PUBLISH_LOG_FILE, data)


if __name__ == "__main__":  # быстрый просмотр метаданных по готовой папке запуска
    import sys
    rd = Path(sys.argv[1])
    st = load_json(rd / "story.json")
    ai = load_json(rd / "ad_info.json")
    print(json.dumps(build_metadata(st, ai), ensure_ascii=False, indent=2))
