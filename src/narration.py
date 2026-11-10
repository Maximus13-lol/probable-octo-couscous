"""narration-director: ударения (RUAccent + stress_dict.json), буква «ё», интонации и паузы → SSML для Azure.

Схема:
  1. RUAccent размечает каждую сцену целиком (контекст нужен для омографов) → «ё» и позиция ударения.
  2. Gemini как режиссёр озвучки делит сцену на смысловые фрагменты и задаёт паузы, темп, высоту
     и ключевые слова (строгий JSON). Без Gemini работает режиссёр на правилах.
  3. Код собирает из этого SSML (текст гарантированно дословный → тайминги субтитров совпадают),
     валидирует его; если что-то не так — простой SSML «текст + паузы».

Ударение передаётся символом U+0301 (combining acute) после ударной гласной: эксперимент на нейродвижке
Microsoft показал, что «за́мок» и «замо́к» синтезируются по-разному, а слово остаётся одним WordBoundary.
Знак «+» использовать нельзя — синтезатор читает его как отдельный токен.
"""
from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import gemini
from .utils import CONFIG_DIR, ROOT, env, load_json, log, save_json

ACUTE = "́"
VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+")
ACC_WORD_RE = re.compile(r"[+А-Яа-яЁёA-Za-z0-9]+")
STRESS_DICT_FILE = ROOT / "stress_dict.json"
SSML_NS = "http://www.w3.org/2001/10/synthesis"
MSTTS_NS = "https://www.w3.org/2001/mstts"
ALLOWED_TAGS = {f"{{{SSML_NS}}}{t}" for t in ("speak", "voice", "prosody", "break", "emphasis")} | \
               {f"{{{MSTTS_NS}}}{t}" for t in ("express-as", "silence")}


def voice_config() -> dict:
    cfg = load_json(CONFIG_DIR / "voice.json", default={}) or {}
    if env("TTS_VOICE"):
        cfg["voice"] = env("TTS_VOICE")
    cfg.setdefault("voice", "ru-RU-DmitryNeural")
    cfg.setdefault("stress", {"method": "acute", "mode": "homographs"})
    cfg.setdefault("pauses_ms", {"after_hook": [300, 600], "before_twist": [300, 600],
                                 "before_resolution": [300, 600], "between_parts": [150, 250]})
    cfg.setdefault("limits", {"rate_min": -15, "rate_max": 10, "pitch_min": -8, "pitch_max": 10})
    return cfg


def norm(s: str) -> str:
    """Для сравнения текстов: без ударений, ё→е, только буквы/цифры, нижний регистр."""
    s = s.replace(ACUTE, "").replace("ё", "е").replace("Ё", "Е").lower()
    return "".join(ch for ch in s if ch.isalnum())


# ====================================================================== ударения
@dataclass
class WordInfo:
    start: int          # позиция в исходном тексте сцены
    end: int
    display: str        # с «ё» — для субтитров
    speak: str          # с U+0301 — для синтеза
    stressed: bool = False
    source: str = ""    # ruaccent | dict | yo


def load_stress_dict() -> dict[str, str]:
    """stress_dict.json: {"замок": "зам+ок", "все": "всё"} — ключ без ударения, значение с «+» перед ударной гласной."""
    raw = load_json(STRESS_DICT_FILE, default={}) or {}
    return {norm(k): v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}


def _apply_acc(word: str, acc: str) -> tuple[str, int | None]:
    """word — как в тексте; acc — форма с «+» перед ударной гласной (и, возможно, «ё»).
    Возвращает (слово с «ё», индекс ударной гласной или None)."""
    clean = acc.replace("+", "")
    if len(clean) != len(word):
        return word, None
    out = []
    for w, a in zip(word, clean):
        if a in "ёЁ" and w in "еЕ":
            out.append("ё" if w == "е" else "Ё")
        else:
            out.append(w)
    idx = None
    if "+" in acc:
        idx = acc.index("+")  # число символов до «+» = индекс ударной буквы в clean
    return "".join(out), idx


def _patch_onnx_inputs(acc) -> None:
    """Совместимость RUAccent со свежим transformers: токенизатор больше не отдаёт token_type_ids,
    а ONNX-модели их требуют. Недостающие входы подставляем нулями (стандартное значение для одного сегмента)."""
    import numpy as np

    for name in ("accent_model", "omograph_model", "stress_usage_predictor", "yo_homograph_model"):
        model = getattr(acc, name, None)
        session = getattr(model, "session", None)
        if session is None:
            continue
        required = [i.name for i in session.get_inputs()]
        orig_run = session.run

        def run(output_names, feed, *args, _orig=orig_run, _req=required, **kwargs):
            if "input_ids" in feed:
                for r in _req:
                    if r not in feed:
                        feed = dict(feed, **{r: np.zeros_like(feed["input_ids"])})
            return _orig(output_names, feed, *args, **kwargs)

        try:
            session.run = run
        except AttributeError:  # на случай, если атрибут нельзя переопределить
            log.debug("Не удалось обернуть ONNX-сессию %s", name)


class Stresser:
    def __init__(self, cfg: dict):
        self.mode = cfg.get("stress", {}).get("mode", "homographs")
        self.method = cfg.get("stress", {}).get("method", "acute")
        self.user_dict = load_stress_dict()
        self._acc = None
        self._failed = False

    def _model(self):
        if self._acc is None and not self._failed:
            try:
                from ruaccent import RUAccent
                workdir = env("RUACCENT_DIR", str(ROOT / ".cache" / "ruaccent"))
                Path(workdir).mkdir(parents=True, exist_ok=True)
                acc = RUAccent()
                acc.load(omograph_model_size="turbo3.1", use_dictionary=True, workdir=workdir)
                _patch_onnx_inputs(acc)
                self._acc = acc
                log.info("RUAccent загружен (%s)", workdir)
            except Exception as exc:  # noqa: BLE001
                self._failed = True
                log.warning("RUAccent недоступен (%s) — ударения только из stress_dict.json", exc)
        return self._acc

    def is_homograph(self, word: str) -> bool:
        m = self._model()
        if not m:
            return False
        low = word.lower()
        return low in m.omographs or low.replace("ё", "е") in m.omographs

    def process(self, text: str) -> list[WordInfo]:
        words = [(m.start(), m.end(), m.group()) for m in WORD_RE.finditer(text)]
        accented: list[str | None] = [None] * len(words)
        model = self._model()
        if model:
            try:
                out = model.process_all(text)
                acc_words = ACC_WORD_RE.findall(out)
                a_norm = [norm(w.replace("+", "")) for w in acc_words]
                o_norm = [norm(w) for _, _, w in words]
                sm = difflib.SequenceMatcher(a=o_norm, b=a_norm, autojunk=False)
                for tag, i1, i2, j1, j2 in sm.get_opcodes():
                    if tag == "equal":
                        for k in range(i2 - i1):
                            accented[i1 + k] = acc_words[j1 + k]
            except Exception as exc:  # noqa: BLE001
                log.warning("RUAccent упал на тексте (%s) — без ударений", exc)

        result: list[WordInfo] = []
        for (s, e, w), acc in zip(words, accented):
            display, idx, source = w, None, ""
            user = self.user_dict.get(norm(w))
            if user:
                display, idx = _apply_acc(w, user)
                source = "dict"
            elif acc:
                display, idx = _apply_acc(w, acc)
                source = "ruaccent"
            vowels = sum(ch in VOWELS for ch in display)
            need = (
                idx is not None and vowels > 1 and self.method == "acute" and "ё" not in display.lower()
                and (source == "dict" or self.mode == "all" or self.is_homograph(w))
            )
            speak = display
            if need and display[idx] in VOWELS:
                speak = display[: idx + 1] + ACUTE + display[idx + 1:]
            if display != w and source != "dict":
                source = "yo"
            result.append(WordInfo(s, e, display, speak, stressed=speak != display, source=source))
        return result


def render(text: str, words: list[WordInfo], start: int, end: int, kind: str) -> str:
    """Подстрока text[start:end] в варианте display (с «ё») или speak (с ударениями)."""
    out, pos = [], start
    for w in words:
        if w.end <= start or w.start >= end:
            continue
        out.append(text[pos:w.start])
        out.append(w.display if kind == "display" else w.speak)
        pos = w.end
    out.append(text[pos:end])
    return "".join(out)


# ====================================================================== режиссёр
@dataclass
class Segment:
    start: int
    end: int
    pause_after_ms: int = 0
    rate: int = 0
    pitch: int = 0
    emphasis: list[str] = field(default_factory=list)


def _locate(scene_text: str, pieces: list[str]) -> list[tuple[int, int]] | None:
    """Находит фрагменты подряд в тексте сцены, игнорируя пробелы/регистр/ударения. None — если не сходится."""
    spans, pos = [], 0
    for piece in pieces:
        target = norm(piece)
        if not target:
            return None
        # идём по тексту, накапливая нормализованные символы
        i, acc, first = pos, "", None
        while i < len(scene_text) and len(acc) < len(target):
            n = norm(scene_text[i])
            if n:
                if first is None:
                    first = i
                acc += n
            i += 1
        if acc != target:
            return None
        # захватываем хвостовую пунктуацию до следующей буквы
        while i < len(scene_text) and not scene_text[i].isalnum() and not scene_text[i].isspace():
            i += 1
        spans.append((first if first is not None else pos, i))
        pos = i
    if norm(scene_text[pos:]):
        return None
    return spans


def _clamp(v, lo, hi) -> int:
    try:
        return int(max(lo, min(hi, int(round(float(v))))))
    except (TypeError, ValueError):
        return 0


def rule_director(scenes: list[dict], cfg: dict) -> list[dict]:
    """Режиссёр на правилах: паузы между предложениями, после хука, перед поворотом и развязкой."""
    p = cfg["pauses_ms"]
    mid = lambda r: (r[0] + r[1]) // 2  # noqa: E731
    out = []
    n = len(scenes)
    for k, sc in enumerate(scenes):
        text = sc["text"]
        sents = [m for m in re.finditer(r"[^.!?…]+[.!?…]*[»”]?", text) if norm(m.group())]
        segs = []
        for j, m in enumerate(sents):
            q = m.group().strip().endswith("?")
            segs.append({"text": m.group().strip(), "pause_after_ms": mid(p["between_parts"]) if j < len(sents) - 1 else 0,
                         "rate": -3 if q else 0, "pitch": 6 if q else 0, "emphasis": []})
        nxt = scenes[k + 1]["text"] if k + 1 < n else ""
        if k == 0:
            pause, role = mid(p["after_hook"]), "hook"
        elif k == n - 2:
            pause, role = mid(p["before_resolution"]), "build"
        elif re.match(r"^(Но|Однако|И вдруг|Внезапно|А потом|Позже)\b", nxt):
            pause, role = mid(p["before_twist"]), "build"
        else:
            pause, role = mid(p["between_parts"]), "build"
        if k == n - 1:
            role, pause = "resolution", 0
            for s in segs:
                s["rate"] = min(s["rate"], -5)
        out.append({"index": sc["index"], "role": role, "segments": segs, "pause_after_ms": pause})
    return out


def gemini_director(scenes: list[dict], cfg: dict, voice_styles: list[str]) -> list[dict]:
    p, lim = cfg["pauses_ms"], cfg["limits"]
    listing = "\n".join(f"{s['index']}: {s['text']}" for s in scenes)
    prompt = f"""Ты режиссёр озвучки мистических YouTube Shorts на русском. Диктор читает историю по сценам.
Сцены (номер: текст):
{listing}

Для КАЖДОЙ сцены раздели текст на смысловые фрагменты (segments) и задай подачу:
- text — дословный фрагмент сцены; фрагменты подряд должны дать текст сцены целиком, без изменений.
- pause_after_ms — пауза после фрагмента внутри сцены: {p['between_parts'][0]}–{p['between_parts'][1]} мс между смысловыми частями,
  {p['before_twist'][0]}–{p['before_twist'][1]} мс перед поворотом сюжета; 0 у последнего фрагмента сцены.
- rate — темп в процентах относительно базового ({lim['rate_min']}…{lim['rate_max']}): чуть медленнее (−5…−12) в напряжённых местах.
- pitch — высота в процентах ({lim['pitch_min']}…{lim['pitch_max']}): чуть выше (+4…+8) в вопросах.
- emphasis — 0–2 ключевых слова загадки из этого фрагмента (дословно, как в тексте), которые надо выделить голосом.
Для каждой сцены также:
- role — hook | build | twist | resolution;
- pause_after_ms — пауза ПОСЛЕ сцены: {p['after_hook'][0]}–{p['after_hook'][1]} мс после хука (сцена 0),
  {p['before_twist'][0]}–{p['before_twist'][1]} мс перед поворотом сюжета, {p['before_resolution'][0]}–{p['before_resolution'][1]} мс перед развязкой,
  иначе {p['between_parts'][0]}–{p['between_parts'][1]} мс; у последней сцены 0.
Стиль голоса (style) выбери из списка {voice_styles or '[] — список пуст, верни null'} или null.
Также опиши по-английски манеру чтения для нейросетевого диктора (tts_style, 1–2 предложения):
тембр, темп, настроение, как звучат вопросы и напряжённые места.

Верни JSON:
{{"style": null, "tts_style": "Calm, low, mysterious storyteller...", "scenes": [{{"index": 0, "role": "hook", "pause_after_ms": 450,
  "segments": [{{"text": "...", "pause_after_ms": 0, "rate": -4, "pitch": 6, "emphasis": ["исчезли"]}}]}}]}}"""

    def validate(obj):
        if not isinstance(obj, dict) or not isinstance(obj.get("scenes"), list):
            return "нужен объект с полем scenes"
        if len(obj["scenes"]) != len(scenes):
            return f"нужно ровно {len(scenes)} сцен"
        for sc, d in zip(scenes, obj["scenes"]):
            segs = d.get("segments") or []
            if not segs or _locate(sc["text"], [s.get("text", "") for s in segs]) is None:
                return f"фрагменты сцены {sc['index']} не совпадают дословно с её текстом"
        return None

    return gemini.generate_json(prompt, temperature=0.4, validate=validate, attempts=3)


def build_segments(scene_text: str, plan: dict, cfg: dict) -> list[Segment]:
    lim = cfg["limits"]
    spans = _locate(scene_text, [s["text"] for s in plan["segments"]])
    if spans is None:  # не должно случаться после validate, но на всякий случай — одна целая фраза
        return [Segment(0, len(scene_text))]
    segs = []
    for (s, e), d in zip(spans, plan["segments"]):
        segs.append(Segment(
            s, e,
            pause_after_ms=_clamp(d.get("pause_after_ms", 0), 0, 900),
            rate=_clamp(d.get("rate", 0), lim["rate_min"], lim["rate_max"]),
            pitch=_clamp(d.get("pitch", 0), lim["pitch_min"], lim["pitch_max"]),
            emphasis=[w for w in d.get("emphasis") or [] if isinstance(w, str)][:2],
        ))
    segs[-1].pause_after_ms = 0
    return segs


# ====================================================================== SSML
def _pct(base: str, delta: int) -> str:
    b = int(re.sub(r"[^\d-]", "", base or "0") or 0)
    v = b + delta
    return f"{'+' if v >= 0 else ''}{v}%"


def _speak_with_emphasis(text: str, words: list[WordInfo], seg: Segment, cfg: dict) -> str:
    """Текст фрагмента (escape) с выделенными ключевыми словами."""
    targets = {norm(w) for w in seg.emphasis}
    out, pos = [], seg.start
    for w in words:
        if w.end <= seg.start or w.start >= seg.end:
            continue
        out.append(escape(text[pos:w.start]))
        token = escape(w.speak)
        if norm(w.display) in targets:
            if cfg.get("emphasis") == "emphasis":
                token = f'<emphasis level="moderate">{token}</emphasis>'
            else:
                token = f'<prosody rate="-12%" pitch="+4%" volume="+15%">{token}</prosody>'
            targets.discard(norm(w.display))
        out.append(token)
        pos = w.end
    out.append(escape(text[pos:seg.end]))
    return "".join(out).strip()


# ---------------------------------------------------------------------- Gemini TTS
GEMINI_DEFAULT_STYLE = (
    "Narrate in Russian as a calm, mysterious storyteller of a short documentary about an unsolved mystery: "
    "low, intimate voice, measured pace, quiet suspense, natural Russian intonation, "
    "rising intonation on questions, slow down slightly at tense moments, no exaggerated acting."
)
TAG_RE = re.compile(r"<(short pause|long pause)>")


def pause_tag(ms: int) -> str:
    if ms >= 300:
        return " <long pause> "
    if ms >= 120:
        return " <short pause> "
    return " "


def build_tts_text(text: str, words: list[WordInfo], segs: list[Segment], cfg: dict) -> str:
    """Текст для Gemini TTS: дословный транскрипт + паузы-теги + КАПС у ключевых слов + ударения."""
    caps = cfg.get("gemini", {}).get("emphasis", "caps") == "caps"
    parts = []
    for seg in segs:
        targets = {norm(w) for w in seg.emphasis}
        out, pos = [], seg.start
        for w in words:
            if w.end <= seg.start or w.start >= seg.end:
                continue
            out.append(text[pos:w.start])
            tok = w.speak
            if caps and norm(w.display) in targets:
                tok = tok.upper()
                targets.discard(norm(w.display))
            out.append(tok)
            pos = w.end
        out.append(text[pos:seg.end])
        parts.append("".join(out).strip())
        if seg.pause_after_ms:
            parts.append(pause_tag(seg.pause_after_ms).strip())
    return " ".join(p for p in parts if p)


def validate_tts_text(tts_text: str, expected: str) -> str | None:
    if norm(TAG_RE.sub(" ", tts_text)) != norm(expected):
        return "текст для Gemini TTS не совпадает со сценой"
    if re.search(r"<(?!short pause>|long pause>)[^>]*>", tts_text):
        return "недопустимый тег"
    return None


def _wrap(voice: str, inner: str) -> str:
    return (f'<speak version="1.0" xmlns="{SSML_NS}" xmlns:mstts="{MSTTS_NS}" xml:lang="ru-RU">'
            f'<voice name={quoteattr(voice)}>'
            f'<mstts:silence type="Leading-exact" value="50ms"/>'
            f'<mstts:silence type="Tailing-exact" value="50ms"/>'
            f"{inner}</voice></speak>")


def build_ssml(text: str, words: list[WordInfo], segs: list[Segment], cfg: dict, voice: str,
               style: str | None) -> str:
    parts = []
    for seg in segs:
        body = _speak_with_emphasis(text, words, seg, cfg)
        if not body:
            continue
        rate, pitch = _pct(cfg.get("base_rate", "+0%"), seg.rate), _pct(cfg.get("base_pitch", "+0%"), seg.pitch)
        parts.append(f'<prosody rate="{rate}" pitch="{pitch}">{body}</prosody>')
        if seg.pause_after_ms:
            parts.append(f'<break time="{seg.pause_after_ms}ms"/>')
    inner = "".join(parts)
    if style:
        deg = float(cfg.get("style_degree", 1.0))
        inner = f'<mstts:express-as style={quoteattr(style)} styledegree="{deg:.1f}">{inner}</mstts:express-as>'
    return _wrap(voice, inner)


def plain_ssml(text: str, words: list[WordInfo], voice: str, cfg: dict) -> str:
    """Запасной вариант: без prosody/express-as, только текст (с «ё» и ударениями) и паузы между предложениями."""
    speak = render(text, words, 0, len(text), "speak")
    sents = [s.strip() for s in re.findall(r"[^.!?…]+[.!?…]*[»”]?", speak) if norm(s)]
    pause = cfg["pauses_ms"]["between_parts"][1]
    return _wrap(voice, f'<break time="{pause}ms"/>'.join(escape(s) for s in sents) or escape(speak))


def validate_ssml(ssml: str, expected_text: str) -> str | None:
    """None — всё хорошо, иначе описание проблемы."""
    try:
        root = ET.fromstring(ssml)
    except ET.ParseError as exc:
        return f"XML не парсится: {exc}"
    if root.tag != f"{{{SSML_NS}}}speak":
        return "корневой элемент должен быть <speak>"
    for el in root.iter():
        if el.tag not in ALLOWED_TAGS:
            return f"недопустимый элемент {el.tag}"
        if el.tag.endswith("}break"):
            m = re.fullmatch(r"(\d+)ms", el.get("time", ""))
            if not m or int(m.group(1)) > 5000:
                return f"неверная пауза {el.get('time')}"
        if el.tag.endswith("}prosody"):
            for attr in ("rate", "pitch", "volume"):
                v = el.get(attr)
                if v is not None and not re.fullmatch(r"[+-]?\d{1,3}%", v):
                    return f"неверный {attr}={v}"
    spoken = "".join(root.itertext())
    if norm(spoken) != norm(expected_text):
        return "текст в SSML не совпадает с текстом сцены"
    if len(ssml) > 9000:
        return "SSML слишком длинный"
    return None


def gemini_script(narr: list[dict]) -> str:
    """Весь ролик одним текстом для Gemini TTS: сцены разделены паузами-тегами."""
    parts = []
    for i, sc in enumerate(narr):
        parts.append(sc["tts_text"])
        if i < len(narr) - 1:
            parts.append(pause_tag(sc.get("pause_after_ms", 200)).strip() or "<short pause>")
    return " ".join(parts)


# ====================================================================== шаг конвейера
def direct(board: dict, run_dir: Path, voice: str | None = None, voice_styles: list[str] | None = None) -> list[dict]:
    """Готовит для каждой сцены: display_text (с «ё»), ssml, plain_ssml, pause_after_ms.
    Записывает narration/scene_XX.ssml и narration.json (артефакты)."""
    cfg = voice_config()
    voice = voice or cfg["voice"]
    scenes = board["scenes"]
    stresser = Stresser(cfg)

    style = cfg.get("style") if cfg.get("style") in (voice_styles or []) else None
    if cfg.get("style") and not style:
        log.debug("Голос %s не поддерживает стиль «%s» (или список стилей недоступен) — только prosody",
                 voice, cfg.get("style"))

    plans, director = None, "rules"
    tts_style = cfg.get("gemini", {}).get("style") or GEMINI_DEFAULT_STYLE
    if gemini.available():
        try:
            res = gemini_director(scenes, cfg, voice_styles or [])
            plans = res["scenes"]
            director = "gemini"
            if res.get("style") in (voice_styles or []):
                style = res["style"]
            if isinstance(res.get("tts_style"), str) and 20 < len(res["tts_style"]) < 600 \
                    and not cfg.get("gemini", {}).get("style"):
                tts_style = res["tts_style"]
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini-режиссёр недоступен (%s) — разметка по правилам", exc)
    if plans is None:
        plans = rule_director(scenes, cfg)

    out_dir = run_dir / "narration"
    out_dir.mkdir(parents=True, exist_ok=True)
    result, stressed_total = [], []
    for sc, plan in zip(scenes, plans):
        text = sc["text"]
        words = stresser.process(text)
        segs = build_segments(text, plan, cfg)
        ssml = build_ssml(text, words, segs, cfg, voice, style)
        problem = validate_ssml(ssml, text)
        fallback = plain_ssml(text, words, voice, cfg)
        if problem:
            log.warning("Сцена %d: SSML не прошёл проверку (%s) — простой SSML с паузами", sc["index"], problem)
            ssml = fallback
            problem2 = validate_ssml(ssml, text)
            if problem2:
                raise RuntimeError(f"Даже простой SSML невалиден: {problem2}")
        tts_text = build_tts_text(text, words, segs, cfg)
        if validate_tts_text(tts_text, text):
            tts_text = render(text, words, 0, len(text), "speak")
        display = render(text, words, 0, len(text), "display")
        sc["display_text"] = display
        stressed = [w.speak for w in words if w.stressed]
        stressed_total += stressed
        (out_dir / f"scene_{sc['index']:02d}.ssml").write_text(ssml, encoding="utf-8")
        result.append({
            "index": sc["index"], "role": plan.get("role"), "text": text, "display_text": display,
            "ssml": ssml, "plain_ssml": fallback, "ssml_fallback": bool(problem),
            "tts_text": tts_text, "tts_style": tts_style,
            "pause_after_ms": _clamp(plan.get("pause_after_ms", 0), 0, 900) if sc is not scenes[-1] else 0,
            "stressed_words": stressed,
            "yo_words": [w.display for w in words if w.source == "yo"],
        })
    # для удобства — одна склеенная разметка всего ролика
    (out_dir / "full.ssml.txt").write_text("\n\n".join(f"<!-- сцена {r['index']} ({r['role']}), пауза после: "
                                                      f"{r['pause_after_ms']} мс -->\n{r['ssml']}" for r in result),
                                           encoding="utf-8")
    (out_dir / "gemini_tts_script.txt").write_text(
        f"STYLE: {tts_style}\n\n" + gemini_script(result), encoding="utf-8")
    save_json(run_dir / "narration.json", {"voice": voice, "style": style, "tts_style": tts_style,
                                           "director": director, "scenes": result})
    log.info("Режиссура: %s, голос %s, стиль %s; ударения: %s", director, voice, style or "—",
             ", ".join(stressed_total) or "нет")
    return result
