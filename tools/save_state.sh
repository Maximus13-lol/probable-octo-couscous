#!/usr/bin/env bash
# Сохранить состояние запуска в main без конфликтов: свежий main + записи этого запуска (src/state_sync.py).
set -u
MSG="$1"
git config user.name "shorts-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
TMP=$(mktemp -d)
python -m src.state_sync save "$TMP"
for i in 1 2 3 4 5; do
  git fetch -q origin main && git reset -q --hard origin/main && python -m src.state_sync merge "$TMP" || { sleep $((i * 5)); continue; }
  git add publish_log.json used_topics.json ads/log.json trends_quota.json 2>/dev/null || true
  if git diff --cached --quiet; then echo "Нет изменений"; exit 0; fi
  git commit -qm "$MSG" && git push -q origin HEAD:main && { echo "Состояние сохранено"; exit 0; }
  sleep $((i * 5))
done
echo "::error::Не удалось сохранить состояние в main после 5 попыток"
exit 1
