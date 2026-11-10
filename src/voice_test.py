"""Прослушивание русских нейроголосов Azure, чтобы выбрать лучший.

    python -m src.voice_test                       # все голоса ru-RU, тестовая фраза
    python -m src.voice_test --text "Своя фраза?"  # своя фраза
    python -m src.voice_test --multilingual        # + многоязычные голоса, умеющие говорить по-русски
    python -m src.voice_test --styles              # + каждый стиль голоса (если есть)
    python -m src.voice_test --stress-test         # сравнение: без ударений / с ударениями U+0301 / через RUAccent

Результат: output/voice_test/*.mp3 и index.html с плеерами (открыть в браузере).
Выбранный голос пропишите в config/voice.json → "voice".
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import requests

from .narration import ACUTE, MSTTS_NS, SSML_NS, Stresser, render, voice_config
from .tts import list_voices
from .utils import OUTPUT_DIR, env, retry, setup_logging

DEFAULT_TEXT = ("Почему три смотрителя маяка исчезли без следа? "
                "Дверь в замке была заперта, а на столе остался нетронутый ужин. "
                "Ответ нашли лишь спустя много лет.")
STRESS_PAIRS = [
    ("Старый замок стоял на скале.", "Старый за́мок стоял на скале."),
    ("Он повесил на дверь тяжёлый замок.", "Он повесил на дверь тяжёлый замо́к."),
    ("Это была настоящая мука.", "Это была настоящая му́ка."),
    ("Мука для хлеба кончилась.", "Мука́ для хлеба кончилась."),
    ("Все ждали ответа, но всё было тихо.", "Все́ ждали ответа, но всё было тихо."),
]


@retry(tries=3, base_delay=3, exceptions=(requests.RequestException,), label="azure.rest")
def synth_rest(ssml: str, out: Path) -> None:
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


def ssml_for(voice: str, text: str, style: str | None = None, lang: str = "ru-RU") -> str:
    body = escape(text)
    if style:
        body = f'<mstts:express-as style={quoteattr(style)}>{body}</mstts:express-as>'
    if lang != "ru-RU":  # многоязычный голос: явно просим говорить по-русски
        body = f'<lang xml:lang="ru-RU">{body}</lang>'
    return (f'<speak version="1.0" xmlns="{SSML_NS}" xmlns:mstts="{MSTTS_NS}" xml:lang="{lang}">'
            f'<voice name={quoteattr(voice)}>{body}</voice></speak>')


def main(argv=None) -> None:
    setup_logging()
    p = argparse.ArgumentParser(prog="python -m src.voice_test")
    p.add_argument("--text", default=DEFAULT_TEXT)
    p.add_argument("--multilingual", action="store_true")
    p.add_argument("--styles", action="store_true")
    p.add_argument("--stress-test", action="store_true")
    p.add_argument("--voice", help="голос для --stress-test (по умолчанию из config/voice.json)")
    a = p.parse_args(argv)
    if not (env("AZURE_SPEECH_KEY") and env("AZURE_SPEECH_REGION")):
        raise SystemExit("Задайте AZURE_SPEECH_KEY и AZURE_SPEECH_REGION")

    out = OUTPUT_DIR / "voice_test"
    out.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str]] = []  # (подпись, файл, детали)
    voices = list_voices()
    ru = [v for v in voices if v.get("Locale") == "ru-RU" and v.get("VoiceType") == "Neural"]
    multi = [v for v in voices if a.multilingual and v.get("Locale") != "ru-RU"
             and "ru-RU" in (v.get("SecondaryLocaleList") or [])]

    if a.stress_test:
        voice = a.voice or voice_config()["voice"]
        stresser = Stresser(voice_config())
        for i, (plain, manual) in enumerate(STRESS_PAIRS):
            words = stresser.process(plain)
            auto = render(plain, words, 0, len(plain), "speak")
            for kind, text in (("без разметки", plain), ("ударение вручную", manual), ("RUAccent", auto)):
                f = out / f"stress_{i}_{kind.replace(' ', '_')}.mp3"
                synth_rest(ssml_for(voice, text), f)
                rows.append((f"{voice} — {kind}", f.name, text.replace(ACUTE, "́")))
    else:
        for v in ru + multi:
            name, lang = v["ShortName"], v["Locale"]
            styles = (v.get("StyleList") or []) if a.styles else []
            for style in [None] + styles:
                f = out / f"{name}{'_' + style if style else ''}.mp3"
                synth_rest(ssml_for(name, a.text, style, lang), f)
                detail = (f"{v.get('Gender')}, {v.get('LocalName') or ''}; стили: "
                          f"{', '.join(v.get('StyleList') or []) or 'нет'}")
                rows.append((f"{name}{' / ' + style if style else ''}", f.name, detail))
                print("готово:", f.name)

    page = ["<!doctype html><meta charset='utf-8'><title>Голоса Azure</title>",
            "<style>body{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 16px}"
            "td{padding:6px 10px;border-bottom:1px solid #ddd}</style>",
            f"<h1>Голоса Azure</h1><p>Фраза: {html.escape(a.text if not a.stress_test else 'тест ударений')}</p><table>"]
    for label, fname, detail in rows:
        page.append(f"<tr><td><b>{html.escape(label)}</b><br><small>{html.escape(detail)}</small></td>"
                    f"<td><audio controls preload=none src='{fname}'></audio></td></tr>")
    page.append("</table><p>Выбранный голос впишите в <code>config/voice.json</code> → <code>voice</code>.</p>")
    (out / "index.html").write_text("\n".join(page), encoding="utf-8")
    print(f"\nГотово: {len(rows)} файлов → {out / 'index.html'}")
    if not a.stress_test:
        print("Русские голоса:", ", ".join(v["ShortName"] for v in ru) or "не найдены (проверьте регион)")


if __name__ == "__main__":
    main()
