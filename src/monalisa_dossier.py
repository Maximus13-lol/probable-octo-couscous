"""Готовит тестовое досье о краже «Моны Лизы» на основе архивных источников."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from . import gemini

SOURCES = {
    "Лувр": "https://www.louvre.fr/en/explore/the-palace/from-the-mona-lisa-to-the-wedding-feast-at-cana",
    "Каталог Лувра": "https://collections.louvre.fr/en/ark:/53355/cl010062370",
    "Библиотека Конгресса": "https://guides.loc.gov/chronicling-america-theft-mona-lisa",
    "Учебное досье Лувра": "https://louvrekids.louvre.fr/docs/Transcription%20textuelle%20Joconde_EN_FINAL_SRDE.pdf",
}
MATERIALS = [
    ("wall.jpg", "f/f8/Mona_Lisa_stolen-1911.jpg", "Пустое место в Лувре • неизвестный фотограф • 1911"),
    ("portrait.jpg", "6/6a/Mona_Lisa.jpg", "Портрет Лизы Герардини • Леонардо да Винчи • Лувр"),
]
SECTIONS = [
    ("Пустая стена", "Двадцать первого августа 1911 года картина исчезла. Фотография пустого места показывает результат, а не момент кражи."),
    ("Портрет до кражи", "Леонардо написал портрет, который Лувр называет портретом Лизы Герардини. Уже до пропажи портрет был ценным и известным."),
    ("Что такое сфумато", "Лувр отмечает мягкие переходы в живописи и пейзаж. Объясни, куда смотрит зритель, и как фотография отличается от оригинала."),
    ("Италия и Франция", "Каталог Лувра прослеживает историю портрета во французских королевских коллекциях задолго до Наполеона."),
    ("Работа Перуджи", "Винченцо Перуджа был стекольщиком, работавшим в Лувре. Не добавляй деталей проникновения, инструментов и обхода охраны."),
    ("Первые сообщения", "Архив Библиотеки Конгресса ведёт к публикациям американской прессы об истории. Заголовки отражали знания и предположения своего дня."),
    ("Газетный рисунок", "Иллюстрация La Domenica del Corriere вышла в сентябре 1911 года. Это редакционная реконструкция, а не свидетельство очевидца."),
    ("Два года без картины", "Лувр говорит о наградах и более чем двух годах без картины. Разбери, как различаются стимулы газетчиков, полиции и музея."),
    ("Флоренция", "В декабре 1913 года Перуджа предложил картину итальянскому торговцу произведениями искусства. Связь с властями привела к её возвращению."),
    ("Мотив и проверка", "В прессе Перуджа представлял свой поступок как возвращение картины Италии. Это заявление участника, его нельзя считать полным объяснением."),
    ("Возвращение", "По учебному материалу Лувра, картина вернулась на своё место четвёртого января 1914 года. Библиотека Конгресса указывает на судебные сообщения июня 1914 года."),
    ("Что изменила пропажа", "Лувр связывает кражу с ростом известности картины. Итог: архивы подтверждают отдельные части истории, а глобальная слава имеет больше одной причины."),
]


def prepare(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    images = []
    for name, remote, credit in MATERIALS:
        source = Path(__file__).resolve().parents[1] / "assets" / "archive-pilot" / name
        shutil.copyfile(source, root / name)
        images.append(dict(image=name, credit=credit, license="Общественное достояние",
                           source_url="https://commons.wikimedia.org/wiki/File:" + remote.rsplit("/", 1)[1]))
    chapters = []
    for index, section in enumerate(SECTIONS):

        def validate(item):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                return "нужен объект с текстом главы"
            count = len(item["text"].split())
            if not 170 <= count <= 300:
                return f"сейчас {count} слов, нужно около 220"
            return None

        prompt = (
            "Русский документальный сценарий канала «Папка №13» о краже Моны Лизы. "
            "Верни JSON-объект с полем text: цельный текст рассказчика для одной главы, примерно 220 слов. "
            "Не пересказывай вступление в каждой главе. Не выдумывай факты, даты, диалоги или выдержки. "
            "Расширяй материал оригинальным анализом свидетельств и связью с соседними главами. "
            "Даты и числа пиши словами. Используй только факты из следующего редакторского досье:\n"
            + json.dumps(SECTIONS, ensure_ascii=False)
            + "\nНапиши только эту главу: " + json.dumps(section, ensure_ascii=False)
        )
        prose = gemini.generate_json(prompt, temperature=0.35, validate=validate)["text"]
        image = images[0 if index in {0, 4, 5, 6, 7, 8, 9} else 1]
        words = prose.split()
        # Нарезаем на короткие фразы, предпочитая границы предложений.
        while words:
            limit = min(len(words), 65)
            end = next((i for i in range(limit, 34, -1) if re.search(r"[.!?…]$", words[i - 1])), limit)
            chapters.append(dict(text=" ".join(words[:end]), **image))
            words = words[end:]
    path = root / "dossier.json"
    path.write_text(json.dumps({"title": "Кража Моны Лизы", "headline": "Куда исчезла Мона Лиза?",
                                "references": SOURCES, "chapters": chapters},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    prepare(parser.parse_args().output)
