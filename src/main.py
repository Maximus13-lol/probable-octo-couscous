"""Конвейер YouTube Shorts:
trend-scout → story-writer → storyboard → narration-director → картинки → озвучка (Azure) →
субтитры+сборка → ad-inserter → publisher.

    python -m src.main                 # полный прогон с публикацией
    python -m src.main --no-upload     # всё, кроме загрузки
    python -m src.main --test-ad       # + принудительная тестовая реклама (без загрузки)
    python -m src.main --topic "..."   # тема вручную, без trend-scout
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import time
import traceback

from . import ads, assemble, images, narration, story, storyboard, trends, tts, upload
from .utils import OUTPUT_DIR, load_json, log, save_json, setup_logging


def step(n: int, name: str) -> float:
    log.info("━━━━━━━━ [%d/9] %s ━━━━━━━━", n, name)
    return time.time()


def run(args) -> int:
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"run_id": run_id, "no_upload": args.no_upload, "test_ad": args.test_ad, "steps": {}}

    def done(name: str, t0: float) -> None:
        summary["steps"][name] = round(time.time() - t0, 1)

    try:
        t = step(1, "trend-scout")
        choice = trends.scout(run_dir, forced_topic=args.topic)
        done("trend-scout", t)

        t = step(2, "story-writer")
        st = story.write_story(choice, run_dir)
        if args.no_upload and not args.mark_used:
            log.info("--no-upload: тема НЕ записывается в used_topics.json (добавьте --mark-used, чтобы записать)")
        else:
            story.mark_topic_used(choice, st)
        done("story-writer", t)

        t = step(3, "storyboard")
        board = storyboard.make_storyboard(st, choice.get("topic_en", choice["topic"]), run_dir)
        done("storyboard", t)

        t = step(4, "narration-director (ударения, паузы, SSML)")
        voice_cfg = narration.voice_config()
        styles = tts.voice_styles(voice_cfg["voice"]) if tts.azure_available() else []
        narr = narration.direct(board, run_dir, voice_cfg["voice"], styles)
        done("narration", t)

        t = step(5, "картинки (Pollinations)")
        imgs = images.generate_images(board, run_dir)
        save_json(run_dir / "storyboard.json", board)  # + полные промпты
        done("images", t)

        t = step(6, "озвучка (Azure AI Speech)")
        voice = tts.synthesize(narr, run_dir, allow_silent=args.no_upload)
        done("tts", t)

        t = step(7, "субтитры + сборка (MoviePy)")
        video = assemble.assemble(board, imgs, voice, run_dir)
        done("assembly", t)

        t = step(8, "ad-inserter")
        ad_info = ads.insert_ad(video, run_dir, test=args.test_ad, campaign_id=args.ad_campaign)
        final = run_dir / "final.mp4"
        shutil.copy(ad_info["video"] if ad_info else video, final)
        done("ads", t)

        t = step(9, "publisher")
        asm = load_json(run_dir / "assembly.json", default={}) or {}
        meta = upload.build_metadata(st, ad_info, asm.get("music_credit"))
        save_json(run_dir / "metadata.json", meta)
        summary["voice"] = voice[0]["voice"] if voice else None
        summary.update({"topic": choice["topic"], "title": meta["title"], "video": str(final),
                        "duration": asm.get("duration"), "ad": ad_info and ad_info["campaign"]})
        if args.no_upload or args.test_ad:
            log.info("Загрузка пропущена (%s). Видео: %s", "--test-ad" if args.test_ad else "--no-upload", final)
        else:
            res = upload.upload(final, meta, run_dir)
            summary["upload"] = res
            upload.append_publish_log({
                "date": dt.date.today().isoformat(), "run_id": run_id, "video_id": res["video_id"],
                "url": res["url"], "title": meta["title"], "topic": choice["topic"],
                "source": choice.get("source"), "privacy": res["privacy"],
                "ad_campaign": ad_info["campaign"] if ad_info else None,
            })
            if ad_info:
                ads.log_impression(ad_info, res["url"], meta["title"])
        done("publisher", t)
        summary["status"] = "ok"
        return 0
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "error"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()
        log.error("Конвейер упал: %s", exc)
        log.debug(summary["traceback"])
        return 1
    finally:
        save_json(run_dir / "summary.json", summary)
        (OUTPUT_DIR / "LATEST").write_text(run_id, encoding="utf-8")
        log.info("Итог: %s", {k: v for k, v in summary.items() if k != "traceback"})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.main", description="Автоматический конвейер YouTube Shorts")
    p.add_argument("--no-upload", action="store_true", help="не загружать на YouTube")
    p.add_argument("--test-ad", action="store_true", help="вставить тестовую рекламу (подразумевает --no-upload)")
    p.add_argument("--ad-campaign", help="принудительно использовать кампанию с этим id")
    p.add_argument("--topic", help="задать тему вручную")
    p.add_argument("--mark-used", action="store_true", help="записать тему в used_topics.json даже при --no-upload")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if args.test_ad:
        args.no_upload = True
    setup_logging(10 if args.verbose else 20)
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
