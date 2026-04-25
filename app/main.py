"""0bt — Flask app: upload, dedup, torrent gen, transmission seed, HTTP/BT download."""
from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import IO

import magic
import validators
from flask import Flask, Response, abort, make_response, redirect, request, url_for
from flask.wrappers import Request as FlaskRequest
from humanize import naturalsize
from short_url import UrlEncoder
from sqlalchemy import select
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from .config import settings
from .models import URL, File, db
from .retention import expiry_for
from .storage import commit_temp, file_dir, find_canonical, stream_to_temp, torrent_path
from .torrents import Transmission, make_torrent, write_torrent

log = logging.getLogger("0bt")

# Same alphabet as upstream 0x0 so legacy short URLs keep working.
_url_encoder = UrlEncoder(
    alphabet="DEQhd2uFteibPwq0SWBInTpA_jcZL5GKz3YCR14Ulk87Jors9vNHgfaOmMXy6Vx-",
    block_size=16,
)


class StreamingRequest(FlaskRequest):
    """Spool large multipart bodies to a temp file in our storage volume,
    not to /tmp (which is tmpfs / limited / on a different FS than storage).

    This makes 1 GB uploads trivially memory-safe and lets the eventual move
    to storage be a same-FS rename instead of a multi-GB copy.
    """

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        tmp_dir = os.path.join(settings.storage_path, ".incoming")
        os.makedirs(tmp_dir, exist_ok=True)
        return tempfile.TemporaryFile(dir=tmp_dir)


def _detect_mime(path: Path) -> str:
    try:
        m = magic.Magic(mime=True, mime_encoding=False)
        return m.from_file(str(path)) or "application/octet-stream"
    except Exception:
        return "application/octet-stream"


def _normalize_ext(filename: str | None, mime: str) -> str:
    if filename:
        ext = os.path.splitext(filename)[1]
        if ext:
            return ext[:16]
    guess = mimetypes.guess_extension(mime.split(";")[0]) or ""
    return guess[:16]


def _short_for(file_id: int) -> str:
    return _url_encoder.enbase(file_id, 1)


def _file_url_path(f: File, *, with_ext: bool = True) -> str:
    short = _short_for(f.id)
    if with_ext and f.ext:
        return f"/{short}{f.ext}"
    return f"/{short}"


def _abs_url(path: str) -> str:
    return settings.base_url.rstrip("/") + path


def create_app(*, init_db: bool = True) -> Flask:
    app = Flask(__name__)
    app.request_class = StreamingRequest

    app.config["SQLALCHEMY_DATABASE_URI"] = settings.db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    # File parts in multipart go through StreamingRequest._get_file_stream (disk
    # tempfile in the storage volume); regular form fields stay capped at the
    # default 500 KiB which is plenty for "shorten=…" and similar.

    storage_root = Path(settings.storage_path)
    storage_root.mkdir(parents=True, exist_ok=True)

    # SQLite path bootstrap
    if settings.db_url.startswith("sqlite:///"):
        sqlite_file = settings.db_url[len("sqlite:///"):]
        Path(sqlite_file).parent.mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    transmission = Transmission(
        host=settings.transmission_host,
        port=settings.transmission_port,
        user=settings.transmission_user,
        password=settings.transmission_password,
        path=settings.transmission_path,
    )

    if init_db:
        with app.app_context():
            db.create_all()

    # ---- Routes -------------------------------------------------------------

    from . import tracker as _tracker

    @app.get("/announce")
    def _announce():
        return _tracker.announce()

    @app.get("/scrape")
    def _scrape():
        return _tracker.scrape()

    @app.get("/healthz")
    def healthz():
        ok = True
        out = {"status": "ok"}
        try:
            db.session.execute(db.text("SELECT 1"))
            out["db"] = "ok"
        except Exception as e:
            out["db"] = f"down: {e}"
            ok = False
        out["transmission"] = "ok" if transmission.healthy() else "down"
        return (out, 200 if ok else 503)

    @app.get("/robots.txt")
    def robots():
        return ("User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"})

    @app.get("/")
    def index():
        return _index_page()

    @app.post("/")
    def upload():
        addr = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        addr = addr.split(",")[0].strip()
        ua = request.headers.get("User-Agent", "")[:512]

        if "file" in request.files:
            return _store_file(request.files["file"], addr=addr, ua=ua, transmission=transmission)
        if "shorten" in request.form:
            return _shorten(request.form["shorten"])
        if "url" in request.form:
            # remote URL ingest — out of scope for v2; clients can pre-download
            abort(400, description="remote URL ingest is not supported in this version")
        abort(400)

    @app.get("/<path:path>")
    def fetch(path: str):
        # split off optional .ext or .torrent suffix
        base, ext = os.path.splitext(path)
        try:
            file_id = _url_encoder.debase(base)
        except Exception:
            abort(404)
        # SQLite INTEGER is 64-bit signed; anything bigger means the short
        # code can't possibly match a real row.
        if file_id <= 0 or file_id > 2**63 - 1:
            abort(404)

        if ext == ".torrent":
            return _serve_torrent(file_id)

        f = db.session.get(File, file_id)
        if f and not f.removed and (ext == "" or ext == f.ext):
            return _serve_file(f)

        # Maybe it's a shortened URL
        u = db.session.get(URL, file_id)
        if u:
            return redirect(u.url, code=302)
        abort(404)

    @app.errorhandler(HTTPException)
    def _http_err(e: HTTPException):
        body = f"{e.code} {e.name}\n"
        if e.description and e.description != e.name:
            body += f"{e.description}\n"
        return body, e.code, {"Content-Type": "text/plain; charset=utf-8"}

    return app


# ---- helpers ----------------------------------------------------------------


def _store_file(filestorage, *, addr: str, ua: str, transmission: Transmission):
    storage_root = Path(settings.storage_path)
    src_stream: IO[bytes] = filestorage.stream

    digest, size, tmp = stream_to_temp(src_stream, storage_root)
    if size == 0:
        tmp.unlink(missing_ok=True)
        abort(400, description="empty upload")

    existing = db.session.execute(select(File).where(File.sha256 == digest)).scalar_one_or_none()
    if existing:
        tmp.unlink(missing_ok=True)
        if existing.removed:
            abort(451)
        return _format_response(existing, transmission_added=False)

    mime = _detect_mime(tmp)
    if mime in settings.mime_blacklist:
        tmp.unlink(missing_ok=True)
        abort(415)

    raw_filename = secure_filename(filestorage.filename or "") or ""
    ext = _normalize_ext(raw_filename, mime)
    # Build a friendly display name (used as the file name inside the torrent
    # *and* on disk in the per-upload directory). Falls back to <digest><ext>
    # so it's always non-empty and safe.
    if raw_filename:
        if ext and not raw_filename.endswith(ext):
            display = raw_filename + ext
        else:
            display = raw_filename
    else:
        display = f"{digest}{ext}"

    final = commit_temp(tmp, storage_root, digest, display)
    expiry = expiry_for(
        size,
        max_size=settings.max_content_length,
        min_days=settings.retention_min_days,
        max_days=settings.retention_max_days,
    )

    f = File(
        sha256=digest,
        ext=ext,
        mime=mime,
        size=size,
        addr=addr,
        user_agent=ua,
        expires_at=expiry,
        display_name=display,
    )
    db.session.add(f)
    db.session.commit()

    web_seed = _abs_url(_file_url_path(f, with_ext=True))
    t = make_torrent(
        final,
        display_name=display,
        trackers=settings.all_trackers,
        web_seeds=[web_seed],
    )
    tpath = torrent_path(storage_root, digest)
    write_torrent(t, tpath)
    f.magnet = str(t.magnet())
    f.info_hash = str(t.infohash)
    db.session.commit()

    # download_dir is the per-upload directory; the torrent's info.name is the
    # display name; transmission finds the file at <download_dir>/<info.name>.
    seeded = transmission.add_torrent(tpath, download_dir=str(file_dir(storage_root, digest)))

    return _format_response(f, transmission_added=seeded)


def _format_response(f: File, *, transmission_added: bool):
    body_lines = [
        _abs_url(_file_url_path(f, with_ext=True)),
        _abs_url(f"/{_short_for(f.id)}.torrent"),
    ]
    if f.magnet:
        body_lines.append(f.magnet)
    body = "\n".join(body_lines) + "\n"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if not transmission_added:
        headers["X-0bt-Transmission"] = "down"
    return body, 200, headers


def _serve_file(f: File):
    storage_root = Path(settings.storage_path)
    fp = file_dir(storage_root, f.sha256) / (f.display_name or f"{f.sha256}{f.ext}")
    if not fp.exists():
        # Fallback: scan the per-upload directory.
        canonical = find_canonical(storage_root, f.sha256)
        if canonical is None:
            abort(404)
        fp = canonical

    mode = settings.use_x_accel_redirect
    if mode == 1:
        # nginx: X-Accel-Redirect to internal location
        rel = f"/internal/{f.sha256}/{fp.name}"
        resp = make_response()
        resp.headers["Content-Type"] = f.mime
        resp.headers["Content-Length"] = str(f.size)
        resp.headers["X-Accel-Redirect"] = rel
        return resp

    # Plain Flask serve via send_file with no caching, supports range
    from flask import send_file
    return send_file(
        fp,
        mimetype=f.mime,
        as_attachment=False,
        download_name=f"{_short_for(f.id)}{f.ext}",
        conditional=True,
    )


def _serve_torrent(file_id: int):
    f = db.session.get(File, file_id)
    if not f or f.removed:
        abort(404)
    storage_root = Path(settings.storage_path)
    tpath = torrent_path(storage_root, f.sha256)
    if not tpath.exists():
        abort(404)
    from flask import send_file
    return send_file(
        tpath,
        mimetype="application/x-bittorrent",
        as_attachment=True,
        download_name=f"{_short_for(file_id)}.torrent",
    )


def _shorten(url: str):
    if len(url) > 4096:
        abort(414)
    if not validators.url(url):
        abort(400)
    if url.startswith(settings.base_url) or url.startswith(settings.base_url_https):
        abort(400, description="recursion is not allowed")
    existing = db.session.execute(select(URL).where(URL.url == url)).scalar_one_or_none()
    if existing:
        u = existing
    else:
        u = URL(url=url)
        db.session.add(u)
        db.session.commit()
    return _abs_url(f"/{_short_for(u.id)}") + "\n", 200, {"Content-Type": "text/plain"}


def _index_page() -> Response:
    maxsize = naturalsize(settings.max_content_length, binary=True)
    body = f"""<!doctype html>
<html><head><title>0bt — file host with BitTorrent</title>
<style>body{{font-family:monospace;max-width:80ch;margin:2em auto;padding:0 1em;color:#222}}pre{{white-space:pre-wrap}}</style>
</head><body>
<h1>0bt</h1>
<p>A no-frills file host that gives you back an HTTP URL <em>and</em> a BitTorrent magnet for every upload. Fork of <a href="https://0x0.st">0x0.st</a>.</p>
<h2>Use</h2>
<pre>
# Upload (returns: HTTP URL, .torrent URL, magnet URI)
curl -F "file=@yourfile.bin" {settings.base_url}

# Shorten a URL
curl -F "shorten=https://example.com/some/long/url" {settings.base_url}
</pre>
<h2>Limits</h2>
<p>Max upload size: <strong>{maxsize}</strong>. File retention: {settings.retention_min_days}–{settings.retention_max_days} days, decreasing with size.</p>
<h2>BitTorrent</h2>
<p>Each upload is automatically converted to a torrent and seeded by this host. Magnet links include public trackers and a webseed pointing back at the HTTP URL, so peers can swarm together or fall back to direct HTTP.</p>
</body></html>
"""
    return Response(body, mimetype="text/html")


# Note: there is no module-level `app = create_app()` here — wsgi.py is the
# gunicorn entry point so that importing `app.tracker`, `app.storage`, etc.
# from tests or scripts doesn't eagerly create directories or open the DB.
