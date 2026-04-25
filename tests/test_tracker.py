"""Tests for the in-app HTTP BitTorrent tracker."""
from __future__ import annotations

import binascii
from io import BytesIO
from urllib.parse import quote_from_bytes

from app.tracker import bencode


def _bdecode(data: bytes):
    """Tiny bdecoder for assertions."""
    pos = [0]

    def parse():
        c = data[pos[0]:pos[0] + 1]
        if c == b"i":
            pos[0] += 1
            end = data.index(b"e", pos[0])
            n = int(data[pos[0]:end])
            pos[0] = end + 1
            return n
        if c == b"l":
            pos[0] += 1
            out = []
            while data[pos[0]:pos[0] + 1] != b"e":
                out.append(parse())
            pos[0] += 1
            return out
        if c == b"d":
            pos[0] += 1
            out = {}
            while data[pos[0]:pos[0] + 1] != b"e":
                k = parse()
                v = parse()
                out[k] = v
            pos[0] += 1
            return out
        # bytestring: <len>:<bytes>
        end = data.index(b":", pos[0])
        n = int(data[pos[0]:end])
        pos[0] = end + 1
        s = data[pos[0]:pos[0] + n]
        pos[0] += n
        return s

    return parse()


def test_bencode_roundtrip():
    cases = [
        42,
        0,
        -7,
        b"hi",
        b"",
        [1, b"two", [3, b"four"]],
        {b"a": 1, b"b": [b"c", b"d"]},
    ]
    for c in cases:
        assert _bdecode(bencode(c)) == c


def _upload(client, name="hello.txt", payload=b"hello world\n"):
    r = client.post("/", data={"file": (BytesIO(payload), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    lines = r.data.decode().strip().split("\n")
    return {"http_url": lines[0], "torrent_url": lines[1], "magnet": lines[2]}


def _info_hash_from_magnet(magnet: str) -> bytes:
    import re
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    assert m, magnet
    return binascii.unhexlify(m.group(1))


def test_announce_unknown_info_hash(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        bogus = b"\x00" * 20
        qs = (
            f"info_hash={quote_from_bytes(bogus)}"
            f"&peer_id={quote_from_bytes(b'-AB1234-' + b'x' * 12)}"
            "&port=51413&uploaded=0&downloaded=0&left=0&compact=1"
        )
        r = c.get(f"/announce?{qs}")
        assert r.status_code == 200
        body = _bdecode(r.data)
        assert b"failure reason" in body


def test_announce_registers_and_returns_peers(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        up = _upload(c)
        ih = _info_hash_from_magnet(up["magnet"])

        # Two distinct peer_ids announce — the second should see the first.
        peer_a = b"-AA1111-" + b"a" * 12
        peer_b = b"-BB2222-" + b"b" * 12
        common = (
            f"info_hash={quote_from_bytes(ih)}"
            "&port=6881&uploaded=0&downloaded=0&left=100&compact=1"
        )
        r1 = c.get(
            f"/announce?{common}&peer_id={quote_from_bytes(peer_a)}",
            environ_base={"REMOTE_ADDR": "203.0.113.10"},
        )
        assert r1.status_code == 200
        body1 = _bdecode(r1.data)
        assert b"failure reason" not in body1
        # peer_a's response must NOT include itself
        assert peer_a not in body1.get(b"peers", b"")  # compact form, just bytes

        r2 = c.get(
            f"/announce?{common}&peer_id={quote_from_bytes(peer_b)}&port=6882",
            environ_base={"REMOTE_ADDR": "203.0.113.11"},
        )
        body2 = _bdecode(r2.data)
        assert b"failure reason" not in body2
        # peer_b should see peer_a (compact peer = 4-byte ip + 2-byte port)
        peer_bytes = body2[b"peers"]
        assert len(peer_bytes) % 6 == 0
        assert len(peer_bytes) >= 6
        # Decode and confirm 203.0.113.10:6881 is in there
        import socket
        import struct
        found = False
        for i in range(0, len(peer_bytes), 6):
            ip = socket.inet_ntoa(peer_bytes[i:i + 4])
            port = struct.unpack(">H", peer_bytes[i + 4:i + 6])[0]
            if ip == "203.0.113.10" and port == 6881:
                found = True
        assert found, peer_bytes.hex()


def test_announce_drops_private_when_proxy_configured(app_factory, monkeypatch):
    """Behind a proxy, RFC1918 announces are not registered (they're our
    internal transmission, not a real external peer)."""
    monkeypatch.setenv("BT_PUBLIC_HOST", "example.invalid")
    monkeypatch.setenv("BT_PUBLIC_PORT", "17187")
    app, _ = app_factory()
    with app.test_client() as c:
        up = _upload(c)
        ih = _info_hash_from_magnet(up["magnet"])

        # Private-source peer: should be ignored (not registered).
        peer_internal = b"-TR410B-" + b"i" * 12
        c.get(
            f"/announce?info_hash={quote_from_bytes(ih)}"
            f"&peer_id={quote_from_bytes(peer_internal)}"
            "&port=51413&uploaded=0&downloaded=0&left=0&compact=1",
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
        )

        # Public-source peer: should be registered AND get the seeder back.
        peer_external = b"-AA1111-" + b"x" * 12
        r = c.get(
            f"/announce?info_hash={quote_from_bytes(ih)}"
            f"&peer_id={quote_from_bytes(peer_external)}"
            "&port=6881&uploaded=0&downloaded=0&left=100&compact=1",
            environ_base={"REMOTE_ADDR": "198.51.100.7"},
        )
        body = _bdecode(r.data)
        assert b"failure reason" not in body
        # The seeder injection requires BT_PUBLIC_HOST to resolve. Our test
        # uses an unresolvable name so seeder=None → no synthetic peer added.
        # The internal peer must NOT appear.
        peer_bytes = body[b"peers"]
        # 10.0.0.5 must not be in peers
        assert b"\x0a\x00\x00\x05" not in peer_bytes


def test_seeder_hostname_returns_noncompact(app_factory, monkeypatch):
    """When BT_PUBLIC_HOST is a hostname (not an IP), responses must use
    non-compact dict form so the peer can resolve it themselves — server-side
    resolution may give a wrong (platform-internal) address."""
    monkeypatch.setenv("BT_PUBLIC_HOST", "shuttle.proxy.rlwy.net")
    monkeypatch.setenv("BT_PUBLIC_PORT", "17187")
    app, _ = app_factory()
    with app.test_client() as c:
        up = _upload(c)
        ih = _info_hash_from_magnet(up["magnet"])
        peer = b"-AA1111-" + b"x" * 12
        r = c.get(
            f"/announce?info_hash={quote_from_bytes(ih)}"
            f"&peer_id={quote_from_bytes(peer)}"
            "&port=6881&uploaded=0&downloaded=0&left=100&compact=1",
            environ_base={"REMOTE_ADDR": "198.51.100.7"},
        )
        body = _bdecode(r.data)
        assert b"failure reason" not in body
        # Even though the client requested compact=1, the seeder is a hostname
        # — server must downgrade to the non-compact list-of-dicts form.
        assert isinstance(body[b"peers"], list)
        seeder_dicts = [p for p in body[b"peers"] if p[b"ip"] == b"shuttle.proxy.rlwy.net"]
        assert seeder_dicts, body[b"peers"]
        assert seeder_dicts[0][b"port"] == 17187


def test_magnet_includes_own_tracker(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        up = _upload(c)
        # Magnet's first tracker should be our /announce
        assert "test.local%2Fannounce" in up["magnet"] or "test.local/announce" in up["magnet"]
