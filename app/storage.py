"""Streaming, content-addressed file storage.

Layout (one directory per upload, named by sha256):

    <storage_root>/
      .incoming/<random>           ← spool tempfiles during upload
      <sha256>/
        <display_name>             ← canonical content
        meta.torrent               ← bencoded torrent

This layout makes Transmission seeding trivial: download_dir is set to
``<storage_root>/<sha256>/`` and the torrent's info.name is the display name,
so Transmission finds the file directly without any symlink dance.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

CHUNK = 1 << 20  # 1 MiB


def file_dir(storage_root: Path, digest: str) -> Path:
    return storage_root / digest


def torrent_path(storage_root: Path, digest: str) -> Path:
    return file_dir(storage_root, digest) / "meta.torrent"


def incoming_dir(storage_root: Path) -> Path:
    p = storage_root / ".incoming"
    p.mkdir(parents=True, exist_ok=True)
    return p


def stream_to_temp(src: BinaryIO, storage_root: Path) -> tuple[str, int, Path]:
    """Stream ``src`` to a temp file in storage_root, hashing as we go.

    Returns ``(sha256_hex, total_bytes, tmp_path)``. Caller decides whether to
    promote the tempfile to its final location or unlink (e.g., on dedup).
    """
    storage_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = incoming_dir(storage_root)
    h = hashlib.sha256()
    total = 0
    fd, tmp_path = tempfile.mkstemp(prefix="upload-", dir=str(tmp_dir))
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                buf = src.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
                h.update(buf)
                total += len(buf)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return h.hexdigest(), total, tmp


def commit_temp(tmp: Path, storage_root: Path, digest: str, display_name: str) -> Path:
    """Move ``tmp`` to its final location ``<storage_root>/<digest>/<display_name>``.

    If the destination already exists (concurrent upload of same content), the
    tmp is unlinked and the existing path is returned. The committed file is
    set to 0644 so a sidecar Transmission container running under a different
    UID (linuxserver/transmission uses 1000:1000 by default) can read it.
    """
    d = file_dir(storage_root, digest)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o755)
    except OSError:
        pass
    final = d / display_name
    if final.exists():
        tmp.unlink(missing_ok=True)
    else:
        shutil.move(str(tmp), str(final))
    try:
        os.chmod(final, 0o644)
    except OSError:
        pass
    return final


def find_canonical(storage_root: Path, digest: str) -> Path | None:
    """Return the existing canonical file path for ``digest``, or None.

    The directory ``<digest>/`` typically contains exactly one regular file
    next to ``meta.torrent``; that's the canonical content.
    """
    d = file_dir(storage_root, digest)
    if not d.is_dir():
        return None
    for entry in d.iterdir():
        if entry.is_file() and entry.name != "meta.torrent":
            return entry
    return None
