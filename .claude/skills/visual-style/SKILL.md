---
name: visual-style
description: Единый визуальный стиль канала (cinematic, moody lighting, detailed illustration), фиксированный seed и постоянные персонажи для картинок Pollinations. Используй при смене стиля, seed, модели или при проблемах с консистентностью картинок.
---

# visual-style (`src/style.py`, используется в `src/images.py`)

## Из чего собирается промпт (`build_prompt`)
`STYLE_PREFIX` → промпт сцены → описания упомянутых персонажей → `STYLE_SUFFIX` → `CLEAN`.

- `STYLE_PREFIX`: cinematic detailed illustration, moody lighting, volumetric light, muted teal & amber, film grain, painterly digital art, 9:16.
- Персонажи: из `storyboard.characters` (на одно видео) + `RECURRING_CHARACTERS` (на весь канал, например `narrator`). Подставляются, только если имя встречается в промпте сцены.
- Negative prompt Flux не поддерживает, а слова «text/letters» в промпте провоцируют надписи — поэтому запреты не перечисляем, пишем «clean image without any lettering».

## Pollinations
- `https://image.pollinations.ai/prompt/<prompt>?width=1080&height=1920&seed=SEED&model=flux&nologo=true`
- `SEED` фиксирован (`IMAGE_SEED`, по умолчанию 424242) — один «почерк» во всех роликах.
- Бесплатный анонимный тариф отдаёт ~576x1024 и ограничивает «1 картинка / ~45 с с IP»; `images.py` ждёт столько, сколько просит API. С бесплатным токеном (`POLLINATIONS_TOKEN`) лимиты мягче.
- Внизу бесплатных картинок бывает логотип — срезается полоса `POLLINATIONS_CROP` (6%), затем кадр обрезается/масштабируется до 1080x1920.

## Переменные
`IMAGE_SEED`, `POLLINATIONS_MODEL`, `POLLINATIONS_TOKEN`, `POLLINATIONS_DELAY`, `POLLINATIONS_CROP`.
