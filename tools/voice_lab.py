"""Стенд сравнения озвучки голосом автора: несколько моделей Qwen3-TTS × варианты текста (обычный /
ударная гласная заглавной). Пишет lab/<модель>_<вариант>.wav. Запускается из workflow voice-lab.yml."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

cfg = json.load(open("config/voice.json", encoding="utf-8"))["qwen"]
variants = {"plain": os.environ["LAB_PLAIN"], "caps": os.environ["LAB_CAPS"]}
models = sys.argv[1:] or ["Qwen/Qwen3-TTS-12Hz-0.6B-Base", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"]
out = Path("lab")
out.mkdir(exist_ok=True)
report = []
for name in models:
    t0 = time.time()
    m = Qwen3TTSModel.from_pretrained(name, device_map="cpu", dtype=torch.float32)
    p = m.create_voice_clone_prompt(ref_audio=cfg["ref_audio"], ref_text=cfg["ref_text"], x_vector_only_mode=False)
    tag = name.split("-")[-2]  # 0.6B / 1.7B
    for v, text in variants.items():
        parts = []
        t1 = time.time()
        for sent in [s.strip() for s in text.replace("!", ".").split(".") if s.strip()]:
            torch.manual_seed(1)
            w, sr = m.generate_voice_clone(text=sent + ".", language="Russian", voice_clone_prompt=p,
                                           max_new_tokens=int(len(sent) * 1.6) + 40)
            parts += [np.asarray(w[0]), np.zeros(int(sr * 0.4))]
        a = np.concatenate(parts)
        sf.write(out / f"{tag}_{v}.wav", a, sr)
        report.append({"model": tag, "variant": v, "audio_sec": round(len(a) / sr, 1),
                       "gen_sec": round(time.time() - t1, 1)})
        print(report[-1], flush=True)
    del m
json.dump(report, open(out / "report.json", "w"), ensure_ascii=False, indent=1)
