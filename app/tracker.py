"""HTTP BitTorrent tracker — replaces an external opentracker.

Why this lives in the app process: when the deployed transmission sits behind
a TCP proxy that translates ports (Railway), no external tracker can correctly
publish the seeder's reachable address. opentracker and public trackers both
record the announcer's source IP, which is the platform's egress address
(unreachable inbound), not the public proxy domain.

The app is the one component that knows both:
  - the info_hash of every uploaded file (from the File table)
  - the proxy host:port the deployment was given
…so it can synthesise the right peer for every announce.

Behavioural rules:
  - Only info_hashes we know about (i.e. were uploaded through this app) are
    served. This keeps random clients from using us as a free public tracker.
  - When BT_PUBLIC_HOST/PORT are configured, every announce response is
    augmented with that address as a confirmed seeder.
  - Announces from RFC1918 / loopback ranges are *not* registered as peers
    when running behind a proxy: those are our own internal services
    (transmission), not real peers, and their self-reported address would
    mislead other clients.
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import unquote_to_bytes

from flask import Response, request
from sqlalchemy import select

from .config import settings
from .models import File, db


# ---- bencode -----------------------------------------------------------------


def bencode(obj) -> bytes:
    if isinstance(obj, bool):
        return bencode(int(obj))
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    if isinstance(obj, str):
        b = obj.encode("utf-8")
        return str(len(b)).encode() + b":" + b
    if isinstance(obj, (list, tuple)):
        return b"l" + b"".join(bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        out = b"d"
        keys = sorted(obj.keys(), key=lambda x: x if isinstance(x, bytes) else x.encode())
        for k in keys:
            kb = k if isinstance(k, bytes) else k.encode()
            out += bencode(kb) + bencode(obj[k])
        return out + b"e"
    raise TypeError(f"can't bencode {type(obj).__name__}")


# ---- request parsing ---------------------------------------------------------


def _parse_query_bytes() -> dict[str, list[bytes]]:
    """Parse the raw QUERY_STRING into bytes per key.

    Werkzeug's request.args utf-8-decodes values, which silently corrupts
    info_hash and peer_id (binary 20-byte fields). Going through the raw
    QUERY_STRING with unquote_to_bytes is the only way to recover them.
    """
    qs = request.environ.get("QUERY_STRING", "")
    out: dict[str, list[bytes]] = defaultdict(list)
    for pair in qs.split("&") if qs else ():
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k].append(unquote_to_bytes(v))
        else:
            out[pair].append(b"")
    return out


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True  # treat unparseable as private (unsafe to advertise)
    return a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified


# ---- peer registry -----------------------------------------------------------


PEER_TTL_SEC = 30 * 60


class PeerTable:
    """Per-info_hash registry of recently-announcing peers."""

    def __init__(self) -> None:
        self._lock = Lock()
        # info_hash -> peer_id -> (ip, port, last_seen, left)
        self._peers: dict[bytes, dict[bytes, tuple[str, int, float, int]]] = defaultdict(dict)

    def upsert(self, info_hash: bytes, peer_id: bytes, ip: str, port: int, left: int) -> None:
        with self._lock:
            self._peers[info_hash][peer_id] = (ip, port, time.time(), left)

    def remove(self, info_hash: bytes, peer_id: bytes) -> None:
        with self._lock:
            self._peers[info_hash].pop(peer_id, None)

    def get(self, info_hash: bytes, exclude: bytes | None = None) -> list[tuple[str, int, int]]:
        cutoff = time.time() - PEER_TTL_SEC
        out: list[tuple[str, int, int]] = []
        with self._lock:
            d = self._peers.get(info_hash, {})
            for pid, (ip, port, ts, left) in list(d.items()):
                if ts < cutoff:
                    d.pop(pid, None)
                    continue
                if pid == exclude:
                    continue
                out.append((ip, port, left))
        return out

    def stats(self, info_hash: bytes) -> tuple[int, int]:
        complete = incomplete = 0
        cutoff = time.time() - PEER_TTL_SEC
        with self._lock:
            d = self._peers.get(info_hash, {})
            for pid, (_ip, _p, ts, left) in list(d.items()):
                if ts < cutoff:
                    d.pop(pid, None)
                    continue
                if left == 0:
                    complete += 1
                else:
                    incomplete += 1
        return complete, incomplete


peers = PeerTable()


# ---- deployed seeder address resolution --------------------------------------


def _seeder_endpoint() -> tuple[str, int] | None:
    """Return ``(host, port)`` for the deployed seeder, where ``host`` may be
    either a literal IPv4 address or a DNS hostname.

    Crucially, we do NOT resolve hostnames here. On platforms like Railway,
    the in-container DNS view of the proxy domain returns an internal-only IP
    that isn't reachable from the public internet — so resolving server-side
    would publish a wrong address to peers. Returning the hostname lets each
    peer resolve from its own (public) DNS view, which always gives the
    externally-reachable address.
    """
    return settings.public_seeder_addr


# ---- responses ---------------------------------------------------------------


def _bencoded_failure(reason: str) -> Response:
    return Response(bencode({b"failure reason": reason.encode()}),
                    status=200, mimetype="text/plain")


def _bencoded_ok(body: dict) -> Response:
    return Response(bencode(body), status=200, mimetype="text/plain")


# ---- handlers ----------------------------------------------------------------


def announce() -> Response:
    p = _parse_query_bytes()
    info_hash = (p.get("info_hash") or [b""])[0]
    peer_id = (p.get("peer_id") or [b""])[0]
    if len(info_hash) != 20:
        return _bencoded_failure("invalid info_hash")
    if len(peer_id) != 20:
        return _bencoded_failure("invalid peer_id")

    try:
        port = int((p.get("port") or [b"0"])[0])
    except ValueError:
        return _bencoded_failure("invalid port")
    if not (1 <= port <= 65535):
        return _bencoded_failure("invalid port")
    try:
        left = int((p.get("left") or [b"0"])[0])
    except ValueError:
        left = 0
    event = (p.get("event") or [b""])[0].decode("ascii", errors="ignore")
    compact = (p.get("compact") or [b"1"])[0] == b"1"
    try:
        numwant = max(0, min(100, int((p.get("numwant") or [b"50"])[0])))
    except ValueError:
        numwant = 50

    # Only serve info_hashes we know about.
    f = db.session.execute(
        select(File).where(File.info_hash == info_hash.hex())
    ).scalar_one_or_none()
    if f is None:
        return _bencoded_failure("unknown info_hash")
    if f.removed:
        return _bencoded_failure("torrent removed")

    seeder = _seeder_endpoint()
    client_ip = _client_ip()
    behind_proxy = settings.public_seeder_addr is not None

    # When deployed behind a proxy, drop announces from RFC1918/loopback/etc —
    # those are our internal services (transmission) reaching us via the
    # private network, not external peers, and their self-reported address
    # would mislead the swarm. The seeder injection below is what advertises
    # the deployed transmission correctly.
    if event == "stopped":
        peers.remove(info_hash, peer_id)
    elif behind_proxy and _is_private(client_ip):
        pass
    else:
        peers.upsert(info_hash, peer_id, client_ip, port, left)

    out_peers: list[tuple[str, int, int]] = []
    if seeder is not None:
        out_peers.append((seeder[0], seeder[1], 0))
    out_peers.extend(peers.get(info_hash, exclude=peer_id))
    out_peers = out_peers[:numwant]

    complete, incomplete = peers.stats(info_hash)
    if seeder is not None:
        complete = max(complete, 1)

    body: dict = {
        b"interval": 1800,
        b"min interval": 60,
        b"complete": complete,
        b"incomplete": incomplete,
    }

    # Compact (BEP-23) requires literal IPv4 in 6-byte format. Fall back to the
    # non-compact dict form whenever any peer is given as a hostname (e.g. the
    # deployed seeder advertised by name) — modern BT clients accept both.
    def _is_ipv4(s: str) -> bool:
        try:
            socket.inet_aton(s)
            return True
        except OSError:
            return False

    use_compact = compact and all(_is_ipv4(host) for host, _, _ in out_peers)

    if use_compact:
        buf = b""
        for ip4, port4, _left in out_peers:
            buf += socket.inet_aton(ip4) + struct.pack(">H", port4)
        body[b"peers"] = buf
    else:
        body[b"peers"] = [
            {b"ip": host.encode(), b"port": port4}
            for host, port4, _ in out_peers
        ]
    return _bencoded_ok(body)


def scrape() -> Response:
    p = _parse_query_bytes()
    hashes = p.get("info_hash") or []
    files: dict[bytes, dict] = {}
    seeder = _seeder_addr()
    for h in hashes:
        if len(h) != 20:
            continue
        f = db.session.execute(
            select(File).where(File.info_hash == h.hex())
        ).scalar_one_or_none()
        if f is None or f.removed:
            continue
        complete, incomplete = peers.stats(h)
        if seeder is not None:
            complete = max(complete, 1)
        files[h] = {
            b"complete": complete,
            b"incomplete": incomplete,
            b"downloaded": 0,
        }
    return _bencoded_ok({b"files": files})
