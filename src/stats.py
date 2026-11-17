"""Статистика канала: публичные метрики всех роликов (YouTube Data API) + удержание, если токен это позволяет
(YouTube Analytics API, нужна область yt-analytics.readonly — без неё раздел просто пропускается).

    python -m src.stats        # → stats/report.json и stats/report.md
"""
from __future__ import annotations

import datetime as dt
import statistics as st
import sys

from .upload import get_youtube_client
from .utils import PUBLISH_LOG_FILE, ROOT, load_json, log, save_json, setup_logging

OUT = ROOT / "stats"


def _iso_dur(s: str) -> int:
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def uploads(yt) -> tuple[dict, list[str]]:
    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    return ch, ids


def videos(yt, ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics,contentDetails,status", id=",".join(ids[i:i + 50])).execute()
        out += r.get("items", [])
    return out


def analytics(channel_id: str, ids: list[str], start: str) -> tuple[dict, str | None]:
    """Удержание и подписки по роликам из YouTube Analytics API. Возвращает ({id: metrics}, ошибка)."""
    try:
        from googleapiclient.discovery import build

        from .upload import _oauth_credentials
        ya = build("youtubeAnalytics", "v2", credentials=_oauth_credentials(), cache_discovery=False)
        r = ya.reports().query(
            ids="channel==MINE", startDate=start, endDate=dt.date.today().isoformat(),
            metrics="views,averageViewDuration,averageViewPercentage,likes,subscribersGained",
            dimensions="video", filters="video==" + ",".join(ids[:200]), maxResults=200, sort="-views").execute()
        cols = [c["name"] for c in r.get("columnHeaders", [])]
        return {row[0]: dict(zip(cols, row)) for row in r.get("rows", [])}, None
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc)[:300]


def main() -> int:
    setup_logging()
    yt = get_youtube_client()
    if yt is None:
        print("❌ Нет ключей YouTube (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN)")
        return 1
    ch, ids = uploads(yt)
    vids = videos(yt, ids)
    plog = {e.get("video_id"): e for e in (load_json(PUBLISH_LOG_FILE, default=[]) or [])}
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for v in vids:
        sn, s, cd, stt = v["snippet"], v.get("statistics", {}), v["contentDetails"], v["status"]
        pub = dt.datetime.fromisoformat((stt.get("publishAt") or sn["publishedAt"]).replace("Z", "+00:00"))
        if stt.get("privacyStatus") == "private" and pub > now:
            continue  # ещё не вышел
        e = plog.get(v["id"], {})
        hours = max(1.0, (now - pub).total_seconds() / 3600)
        views = int(s.get("viewCount", 0))
        rows.append({
            "id": v["id"], "title": sn["title"], "privacy": stt.get("privacyStatus"),
            "published": pub.isoformat(timespec="minutes"), "hours": round(hours, 1),
            "duration": _iso_dur(cd.get("duration")), "views": views,
            "likes": int(s.get("likeCount", 0)), "comments": int(s.get("commentCount", 0)),
            "views_per_hour": round(views / hours, 2),
            "like_rate": round(int(s.get("likeCount", 0)) / views * 100, 2) if views else 0.0,
            "slot": e.get("slot"), "source": e.get("source"), "topic": e.get("topic"),
            "manual": e.get("manual"), "pilot": stt.get("privacyStatus") == "unlisted",
        })
    start = min((r["published"][:10] for r in rows), default=dt.date.today().isoformat())
    an, an_err = analytics(ch["id"], [r["id"] for r in rows], start)
    for r in rows:
        a = an.get(r["id"])
        if a:
            r.update(avg_view_sec=a.get("averageViewDuration"), avg_view_pct=a.get("averageViewPercentage"),
                     subs_gained=a.get("subscribersGained"))
    rows.sort(key=lambda r: r["published"])
    public = [r for r in rows if r["privacy"] == "public"]

    def agg(key):
        groups: dict = {}
        for r in public:
            groups.setdefault(r.get(key) or "—", []).append(r)
        return {k: {"n": len(g), "views_median": st.median(x["views"] for x in g),
                    "vph_median": round(st.median(x["views_per_hour"] for x in g), 2),
                    "like_rate_avg": round(st.mean(x["like_rate"] for x in g), 2)} for k, g in groups.items()}

    report = {
        "generated": now.isoformat(timespec="minutes"),
        "channel": {"title": ch["snippet"]["title"], "subscribers": ch["statistics"].get("subscriberCount"),
                    "views": ch["statistics"].get("viewCount"), "videos": ch["statistics"].get("videoCount")},
        "analytics_error": an_err, "videos": rows,
        "by_slot": agg("slot"), "by_source": agg("source"),
        "totals": {"public": len(public), "views": sum(r["views"] for r in public),
                   "likes": sum(r["likes"] for r in public), "comments": sum(r["comments"] for r in public)},
    }
    OUT.mkdir(exist_ok=True)
    save_json(OUT / "report.json", report)
    lines = [f"# Статистика канала «{report['channel']['title']}» — {report['generated']} UTC", "",
             f"Подписчиков: {report['channel']['subscribers']}, просмотров всего: {report['channel']['views']}, "
             f"роликов: {report['channel']['videos']}", "",
             "| Дата (UTC) | Ролик | Слот | Источник | Просмотры | Просм./час | Лайки | Комм. | Удержание |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ret = f"{r['avg_view_pct']:.0f}%" if r.get("avg_view_pct") is not None else "—"
        mark = " (по ссылке)" if r["pilot"] else ""
        lines.append(f"| {r['published'][:16]} | {r['title'][:45]}{mark} | {r['slot'] or '—'} | {r['source'] or '—'} | "
                     f"{r['views']} | {r['views_per_hour']} | {r['likes']} | {r['comments']} | {ret} |")
    if an_err:
        lines += ["", f"Удержание недоступно: {an_err}"]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Отчёт: %d роликов, публичных %d", len(rows), len(public))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
