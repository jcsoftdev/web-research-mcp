from datetime import datetime, timedelta, timezone

import pytest

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
        topic="server-components",
        summary="sum",
        content="# Doc\nrender on the server\n",
        sources=["https://react.dev"],
        status_tag="current",
        version_locked=False,
        now=NOW,
    )
    kw.update(over)
    return repo.save_research(**kw)


# ---- save + supersede ----

def test_save_creates_latest_entry(repo):
    entry = _save(repo)
    assert entry.slug == "react/18/server-components"
    assert entry.is_latest is True
    assert entry.superseded_by is None
    assert entry.sources == ["https://react.dev"]


def test_save_higher_version_supersedes_previous(repo):
    old = _save(repo, version="18")
    new = _save(repo, version="19")
    old_reloaded = repo.get_reference(old.slug)
    assert new.is_latest is True
    assert old_reloaded.is_latest is False
    assert old_reloaded.superseded_by == new.id


def test_save_lower_version_is_historical(repo):
    latest = _save(repo, version="19")
    older = _save(repo, version="18")
    assert older.is_latest is False
    # existing latest untouched
    assert repo.get_reference(latest.slug).is_latest is True
    assert repo.get_reference(latest.slug).superseded_by is None


def test_exactly_one_latest_after_supersede(repo):
    _save(repo, version="18")
    _save(repo, version="19")
    count = repo.conn.execute(
        "SELECT COUNT(*) FROM research_entries WHERE tech='react' AND topic='server-components' AND is_latest=1"
    ).fetchone()[0]
    assert count == 1


def test_resaving_same_slug_refreshes_without_duplicate(repo):
    _save(repo, version="18", content="old")
    refreshed = _save(repo, version="18", content="new content", now=NOW + timedelta(days=1))
    total = repo.conn.execute("SELECT COUNT(*) FROM research_entries").fetchone()[0]
    assert total == 1
    assert repo.get_reference(refreshed.slug).content == "new content"


def test_version_locked_has_no_ttl(repo):
    entry = _save(repo, version_locked=True)
    assert entry.ttl_days is None


def test_unlocked_gets_default_ttl(repo):
    assert _save(repo).ttl_days == 30


# ---- get ----

def test_get_missing_returns_none(repo):
    assert repo.get_reference("nope/x") is None


def test_get_section_returns_only_that_section(repo):
    content = "# Title\nintro\n## Current API\nuse foo()\n## Deprecated\navoid bar()\n"
    _save(repo, content=content)
    section = repo.get_reference("react/18/server-components", section="Current API")
    assert "use foo()" in section.content
    assert "avoid bar()" not in section.content
    assert "intro" not in section.content


# ---- check ----

def test_check_hit_with_version(repo):
    _save(repo, version="18")
    res = repo.check_reference("react", "server-components", version="18", now=NOW)
    assert res["exists"] is True
    assert res["slug"] == "react/18/server-components"
    assert res["resolved_version"] == "18"
    assert res["status_tag"] == "current"
    assert res["stale"] is False
    assert "content" not in res


def test_check_without_version_resolves_latest(repo):
    _save(repo, version="18")
    _save(repo, version="19")
    res = repo.check_reference("react", "server-components", now=NOW)
    assert res["exists"] is True
    assert res["resolved_version"] == "19"
    assert res["is_latest"] is True


def test_check_miss_is_flat(repo):
    res = repo.check_reference("svelte", "runes", now=NOW)
    assert res == {"exists": False}


def test_check_reports_stale_after_ttl(repo):
    _save(repo, version="18", now=NOW - timedelta(days=40))
    res = repo.check_reference("react", "server-components", version="18", now=NOW)
    assert res["stale"] is True


# ---- search ----

def test_search_finds_by_content_term(repo):
    _save(repo, topic="server-components", content="server rendering with RSC")
    hits = repo.search_reference("rendering")
    assert any(h["topic"] == "server-components" for h in hits)


def test_search_filters_by_tech(repo):
    _save(repo, tech="react", topic="hooks", content="useState hook")
    _save(repo, tech="vue", topic="composition", content="useState-like ref hook")
    hits = repo.search_reference("hook", tech="vue")
    assert all(h["tech"] == "vue" for h in hits)
    assert hits


def test_search_sanitizes_malicious_query(repo):
    _save(repo)
    # These would be FTS5 syntax errors if not quote-wrapped.
    for bad in ['foo" OR bar (', '"; DROP TABLE research_entries; --', 'a NEAR( b', '*']:
        assert isinstance(repo.search_reference(bad), list)  # no raise


# ---- list_tree ----

def test_list_tree_groups_tech_version_topic(repo):
    _save(repo, tech="react", version="19", topic="server-components")
    _save(repo, tech="react", version="19", topic="actions")
    tree = repo.list_tree()
    react = next(t for t in tree if t["tech"] == "react")
    v19 = next(v for v in react["versions"] if v["version"] == "19")
    topics = {t["topic"] for t in v19["topics"]}
    assert topics == {"server-components", "actions"}
    assert all("content" not in t for t in v19["topics"])


def test_list_tree_filters_by_tech(repo):
    _save(repo, tech="react", topic="hooks")
    _save(repo, tech="vue", topic="refs")
    techs = {t["tech"] for t in repo.list_tree(tech="react")}
    assert techs == {"react"}


def test_list_tree_flags_stale(repo):
    _save(repo, version="18", now=NOW - timedelta(days=40))
    tree = repo.list_tree(now=NOW)
    topic = tree[0]["versions"][0]["topics"][0]
    assert topic["stale"] is True
    assert topic["is_latest"] is True


# ---- dedup: find_similar_topics ----

def test_find_similar_topics_matches_near_spelling(repo):
    _save(repo, tech="react", version="19", topic="server-components")
    hits = repo.find_similar_topics("React", "server-component")
    assert [h["topic"] for h in hits] == ["server-components"]
    assert hits[0]["slug"] == "react/19/server-components"
    assert hits[0]["similarity"] >= 0.6


def test_find_similar_topics_excludes_exact(repo):
    _save(repo, tech="react", version="19", topic="server-components")
    assert repo.find_similar_topics("react", "server-components") == []


def test_find_similar_topics_scoped_to_tech(repo):
    _save(repo, tech="vue", version="3", topic="composition")
    assert repo.find_similar_topics("react", "compositions") == []


def test_find_similar_topics_dedups_across_versions(repo):
    _save(repo, tech="react", version="18", topic="hooks")
    _save(repo, tech="react", version="19", topic="hooks")
    hits = repo.find_similar_topics("react", "hook")
    assert len(hits) == 1  # one entry per distinct topic
    assert hits[0]["topic"] == "hooks"


def test_find_similar_topics_empty_when_nothing_close(repo):
    _save(repo, tech="react", version="19", topic="server-components")
    assert repo.find_similar_topics("react", "routing") == []


# ---- invalidate ----

def test_invalidate_forces_stale(repo):
    _save(repo, version="18")
    assert repo.invalidate_reference("react/18/server-components") is True
    res = repo.check_reference("react", "server-components", version="18", now=NOW)
    assert res["stale"] is True


def test_invalidate_missing_returns_false(repo):
    assert repo.invalidate_reference("ghost/x") is False
