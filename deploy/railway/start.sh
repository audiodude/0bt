#!/usr/bin/env bash
# Bootstrap the all-in-one container on Railway.
#
# Responsibilities:
#  - render transmission settings.json from env vars
#  - ensure /data subdirs exist with correct ownership
#  - hand off to supervisord which runs transmission-daemon + gunicorn
set -euo pipefail

: "${PORT:=8080}"
: "${TRANSMISSION_RPC_PORT:=9091}"
: "${TRANSMISSION_RPC_USER:=transmission}"
: "${TRANSMISSION_RPC_PASSWORD:?TRANSMISSION_RPC_PASSWORD is required}"
: "${BT_PEER_PORT:=51413}"
: "${FHOST_STORAGE_PATH:=/data/up}"
: "${FHOST_DB_URL:=sqlite:////data/db/0bt.sqlite}"

mkdir -p /data/up /data/db /data/transmission

# Render settings.json. The keys are exactly transmission's settings names.
cat > /data/transmission/settings.json <<EOF
{
    "rpc-enabled": true,
    "rpc-bind-address": "127.0.0.1",
    "rpc-port": ${TRANSMISSION_RPC_PORT},
    "rpc-url": "/transmission/",
    "rpc-whitelist-enabled": false,
    "rpc-host-whitelist-enabled": false,
    "rpc-authentication-required": true,
    "rpc-username": "${TRANSMISSION_RPC_USER}",
    "rpc-password": "${TRANSMISSION_RPC_PASSWORD}",
    "download-dir": "${FHOST_STORAGE_PATH}",
    "incomplete-dir-enabled": false,
    "watch-dir-enabled": false,
    "umask": 18,
    "peer-port": ${BT_PEER_PORT},
    "peer-port-random-on-start": false,
    "port-forwarding-enabled": false,
    "dht-enabled": true,
    "lpd-enabled": false,
    "pex-enabled": true,
    "utp-enabled": true,
    "ratio-limit": 0,
    "ratio-limit-enabled": false,
    "idle-seeding-limit": 0,
    "idle-seeding-limit-enabled": false,
    "speed-limit-down-enabled": false,
    "speed-limit-up-enabled": false,
    "blocklist-enabled": false,
    "encryption": 1,
    "preallocation": 0,
    "message-level": 2
}
EOF

# Some hosting platforms (Railway) expose an externally-reachable host:port for
# inbound BT peers via their TCP proxy. If the operator gave us one, set
# transmission's announced peer-port so trackers/DHT advertise the right thing,
# and override announce-ip so trackers don't record the egress IP (which isn't
# reachable from outside).
if [ -n "${BT_ANNOUNCE_PORT:-}" ] || [ -n "${BT_PUBLIC_HOST:-}" ]; then
  python3 - <<'PY'
import json, os, socket
path = "/data/transmission/settings.json"
with open(path) as f: s = json.load(f)
ann_port = os.environ.get("BT_ANNOUNCE_PORT")
if ann_port:
    s["peer-port"] = int(ann_port)
host = os.environ.get("BT_PUBLIC_HOST")
if host:
    try:
        ip = socket.gethostbyname(host)
        # Some clients want a name; transmission expects a single IP.
        s["announce-ip"] = ip
        s["announce-ip-enabled"] = True
        print(f"[start] announce-ip set to {ip} (from {host})")
    except OSError as e:
        print(f"[start] could not resolve BT_PUBLIC_HOST={host}: {e}")
with open(path, "w") as f: json.dump(s, f, indent=4)
PY
fi

# transmission-daemon runs as root inside the container; everything in /data
# is owned by root, so no UID mismatch.

exec /usr/bin/supervisord -n -c /app/deploy/railway/supervisord.conf
