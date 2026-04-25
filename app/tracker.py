"""HTTP BitTorrent tracker (BEP-3 / BEP-23).

The app advertises itself as a tracker (`/announce` + `/scrape`) and prepends
its own announce URL to every magnet. On a normal deployment with a real
public IP this is a useful low-latency complement to public trackers and DHT —
peers often find each other via the in-app tracker before public-tracker
discovery has even completed its first scrape.

Only info_hashes the app actually owns (i.e. were uploaded through it) are
served, so we're not a free public tracker for arbitrary clients.
"""
from __future__ import annotations

import socket
import struct
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import unquote_to_bytes

from flask import Response, request
from sqlalchemy import select

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

    f = db.session.execute(
        select(File).where(File.info_hash == info_hash.hex())
    ).scalar_one_or_none()
    if f is None:
        return _bencoded_failure("unknown info_hash")
    if f.removed:
        return _bencoded_failure("torrent removed")

    client_ip = _client_ip()
    if event == "stopped":
        peers.remove(info_hash, peer_id)
    else:
        peers.upsert(info_hash, peer_id, client_ip, port, left)

    out_peers = peers.get(info_hash, exclude=peer_id)[:numwant]
    complete, incomplete = peers.stats(info_hash)

    body: dict = {
        b"interval": 1800,
        b"min interval": 60,
        b"complete": complete,
        b"incomplete": incomplete,
    }
    if compact:
        buf = b""
        for ip4, port4, _left in out_peers:
            try:
                buf += socket.inet_aton(ip4) + struct.pack(">H", port4)
            except OSError:
                continue
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
    for h in hashes:
        if len(h) != 20:
            continue
        f = db.session.execute(
            select(File).where(File.info_hash == h.hex())
        ).scalar_one_or_none()
        if f is None or f.removed:
            continue
        complete, incomplete = peers.stats(h)
        files[h] = {b"complete": complete, b"incomplete": incomplete, b"downloaded": 0}
    return _bencoded_ok({b"files": files})
