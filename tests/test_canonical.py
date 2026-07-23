from datetime import datetime, timezone

import pytest

from web_research_mcp.core.canonical import TOPIC_ALIASES, canonical_topic
from web_research_mcp.core.repository import Repository
from web_research_mcp.core.store import connect

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    return Repository(connect(":memory:"), default_ttl_days=30)


def _save(repo, **over):
    kw = dict(
        tech="react",
        version="18",
        topic="latest-version",
        summary="sum",
        content="# Doc\nwhat is new\n",
        sources=[],
        status_tag="current",
        version_locked=False,
        now=NOW,
    )
    kw.update(over)
    return repo.save_research(**kw).entry


# ---- canonical_topic ----

def test_alias_maps_to_canonical():
    for alias, canonical in TOPIC_ALIASES.items():
        assert canonical_topic(alias) == canonical
    assert canonical_topic("whats-new") == "latest-version"


def test_unknown_topic_passes_through():
    assert canonical_topic("server-components") == "server-components"


def test_normalizes_before_alias_lookup():
    assert canonical_topic("Latest Version Features") == "latest-version"
    assert canonical_topic("Whats New!") == "latest-version"


def test_normalizes_unknown_topics_too():
    assert canonical_topic("Server Components") == "server-components"


# ---- repository wiring ----

def test_save_with_alias_topic_stores_canonical_slug(repo):
    entry = _save(repo, topic="whats-new")
    assert entry.slug == "react/18/latest-version"
    assert entry.topic == "latest-version"


def test_check_with_alias_finds_canonical_entry(repo):
    _save(repo, topic="latest-version")
    res = repo.check_reference("react", "latest-version-features", version="18", now=NOW)
    assert res["exists"] is True
    assert res["slug"] == "react/18/latest-version"


def test_check_without_version_canonicalizes(repo):
    _save(repo, topic="latest-version")
    res = repo.check_reference("react", "Whats New", now=NOW)
    assert res["exists"] is True


def test_resolve_with_alias_finds_canonical_entry(repo):
    _save(repo, topic="latest-version")
    res = repo.resolve_reference("react", "whats-new", version="18", now=NOW)
    assert res["exists"] is True
    assert res["slug"] == "react/18/latest-version"


def test_batch_with_alias_finds_canonical_entry(repo):
    _save(repo, topic="latest-version")
    results = repo.check_reference_batch(
        [{"tech": "react", "topic": "new-features", "version": "18"}], now=NOW
    )
    assert results[0]["exists"] is True
    assert results[0]["slug"] == "react/18/latest-version"


def test_get_reference_with_alias_slug_resolves(repo):
    _save(repo, topic="latest-version")
    entry = repo.get_reference("react/18/whats-new")
    assert entry is not None
    assert entry.slug == "react/18/latest-version"


def test_get_reference_with_general_alias_slug_resolves(repo):
    _save(repo, version=None, topic="latest-version")
    entry = repo.get_reference("react/what-s-new")
    assert entry is not None
    assert entry.slug == "react/latest-version"


def test_get_reference_malformed_slug_returns_none(repo):
    assert repo.get_reference("not-a-slug") is None
    assert repo.get_reference("too/many/segments/here") is None


# ---- legacy rows saved before canonicalization existed ----

def _save_raw_legacy_row(repo, tech="react", version="18", topic="whats-new"):
    # Bypass save_research's canonicalization entirely, simulating a row
    # written before core/canonical.py existed — topic stored verbatim.
    from web_research_mcp.core.slug import make_slug, normalize_segment

    slug = make_slug(tech, version, topic)
    repo.conn.execute(
        "INSERT INTO research_entries "
        "(slug, tech, version, topic, summary, content, status_tag, sources, tags, "
        "version_locked, is_latest, ttl_days, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            slug, normalize_segment(tech), normalize_segment(version), topic,
            "s", "c", "current", "[]", "[]", 0, 1, 30,
            NOW.isoformat(), NOW.isoformat(),
        ),
    )
    repo.conn.commit()
    return slug


def test_check_reference_finds_legacy_alias_topic_row(repo):
    _save_raw_legacy_row(repo)
    res = repo.check_reference("react", "latest-version", version="18", now=NOW)
    assert res["exists"] is True


def test_resolve_reference_finds_legacy_alias_topic_row(repo):
    _save_raw_legacy_row(repo)
    res = repo.resolve_reference("react", "latest-version", version="18", now=NOW)
    assert res["exists"] is True


def test_save_research_over_legacy_row_refreshes_instead_of_forking(repo):
    slug = _save_raw_legacy_row(repo)
    result = repo.save_research(
        tech="react", version="18", topic="latest-version",
        summary="new sum", content="new content", sources=[],
        status_tag="current", version_locked=False, force=True, now=NOW,
    )
    assert result.entry.slug == slug  # refreshed in place, not forked
    rows = repo.conn.execute("SELECT COUNT(*) as n FROM research_entries").fetchone()
    assert rows["n"] == 1
