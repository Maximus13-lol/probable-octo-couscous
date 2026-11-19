"""Надёжное сохранение состояния (publish_log.json, used_topics.json, ads/log.json, trends_quota.json)
в main без конфликтов: свежий main + записи этого запуска. Раньше `git pull --rebase` падал на конфликте
в publish_log.json, и запись о загруженном ролике терялась (19.11.2026, «Дело №2»).

    python -m src.state_sync save <папка-копия>   # скопировать файлы состояния этого запуска
    python -m src.state_sync merge <папка-копия>  # после reset на origin/main: наложить записи запуска
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

FILES = ["publish_log.json", "used_topics.json", "ads/log.json", "trends_quota.json"]


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _key(e) -> str:
    if isinstance(e, dict):
        for k in ("video_id", "run_id"):
            if e.get(k):
                return f"{k}:{e[k]}"
    return json.dumps(e, ensure_ascii=False, sort_keys=True)


def merge_lists(remote: list, ours: list) -> list:
    out, idx = [], {}
    for e in remote:
        idx[_key(e)] = len(out)
        out.append(e)
    for e in ours:
        k = _key(e)
        if k in idx:
            r = out[idx[k]]
            if isinstance(r, dict) and isinstance(e, dict):
                out[idx[k]] = {**e, **r, **{f: v for f, v in e.items() if f not in r or v and not r.get(f)}}
        else:
            idx[k] = len(out)
            out.append(e)
    return out


def save(dst: Path) -> None:
    for f in FILES:
        if Path(f).exists():
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f)


def merge(src: Path) -> None:
    for f in FILES:
        ours = _load(src / f)
        if ours is None:
            continue
        remote = _load(Path(f))
        if isinstance(ours, list) and isinstance(remote, list):
            new = merge_lists(remote, ours)
        else:
            new = ours  # словари (квоты) — значение этого запуска новее
        Path(f).parent.mkdir(parents=True, exist_ok=True)
        Path(f).write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    {"save": save, "merge": merge}[sys.argv[1]](Path(sys.argv[2]))
