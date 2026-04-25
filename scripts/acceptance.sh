#!/usr/bin/env bash
# Acceptance test for 0bt running at $BASE_URL.
# Tests: upload various sizes, download via HTTP, verify integrity, verify
# torrent + magnet are returned, verify transmission picked up the seed.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
WORK="${WORK:-/tmp/0bt-acceptance}"
SIZES_K="${SIZES_K:-500 10240 102400 512000 1048576}"  # K of: 500K, 10M, 100M, 500M, 1G

mkdir -p "$WORK"
cd "$WORK"

pass=0; fail=0; results=()

note() { printf '  • %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }

for k in $SIZES_K; do
  bytes=$((k * 1024))
  name="upload-${k}k.bin"
  printf '\n=== %s (%d bytes) ===\n' "$name" "$bytes"

  # Create deterministic fast-random source: head -c N from /dev/urandom is fine.
  note "creating $bytes random bytes"
  head -c "$bytes" /dev/urandom > "$name"
  sha_in=$(sha256sum "$name" | awk '{print $1}')
  note "sha256(in)=$sha_in"

  # Upload (curl -F)
  note "uploading…"
  resp_file="resp-${k}k.txt"
  http_code=$(curl -sS -o "$resp_file" -w '%{http_code}' \
                -X POST -F "file=@${name}" \
                --max-time 600 \
                "$BASE_URL/")
  if [ "$http_code" != "200" ]; then
    bad "upload returned HTTP $http_code"; cat "$resp_file"; continue
  fi
  ok  "upload returned 200"
  url=$(awk 'NR==1' "$resp_file")
  turl=$(awk 'NR==2' "$resp_file")
  magnet=$(awk 'NR==3' "$resp_file")

  if [[ "$url" =~ ^https?:// ]]; then ok "got http url: $url"; else bad "no http url: $url"; fi
  if [[ "$turl" =~ \.torrent$ ]]; then ok "got torrent url: $turl"; else bad "no torrent url: $turl"; fi
  if [[ "$magnet" =~ ^magnet: ]];   then ok "got magnet uri";        else bad "no magnet: $magnet"; fi

  # Extract info_hash from magnet for transmission cross-check
  ih=$(printf '%s' "$magnet" | grep -oE 'btih:[a-fA-F0-9]+' | head -1 | cut -d: -f2 | tr a-f A-F)

  # Download via HTTP and verify sha256
  note "downloading via HTTP…"
  out="dl-${k}k.bin"
  http_code=$(curl -sS -o "$out" -w '%{http_code}' --max-time 600 "$url")
  if [ "$http_code" = "200" ]; then ok "HTTP DL returned 200"; else bad "HTTP DL HTTP $http_code"; fi
  sha_out=$(sha256sum "$out" | awk '{print $1}')
  if [ "$sha_in" = "$sha_out" ]; then ok "HTTP DL sha256 matches"; else bad "HTTP DL sha mismatch: in=$sha_in out=$sha_out"; fi

  # Download .torrent
  note "downloading .torrent…"
  tcode=$(curl -sS -o "torrent-${k}k.torrent" -w '%{http_code}' --max-time 60 "$turl")
  if [ "$tcode" = "200" ]; then ok ".torrent DL returned 200"; else bad ".torrent DL HTTP $tcode"; fi
  if head -c 1 "torrent-${k}k.torrent" | grep -q d; then ok ".torrent looks bencoded"; else bad ".torrent does not start with d"; fi

  # Verify transmission has it
  note "checking transmission for info_hash $ih…"
  # Hit transmission via the app's network using the compose service name.
  # We do this from the host, but the docker container talks to transmission via its DNS name.
  # Instead, call the host's mapped transmission RPC: but we did not expose it.
  # So we exec inside the app container using docker.
  results+=("$ih")
done

echo
echo "Sizes done. Querying transmission torrent list…"
# Need RPC creds — get from compose env
TRANS_USER=$(awk -F= '/^TRANSMISSION_RPC_USER=/{print $2}' .env 2>/dev/null || echo transmission)
TRANS_PASS=$(awk -F= '/^TRANSMISSION_RPC_PASSWORD=/{print $2}' .env 2>/dev/null || echo "")

# We don't expose transmission RPC to the host. Run inside the app container,
# which has both python+transmission_rpc and the env vars to auth.
listing=$(sudo docker exec 0bt-app-1 python3 -c "
import os
from transmission_rpc import Client
c = Client(host='transmission', port=9091,
           username=os.environ['TRANSMISSION_RPC_USER'],
           password=os.environ['TRANSMISSION_RPC_PASSWORD'])
for t in c.get_torrents():
    print(t.hashString.upper(), t.name, round(t.progress,2), t.status)
")

echo "$listing"
for ih in "${results[@]}"; do
  if printf '%s\n' "$listing" | awk '{print $1}' | grep -qi "^$ih$"; then
    ok "transmission has $ih"
  else
    bad "transmission MISSING $ih"
  fi
done

echo
printf 'pass=%d fail=%d\n' "$pass" "$fail"
exit $((fail == 0 ? 0 : 1))
