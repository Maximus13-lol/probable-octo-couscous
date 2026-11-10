"""Прослушивание голосов, чтобы выбрать лучший.

    python -m src.voice_test                       # 5 голосов Vosk (локально, бесплатно)
    python -m src.voice_test --provider gemini     # голоса Gemini TTS (подборка ~8 голосов)
    python -m src.voice_test --all                 # все 30 голосов Gemini (расходует бесплатный лимит!)
    python -m src.voice_test --provider azure      # русские нейроголоса Azure (нужны ключи Azure)
    python -m src.voice_test --text "Своя фраза?"
    python -m src.voice_test --stress-test         # ударения: без разметки / вручную (U+0301) / через RUAccent

Результат: output/voice_test/*.wav|mp3 и index.html с плеерами (открыть в браузере).
Выбранный голос впишите в config/voice.json: для Gemini — "gemini" → "voice", для Azure — "voice".
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import requests

from .narration import GEMINI_DEFAULT_STYLE, MSTTS_NS, SSML_NS, Stresser, render, voice_config
from .narration import vosk_clean
from .tts import (DEFAULT_GEMINI_MODELS, QuotaExhausted, _vosk_synth, azure_available, gemini_available, gemini_tts,
                  list_voices, vosk_available)
from .utils import OUTPUT_DIR, env, log, retry, setup_logging

DEFAULT_TEXT = ("Почему три смотрителя маяка исчезли без следа? <short pause> "
                "Дверь в замке была заперта, а на столе остался нетронутый ужин. <long pause> "
                "Ответ нашли лишь спустя много лет.")
# Подборка голосов Gemini под мистического рассказчика (описания — из документации Google)
GEMINI_PICKS = {
    "Charon": "Informative", "Algenib": "Gravelly", "Orus": "Firm", "Gacrux": "Mature",
    "Iapetus": "Clear", "Rasalgethi": "Informative", "Sulafat": "Warm", "Kore": "Firm",
}
GEMINI_ALL = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm", "Fenrir": "Excitable",
    "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy", "Callirrhoe": "Easy-going", "Autonoe": "Bright",
    "Enceladus": "Breathy", "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth", "Despina": "Smooth",
    "Erinome": "Clear", "Algenib": "Gravelly", "Rasalgethi": "Informative", "Laomedeia": "Upbeat",
    "Achernar": "Soft", "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature", "Pulcherrima": "Forward",
    "Achird": "Friendly", "Zubenelgenubi": "Casual", "Vindemiatrix": "Gentle", "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable", "Sulafat": "Warm",
}
STRESS_PAIRS = [
    ("Старый замок стоял на скале.", "Старый за́мок стоял на скале."),
    ("Он повесил на дверь тяжёлый замок.", "Он повесил на дверь тяжёлый замо́к."),
    ("Это была настоящая мука.", "Это была настоящая му́ка."),
    ("Мука для хлеба кончилась.", "Мука́ для хлеба кончилась."),
]


@retry(tries=3, base_delay=3, exceptions=(requests.RequestException,), label="azure.rest")
def azure_rest(ssml: str, out: Path) -> None:
    region, key = env("AZURE_SPEECH_REGION"), env("AZURE_SPEECH_KEY")
    r = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3", "User-Agent": "shorts-voice-test"},
        data=ssml.encode("utf-8"), timeout=60,
    )
    if r.status_code in (429, 500, 502, 503):
        raise requests.HTTPError(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    out.write_bytes(r.content)


def azure_ssml(voice: str, text: str, style: str | None = None, lang: str = "ru-RU") -> str:
    text = text.replace("<short pause>", "").replace("<long pause>", "")
    body = escape(text)
    if style:
        body = f'<mstts:express-as style={quoteattr(style)}>{body}</mstts:express-as>'
    if lang != "ru-RU":
        body = f'<lang xml:lang="ru-RU">{body}</lang>'
    return (f'<speak version="1.0" xmlns="{SSML_NS}" xmlns:mstts="{MSTTS_NS}" xml:lang="{lang}">'
            f'<voice name={quoteattr(voice)}>{body}</voice></speak>')


def gemini_say(text: str, voice: str, out: Path) -> None:
    cfg = voice_config().get("gemini", {})
    style = cfg.get("style") or GEMINI_DEFAULT_STYLE
    last = None
    for model in cfg.get("models") or DEFAULT_GEMINI_MODELS:
        try:
            out.write_bytes(gemini_tts(text, style, voice, model))
            return
        except QuotaExhausted:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("%s: %s", model, exc)
    raise RuntimeError(str(last))


def main(argv=None) -> None:
    setup_logging()
    p = argparse.ArgumentParser(prog="python -m src.voice_test")
    p.add_argument("--provider", choices=["vosk", "gemini", "azure"], default=None)
    p.add_argument("--text", default=DEFAULT_TEXT)
    p.add_argument("--all", action="store_true", help="все голоса Gemini (30 запросов)")
    p.add_argument("--multilingual", action="store_true", help="Azure: + многоязычные голоса")
    p.add_argument("--styles", action="store_true", help="Azure: + стили голоса")
    p.add_argument("--stress-test", action="store_true")
    p.add_argument("--voice", help="голос для --stress-test")
    a = p.parse_args(argv)
    provider = a.provider or "vosk"
    if provider == "vosk" and not vosk_available():
        raise SystemExit("Установите vosk-tts: pip install -r requirements.txt")
    if provider == "gemini" and not gemini_available():
        raise SystemExit("Задайте GEMINI_API_KEY")
    if provider == "azure" and not azure_available():
        raise SystemExit("Задайте AZURE_SPEECH_KEY и AZURE_SPEECH_REGION")

    out = OUTPUT_DIR / "voice_test"
    out.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str]] = []
    cfg = voice_config()

    def say(voice: str, text: str, fname: str, lang: str = "ru-RU", style: str | None = None) -> None:
        if provider == "vosk":
            import wave
            synth = _vosk_synth()
            clean = vosk_clean(text.replace("<short pause>", ",").replace("<long pause>", "..."))
            audio = synth.synth_audio(clean, speaker_id=int(voice))
            with wave.open(str(out / fname), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(synth.model.config["audio"].get("sample_rate", 22050)))
                w.writeframes(audio.tobytes())
        elif provider == "gemini":
            gemini_say(text, voice, out / fname)
        else:
            azure_rest(azure_ssml(voice, text, style, lang), out / fname)

    ext = "mp3" if provider == "azure" else "wav"
    try:
        if a.stress_test:
            voice = a.voice or {"gemini": cfg.get("gemini", {}).get("voice", "Charon"),
                                "vosk": str(cfg.get("vosk", {}).get("speaker", 3))}.get(provider, cfg["voice"])
            stresser = Stresser(cfg)
            for i, (plain, manual) in enumerate(STRESS_PAIRS):
                auto = render(plain, stresser.process(plain), 0, len(plain), "speak")
                for kind, text in (("без разметки", plain), ("ударение вручную", manual), ("RUAccent", auto)):
                    fname = f"stress_{i}_{kind.replace(' ', '_')}.{ext}"
                    say(voice, text, fname)
                    rows.append((f"{provider}:{voice} — {kind}", fname, text))
                    print("готово:", fname)
        elif provider == "vosk":
            for sp in range(5):
                fname = f"vosk_{sp}.{ext}"
                say(str(sp), a.text, fname)
                rows.append((f"Vosk: голос {sp}", fname, "выбран сейчас" if sp == cfg.get("vosk", {}).get("speaker", 3) else ""))
                print("готово:", fname)
        elif provider == "gemini":
            for voice, desc in (GEMINI_ALL if a.all else GEMINI_PICKS).items():
                fname = f"gemini_{voice}.{ext}"
                say(voice, a.text, fname)
                rows.append((f"Gemini: {voice}", fname, f"по описанию Google: {desc}"))
                print("готово:", fname)
        else:
            voices = list_voices()
            ru = [v for v in voices if v.get("Locale") == "ru-RU" and v.get("VoiceType") == "Neural"]
            multi = [v for v in voices if a.multilingual and v.get("Locale") != "ru-RU"
                     and "ru-RU" in (v.get("SecondaryLocaleList") or [])]
            for v in ru + multi:
                for style in [None] + ((v.get("StyleList") or []) if a.styles else []):
                    fname = f"azure_{v['ShortName']}{'_' + style if style else ''}.{ext}"
                    say(v["ShortName"], a.text, fname, v["Locale"], style)
                    rows.append((f"Azure: {v['ShortName']}{' / ' + style if style else ''}", fname,
                                 f"{v.get('Gender')}; стили: {', '.join(v.get('StyleList') or []) or 'нет'}"))
                    print("готово:", fname)
    except QuotaExhausted as exc:
        log.warning("Бесплатный лимит на сегодня исчерпан (%s) — сохраняю то, что успели озвучить", exc)

    page = ["<!doctype html><meta charset='utf-8'><meta name=viewport content='width=device-width'>",
            "<title>Голоса</title><style>body{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 16px}"
            "td{padding:8px;border-bottom:1px solid #ddd}audio{width:260px}</style>",
            f"<h1>Голоса ({provider})</h1><p>Фраза: {html.escape(a.text if not a.stress_test else 'тест ударений')}</p><table>"]
    for label, fname, detail in rows:
        page.append(f"<tr><td><b>{html.escape(label)}</b><br><small>{html.escape(detail)}</small></td>"
                    f"<td><audio controls preload=none src='{fname}'></audio></td></tr>")
    where = {"gemini": '"gemini" → "voice"', "vosk": '"vosk" → "speaker"'}.get(provider, '"voice"')
    page.append(f"</table><p>Выбранный голос впишите в <code>config/voice.json</code> → <code>{html.escape(where)}</code>.</p>")
    (out / "index.html").write_text("\n".join(page), encoding="utf-8")
    print(f"\nГотово: {len(rows)} файлов → {out / 'index.html'}")


if __name__ == "__main__":
    main()
