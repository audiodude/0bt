"""Sanity tests: upload roundtrip, dedup, torrent gen, short URL."""
from io import BytesIO


def test_index_renders(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert b"0bt" in r.data


def test_healthz(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        r = c.get("/healthz")
        # 503 because transmission is unreachable in tests; that's fine.
        assert r.status_code in (200, 503)
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert body["transmission"] == "down"


def test_upload_and_download(app_factory):
    app, tmp = app_factory()
    payload = b"hello world\n" * 1000
    with app.test_client() as c:
        r = c.post("/", data={"file": (BytesIO(payload), "hello.txt")},
                   content_type="multipart/form-data")
        assert r.status_code == 200, r.data
        lines = r.data.decode().strip().split("\n")
        assert len(lines) == 3, lines
        url, torrent_url, magnet = lines
        assert url.startswith("http://test.local/")
        assert torrent_url.endswith(".torrent")
        assert magnet.startswith("magnet:?xt=urn:btih:")

        # Download HTTP
        path = url.replace("http://test.local", "")
        r2 = c.get(path)
        assert r2.status_code == 200
        assert r2.data == payload

        # Download torrent
        path_t = torrent_url.replace("http://test.local", "")
        r3 = c.get(path_t)
        assert r3.status_code == 200
        assert r3.headers["Content-Type"].startswith("application/x-bittorrent")
        assert r3.data[:8] == b"d8:annou"  # bencoded "announce"


def test_dedup(app_factory):
    app, _ = app_factory()
    payload = b"some bytes"
    with app.test_client() as c:
        r1 = c.post("/", data={"file": (BytesIO(payload), "a.bin")},
                    content_type="multipart/form-data")
        r2 = c.post("/", data={"file": (BytesIO(payload), "b.bin")},
                    content_type="multipart/form-data")
        assert r1.data.decode().strip().split("\n")[0] == r2.data.decode().strip().split("\n")[0]


def test_shorten(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        r = c.post("/", data={"shorten": "https://example.com/foo"})
        assert r.status_code == 200
        short = r.data.decode().strip()
        assert short.startswith("http://test.local/")
        # Hitting it should redirect
        path = short.replace("http://test.local", "")
        r2 = c.get(path, follow_redirects=False)
        assert r2.status_code == 302
        assert r2.headers["Location"] == "https://example.com/foo"


def test_404_unknown(app_factory):
    app, _ = app_factory()
    with app.test_client() as c:
        r = c.get("/nonexistent")
        assert r.status_code == 404
