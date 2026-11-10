"""visual-style: единый визуальный стиль всех роликов и сборка промптов для картинок."""
from __future__ import annotations

from .utils import env

# Фиксированный seed → Pollinations рисует в одной «руке»; меняется через IMAGE_SEED.
SEED = int(env("IMAGE_SEED", "424242"))
MODEL = env("POLLINATIONS_MODEL", "flux")

STYLE_PREFIX = (
    "cinematic detailed illustration, moody lighting, dramatic volumetric light, "
    "muted teal and amber color palette, soft film grain, painterly digital art, "
    "high detail, vertical composition 9:16"
)
STYLE_SUFFIX = "consistent art style, atmospheric, mysterious mood"
# Flux не поддерживает negative prompt и от слов «text/letters» в промпте чаще рисует надписи,
# поэтому запреты не перечисляем, а описываем «чистый» кадр.
CLEAN = "clean image without any lettering"

# Постоянные персонажи канала: если сцена про «рассказчика»/«исследователя», он выглядит одинаково.
RECURRING_CHARACTERS = {
    "narrator": "a mysterious investigator in a dark long coat and wide-brimmed hat, face half in shadow",
}


def build_prompt(scene_prompt: str, characters: list[dict] | None = None) -> str:
    """Склеивает стиль + описание персонажей (только тех, кто упомянут в сцене) + промпт сцены."""
    char_bits = []
    lowered = scene_prompt.lower()
    all_chars = {c["name"].lower(): c["description"] for c in (characters or []) if c.get("name")}
    all_chars.update(RECURRING_CHARACTERS)
    for name, desc in all_chars.items():
        if name in lowered:
            char_bits.append(f"{name} is {desc}")
    parts = [STYLE_PREFIX, scene_prompt.strip().rstrip(".")]
    if char_bits:
        parts.append("; ".join(char_bits))
    parts.append(STYLE_SUFFIX)
    parts.append(CLEAN)
    return ", ".join(parts)
