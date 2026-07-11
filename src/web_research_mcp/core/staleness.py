"""Staleness / TTL computation.

Rules (per design):

- A negative ``ttl_days`` is a manual invalidation and forces stale
  regardless of everything else (``invalidate_reference``).
- ``version_locked`` entries are historical facts and never expire.
- Otherwise stale iff ``now - updated_at`` is strictly greater than ``ttl_days``.
- No ttl (``None``) on an unlocked entry is treated as never-stale.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stale(
    *,
    version_locked: bool,
    updated_at: datetime | str,
    ttl_days: int | None,
    now: datetime | None = None,
) -> bool:
    """Return True if a reference should be treated as stale."""
    if ttl_days is not None and ttl_days < 0:
        return True
    if version_locked:
        return False
    if ttl_days is None:
        return False
    now = now or datetime.now(timezone.utc)
    age = now - _as_datetime(updated_at)
    return age > timedelta(days=ttl_days)
