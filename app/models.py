from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

db = SQLAlchemy()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class File(db.Model):
    __tablename__ = "file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    mime: Mapped[str] = mapped_column(Text, nullable=False, default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    addr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    magnet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    info_hash: Mapped[str] = mapped_column(String(40), nullable=False, default="", index=True)


class URL(db.Model):
    __tablename__ = "url"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
