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

    use_x_accel_redirect: int = field(default_factory=lambda: _int(os.environ.get("FHOST_USE_X_ACCEL_REDIRECT"), 0))

    mime_blacklist: list[str] = field(default_factory=lambda: _list(
        os.environ.get("FHOST_MIME_BLACKLIST"),
        ["application/x-dosexec", "application/java-archive", "application/java-vm"],
    ))

    @property
    def all_trackers(self) -> list[str]:
        out = list(self.trackers)
        if self.internal_tracker:
            out.insert(0, self.internal_tracker)
        return out

    @property
    def base_url_https(self) -> str:
        if self.base_url.startswith("http://"):
            return "https://" + self.base_url[len("http://") :]
        return self.base_url


settings = Settings()
