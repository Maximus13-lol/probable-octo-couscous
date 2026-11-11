"""ad-inserter: рекламные баннеры в безопасной зоне Shorts. ПО УМОЛЧАНИЮ ВЫКЛЮЧЕН (ads/config.json → enabled).

CLI:
  python -m src.ads add --banner path.png --advertiser "ООО Ромашка" --url https://... \
         --start 2026-11-10 --end 2026-12-10 [--position auto|top-left|top-center|top-right|X,Y]
         [--show-at 20] [--duration 5] [--erid ...] [--legal "ООО Ромашка, ИНН ..."]
  python -m src.ads test [--campaign ID] [--video output/.../video.mp4]   # тестовый рендер
  python -m src.ads list | report | enable | disable
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .subtitles import SUB_ZONE
from .utils import ADS_DIR, OUTPUT_DIR, ROOT, VIDEO_H, VIDEO_W, find_font, load_json, log, save_json, setup_logging

CONFIG_FILE = ADS_DIR / "config.json"
LOG_FILE = ADS_DIR / "log.json"

# Безопасная зона Shorts (кадр 1080x1920): сверху шапка (~0–190 px), справа колонка кнопок,
# снизу подпись/канал (~1450+), субтитры в SUB_ZONE. Баннер — только в верхней половине под шапкой.
SAFE_TOP, SAFE_BOTTOM = 210, 900
SAFE_LEFT, SAFE_RIGHT = 48, VIDEO_W - 48
HOOK_SECONDS = 3.0
MIN_SHOW, MAX_SHOW = 4.0, 6.0

DEFAULT_CONFIG = {
    "enabled": False,
    "opacity": 0.9,
    "fade": 0.5,
    "max_banner_width": 720,
    "max_banner_height": 300,
    "label": "Реклама",
    "campaigns": [],
}


# ------------------------------------------------------------------ config
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_FILE, default={}) or {})
    return cfg


def save_config(cfg: dict) -> None:
    save_json(CONFIG_FILE, cfg)


def _date(s: str | None, default: dt.date) -> dt.date:
    return dt.date.fromisoformat(s) if s else default


def active_campaigns(cfg: dict, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    out = []
    for c in cfg.get("campaigns", []):
        cid = c.get("id", "?")
        if not c.get("enabled", True):
            log.info("Реклама: кампания %s выключена — пропуск", cid)
            continue
        start = _date(c.get("start_date"), dt.date.min)
        end = _date(c.get("end_date"), dt.date.max)
        if today > end:
            log.info("Реклама: кампания %s истекла %s — пропуск", cid, end)
            continue
        if today < start:
            log.info("Реклама: кампания %s начнётся %s — пропуск", cid, start)
            continue
        if not (ROOT / c["banner"]).exists():
            log.warning("Реклама: у кампании %s нет файла баннера %s — пропуск", cid, c["banner"])
            continue
        out.append(c)
    return out


def impressions(campaign_id: str) -> int:
    return sum(1 for e in load_json(LOG_FILE, default=[]) or [] if e.get("campaign") == campaign_id)


def pick_campaign(cfg: dict) -> dict | None:
    act = active_campaigns(cfg)
    if not act:
        return None
    # ротация: кампания с наименьшим числом показов
    return min(act, key=lambda c: (impressions(c["id"]), c.get("priority", 0) * -1))


# ------------------------------------------------------------------ banner
def prepare_banner(campaign: dict, cfg: dict, out: Path) -> Path:
    """Масштабирует баннер, скругляет углы, добавляет плашку «Реклама»."""
    img = Image.open(ROOT / campaign["banner"]).convert("RGBA")
    mw, mh = int(cfg["max_banner_width"]), int(cfg["max_banner_height"])
    scale = min(mw / img.width, mh / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    r = max(8, min(img.size) // 12)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], r, fill=255)
    img.putalpha(Image.fromarray(np.minimum(np.array(img.getchannel("A")), np.array(mask))))
    label = cfg.get("label")
    if label:
        font = ImageFont.truetype(find_font(), 26)
        tw = int(font.getlength(label)) + 20
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([8, 8, 8 + tw, 8 + 38], 8, fill=(0, 0, 0, 170))
        d.text((18, 12), label, font=font, fill=(255, 255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def make_test_banner(out: Path) -> Path:
    img = Image.new("RGBA", (720, 260), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 719, 259], 30, fill=(250, 250, 250, 255), outline=(255, 90, 40, 255), width=8)
    f1, f2 = ImageFont.truetype(find_font(), 58), ImageFont.truetype(find_font(), 34)
    d.text((360, 105), "ВАШ БАННЕР ЗДЕСЬ", font=f1, fill=(30, 30, 30), anchor="mm")
    d.text((360, 180), "тестовая рекламная кампания", font=f2, fill=(255, 90, 40), anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# ------------------------------------------------------------------ placement
def _frames(video: Path, times: list[float]) -> list[np.ndarray]:
    from moviepy import VideoFileClip
    with VideoFileClip(str(video), audio=False) as clip:
        return [clip.get_frame(min(t, clip.duration - 0.05)) for t in times]


def find_empty_area(video: Path, t0: float, t1: float, bw: int, bh: int) -> tuple[int, int]:
    """Ищет в верхней безопасной зоне участок с минимумом деталей (градиентов) за время показа."""
    frames = _frames(video, list(np.linspace(t0, t1, 5)))
    detail = np.zeros((VIDEO_H, VIDEO_W))
    for f in frames:
        g = f.astype(np.float32).mean(axis=2)
        gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
        gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
        detail += gx + gy + 0.15 * np.abs(g - g.mean())  # детали + контраст к среднему
    integ = np.pad(detail.cumsum(0).cumsum(1), ((1, 0), (1, 0)))

    def area(x, y):
        return integ[y + bh, x + bw] - integ[y, x + bw] - integ[y + bh, x] + integ[y, x]

    best, best_xy = None, (SAFE_LEFT, SAFE_TOP)
    cx_mid = (VIDEO_W - bw) / 2
    for y in range(SAFE_TOP, SAFE_BOTTOM - bh + 1, 16):
        for x in range(SAFE_LEFT, SAFE_RIGHT - bw + 1, 16):
            score = area(x, y) / (bw * bh)
            score *= 1 + 0.10 * abs(x - cx_mid) / VIDEO_W + 0.05 * (y - SAFE_TOP) / VIDEO_H  # лёгкое предпочтение центра/верха
            if best is None or score < best:
                best, best_xy = score, (x, y)
    return best_xy


def resolve_position(pos, video: Path, t0: float, t1: float, bw: int, bh: int) -> tuple[int, int]:
    if isinstance(pos, str) and re.fullmatch(r"\d+\s*,\s*\d+", pos):
        pos = dict(zip(("x", "y"), map(int, pos.split(","))))
    if isinstance(pos, dict):
        x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
    elif pos in ("top-left", "top-center", "top-right", "upper-middle"):
        y = SAFE_TOP if pos != "upper-middle" else (SAFE_TOP + SAFE_BOTTOM - bh) // 2
        x = {"top-left": SAFE_LEFT, "top-right": SAFE_RIGHT - bw}.get(pos, (VIDEO_W - bw) // 2)
    else:
        return find_empty_area(video, t0, t1, bw, bh)
    # жёстко зажимаем в безопасную зону, даже если координаты заданы вручную
    x = max(SAFE_LEFT, min(x, SAFE_RIGHT - bw))
    y = max(SAFE_TOP, min(y, SAFE_BOTTOM - bh))
    assert y + bh < SUB_ZONE[0], "баннер не должен заходить на субтитры"
    return x, y


def resolve_timing(campaign: dict, duration: float) -> tuple[float, float]:
    show = float(campaign.get("duration") or 5)
    show = max(MIN_SHOW, min(MAX_SHOW, show))
    start = campaign.get("show_at")
    if start is None:
        start = duration * 0.4
    start = float(start)
    if start < HOOK_SECONDS + 0.5:
        log.info("Реклама: старт %.1f с перенесён на %.1f с (не трогаем хук)", start, HOOK_SECONDS + 0.5)
        start = HOOK_SECONDS + 0.5
    if start + show > duration - 1:
        start = max(HOOK_SECONDS + 0.5, duration - 1 - show)
    return round(start, 2), round(show, 2)


# ------------------------------------------------------------------ render
def _duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(video)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def overlay(video: Path, banner: Path, out: Path, x: int, y: int, start: float, show: float,
            opacity: float, fade: float) -> Path:
    end = start + show
    filt = (
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity:.3f},"
        f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
        f"fade=t=out:st={end - fade:.3f}:d={fade:.3f}:alpha=1[ad];"
        f"[0:v][ad]overlay={x}:{y}:enable='between(t,{start:.3f},{end:.3f})':shortest=1[v]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
           "-loop", "1", "-framerate", "30", "-t", f"{_duration(video):.3f}", "-i", str(banner),
           "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out


def insert_ad(video: Path, run_dir: Path, test: bool = False, campaign_id: str | None = None) -> dict | None:
    """Возвращает информацию о вставленной рекламе (для publisher) или None."""
    cfg = load_config()
    campaign = None
    if campaign_id:
        campaign = next((c for c in cfg["campaigns"] if c.get("id") == campaign_id), None)
        if not campaign:
            raise SystemExit(f"Кампания {campaign_id} не найдена в {CONFIG_FILE}")
    elif test:
        campaign = pick_campaign(cfg) or (cfg["campaigns"][-1] if cfg["campaigns"] else None)
    elif cfg.get("enabled"):
        campaign = pick_campaign(cfg)
        if not campaign:
            log.info("Реклама включена, но активных кампаний нет")
            return None
    else:
        log.info("Реклама выключена (ads/config.json → enabled=false)")
        return None

    if campaign is None:  # тест без кампаний — синтетический баннер
        banner_src = make_test_banner(run_dir / "ad_test_banner_src.png")
        campaign = {"id": "test", "advertiser": "Тестовый рекламодатель", "url": "https://example.com",
                    "banner": str(banner_src.relative_to(ROOT)) if banner_src.is_relative_to(ROOT) else str(banner_src),
                    "position": "auto"}

    dur = _duration(video)
    asm = load_json(run_dir / "assembly.json", default={}) or {}
    if asm.get("cta_start"):  # реклама не должна заходить на концовку «лайк и подписка»
        dur = min(dur, float(asm["cta_start"]))
    start, show = resolve_timing(campaign, dur)
    banner = prepare_banner(campaign, cfg, run_dir / "ad_banner.png")
    bw, bh = Image.open(banner).size
    x, y = resolve_position(campaign.get("position", "auto"), video, start, start + show, bw, bh)
    out = video.with_name(video.stem + "_ad.mp4")
    overlay(video, banner, out, x, y, start, show, float(cfg["opacity"]), float(cfg["fade"]))
    # превью-кадр середины показа — для визуальной проверки
    preview = run_dir / "ad_preview.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start + show / 2:.2f}", "-i", str(out),
                    "-frames:v", "1", "-q:v", "3", str(preview)], check=True)
    info = {
        "campaign": campaign.get("id"), "advertiser": campaign.get("advertiser"), "url": campaign.get("url"),
        "legal": campaign.get("legal", ""), "erid": campaign.get("erid", ""),
        "shown_at": start, "duration": show, "position": {"x": x, "y": y, "w": bw, "h": bh},
        "video": str(out), "preview": str(preview), "test": test,
    }
    save_json(run_dir / "ad_info.json", info)
    log.info("Реклама «%s» вставлена: %.1f–%.1f с, позиция (%d, %d) %dx%d",
             info["campaign"], start, start + show, x, y, bw, bh)
    return info


def description_block(info: dict | None) -> str:
    if not info:
        return ""
    parts = ["Реклама."]
    if info.get("legal") or info.get("advertiser"):
        parts.append(f"Рекламодатель: {info.get('legal') or info.get('advertiser')}.")
    if info.get("erid"):
        parts.append(f"erid: {info['erid']}")
    lines = [" ".join(parts)]
    if info.get("url"):
        lines.append(f"👉 {info['url']}")
    return "\n".join(lines)


def log_impression(info: dict, video_url: str | None, title: str) -> None:
    if not info or info.get("test"):
        return
    entries = load_json(LOG_FILE, default=[]) or []
    entries.append({
        "date": dt.date.today().isoformat(),
        "campaign": info["campaign"], "advertiser": info.get("advertiser"),
        "video_title": title, "video_url": video_url,
        "shown_at_sec": info["shown_at"], "duration_sec": info["duration"],
    })
    save_json(LOG_FILE, entries)


# ------------------------------------------------------------------ CLI
def _slug(s: str) -> str:
    tr = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                       "abvgdeejziiklmnoprstufhccss_y_eua")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower().translate(tr)).strip("-")
    return s or "campaign"


def cmd_add(a) -> None:
    cfg = load_config()
    src = Path(a.banner)
    if not src.exists():
        raise SystemExit(f"Нет файла {src}")
    cid = a.id or f"{_slug(a.advertiser)}-{dt.date.today():%Y%m%d}"
    dst = ADS_DIR / f"{cid}{src.suffix.lower()}"
    if src.resolve() != dst.resolve():
        shutil.copy(src, dst)
    pos = a.position
    if pos and re.fullmatch(r"\d+\s*,\s*\d+", pos):
        pos = dict(zip(("x", "y"), map(int, pos.split(","))))
    camp = {
        "id": cid, "enabled": True, "advertiser": a.advertiser, "legal": a.legal or "", "erid": a.erid or "",
        "banner": str(dst.relative_to(ROOT)), "url": a.url,
        "start_date": a.start or dt.date.today().isoformat(), "end_date": a.end,
        "position": pos or "auto", "show_at": a.show_at, "duration": a.duration,
    }
    cfg["campaigns"] = [c for c in cfg["campaigns"] if c.get("id") != cid] + [camp]
    save_config(cfg)
    print(f"Кампания {cid} добавлена. Реклама в конфиге: {'ВКЛЮЧЕНА' if cfg['enabled'] else 'выключена'}.")


def _latest_video() -> Path | None:
    vids = sorted(OUTPUT_DIR.glob("*/video.mp4"), key=lambda p: p.stat().st_mtime)
    return vids[-1] if vids else None


def cmd_test(a) -> None:
    video = Path(a.video) if a.video else _latest_video()
    if not video or not video.exists():
        raise SystemExit("Нет готового видео: сначала `python -m src.main --no-upload` или укажите --video")
    run_dir = OUTPUT_DIR / f"ad_test_{dt.datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / "video.mp4"
    shutil.copy(video, tmp)
    info = insert_ad(tmp, run_dir, test=True, campaign_id=a.campaign)
    print(f"Тестовый рендер: {info['video']}\nПревью кадра: {info['preview']}\n"
          f"Текст для описания:\n{description_block(info)}")


def cmd_report(a) -> None:
    entries = load_json(LOG_FILE, default=[]) or []
    by: dict[str, list] = {}
    for e in entries:
        if a.campaign and e["campaign"] != a.campaign:
            continue
        by.setdefault(e["campaign"], []).append(e)
    if not by:
        print("Показов пока нет")
    for cid, lst in by.items():
        print(f"\n{cid} ({lst[0].get('advertiser')}): {len(lst)} показ(ов)")
        for e in lst:
            print(f"  {e['date']}  {e.get('video_url') or '-'}  «{e.get('video_title')}»  "
                  f"{e['shown_at_sec']}–{e['shown_at_sec'] + e['duration_sec']} с")


def cmd_list(_a) -> None:
    cfg = load_config()
    print(f"Реклама: {'ВКЛЮЧЕНА' if cfg['enabled'] else 'выключена'}")
    act = {c["id"] for c in active_campaigns(cfg)}
    for c in cfg["campaigns"]:
        print(f"  [{'активна' if c['id'] in act else 'неактивна'}] {c['id']}: {c['advertiser']} "
              f"{c.get('start_date')}…{c.get('end_date')} показов={impressions(c['id'])}")


def cmd_toggle(on: bool) -> None:
    cfg = load_config()
    cfg["enabled"] = on
    save_config(cfg)
    print(f"Реклама {'включена' if on else 'выключена'}")


def main(argv=None) -> None:
    setup_logging()
    p = argparse.ArgumentParser(prog="python -m src.ads", description="Рекламный модуль Shorts")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="добавить кампанию")
    s.add_argument("--banner", required=True)
    s.add_argument("--advertiser", required=True)
    s.add_argument("--url", required=True)
    s.add_argument("--start")
    s.add_argument("--end", required=True)
    s.add_argument("--position", default="auto")
    s.add_argument("--show-at", type=float)
    s.add_argument("--duration", type=float, default=5)
    s.add_argument("--id")
    s.add_argument("--erid", help="токен маркировки рекламы (ОРД)")
    s.add_argument("--legal", help="юр. наименование и ИНН рекламодателя")
    s = sub.add_parser("test", help="тестовый рендер баннера на готовом видео")
    s.add_argument("--campaign")
    s.add_argument("--video")
    s = sub.add_parser("report", help="отчёт по показам")
    s.add_argument("--campaign")
    sub.add_parser("list")
    sub.add_parser("enable")
    sub.add_parser("disable")
    a = p.parse_args(argv)
    {"add": cmd_add, "test": cmd_test, "report": cmd_report, "list": cmd_list,
     "enable": lambda _: cmd_toggle(True), "disable": lambda _: cmd_toggle(False)}[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv[1:])
