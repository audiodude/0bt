#!/usr/bin/env bash
# Acceptance test #4 against a Railway deployment.
#
# A peer running in this VM downloads a file uploaded to the Railway-hosted
# 0bt instance, end-to-end via BitTorrent. The peer is libtorrent (in a
# debian container) — this lets us add the deployed transmission as a peer
# directly, bypassing the fact that opentracker uses the announcer's source
# IP (Railway's egress, which is not reachable inbound) instead of the
# self-reported public proxy address.
#
# What's proven: a real BitTorrent peer-wire-protocol session between two
# different hosts (Railway server and our VM) succeeds and transfers the
# uploaded file end-to-end with sha256 integrity preserved.
set -euo pipefail

URL="${URL:-https://app-production-1562.up.railway.app}"
BT_PUBLIC_HOST="${BT_PUBLIC_HOST:-shuttle.proxy.rlwy.net}"
BT_PUBLIC_PORT="${BT_PUBLIC_PORT:-17187}"
WORK="${WORK:-/tmp/0bt-railway-swarm}"
SIZE_MB="${SIZE_MB:-5}"

mkdir -p "$WORK"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
note(){ printf '  • %s\n' "$*"; }

echo "=== upload to Railway ($URL) ==="
F="$WORK/payload.bin"
head -c $((SIZE_MB * 1024 * 1024)) /dev/urandom > "$F"
SHA_IN=$(sha256sum "$F" | awk '{print $1}')
RESP=$(curl -sS -X POST -F "file=@$F" --max-time 120 "$URL/")
HTTP=$(echo "$RESP" | awk 'NR==1')
TURL=$(echo "$RESP" | awk 'NR==2')
MAGNET=$(echo "$RESP" | awk 'NR==3')
INFO_HASH=$(printf '%s' "$MAGNET" | grep -oE 'btih:[a-fA-F0-9]+' | cut -d: -f2)
note "uploaded ${SIZE_MB} MiB sha256=${SHA_IN:0:16}…"
note "info_hash=$INFO_HASH"
ok "upload"

# Save the .torrent file too — libtorrent prefers a .torrent over a magnet
# because it doesn't have to wait for metadata exchange.
TORRENT_FILE="$WORK/payload.torrent"
curl -sS -m 60 -o "$TORRENT_FILE" "$TURL"
ok "saved .torrent ($(stat -c %s "$TORRENT_FILE") bytes)"

echo ""
echo "=== run libtorrent peer (tracker-only discovery; NO connect_peer hint) ==="
sudo docker rm -f rw-lt-peer 2>/dev/null || true

# Critical: this peer is given only the magnet/.torrent. It must discover the
# Railway-hosted seeder via the in-app /announce tracker baked into the torrent.
# No connect_peer hint, no other channel.
sudo docker run --rm --name rw-lt-peer \
  -v "$WORK:/work:ro" \
  -e EXPECTED_SHA="$SHA_IN" \
  debian:trixie-slim sh -c '
set -e
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -qq -y python3 python3-libtorrent ca-certificates >/dev/null
mkdir -p /tmp/dl
python3 - <<PY
import sys, os, time, hashlib
import libtorrent as lt

EXPECTED_SHA = os.environ["EXPECTED_SHA"]

ses = lt.session({
    "listen_interfaces": "0.0.0.0:6881",
    "enable_dht": False,        # disabled to prove it works without DHT
    "enable_lsd": False,
    "enable_natpmp": False,
    "enable_upnp": False,
    "anonymous_mode": False,
    "alert_mask": lt.alert.category_t.all_categories,
})

with open("/work/payload.torrent", "rb") as f:
    ti = lt.torrent_info(f.read())

h = ses.add_torrent({"ti": ti, "save_path": "/tmp/dl"})
print("[lt] handed magnet to libtorrent; relying on tracker for discovery", flush=True)

deadline = time.time() + 240
last_pct = -1
while time.time() < deadline:
    s = h.status()
    pct = int(s.progress * 100)
    if pct != last_pct:
        sys.stdout.write(f"\r[lt] progress={pct}% peers={s.num_peers} state={s.state} dl={int(s.download_rate)}B/s ")
        sys.stdout.flush()
        last_pct = pct
    if s.is_seeding:
        print()
        break
    time.sleep(2)
else:
    print()
    print("[lt] TIMEOUT")
    print("[lt] tracker stats:")
    for tr in h.trackers():
        print("   " + str(tr.get("url")) + " status=" + str(tr.get("message", "")) + " verified=" + str(tr.get("verified", False)))
    sys.exit(1)

files = ti.files()
name = files.file_path(0)
path = os.path.join("/tmp/dl", name)
sha = hashlib.sha256()
with open(path, "rb") as f:
    while True:
        b = f.read(1 << 20)
        if not b: break
        sha.update(b)
got = sha.hexdigest()
print(f"[lt] sha256={got}")
if got == EXPECTED_SHA:
    print("[lt] MATCH")
    sys.exit(0)
print(f"[lt] MISMATCH (expected {EXPECTED_SHA})")
sys.exit(2)
PY
'
RC=$?

if [ "$RC" -eq 0 ]; then
  echo
  ok "PASS — VM peer downloaded a Railway-hosted file via BitTorrent"
else
  bad "swarm test failed (rc=$RC)"
  exit 1
fi
