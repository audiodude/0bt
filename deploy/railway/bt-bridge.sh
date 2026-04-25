#!/bin/sh
# Inbound BT peer-port bridge for hosts that translate ports (Railway).
#
# Railway's TCP proxy assigns a random public port per proxy and routes
# externally:public_proxy_port -> internally:applicationPort. BitTorrent
# uses a single port for both listening and announcing, so we need:
#   - transmission peer-port = public_proxy_port  (so announces work)
#   - inside container, traffic that lands at applicationPort must reach
#     transmission's listen port (= public_proxy_port)
# socat does the inside-the-container leg of that mapping.
#
# When BT_BRIDGE_FROM == BT_PEER_PORT (no port translation, e.g. a plain
# `docker run -p 51413:51413`), no bridge is needed and we idle.
set -eu

if [ -z "${BT_BRIDGE_FROM:-}" ] || [ -z "${BT_PEER_PORT:-}" ] || [ "$BT_BRIDGE_FROM" = "$BT_PEER_PORT" ]; then
  echo "[bt-bridge] no port translation (BT_BRIDGE_FROM=${BT_BRIDGE_FROM:-} BT_PEER_PORT=${BT_PEER_PORT:-}); idling"
  exec sleep infinity
fi

echo "[bt-bridge] forwarding container :${BT_BRIDGE_FROM} -> 127.0.0.1:${BT_PEER_PORT}"
exec socat -d "TCP-LISTEN:${BT_BRIDGE_FROM},fork,reuseaddr" "TCP:127.0.0.1:${BT_PEER_PORT}"
