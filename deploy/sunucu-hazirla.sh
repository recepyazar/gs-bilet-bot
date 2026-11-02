#!/usr/bin/env bash
# Yeni bir Ubuntu sunucusunu bot için hazırlar. Root olarak, bir kez çalıştırılır:
#   ssh root@<IP> 'bash -s' < deploy/sunucu-hazirla.sh
#
# ÖNCE anahtarla girişin çalıştığından emin ol (ssh-copy-id), yoksa kendini kilitlersin.
set -euo pipefail

echo "==> SSH: şifreyle giriş kapatılıyor (sadece anahtar)"
cat > /etc/ssh/sshd_config.d/00-guvenlik.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
sshd -t
systemctl reload ssh
sshd -T | grep -E "^(passwordauthentication|permitrootlogin)"

echo "==> Paketler: güvenlik duvarı, otomatik güncelleme, Docker"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ufw unattended-upgrades docker.io docker-compose-v2 rsync >/dev/null

echo "==> Güvenlik duvarı: sadece SSH açık"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
ufw status | head -5

echo "==> Otomatik güvenlik güncellemeleri"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Docker"
systemctl enable --now docker >/dev/null 2>&1
docker --version
docker compose version

echo "==> Tamam"
