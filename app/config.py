"""Configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(v: str | None, default: int) -> int:
    if v is None or v.strip() == "":
        return default
    return int(v)


def _list(v: str | None, default: list[str] | None = None) -> list[str]:
    if v is None or v.strip() == "":
        return list(default or [])
    return [s.strip() for s in v.split(",") if s.strip()]


DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://exodus.desync.com:6969/announce",
]


@dataclass(frozen=True)
class Settings:
    base_url: str = field(default_factory=lambda: os.environ.get("FHOST_BASE_URL", "http://localhost:8080"))
    storage_path: str = field(default_factory=lambda: os.environ.get("FHOST_STORAGE_PATH", "/data/up"))
    db_url: str = field(default_factory=lambda: os.environ.get("FHOST_DB_URL", "sqlite:////data/db/0bt.sqlite"))

    max_content_length: int = field(default_factory=lambda: _int(os.environ.get("FHOST_MAX_CONTENT_LENGTH"), 1_610_612_736))
    retention_min_days: int = field(default_factory=lambda: _int(os.environ.get("FHOST_RETENTION_MIN_DAYS"), 30))
    retention_max_days: int = field(default_factory=lambda: _int(os.environ.get("FHOST_RETENTION_MAX_DAYS"), 365))

    trackers: list[str] = field(default_factory=lambda: _list(os.environ.get("FHOST_TRACKERS"), DEFAULT_TRACKERS))
    internal_tracker: str = field(default_factory=lambda: os.environ.get("FHOST_INTERNAL_TRACKER", "").strip())

    transmission_host: str = field(default_factory=lambda: os.environ.get("TRANSMISSION_RPC_HOST", "transmission"))
    transmission_port: int = field(default_factory=lambda: _int(os.environ.get("TRANSMISSION_RPC_PORT"), 9091))
    transmission_user: str = field(default_factory=lambda: os.environ.get("TRANSMISSION_RPC_USER", "transmission"))
    transmission_password: str = field(default_factory=lambda: os.environ.get("TRANSMISSION_RPC_PASSWORD", ""))
    transmission_path: str = field(default_factory=lambda: os.environ.get("TRANSMISSION_RPC_PATH", "/transmission/rpc"))

    # Public BT-peer address of the deployed transmission. Used by the in-app
    # tracker (/announce) to publish the right reachable host:port to peers.
    # When unset, /announce omits a synthetic seeder and serves only registered
    # peers (suitable for local docker-compose where peers can find transmission
    # by its container IP via PEX/DHT or via the published host port).
    bt_public_host: str = field(default_factory=lambda: os.environ.get("BT_PUBLIC_HOST", "").strip())
    bt_public_port: int = field(default_factory=lambda: _int(os.environ.get("BT_PUBLIC_PORT"), 0))

    use_x_accel_redirect: int = field(default_factory=lambda: _int(os.environ.get("FHOST_USE_X_ACCEL_REDIRECT"), 0))

    mime_blacklist: list[str] = field(default_factory=lambda: _list(
        os.environ.get("FHOST_MIME_BLACKLIST"),
        ["application/x-dosexec", "application/java-archive", "application/java-vm"],
    ))

    @property
    def own_announce_url(self) -> str:
        return self.base_url.rstrip("/") + "/announce"

    @property
    def public_seeder_addr(self) -> tuple[str, int] | None:
        from urllib.parse import urlparse

        host = self.bt_public_host or (urlparse(self.base_url).hostname or "")
        port = self.bt_public_port if self.bt_public_port > 0 else 0
        if not host or port <= 0:
            return None
        return (host, port)

    @property
    def all_trackers(self) -> list[str]:
        # The app's own /announce always goes first — it's the only tracker
        # that's guaranteed to publish the right peer address even when the
        # deployed transmission sits behind a port-translating TCP proxy.
        out = [self.own_announce_url]
        if self.internal_tracker:
            out.append(self.internal_tracker)
        out.extend(self.trackers)
        # de-dup, preserve order
        seen: set[str] = set()
        deduped: list[str] = []
        for t in out:
            if t and t not in seen:
                seen.add(t)
                deduped.append(t)
        return deduped

    @property
    def base_url_https(self) -> str:
        if self.base_url.startswith("http://"):
            return "https://" + self.base_url[len("http://") :]
        return self.base_url


settings = Settings()
