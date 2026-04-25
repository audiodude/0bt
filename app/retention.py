"""File retention curve.

Same shape as the original 0x0:

    retention = min_age + (-max_age + min_age) * pow((file_size / max_size - 1), 3)

Smaller files live longer; files at max_size live for min_age days.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def retention_days(file_size: int, max_size: int, min_days: int, max_days: int) -> float:
    if max_size <= 0:
        return float(max_days)
    ratio = max(0.0, min(1.0, file_size / max_size))
    days = min_days + (-max_days + min_days) * pow((ratio - 1), 3)
    return max(float(min_days), min(float(max_days), days))


def expiry_for(file_size: int, max_size: int, min_days: int, max_days: int, *, now: datetime | None = None) -> datetime:
    days = retention_days(file_size, max_size, min_days, max_days)
    return (now or datetime.now(timezone.utc)) + timedelta(days=days)
