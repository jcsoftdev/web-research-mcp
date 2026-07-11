from datetime import datetime, timedelta, timezone

from web_research_mcp.core.staleness import is_stale

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def days_ago(n):
    return NOW - timedelta(days=n)


def test_version_locked_never_stale():
    assert is_stale(version_locked=True, updated_at=days_ago(1000), ttl_days=None, now=NOW) is False


def test_unlocked_within_ttl_is_fresh():
    assert is_stale(version_locked=False, updated_at=days_ago(10), ttl_days=30, now=NOW) is False


def test_unlocked_past_ttl_is_stale():
    assert is_stale(version_locked=False, updated_at=days_ago(40), ttl_days=30, now=NOW) is True


def test_exactly_at_ttl_is_not_stale():
    # age == ttl is not yet past it (strict greater-than).
    assert is_stale(version_locked=False, updated_at=days_ago(30), ttl_days=30, now=NOW) is False


def test_forced_invalidation_negative_ttl_is_stale():
    assert is_stale(version_locked=False, updated_at=NOW, ttl_days=-1, now=NOW) is True


def test_forced_invalidation_beats_version_locked():
    # invalidate_reference forces stale regardless, even on a locked entry.
    assert is_stale(version_locked=True, updated_at=NOW, ttl_days=-1, now=NOW) is True


def test_accepts_iso_string_updated_at():
    assert is_stale(
        version_locked=False,
        updated_at=days_ago(40).isoformat(),
        ttl_days=30,
        now=NOW,
    ) is True


def test_unlocked_without_ttl_is_not_stale():
    assert is_stale(version_locked=False, updated_at=days_ago(999), ttl_days=None, now=NOW) is False
