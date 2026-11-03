#!/usr/bin/env bash
# Sunucuda otomatik güncellemeyi (CD) bir kez kurar. Root olarak:
#   ssh root@<IP> 'bash -s' < deploy/cd-kur.sh
#
# - /opt/gs-bilet-bot klasörünü GitHub reposunun bir kopyasına çevirir
#   (.env ve data/ git'te olmadığı için olduğu gibi kalır)
# - 5 dakikada bir deploy/otomatik-guncelle.sh çalıştıran systemd zamanlayıcısını kurar
set -euo pipefail

REPO="${GSBOT_REPO:-https://github.com/recepyazar/gs-bilet-bot.git}"
DIR="${GSBOT_DIR:-/opt/gs-bilet-bot}"

command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git >/dev/null; }

cd "$DIR"
if [ ! -d .git ]; then
  echo "==> $DIR git deposuna çevriliyor"
  git init -q
  git remote add origin "$REPO"
fi
git fetch -q origin main
git reset -q --hard origin/main
echo "==> Kod: $(git log -1 --format='%h %s')"
test -f .env || { echo "!! .env yok — önce .env'i koy"; exit 1; }

cat > /etc/systemd/system/gsbot-deploy.service <<EOF
[Unit]
Description=GS bilet botu: GitHub'daki yeni commit'i test edip yayına al
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$DIR/deploy/otomatik-guncelle.sh
TimeoutStartSec=900
EOF

cat > /etc/systemd/system/gsbot-deploy.timer <<EOF
[Unit]
Description=GS bilet botu otomatik güncelleme (5 dakikada bir)

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
EOF

chmod +x "$DIR"/deploy/*.sh
systemctl daemon-reload
systemctl enable --now gsbot-deploy.timer
systemctl list-timers gsbot-deploy.timer --no-pager | head -3
echo "==> Tamam. Log: journalctl -u gsbot-deploy -f"
