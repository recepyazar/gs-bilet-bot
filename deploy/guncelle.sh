#!/usr/bin/env bash
# Yereldeki kodu sunucuya kopyalar ve botu yeniden başlatır.
#   deploy/guncelle.sh            (varsayılan: root@194.62.52.208)
#   deploy/guncelle.sh root@<IP>
# .env ve data/ (veritabanı) sunucuda kalır, üzerine yazılmaz.
set -euo pipefail
HOST="${1:-root@194.62.52.208}"
DIR=/opt/gs-bilet-bot
cd "$(dirname "$0")/.."

rsync -az --delete \
  --exclude .git --exclude .venv --exclude .env --exclude data/ \
  --exclude __pycache__ --exclude .pytest_cache \
  ./ "$HOST:$DIR/"

ssh "$HOST" "cd $DIR && mkdir -p data && chown 1000:1000 data && docker compose up -d --build && sleep 5 && docker compose logs --tail 15"
