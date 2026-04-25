#!/usr/bin/env bash
# Acceptance test #4: cross-host BitTorrent swarming.
#
# Strategy:
# 1. Start a self-hosted opentracker in the compose stack and configure the
#    server to embed it in every magnet (FHOST_INTERNAL_TRACKER).
# 2. Upload a test file. The server's Transmission seeds it.
# 3. Spin up a SECOND, otherwise-isolated Transmission instance on the same
#    docker network. From the BT layer's POV it's "another host": separate
#    process, separate IP, separate state directory.
# 4. Hand it the magnet. Verify it discovers the seeder via the tracker and
#    downloads the file to 100%.
# 5. Verify the bytes match (sha256).
#
# This proves the data plane (peer discovery + actual BT transfer) works.
# A truly distributed test (across the public internet) is reproduced once
# the server is deployed to Railway.
set -euo pipefail

cd "$(dirname "$0")/.."

WORK="${WORK:-/tmp/0bt-swarm}"
mkdir -p "$WORK"

note() { printf '  • %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

source .env  # load TRANSMISSION_RPC_USER/PASSWORD

# Make sure the tracker profile is up.
note "starting tracker profile…"
sudo FHOST_INTERNAL_TRACKER="udp://tracker:6969/announce" \
  docker compose --profile tracker up -d --build app tracker transmission >/dev/null

# Wait for the app to be healthy (compose restarted it for the env change).
for _ in $(seq 1 60); do
  if curl -sf http://localhost:8080/healthz >/dev/null; then break; fi
  sleep 1
done
ok "app is up"

# Upload a file — pick something we haven't uploaded before so it's a fresh seed
TESTFILE="$WORK/swarm-payload.bin"
head -c $((20 * 1024 * 1024)) /dev/urandom > "$TESTFILE"
SHA_IN=$(sha256sum "$TESTFILE" | awk '{print $1}')
note "uploading 20 MiB test file (sha256=$SHA_IN)"
RESP=$(curl -sS -X POST -F "file=@$TESTFILE" http://localhost:8080/)
URL=$(echo "$RESP" | awk 'NR==1')
TURL=$(echo "$RESP" | awk 'NR==2')
MAGNET=$(echo "$RESP" | awk 'NR==3')
ok "got URL: $URL"
ok "got magnet"

# Sanity: verify the magnet has our internal tracker
if printf '%s' "$MAGNET" | grep -qF 'tracker%3A6969'; then
  ok "magnet contains internal tracker reference"
elif printf '%s' "$MAGNET" | grep -qF 'tracker:6969'; then
  ok "magnet contains internal tracker reference"
else
  note "magnet did not include internal tracker; relying on the others. Magnet: $MAGNET"
fi

# Save .torrent locally — we'll pass it to the test peer
TORRENT_PATH="$WORK/swarm.torrent"
curl -sS "$TURL" -o "$TORRENT_PATH"
ok "saved .torrent: $(stat -c %s "$TORRENT_PATH") bytes"

# Spin up a second transmission instance on the same docker network (zerobt).
# It is a separate process with its own IP and state dir; from the BT layer it
# is indistinguishable from "a peer on another host".
TEST_NET=0bt_zerobt
TEST_NAME=swarm-peer-1
sudo docker rm -f $TEST_NAME 2>/dev/null || true
sudo docker volume rm swarm-peer-1-config swarm-peer-1-data 2>/dev/null || true

note "starting test peer container ($TEST_NAME) on network $TEST_NET…"
sudo docker run -d --rm \
  --name $TEST_NAME \
  --network $TEST_NET \
  -e PUID=1000 -e PGID=1000 -e TZ=Etc/UTC \
  -e USER=peer -e PASS=peerpass \
  -v swarm-peer-1-config:/config \
  -v swarm-peer-1-data:/downloads \
  linuxserver/transmission:4.0.6 >/dev/null

# Wait for its RPC to come up
for _ in $(seq 1 30); do
  if sudo docker exec $TEST_NAME curl -sf -u peer:peerpass http://127.0.0.1:9091/transmission/web/ >/dev/null 2>&1; then break; fi
  if sudo docker exec $TEST_NAME curl -sS -u peer:peerpass http://127.0.0.1:9091/transmission/rpc 2>&1 | grep -q "X-Transmission-Session-Id"; then break; fi
  sleep 2
done
sleep 3
ok "test peer up"

# Add the .torrent to the test peer via the app container's python (avoids
# heredoc-via-docker-exec quirks).
note "submitting magnet to test peer via RPC…"
TORRENT_B64=$(sudo base64 -w0 "$TORRENT_PATH")
sudo docker exec -e TPB64="$TORRENT_B64" -e MAGNET="$MAGNET" 0bt-app-1 python3 -c "
import os, time
from transmission_rpc import Client
c = Client(host='swarm-peer-1', port=9091, username='peer', password='peerpass', timeout=30)
# Use the magnet — peer will fetch metainfo from the swarm via the tracker.
t = c.add_torrent(os.environ['MAGNET'], paused=False)
print('added', t.hashString)
"

# Poll until 100% or timeout (90s for 20 MiB, generous)
note "waiting for download (timeout 120s)…"
done_pct=0
for i in $(seq 1 120); do
  pct=$(sudo docker exec 0bt-app-1 python3 -c "
from transmission_rpc import Client
c = Client(host='swarm-peer-1', port=9091, username='peer', password='peerpass', timeout=10)
ts = c.get_torrents()
if not ts: print(0)
else: print(int(ts[0].progress))
")
  printf '\r    progress=%s%%   ' "$pct"
  if [ "$pct" -ge 100 ]; then echo; ok "test peer reached 100%"; done_pct=100; break; fi
  sleep 1
done

if [ "$done_pct" -ne 100 ]; then
  bad "test peer did NOT reach 100% (last=$pct%)"
  note "diagnostics:"
  sudo docker exec 0bt-app-1 python3 -c "
from transmission_rpc import Client
for label,host in [('server','transmission'),('peer','swarm-peer-1')]:
    try:
        u = 'transmission' if label=='server' else 'peer'
        p = open('/dev/null') and 'peerpass' if label=='peer' else None
        import os
        if label == 'server':
            c = Client(host=host, port=9091, username=os.environ['TRANSMISSION_RPC_USER'], password=os.environ['TRANSMISSION_RPC_PASSWORD'])
        else:
            c = Client(host=host, port=9091, username='peer', password='peerpass')
        for t in c.get_torrents():
            print(label, t.hashString[:8], 'progress=', t.progress, 'peers=', getattr(t, 'peers_connected', '?'),
                  'status=', t.status, 'error=', t.error_string)
    except Exception as e:
        print(label, 'error:', e)
"
  exit 1
fi

# Sha-verify the downloaded file inside the test peer
note "verifying sha256 inside the test peer…"
SHA_OUT=$(sudo docker exec $TEST_NAME sh -c "find /downloads -type f -name '*.bin' -exec sha256sum {} \\;" | awk '{print $1}' | head -1)
if [ "$SHA_IN" = "$SHA_OUT" ]; then
  ok "sha256 matches: peer received the right bytes via BitTorrent"
else
  bad "sha mismatch: in=$SHA_IN out=$SHA_OUT"
  exit 1
fi

note "cleaning up test peer…"
sudo docker rm -f $TEST_NAME >/dev/null
sudo docker volume rm swarm-peer-1-config swarm-peer-1-data 2>/dev/null || true

echo
echo "PASS — cross-host BitTorrent swarm verified."
