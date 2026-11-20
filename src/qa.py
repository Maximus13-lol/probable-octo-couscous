"""Контроль качества готового ролика перед публикацией.

1. Громкость к стандарту YouTube (−14 LUFS, пик −1 dBTP) — исправляется сразу, без перекодирования видео.
2. Технические проверки (ffmpeg): длительность, чёрные кадры, провалы звука.
3. Озвучка: доля слов сценария, найденных распознаванием в каждой сцене.
4. Взгляд «редактора»: Gemini смотрит лист из 8 кадров ролика (кривые картинки, мусорный текст, обрезанные
   субтитры, жестокость).

Серьёзная проблема → QAError: ролик не публикуется, а workflow повторяет сборку (новая попытка).
Настройки — config/qa.json.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
from pathlib import Path

from .utils import CONFIG_DIR, OUTPUT_DIR, load_json, log, save_json

RETRY_MARK = OUTPUT_DIR / ".qa_failed_once"  # повторная попытка: замечания «редактора» не блокируют выпуск

DEFAULTS = {
    "enabled": True,
    "loudness_lufs": -14.0,
    "true_peak": -1.0,
    "min_duration": 12,
    "max_duration": 90,
    "black_min_sec": 0.6,
    "silence_db": -45,
    "silence_min_sec": 1.3,
    "min_alignment": 0.7,
    "vision": True,
}


class QAError(RuntimeError):
    pass


def config() -> dict:
    c = dict(DEFAULTS)
    c.update(load_json(CONFIG_DIR / "qa.json", default={}) or {})
    return c


def _ffmpeg_stderr(args: list[str]) -> str:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    return p.stderr


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def normalize_loudness(path: Path, cfg: dict) -> dict:
    """Двухпроходный loudnorm: звук к −14 LUFS, видео копируется без перекодирования."""
    target = f"I={cfg['loudness_lufs']}:TP={cfg['true_peak']}:LRA=11"
    err = _ffmpeg_stderr(["-i", str(path), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        log.warning("QA: не удалось измерить громкость — пропускаю нормализацию")
        return {}
    meas = json.loads(m.group())
    af = (f"loudnorm={target}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
          f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
          f":offset={meas['target_offset']}:linear=true")
    tmp = path.with_name(path.stem + "_loud.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c:v", "copy", "-af", af,
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(path)
    log.info("QA: громкость %.1f → %.1f LUFS", float(meas["input_i"]), cfg["loudness_lufs"])
    return {"input_lufs": float(meas["input_i"]), "target_lufs": cfg["loudness_lufs"]}


def _black_segments(path: Path, min_sec: float) -> list[tuple[float, float]]:
    err = _ffmpeg_stderr(["-i", str(path), "-vf", f"blackdetect=d={min_sec}:pix_th=0.08", "-an", "-f", "null", "-"])
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]


def _silences(path: Path, db: float, min_sec: float) -> list[tuple[float, float]]:
    err = _ffmpeg_stderr(["-i", str(path), "-af", f"silencedetect=n={db}dB:d={min_sec}", "-vn", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def _contact_sheet(path: Path, dur: float, n: int = 8) -> bytes:
    """Лист из n кадров ролика (2 ряда × 4) для одного запроса к Gemini."""
    from PIL import Image
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=270:480", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True).stdout
        if raw:
            frames.append(Image.open(io.BytesIO(raw)).convert("RGB"))
    sheet = Image.new("RGB", (270 * 4, 480 * 2), (0, 0, 0))
    for i, f in enumerate(frames[:8]):
        sheet.paste(f, ((i % 4) * 270, (i // 4) * 480))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return buf.getvalue()


VISION_PROMPT = """Ты выпускающий редактор YouTube Shorts канала о реальных тайнах истории. Перед тобой 8 кадров
одного ролика (слева направо, сверху вниз). Субтитры внизу и карточка подписки в конце — это нормально.
Найди СЕРЬЁЗНЫЕ проблемы, из-за которых ролик нельзя публиковать:
- искажённые лица или тела (лишние пальцы, «расплавленные» лица), явный брак генерации;
- бессмысленный текст или надписи-каракули на самих картинках (не субтитры);
- обрезанные или наезжающие друг на друга субтитры, пустые или почти чёрные кадры;
- кровь, жестокость, обнажённость.
Мелкие стилистические недочёты не считай. Верни JSON:
{"ok": true|false, "problems": [{"frame": <1-8>, "issue": "<кратко по-русски>", "severity": "high|low"}]}"""


def vision_review(path: Path, dur: float) -> dict:
    from . import gemini
    sheet = _contact_sheet(path, dur)
    obj = gemini.vision_json(VISION_PROMPT, sheet)
    if not isinstance(obj, dict):
        return {"ok": True, "problems": [], "note": "ответ без структуры"}
    return obj


def check(final: Path, voice: list[dict], run_dir: Path) -> dict:
    """Нормализует громкость и проверяет ролик. Бросает QAError при серьёзной проблеме."""
    cfg = config()
    report: dict = {"problems": [], "warnings": []}
    if not cfg["enabled"]:
        return report
    try:
        report["loudness"] = normalize_loudness(final, cfg)
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"нормализация громкости: {exc}")

    dur = _duration(final)
    report["duration"] = round(dur, 2)
    if not cfg["min_duration"] <= dur <= cfg["max_duration"]:
        report["problems"].append(f"длительность {dur:.1f} с вне {cfg['min_duration']}–{cfg['max_duration']} с")

    blacks = [(a, b) for a, b in _black_segments(final, cfg["black_min_sec"])]
    if blacks:
        report["problems"].append("чёрные кадры: " + ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in blacks[:3]))

    tail = dur - 1.5  # тишина в самом конце (после концовки) допустима
    gaps = [(a, b) for a, b in _silences(final, cfg["silence_db"], cfg["silence_min_sec"]) if a < tail]
    if gaps:
        report["problems"].append("провал звука: " + ", ".join(
            f"{a:.1f}–{(b if b is not None else dur):.1f} с" for a, b in gaps[:3]))

    weak = [v for v in voice if float(v.get("alignment", 1.0)) < cfg["min_alignment"]]
    if weak:
        report["warnings"].append("сцены с плохо распознанной речью: " + ", ".join(
            f"{v['index']} ({float(v['alignment']) * 100:.0f}%)" for v in weak))
        if len(weak) > max(1, len(voice) // 4):
            report["problems"].append(f"речь плохо распознаётся в {len(weak)} сценах из {len(voice)}")

    if cfg["vision"]:
        try:
            vr = vision_review(final, dur)
            report["vision"] = vr
            high = [p for p in vr.get("problems") or [] if isinstance(p, dict) and p.get("severity") == "high"]
            if high:
                msg = "редактор: " + "; ".join(f"кадр {p.get('frame')}: {p.get('issue')}" for p in high[:3])
                (report["warnings"] if RETRY_MARK.exists() else report["problems"]).append(msg)
        except Exception as exc:  # noqa: BLE001
            report["warnings"].append(f"просмотр кадров недоступен: {exc}")

    save_json(run_dir / "qa.json", report)
    for w in report["warnings"]:
        log.warning("QA: %s", w)
    if report["problems"]:
        for p in report["problems"]:
            log.error("QA: %s", p)
        RETRY_MARK.parent.mkdir(parents=True, exist_ok=True)
        RETRY_MARK.write_text("1", encoding="utf-8")
        raise QAError("ролик не прошёл контроль качества: " + "; ".join(report["problems"]))
    log.info("QA: ролик прошёл контроль качества (%.1f с)", dur)
    return report
