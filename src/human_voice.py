"""Режим «живой голос»: историю озвучивает автор канала, остальное делает конвейер.

Как это работает (config/human_voice.json, по умолчанию выключено):
1. Запуск конвейера готовит сценарий (тема → текст → раскадровка → ударения) и кладёт его в
   inbox/<id>/: script.md — текст для начитки, *.json — всё, что нужно для сборки. Тема сразу
   помечается использованной. Создаётся уведомление (issue на GitHub) с текстом для начитки.
2. Автор записывает текст целиком (диктофон / голосовое) и загружает файл в ту же папку inbox/<id>/.
3. Следующий запуск по расписанию находит сценарий с записью: распознаёт запись (faster-whisper),
   делит её на сцены по словам, собирает ролик (картинки, субтитры, концовка) и публикует в слот.
   Папка сценария удаляется (запись не копится в репозитории).
Если записи нет дольше fallback_after_hours — сценарий публикуется с голосом Vosk (fallback="vosk")
или ждёт дальше (fallback="wait").
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from pathlib import Path

from . import align
from .utils import CONFIG_DIR, ROOT, load_json, log, save_json

INBOX = ROOT / "inbox"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".flac", ".webm", ".mp4", ".amr", ".3gp"}
DEFAULTS = {"enabled": False, "queue_size": 2, "fallback_after_hours": 24, "fallback": "vosk",
            "min_match": 0.6}


def config() -> dict:
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (load_json(CONFIG_DIR / "human_voice.json", default={}) or {}).items()
              if not k.startswith("_")})
    return c


def enabled() -> bool:
    return bool(config().get("enabled"))


# ------------------------------------------------------------------ очередь сценариев
def _items() -> list[Path]:
    if not INBOX.exists():
        return []
    return sorted(p for p in INBOX.iterdir() if p.is_dir() and (p / "state.json").exists())


def recording(item: Path) -> Path | None:
    files = sorted((p for p in item.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT),
                   key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def pending() -> list[Path]:
    """Сценарии, ещё ждущие записи."""
    return [p for p in _items() if not recording(p)]


def next_ready() -> Path | None:
    """Самый старый сценарий, к которому уже загружена запись."""
    return next((p for p in _items() if recording(p)), None)


def overdue() -> Path | None:
    """Самый старый сценарий без записи, ожидание которого превысило fallback_after_hours."""
    c = config()
    if c.get("fallback") != "vosk":
        return None
    now = dt.datetime.now(dt.timezone.utc)
    for p in pending():
        created = dt.datetime.fromisoformat(load_json(p / "state.json")["created"])
        if now - created > dt.timedelta(hours=float(c["fallback_after_hours"])):
            return p
    return None


def need_new_script() -> bool:
    return len(pending()) < int(config()["queue_size"])


def save_script(run_id: str, choice: dict, st: dict, board: dict, narr: list[dict]) -> Path:
    item = INBOX / run_id
    item.mkdir(parents=True, exist_ok=True)
    save_json(item / "state.json", {"created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                    "choice": choice, "story": st, "board": board, "narration": narr})
    (item / "script.md").write_text(script_markdown(run_id, st, narr), encoding="utf-8")
    log.info("Сценарий для живой озвучки сохранён: inbox/%s/script.md", run_id)
    return item


def load(item: Path) -> dict:
    return load_json(item / "state.json")


def finish(item: Path) -> None:
    shutil.rmtree(item, ignore_errors=True)


def _stressed(text: str) -> str:
    """«+» перед ударной гласной → акут после неё (так удобнее читать человеку)."""
    import re
    return re.sub(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])", "\\1\u0301", text)


def script_markdown(run_id: str, st: dict, narr: list[dict]) -> str:
    title = st.get("title") or "Без названия"
    stress = []
    for sc in narr:
        for w in sc.get("stressed_words") or []:
            if w not in stress:
                stress.append(w)
    lines = [f"# {title}", "",
             f"Сценарий `{run_id}`. Прочитайте текст целиком, одной записью, в спокойном живом темпе.",
             f"Затем загрузите файл записи (mp3, m4a, ogg, wav) в папку `inbox/{run_id}/` — "
             "ролик соберётся и выйдет в ближайший слот.", "",
             "Абзацы — это сцены: между ними можно сделать небольшую паузу. Если оговорились — лучше перезаписать "
             "целиком: повторённая фраза попадёт в ролик.", ""]
    if stress:
        lines += ["**Ударения:** " + ", ".join(_stressed(w) for w in stress), ""]
    lines += ["---", ""]
    for sc in narr:
        lines += [sc["display_text"].strip(), ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ запись → озвучка по сценам
def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, timeout=300)


def _duration(path: Path) -> float:
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def voice_from_recording(rec: Path, narr: list[dict], run_dir: Path) -> list[dict]:
    """Делит одну запись на сцены по таймингам слов. Формат результата — как у tts.synthesize()."""
    adir = run_dir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    full = adir / "human_full.wav"
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,"
            "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.2,areverse,")
    for flt in (trim, ""):
        _ffmpeg("-i", str(rec), "-vn", "-ac", "1", "-ar", "44100",
                "-af", f"{flt}highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11", str(full))
        if _duration(full) > 3:
            break
    total = _duration(full)
    per_scene = [align.WORD_RE.findall(sc["display_text"]) for sc in narr]
    words = [w for ws in per_scene for w in ws]
    timed, matched = align.align_script(words, str(full), total)
    if matched < float(config()["min_match"]):
        raise RuntimeError(f"запись не совпадает со сценарием: узнано {matched:.0%} слов "
                           f"(нужно от {float(config()['min_match']):.0%}) — проверьте, тот ли текст прочитан")
    # границы сцен — посередине паузы между последним словом сцены и первым словом следующей
    bounds, i = [0.0], 0
    for k, ws in enumerate(per_scene[:-1]):
        i += len(ws)
        bounds.append((timed[i - 1]["end"] + timed[i]["start"]) / 2)
    bounds.append(total)
    result, i = [], 0
    for k, sc in enumerate(narr):
        t0, t1 = bounds[k], bounds[k + 1]
        path = adir / f"scene_{sc['index']:02d}.mp3"
        _ffmpeg("-i", str(full), "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-c:a", "libmp3lame", "-b:a", "160k",
                str(path))
        n = len(per_scene[k])
        ws = [{"word": w["word"], "start": max(0.0, w["start"] - t0), "end": max(0.0, w["end"] - t0)}
              for w in timed[i:i + n]]
        i += n
        result.append({"index": sc["index"], "path": str(path), "duration": t1 - t0, "words": ws,
                       "voice": "human", "alignment": round(matched, 3), "pause_after": 0.0})
        log.info("Живая озвучка, сцена %d: %.2f с", sc["index"], t1 - t0)
    save_json(run_dir / "tts.json", result)
    return result


def notify_text(item: Path) -> str:
    """Текст уведомления (issue на GitHub) о новом сценарии."""
    return (item / "script.md").read_text(encoding="utf-8")
