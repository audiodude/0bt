import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app_factory(monkeypatch):
    """Build a fresh Flask app pointing at a temp storage + sqlite DB."""

    def _make():
        tmp = tempfile.mkdtemp(prefix="0bt-test-")
        monkeypatch.setenv("FHOST_STORAGE_PATH", os.path.join(tmp, "up"))
        monkeypatch.setenv("FHOST_DB_URL", f"sqlite:///{tmp}/test.sqlite")
        monkeypatch.setenv("FHOST_BASE_URL", "http://test.local")
        monkeypatch.setenv("TRANSMISSION_RPC_HOST", "127.0.0.1")
        monkeypatch.setenv("TRANSMISSION_RPC_PORT", "1")  # always-down
        monkeypatch.setenv("FHOST_TRACKERS", "udp://tracker.example:6969/announce")
        # Force the config dataclass to re-read env, and reset module state
        # (peer table) so tests don't see leftovers from earlier tests.
        import importlib
        from app import config as cfg, main, tracker
        importlib.reload(cfg)
        importlib.reload(tracker)
        importlib.reload(main)
        app = main.create_app()
        app.config["TESTING"] = True
        return app, Path(tmp)

    return _make
