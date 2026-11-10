"""Демо-данные для прогона без GEMINI_API_KEY (локальная проверка конвейера)."""

DEMO_TOPIC_EN = "Flannan Isles lighthouse keepers disappearance 1900"

DEMO_STORY = {
    "title": "Куда исчезли три смотрителя маяка?",
    "hook": "Куда исчезли три человека с острова, с которого невозможно уйти?",
    "story": (
        "Куда исчезли три человека с острова, с которого невозможно уйти? "
        "Декабрь тысяча девятисотого года. Шотландия, крошечный скалистый остров Эйлеан-Мор. "
        "Три смотрителя круглые сутки следят за маяком. "
        "Но однажды капитан проходящего корабля замечает: огонь не горит. "
        "Через одиннадцать дней на остров высаживается сменщик. "
        "Ворота заперты. Кровати не заправлены. Часы на кухне остановились. "
        "На столе нетронутая еда, а один стул опрокинут. "
        "В кладовой нет двух непромокаемых плащей, а третий висит на месте. "
        "Значит, один из мужчин выбежал в ледяной холод без верхней одежды. Зачем? "
        "В журнале странные записи: опытные моряки молятся, а один из них плачет. "
        "Позже на западной пристани нашли сорванные перила и вывернутые камни на высоте тридцати метров. "
        "Скорее всего, гигантская волна смыла всех троих, когда они бросились спасать снаряжение. "
        "Но последняя запись в журнале до сих пор пугает: «Шторм закончился. Море спокойно»."
    ),
    "description": "Три смотрителя маяка исчезли с крошечного острова в 1900 году. Что произошло на Эйлеан-Мор?",
    "hashtags": ["#тайна", "#загадка", "#маяк", "#мистика", "#необъяснимое"],
}

DEMO_STORYBOARD = {
    "characters": [
        {"name": "keeper", "description": "a bearded Scottish lighthouse keeper in his 40s, dark wool sweater, oilskin coat"},
    ],
    "scenes": [
        {"text": "Куда исчезли три человека с острова, с которого невозможно уйти?",
         "prompt": "a lonely lighthouse on a tiny rocky island in a stormy North Atlantic sea at dusk, no people, mysterious"},
        {"text": "Декабрь тысяча девятисотого года. Шотландия, крошечный скалистый остров Эйлеан-Мор.",
         "prompt": "aerial view of a small steep rocky Scottish island with a stone lighthouse, grey winter sea, 1900"},
        {"text": "Три смотрителя круглые сутки следят за маяком.",
         "prompt": "three lighthouse keepers in oilskin coats standing near the lighthouse lantern at night, keeper in front"},
        {"text": "Но однажды капитан проходящего корабля замечает: огонь не горит.",
         "prompt": "a steamship captain on deck looking through a brass telescope at a dark unlit lighthouse on the horizon"},
        {"text": "Через одиннадцать дней на остров высаживается сменщик.",
         "prompt": "a small rowing boat landing at a rocky island jetty, a man climbing stone steps toward a lighthouse"},
        {"text": "Ворота заперты. Кровати не заправлены. Часы на кухне остановились.",
         "prompt": "interior of an empty old lighthouse keepers' room, unmade iron beds, stopped wall clock, cold blue light"},
        {"text": "На столе нетронутая еда, а один стул опрокинут.",
         "prompt": "an old wooden kitchen table with untouched food plates, one chair knocked over on the floor, candlelight"},
        {"text": "В кладовой нет двух непромокаемых плащей, а третий висит на месте. Значит, один из мужчин выбежал в ледяной холод без верхней одежды. Зачем?",
         "prompt": "a single yellow oilskin coat hanging alone on hooks in a dim storeroom, two empty hooks beside it"},
        {"text": "В журнале странные записи: опытные моряки молятся, а один из них плачет.",
         "prompt": "close-up of an old handwritten logbook on a desk lit by an oil lamp, ink pen, eerie atmosphere"},
        {"text": "Позже на западной пристани нашли сорванные перила и вывернутые камни на высоте тридцати метров.",
         "prompt": "a broken iron railing and torn up stones on a high cliff landing above a raging sea"},
        {"text": "Скорее всего, гигантская волна смыла всех троих, когда они бросились спасать снаряжение.",
         "prompt": "a gigantic ocean wave crashing over a cliff top at night, tiny human silhouettes, lighthouse in background"},
        {"text": "Но последняя запись в журнале до сих пор пугает: «Шторм закончился. Море спокойно».",
         "prompt": "a calm misty sea at dawn around a silent lighthouse, an open logbook on a windowsill in the foreground"},
    ],
}
