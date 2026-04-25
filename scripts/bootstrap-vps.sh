#!/usr/bin/env bash
# One-shot bootstrap for a fresh Debian/Ubuntu VPS.
#
# Run as root on the target server, e.g.:
#   ssh root@your-server 'bash -s' < scripts/bootstrap-vps.sh \
#       files.example.com you@example.com
#
# Args:
#   1. CADDY_DOMAIN       -- the public hostname (DNS A record must already exist)
#   2. CADDY_ADMIN_EMAIL  -- contact email for Let's Encrypt
set -euo pipefail

CADDY_DOMAIN="${1:-}"
CADDY_ADMIN_EMAIL="${2:-}"
if [ -z "$CADDY_DOMAIN" ] || [ -z "$CADDY_ADMIN_EMAIL" ]; then
  echo "usage: $0 <domain> <admin-email>" >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root" >&2
  exit 1
fi

REPO_DIR=/opt/0bt

echo "[1/5] system update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get full-upgrade -y -qq
apt-get install -y -qq ca-certificates curl git ufw openssl

echo "[2/5] firewall"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80,443/tcp
ufw allow 443/udp
ufw allow 51413
ufw --force enable

echo "[3/5] docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "[4/5] clone + configure 0bt"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone https://github.com/audiodude/0bt.git "$REPO_DIR"
fi
cd "$REPO_DIR"
git pull --ff-only

if [ ! -f .env ]; then
  cp .env.example .env
  TR_PASS=$(openssl rand -hex 16)
  sed -i "s|^TRANSMISSION_RPC_PASSWORD=.*|TRANSMISSION_RPC_PASSWORD=$TR_PASS|" .env
fi
sed -i "s|^FHOST_BASE_URL=.*|FHOST_BASE_URL=https://$CADDY_DOMAIN|" .env
sed -i "s|^CADDY_DOMAIN=.*|CADDY_DOMAIN=$CADDY_DOMAIN|" .env
sed -i "s|^CADDY_ADMIN_EMAIL=.*|CADDY_ADMIN_EMAIL=$CADDY_ADMIN_EMAIL|" .env

echo "[5/5] bring up the stack"
# Don't `docker compose pull` separately — it errors on the locally-built
# `0bt-app:local` image. `up --build` builds the app and pulls everything else.
docker compose --profile caddy up -d --build

cat <<EOF

[done]
  - app + transmission + caddy are running
  - https://$CADDY_DOMAIN/healthz should return ok within a minute
    (Caddy needs port 80 reachable to issue the Let's Encrypt cert)
  - logs: docker compose -f $REPO_DIR/docker-compose.yaml logs -f
  - update later: cd $REPO_DIR && git pull && docker compose --profile caddy up -d --build
EOF
