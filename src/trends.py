"""trend-scout: поиск трендовой темы в нише «тайны / загадки / необъяснимое».

Источники (все бесплатные):
  * YouTube Data API v3 — Shorts за 7 дней, сортировка по просмотрам (≤10 search-запросов в сутки);
  * Reddit JSON без ключа — топ недели r/UnresolvedMysteries, r/HighStrangeness, r/todayilearned;
  * Google Trends через pytrends (любая ошибка → источник пропускается).
Скорость = просмотры (для Reddit — очки) / часы с публикации.
Топ-15 кандидатов уходит в Gemini, он выбирает одну тему, исключая used_topics.json.
"""
from __future__ import annotations

import json

import datetime as dt
import difflib
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from xml.etree import ElementTree
import time
from pathlib import Path

import requests

from . import gemini
from .utils import ROOT, USED_TOPICS_FILE, env, load_json, log, retry, save_json

QUOTA_FILE = ROOT / "trends_quota.json"
MAX_YT_QUERIES_PER_DAY = int(env("MAX_YT_QUERIES", "10"))
TOP_N = 15

YT_QUERIES = [
    ("тайна #shorts", "ru"),
    ("загадка необъяснимое #shorts", "ru"),
    ("необъяснимые случаи #shorts", "ru"),
    ("нераскрытое дело #shorts", "ru"),
    ("мистика реальная история #shorts", "ru"),
    ("unsolved mystery #shorts", "en"),
    ("unexplained mystery #shorts", "en"),
    ("creepy mystery facts #shorts", "en"),
]
REDDIT_SUBS = ["UnresolvedMysteries", "HighStrangeness", "todayilearned"]
SOURCE_WEIGHT = {"youtube": 1.0, "reddit": 0.5, "google_trends": 0.35}   # по статистике канала: провалы — из Reddit/Trends

# Запасные «вечнозелёные» темы — если все источники и Gemini недоступны.
EVERGREEN = [
    ("Исчезновение смотрителей маяка на островах Фланнан (1900)", "Flannan Isles lighthouse keepers disappearance 1900"),
    ("Перевал Дятлова: что случилось с туристами в 1959 году", "Dyatlov Pass incident 1959"),
    ("Сигнал «Wow!» из космоса (1977)", "The Wow! signal 1977"),
    ("Корабль-призрак «Мэри Селест» (1872)", "Mary Celeste ghost ship 1872"),
    ("Рукопись Войнича, которую никто не может прочитать", "Voynich manuscript"),
    ("Человек из Сомертона и шифр «Тамам Шуд»", "Somerton man Tamam Shud case"),
    ("Пропавший рейс 19 в Бермудском треугольнике", "Flight 19 Bermuda Triangle"),
    ("Колония Роанок: исчезновение целого поселения", "Lost colony of Roanoke"),
    ("Огни Хессдалена в Норвегии", "Hessdalen lights Norway"),
    ("Тунгусский взрыв 1908 года", "Tunguska event 1908"),
]


# ---------------------------------------------------------------- utils
def _hours_since(ts: dt.datetime) -> float:
    return max(1.0, (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 3600)


def _norm(s: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in s).split())


def load_used_topics() -> list[dict]:
    data = load_json(USED_TOPICS_FILE, default=[])
    return data if isinstance(data, list) else []


def is_used(topic: str, used: list[dict], threshold: float = 0.82) -> bool:
    t = _norm(topic)
    for item in used:
        for key in ("topic", "topic_en", "source_title"):
            other = _norm(str(item.get(key, "")))
            if other and (t == other or difflib.SequenceMatcher(None, t, other).ratio() >= threshold):
                return True
    return False


# ---------------------------------------------------------------- YouTube
def _quota_left() -> int:
    today = dt.date.today().isoformat()
    q = load_json(QUOTA_FILE, default={}) or {}
    used = q.get("search_calls", 0) if q.get("date") == today else 0
    return max(0, MAX_YT_QUERIES_PER_DAY - used)


def _quota_spend(n: int) -> None:
    today = dt.date.today().isoformat()
    q = load_json(QUOTA_FILE, default={}) or {}
    if q.get("date") != today:
        q = {"date": today, "search_calls": 0}
    q["search_calls"] = q.get("search_calls", 0) + n
    save_json(QUOTA_FILE, q)


def _rotated_queries() -> list[tuple[str, str]]:
    """Каждый запуск начинает с другого запроса — за день покрываются все темы поиска."""
    k = (dt.datetime.now(dt.timezone.utc).hour // 4) % len(YT_QUERIES)
    return YT_QUERIES[k:] + YT_QUERIES[:k]


def fetch_youtube() -> list[dict]:
    from .upload import get_youtube_client  # локальный импорт: googleapiclient тяжёлый

    yt = get_youtube_client(readonly=True)
    if yt is None:
        log.info("YouTube: нет учётных данных (YT_* или YT_API_KEY) — источник пропущен")
        return []
    left = _quota_left()
    if left <= 0:
        log.info("YouTube: дневной лимит поисковых запросов исчерпан — пропуск")
        return []
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: dict[str, str] = {}
    calls = 0
    per_run = int(env("YT_QUERIES_PER_RUN", "3"))  # 3 ролика в день × 3 запроса ≈ дневной лимит 10
    for q, lang in _rotated_queries()[:min(left, per_run)]:
        try:
            resp = retry(tries=3, label="yt.search")(
                lambda: yt.search().list(
                    part="id", q=q, type="video", videoDuration="short", order="viewCount",
                    publishedAfter=after, maxResults=25, relevanceLanguage=lang,
                    safeSearch="moderate",
                ).execute()
            )()
            calls += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("YouTube search «%s» не удался: %s", q, exc)
            calls += 1
            if "quota" in str(exc).lower():
                break
            continue
        for item in resp.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                ids.setdefault(vid, q)
    _quota_spend(calls)
    if not ids:
        return []
    out: list[dict] = []
    id_list = list(ids)
    for i in range(0, len(id_list), 50):
        chunk = id_list[i:i + 50]
        try:
            resp = retry(tries=3, label="yt.videos")(
                lambda: yt.videos().list(part="snippet,statistics", id=",".join(chunk)).execute()
            )()
        except Exception as exc:  # noqa: BLE001
            log.warning("YouTube videos.list не удался: %s", exc)
            continue
        for v in resp.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            views = int(st.get("viewCount", 0))
            published = dt.datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
            hours = _hours_since(published)
            out.append({
                "source": "youtube",
                "title": sn["title"],
                "url": f"https://youtube.com/shorts/{v['id']}",
                "query": ids[v["id"]],
                "metrics": {
                    "views": views,
                    "likes": int(st.get("likeCount", 0)),
                    "hours_since_publish": round(hours, 1),
                    "velocity": round(views / hours, 2),
                },
            })
    log.info("YouTube: %d запросов, %d видео", calls, len(out))
    return out


# ---------------------------------------------------------------- Reddit
def _reddit_rss(sub: str, headers: dict) -> list[dict]:
    """Запасной путь: Atom-лента топа недели. Очков в ней нет — «скорость» считаем по месту в топе."""
    import xml.etree.ElementTree as ET
    try:
        r = requests.get(f"https://www.reddit.com/r/{sub}/top/.rss", params={"t": "week", "limit": 25},
                         headers=headers, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:  # noqa: BLE001
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    out = []
    for i, e in enumerate(entries):
        title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        link = e.find("a:link", ns)
        when = e.findtext("a:published", default="", namespaces=ns) or e.findtext("a:updated", default="", namespaces=ns)
        try:
            hours = _hours_since(dt.datetime.fromisoformat(when.replace("Z", "+00:00")))
        except ValueError:
            hours = 72.0
        rank_score = len(entries) - i
        out.append({"source": "reddit", "title": title, "url": link.get("href") if link is not None else "",
                    "subreddit": sub, "metrics": {"rank_in_week_top": i + 1, "hours_since_publish": round(hours, 1),
                                                  "velocity": round(rank_score / max(hours, 1) * 100, 2)}})
    return [x for x in out if x["title"]]


def fetch_reddit() -> list[dict]:
    out: list[dict] = []
    headers = {"User-Agent": "shorts-trend-scout/1.0 (personal research script)"}
    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/top.json"
        try:
            @retry(tries=3, base_delay=3, exceptions=(requests.RequestException,), label=f"reddit.{sub}")
            def _get():
                r = requests.get(url, params={"t": "week", "limit": 25}, headers=headers, timeout=30)
                if r.status_code in (429, 500, 502, 503):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.json()
            data = _get()
        except Exception as exc:  # noqa: BLE001
            rss = _reddit_rss(sub, headers)
            if rss:
                log.info("Reddit r/%s: JSON недоступен (%s), взял RSS-ленту — %d постов", sub, str(exc)[:60], len(rss))
                out += rss
            else:
                log.warning("Reddit r/%s недоступен (JSON и RSS): %s — пропуск", sub, exc)
            continue
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            if p.get("stickied") or p.get("over_18"):
                continue
            created = dt.datetime.fromtimestamp(p.get("created_utc", time.time()), dt.timezone.utc)
            hours = _hours_since(created)
            score = int(p.get("score", 0))
            out.append({
                "source": "reddit",
                "title": p.get("title", ""),
                "url": "https://www.reddit.com" + p.get("permalink", ""),
                "subreddit": sub,
                "metrics": {
                    "score": score,
                    "comments": int(p.get("num_comments", 0)),
                    "hours_since_publish": round(hours, 1),
                    "velocity": round(score / hours, 2),
                },
            })
        time.sleep(1.5)  # вежливость к анонимному API
    log.info("Reddit: %d постов", len(out))
    return out


# ---------------------------------------------------------------- Google Trends
def fetch_google_trends() -> list[dict]:
    try:
        from pytrends.request import TrendReq
    except Exception as exc:  # noqa: BLE001
        log.warning("pytrends не установлен: %s — пропуск", exc)
        return []
    out: list[dict] = []
    try:
        # retries=0: встроенные повторы pytrends несовместимы с urllib3 2.x (method_whitelist)
        py = TrendReq(hl="ru-RU", tz=180, timeout=(10, 25), retries=0)
        # «Растущие» запросы вокруг ниши — самый релевантный сигнал
        @retry(tries=3, base_delay=5, label="pytrends")
        def _related():
            py.build_payload(["загадка", "тайна", "мистика"], timeframe="now 7-d", geo="RU")
            return py.related_queries() or {}
        related = _related()
        for kw, block in related.items():
            rising = (block or {}).get("rising")
            if rising is None:
                continue
            for _, row in rising.head(10).iterrows():
                out.append({
                    "source": "google_trends",
                    "title": str(row["query"]),
                    "url": "https://trends.google.com/trends/explore?geo=RU&q=" + str(row["query"]),
                    "metrics": {"growth_percent": int(row["value"]), "seed_keyword": kw,
                                "velocity": float(row["value"])},
                })
    except Exception as exc:  # noqa: BLE001
        log.warning("Google Trends недоступен: %s — пропуск", exc)
    log.info("Google Trends: %d запросов", len(out))
    return out


# ---------------------------------------------------------------- ranking
def rank_candidates(items: list[dict], used: list[dict]) -> list[dict]:
    by_src: dict[str, list[dict]] = {}
    for it in items:
        if not it.get("title") or is_used(it["title"], used):
            continue
        by_src.setdefault(it["source"], []).append(it)
    ranked: list[dict] = []
    for src, lst in by_src.items():
        top = max((x["metrics"].get("velocity", 0) for x in lst), default=0) or 1
        for x in lst:
            x["score"] = round(SOURCE_WEIGHT.get(src, 0.5) * x["metrics"].get("velocity", 0) / top, 4)
        ranked += lst
    ranked.sort(key=lambda x: x["score"], reverse=True)
    # разнообразие: не больше 7 кандидатов от одного источника
    picked, per_src = [], {}
    for x in ranked:
        if per_src.get(x["source"], 0) >= 7:
            continue
        per_src[x["source"]] = per_src.get(x["source"], 0) + 1
        picked.append(x)
        if len(picked) >= TOP_N:
            break
    return picked


def _documented_hint() -> str:
    """С улучшениями качества (config/quality.json) — предпочтение задокументированным историям:
    по ним есть статья-справка и настоящие архивные фото."""
    from . import quality
    if not quality.on("archive_photos"):
        return ""
    return ("\nПредпочитай реальные задокументированные случаи (конкретные место, год, люди), о которых есть "
            "статья в Википедии и сохранились фотографии или документы: в ролик пойдут настоящие архивные кадры.")


def _retention_hint() -> str:
    """Что зрители досматривают лучше: лучшие и худшие ролики канала по среднему проценту просмотра
    (stats/report.json, обновляется ежедневно workflow stats.yml)."""
    try:
        from .utils import ROOT
        d = json.loads((ROOT / "stats" / "report.json").read_text(encoding="utf-8"))
        vids = d["videos"] if isinstance(d, dict) else d
        rated = [v for v in vids if v.get("avg_view_pct") and (v.get("views") or 0) >= 200
                 and not v.get("pilot") and not v.get("manual")]  # пробные/ручные — другой формат
        if len(rated) < 6:
            return ""
        rated.sort(key=lambda v: v["avg_view_pct"], reverse=True)
        fmt = lambda v: f"«{v.get('topic') or v['title']}» ({v['avg_view_pct']:.0f}%)"  # noqa: E731
        return ("\nДанные канала — досматриваемость (средний % просмотра). Лучше всего досматривают: "
                + "; ".join(fmt(v) for v in rated[:5]) + ". Хуже всего: " + "; ".join(fmt(v) for v in rated[-4:])
                + ". Выбирай темы, похожие по типу на лучшие (загадка, место, люди), и избегай типа худших.")
    except Exception:  # noqa: BLE001
        return ""


def choose_with_gemini(cands: list[dict], used: list[dict], *, news_only: bool = False) -> dict:
    cands = cands[:12]
    criteria = {"hook": 3, "evidence": 3, "visuals": 2, "channel_fit": 2}
    lines = []
    for i, c in enumerate(cands):
        m = c["metrics"]
        lines.append(f"{i}. [{c['source']}] {c['title']} | скорость={m.get('velocity')} | {m}")
    used_list = "; ".join(u.get("topic", "") for u in used[-150:]) or "нет"
    news_rule = ("Допускай только конкретное новое событие или новый документ из заголовка, "
                 "подходящий жанру загадок. Не превращай дату публикации пересказа в дату события."
                 if news_only else "")
    prompt = f"""Ты продюсер YouTube Shorts канала на русском языке про тайны, загадки и необъяснимые случаи.
Ниже кандидаты из трендов (YouTube Shorts, Reddit, Google Trends) с метрикой скорости (просмотры или очки в час).

{chr(10).join(lines)}

Уже использованные темы (НЕЛЬЗЯ повторять, даже в другой формулировке): {used_list}

Оцени каждого кандидата от 0 до 5 по четырём признакам: hook — конкретный вопрос
с ответом в финале, evidence — возможность проверить историю по источникам,
visuals — наличие осмысленного визуального ряда, channel_fit — интерес для аудитории канала.
Не утверждай, что источник или архивный кадр проверен, если это следует только из заголовка.
Выбери подходящую тему с максимальной взвешенной оценкой: 3*hook + 3*evidence + 2*visuals + 2*channel_fit.
Для неподходящих тем дай eligible=false и кратко объясни причину.
{news_rule}
Тема должна быть безопасной для YouTube: без жестокости и подробностей убийств, без детей в «взрослом»
контексте (браки, любовь, насилие) и без детей-жертв, без политики, без болезней и катастроф с жертвами.
Если кандидат — не про тайну, можно взять из него загадочный факт.{_documented_hint()}{_retention_hint()}
Верни JSON:
{{"index": <номер кандидата>, "topic": "<тема по-русски, конкретно>", "topic_en": "<topic in English>",
  "hook": "<хук-вопрос по-русски, до 12 слов>", "reason": "<почему выбрана, 1-2 предложения>",
  "assessments": [{{"index": 0, "hook": 0, "evidence": 0, "visuals": 0, "channel_fit": 0,
                   "eligible": true, "reason": "<оценка кандидата>"}}]}}"""

    def validate(obj):
        if not isinstance(obj, dict) or not obj.get("topic"):
            return "нет поля topic"
        if not isinstance(obj.get("index"), int) or not 0 <= obj["index"] < len(cands):
            return f"index должен быть от 0 до {len(cands) - 1}"
        scores = {}
        assessments = obj.get("assessments")
        if not isinstance(assessments, list) or len(assessments) != len(cands):
            return "нужна оценка каждого кандидата"
        seen = set()
        for row in assessments:
            if not isinstance(row, dict):
                return "оценка должна быть объектом"
            index = row.get("index")
            if type(index) is not int or index not in range(len(cands)) or index in seen:
                return "индексы оценок должны быть уникальными и соответствовать кандидатам"
            seen.add(index)
            if type(row.get("eligible")) is not bool or not isinstance(row.get("reason"), str) or not row["reason"].strip():
                return "укажи пригодность и причину по каждому кандидату"
            if any(type(row.get(key)) is not int or not 0 <= row[key] <= 5 for key in criteria):
                return "оценки должны быть целыми числами от 0 до 5"
            if row["eligible"]:
                scores[index] = sum(row[key] * weight for key, weight in criteria.items())
        if obj["index"] not in scores or scores[obj["index"]] != max(scores.values()):
            return "выбери подходящего кандидата с максимальной суммой"
        if is_used(obj["topic"], used):
            return f"тема «{obj['topic']}» уже использовалась"
        return None

    return gemini.generate_json(prompt, temperature=0.7, validate=validate)


def news_slot() -> dt.date | None:
    if env("SLOT") != "18:00-19:30":
        return None
    slot_date = env("SLOT_DATE")
    day = dt.date.fromisoformat(slot_date) if slot_date else dt.datetime.now(ZoneInfo("Europe/Moscow")).date()
    return day if (day - dt.date(2026, 11, 21)).days % 2 == 0 else None


def fresh_candidates(day: dt.date) -> list[dict]:
    timezone = ZoneInfo("Europe/Moscow")
    deadline = dt.datetime.combine(day, dt.time(19, 30), timezone)
    now = dt.datetime.now(timezone)
    cutoff = deadline - dt.timedelta(hours=48)
    response = requests.get(
        "https://news.google.com/rss/search",
        params={"q": "(археологи OR архив OR загадка) (находка OR открытие OR документы) when:3d",
                "hl": "ru", "gl": "RU", "ceid": "RU:ru"},
        timeout=12,
    )
    response.raise_for_status()
    candidates = []
    for item in ElementTree.fromstring(response.content).findall("./channel/item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(timezone)
        except (TypeError, ValueError, OverflowError):
            continue
        title = item.findtext("title", "").strip()
        url = item.findtext("link", "").strip()
        if not title or not url.startswith("https://news.google.com/") or not cutoff <= published <= min(now, deadline):
            continue
        candidates.append({"title": title, "url": url, "source": "news",
                           "metrics": {"published_at": published.isoformat(),
                                       "publisher": item.findtext("source", ""),
                                       "velocity": 1}})
    return candidates[:12]


def scout(run_dir: Path, forced_topic: str | None = None, exclude: list[str] | None = None) -> dict:
    """exclude — темы, отклонённые в этом запуске (например, история вышла похожей на прошлую)."""
    from . import uniqueness

    used = load_used_topics()
    exclude = list(exclude or [])
    blocked = used + [{"topic": t} for t in exclude]
    report: dict = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "sources": {},
                    "rejected": [{"topic": t, "why": "отклонено в этом запуске"} for t in exclude]}

    if forced_topic:
        choice = {"topic": forced_topic, "topic_en": forced_topic, "hook": "", "source": "manual",
                  "reason": "тема задана вручную (--topic)", "metrics": {}}
        report.update({"chosen": choice, "candidates": []})
        save_json(run_dir / "trends_report.json", report)
        return choice

    target_day = news_slot()
    if target_day is not None:
        report["news"] = {"slot_date": target_day.isoformat(), "status": "fallback"}
        try:
            news = [item for item in fresh_candidates(target_day) if not is_used(item["title"], blocked)]
            report["news"]["candidates"] = news
            if news and gemini.available():
                result = choose_with_gemini(news, blocked, news_only=True)
                candidate = news[result["index"]]
                if not uniqueness.gemini_duplicate(result["topic"], result.get("hook", ""), used):
                    choice = {"topic": result["topic"], "topic_en": result.get("topic_en") or result["topic"],
                              "hook": result.get("hook", ""), "source": "news",
                              "source_title": candidate["title"], "source_url": candidate["url"],
                              "metrics": candidate["metrics"], "reason": result.get("reason", "")}
                    report["news"].update(status="selected", assessments=result["assessments"])
                    report["chosen"] = choice
                    save_json(run_dir / "trends_report.json", report)
                    return choice
            report["news"]["reason"] = "нет свежей подходящей неповторяющейся темы"
        except Exception as exc:
            report["news"]["reason"] = type(exc).__name__
            log.warning("Свежий инфоповод недоступен: %s", exc)

    items: list[dict] = []
    for name, fn in (("youtube", fetch_youtube), ("reddit", fetch_reddit), ("google_trends", fetch_google_trends)):
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 — ни один источник не должен ронять конвейер
            log.warning("Источник %s упал: %s", name, exc)
            got = []
        report["sources"][name] = len(got)
        items += got

    cands = rank_candidates(items, blocked)
    report["candidates"] = cands
    choice: dict | None = None

    # 1) Gemini выбирает из трендов; смысловая проверка на повтор, до 3 попыток
    if cands and gemini.available():
        pool = list(cands)
        for _ in range(3):
            if not pool:
                break
            try:
                g = choose_with_gemini(pool, blocked)
            except Exception as exc:  # noqa: BLE001
                log.warning("Gemini не смог выбрать тему: %s", exc)
                break
            report.setdefault("editorial_assessments", []).append([
                {**row, "title": pool[row["index"]]["title"], "url": pool[row["index"]].get("url")}
                for row in g["assessments"]
            ])
            c = pool[g["index"]]
            dup = uniqueness.gemini_duplicate(g["topic"], g.get("hook", ""), used)
            if dup:
                report["rejected"].append({"topic": g["topic"], "why": f"повторяет «{dup}»"})
                blocked.append({"topic": g["topic"], "topic_en": g.get("topic_en", "")})
                pool = [x for x in pool if x is not c]
                continue
            choice = {"topic": g["topic"], "topic_en": g.get("topic_en", g["topic"]), "hook": g.get("hook", ""),
                      "source": c["source"], "source_title": c["title"], "source_url": c.get("url"),
                      "metrics": c["metrics"], "score": c.get("score"), "reason": g.get("reason", "")}
            break

    # 2) Свежая тема от Gemini, если тренды не дали новой
    if choice is None and gemini.available():
        for _ in range(3):
            fresh = uniqueness.gemini_fresh_topic(used, [b["topic"] for b in blocked[len(used):]])
            if not fresh:
                break
            why = "уже была" if is_used(fresh["topic"], blocked) else None
            dup = None if why else uniqueness.gemini_duplicate(fresh["topic"], fresh.get("hook", ""), used)
            if why or dup:
                report["rejected"].append({"topic": fresh["topic"], "why": why or f"повторяет «{dup}»"})
                blocked.append({"topic": fresh["topic"]})
                continue
            choice = fresh
            break

    # 3) Без Gemini — лидер по скорости, затем вечнозелёные темы
    if choice is None and cands and not gemini.available():
        c = cands[0]
        choice = {"topic": c["title"], "topic_en": c["title"], "hook": "", "source": c["source"],
                  "source_title": c["title"], "source_url": c.get("url"), "metrics": c["metrics"],
                  "score": c.get("score"), "reason": "максимальная скорость (Gemini недоступен для выбора)"}
    if choice is None:
        for ru, en in EVERGREEN:
            if not is_used(ru, blocked) and not is_used(en, blocked):
                why = "нет кандидатов из трендов" if not cands else "нет GEMINI_API_KEY — демо-режим"
                choice = {"topic": ru, "topic_en": en, "hook": "", "source": "evergreen",
                          "metrics": {}, "reason": f"запасная вечнозелёная тема ({why})"}
                break
    if choice is None:
        raise RuntimeError("Нет новых тем: тренды, Gemini и запасной список не дали неповторяющейся темы")

    report["chosen"] = choice
    save_json(run_dir / "trends_report.json", report)
    log.info("Выбрана тема: %s [%s] — %s", choice["topic"], choice["source"], choice["reason"])
    return choice
