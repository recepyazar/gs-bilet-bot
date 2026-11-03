#!/usr/bin/env bash
# Sunucu tarafı CD. systemd zamanlayıcısı (gsbot-deploy.timer) 5 dakikada bir çalıştırır.
#
#   GitHub'da yeni commit var mı?
#     yoksa            → çık
#     varsa            → testleri yeni kodla docker içinde koş
#       geçti          → kodu güncelle, botu yeniden kur, Telegram'a 🚀
#       kaldı          → hiçbir şeye dokunma (eski sürüm çalışır), Telegram'a ❌
#
# Kalan bir commit tekrar tekrar denenmez; yeni bir commit gelince yeniden dener.
# Elle çalıştırmak için:  systemctl start gsbot-deploy   (log: journalctl -u gsbot-deploy)
set -euo pipefail

DIR="${GSBOT_DIR:-/opt/gs-bilet-bot}"
BRANCH="${GSBOT_BRANCH:-main}"
cd "$DIR"

exec 9>/run/gsbot-deploy.lock
flock -n 9 || { echo "Başka bir güncelleme sürüyor."; exit 0; }

notify() {
  # .env'den token ve yönetici ID'lerini okur; düz metin gönderir (HTML kaçışı gerekmesin)
  local token admins
  token=$(grep -E '^TELEGRAM_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"'"'" || true)
  admins=$(grep -E '^ADMIN_IDS=' .env | head -1 | cut -d= -f2- | tr -d '"'"' " || true)
  [ -n "$token" ] && [ -n "$admins" ] || return 0
  for id in ${admins//,/ }; do
    curl -s -o /dev/null --max-time 20 "https://api.telegram.org/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${id}" --data-urlencode "text=$1" || true
  done
}

git fetch -q origin "$BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
[ "$LOCAL" = "$REMOTE" ] && exit 0

if [ -f .deploy-failed ] && [ "$(cat .deploy-failed)" = "$REMOTE" ]; then
  exit 0   # bu commit zaten kaldı, bildirildi
fi

SHORT=$(git rev-parse --short "$REMOTE")
SUBJECT=$(git log -1 --format=%s "$REMOTE")
echo "==> Yeni commit: $SHORT $SUBJECT"

# Testler: çalışma klasörüne dokunmadan, commit'in kendisinden
LOG=$(mktemp)
if ! git archive --format=tar "$REMOTE" | docker build --target test -t gsbot-test - >"$LOG" 2>&1; then
  echo "$REMOTE" > .deploy-failed
  TAIL=$(grep -E "FAILED|Error|error|passed|failed" "$LOG" | tail -8 | cut -c1-200)
  echo "==> Testler kaldı"; tail -40 "$LOG"
  notify "❌ Güncelleme yapılmadı, testler kaldı
$SHORT $SUBJECT

$TAIL

Eski sürüm çalışmaya devam ediyor."
  rm -f "$LOG"
  exit 1
fi
PASSED=$(grep -oE "[0-9]+ passed" "$LOG" | tail -1 || true)
rm -f "$LOG"
echo "==> Testler geçti ($PASSED)"

git reset -q --hard "$REMOTE"
rm -f .deploy-failed
mkdir -p data && chown 1000:1000 data
git log -1 --format='%h %s (%cd)' --date=format:'%d.%m.%Y %H:%M' > gsbot/VERSION

if ! docker compose up -d --build >/dev/null 2>&1; then
  notify "⚠️ Testler geçti ama bot yeniden kurulamadı: $SHORT $SUBJECT
Sunucuda: journalctl -u gsbot-deploy"
  exit 1
fi

sleep 15
if [ "$(docker compose ps --status running -q | wc -l)" -lt 1 ]; then
  notify "⚠️ $SHORT yayına alındı ama bot çalışmıyor. Sunucuda: docker compose logs"
  exit 1
fi

docker image prune -f >/dev/null 2>&1 || true
echo "==> Yayında: $SHORT"
notify "🚀 Bot güncellendi ($PASSED)
$SHORT $SUBJECT"
