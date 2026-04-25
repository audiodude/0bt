#!/usr/bin/env python3
"""Delete expired files from disk + DB. Run periodically (cron / Railway cron)."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the parent directory importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import File, db  # noqa: E402
from app.storage import file_dir, torrent_path  # noqa: E402

log = logging.getLogger("prune")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    app = create_app(init_db=False)
    storage_root = Path(settings.storage_path)
    deleted_count = 0
    deleted_bytes = 0
    with app.app_context():
        now = datetime.now(timezone.utc)
        rows = db.session.execute(
            db.select(File).where(File.removed.is_(False), File.expires_at < now)
        ).scalars().all()
        for f in rows:
            d = file_dir(storage_root, f.sha256)
            tp = torrent_path(storage_root, f.sha256)
            if d.is_dir():
                for entry in d.iterdir():
                    try:
                        if entry.is_file():
                            deleted_bytes += entry.stat().st_size
                            entry.unlink()
                    except OSError as e:
                        log.warning("could not unlink %s: %s", entry, e)
                try:
                    d.rmdir()
                except OSError:
                    pass
            f.removed = True
            deleted_count += 1
        db.session.commit()
    log.info("pruned %d files, freed %d bytes", deleted_count, deleted_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
