"""Torrent file generation and Transmission RPC integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from torf import Torrent
from transmission_rpc import Client as TransmissionClient
from transmission_rpc.error import TransmissionError

log = logging.getLogger(__name__)


def make_torrent(
    file_path: Path,
    *,
    display_name: str,
    trackers: list[str],
    web_seeds: list[str] | None = None,
    private: bool = False,
) -> Torrent:
    """Build a Torrent metainfo for ``file_path``.

    The first tracker URL is the primary; subsequent ones are added as a
    backup tier. Web seeds (BEP-19) let HTTP-only clients fetch directly from
    our app even before BT peers are found.
    """
    t = Torrent(
        path=file_path,
        name=display_name,
        trackers=trackers or [],
        webseeds=web_seeds or [],
        private=private,
        created_by="0bt 2.0",
        creation_date=datetime.now(timezone.utc),
    )
    t.generate()
    return t


def write_torrent(t: Torrent, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    t.write(str(dest), overwrite=True)
    return dest


class Transmission:
    """Thin wrapper over transmission-rpc with graceful 'down' handling.

    A failure to reach Transmission must not break uploads; the magnet/torrent
    are still useful via public trackers + DHT, and the file can still be
    downloaded over HTTP.
    """

    def __init__(self, host: str, port: int, user: str, password: str, path: str = "/transmission/rpc"):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._path = path
        self._client: TransmissionClient | None = None

    def _connect(self) -> TransmissionClient | None:
        if self._client is not None:
            return self._client
        try:
            self._client = TransmissionClient(
                host=self._host,
                port=self._port,
                username=self._user or None,
                password=self._password or None,
                path=self._path,
                timeout=15,
            )
            return self._client
        except (TransmissionError, requests.RequestException, OSError) as e:
            log.warning("transmission connect failed: %s", e)
            return None

    def healthy(self) -> bool:
        c = self._connect()
        if c is None:
            return False
        try:
            c.session_stats()
            return True
        except (TransmissionError, requests.RequestException, OSError) as e:
            log.warning("transmission unhealthy: %s", e)
            self._client = None
            return False

    def add_torrent(self, torrent_path: Path, download_dir: str) -> bool:
        c = self._connect()
        if c is None:
            return False
        try:
            with open(torrent_path, "rb") as f:
                # Pass raw torrent bytes via filename keyword as base64;
                # transmission-rpc handles encoding.
                c.add_torrent(f, download_dir=download_dir, paused=False)
            return True
        except (TransmissionError, requests.RequestException, OSError) as e:
            log.warning("add_torrent failed for %s: %s", torrent_path, e)
            self._client = None
            return False
