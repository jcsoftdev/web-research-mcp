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
    return repo.save_research(**kw).entry


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
    # force=True: the first entry is still fresh, so the guard would block.
    refreshed = _save(repo, version="18", content="new content", force=True, now=NOW + timedelta(days=1))
    total = repo.conn.execute("SELECT COUNT(*) FROM research_entries").fetchone()[0]
    assert total == 1
    assert repo.get_reference(refreshed.slug).content == "new content"


def test_version_locked_has_no_ttl(repo):
    entry = _save(repo, version_locked=True)
    assert entry.ttl_days is None


def test_unlocked_gets_default_ttl(repo):
    assert _save(repo).ttl_days == 30


# ---- redundant save guard ----

def test_save_blocked_when_fresh_entry_exists(repo):
    _save(repo, version="18")
    result = repo.save_research(
        tech="react",
        version="18",
        topic="server-components",
        summary="sum",
        content="re-researched",
        sources=[],
        status_tag="current",
        version_locked=False,
        now=NOW + timedelta(days=1),
    )
    assert result.blocked is True
    assert result.entry is None
    assert result.existing_slug == "react/18/server-components"
    total = repo.conn.execute("SELECT COUNT(*) FROM research_entries").fetchone()[0]
    assert total == 1
    assert repo.get_reference("react/18/server-components").content != "re-researched"


def test_save_allowed_when_existing_entry_is_stale(repo):
    _save(repo, version="18", now=NOW - timedelta(days=40))
    result = repo.save_research(
        tech="react",
        version="18",
        topic="server-components",
        summary="sum",
        content="refreshed",
        sources=[],
        status_tag="current",
        version_locked=False,
        now=NOW,
    )
    assert result.blocked is False
    assert result.entry is not None
    assert result.existing_slug is None
    assert repo.get_reference("react/18/server-components").content == "refreshed"


def test_save_force_bypasses_fresh_guard(repo):
    _save(repo, version="18")
    result = repo.save_research(
        tech="react",
        version="18",
        topic="server-components",
        summary="sum",
        content="forced",
        sources=[],
        status_tag="current",
        version_locked=False,
        force=True,
        now=NOW + timedelta(days=1),
    )
    assert result.blocked is False
    assert result.entry is not None
    assert repo.get_reference("react/18/server-components").content == "forced"


def test_save_new_slug_unaffected_by_guard(repo):
    _save(repo, version="18")
    result = repo.save_research(
        tech="react",
        version="19",
        topic="server-components",
        summary="sum",
        content="c",
        sources=[],
        status_tag="current",
        version_locked=False,
        now=NOW,
    )
    assert result.blocked is False
    assert result.entry.slug == "react/19/server-components"


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


# ---- resolve_reference ----

def test_resolve_miss_is_flat(repo):
    assert repo.resolve_reference("svelte", "runes", now=NOW) == {"exists": False}


def test_resolve_hit_fresh_includes_content(repo):
    _save(repo, version="18")
    res = repo.resolve_reference("react", "server-components", version="18", now=NOW)
    assert res["exists"] is True
    assert res["stale"] is False
    assert res["slug"] == "react/18/server-components"
    assert res["resolved_version"] == "18"
    assert res["status_tag"] == "current"
    assert res["is_latest"] is True
    assert res["summary"] == "sum"
    assert res["sources"] == ["https://react.dev"]
    assert res["content"] == "# Doc\nrender on the server\n"


def test_resolve_without_version_resolves_latest(repo):
    _save(repo, version="18")
    _save(repo, version="19")
    res = repo.resolve_reference("react", "server-components", now=NOW)
    assert res["exists"] is True
    assert res["resolved_version"] == "19"


def test_resolve_hit_stale_flagged(repo):
    _save(repo, version="18", now=NOW - timedelta(days=40))
    res = repo.resolve_reference("react", "server-components", version="18", now=NOW)
    assert res["stale"] is True


def test_resolve_max_age_makes_fresh_entry_stale(repo):
    _save(repo, version="18", now=NOW - timedelta(days=10))
    res = repo.resolve_reference(
        "react", "server-components", version="18", max_age_days=5, now=NOW
    )
    assert res["stale"] is True
    # same entry without the override stays fresh under its own ttl
    assert repo.resolve_reference("react", "server-components", version="18", now=NOW)["stale"] is False


def test_resolve_max_age_makes_stale_entry_fresh(repo):
    _save(repo, version="18", now=NOW - timedelta(days=40))
    res = repo.resolve_reference(
        "react", "server-components", version="18", max_age_days=60, now=NOW
    )
    assert res["stale"] is False


def test_resolve_max_age_keeps_version_locked_never_stale(repo):
    _save(repo, version="18", version_locked=True, now=NOW - timedelta(days=400))
    res = repo.resolve_reference(
        "react", "server-components", version="18", max_age_days=1, now=NOW
    )
    assert res["stale"] is False


def test_resolve_max_age_keeps_invalidated_always_stale(repo):
    _save(repo, version="18", now=NOW)
    repo.invalidate_reference("react/18/server-components")
    res = repo.resolve_reference(
        "react", "server-components", version="18", max_age_days=9999, now=NOW
    )
    assert res["stale"] is True


# ---- check_reference_batch ----

def test_batch_mixed_hit_miss_stale(repo):
    _save(repo, tech="react", version="18", topic="hooks", now=NOW)
    _save(repo, tech="vue", version="3", topic="refs", now=NOW - timedelta(days=40))
    results = repo.check_reference_batch(
        [
            {"tech": "react", "topic": "hooks", "version": "18"},
            {"tech": "vue", "topic": "refs", "version": "3"},
            {"tech": "svelte", "topic": "runes"},
        ],
        now=NOW,
    )
    assert len(results) == 3
    fresh, stale, miss = results
    assert fresh["exists"] is True
    assert fresh["stale"] is False
    assert stale["exists"] is True
    assert stale["stale"] is True
    assert miss == {"tech": "svelte", "topic": "runes", "exists": False}


def test_batch_echoes_tech_topic_and_version(repo):
    _save(repo, version="18")
    results = repo.check_reference_batch(
        [{"tech": "react", "topic": "server-components", "version": "18"}], now=NOW
    )
    res = results[0]
    assert res["tech"] == "react"
    assert res["topic"] == "server-components"
    assert res["version"] == "18"
    assert res["slug"] == "react/18/server-components"


def test_batch_without_version_resolves_latest_and_omits_version(repo):
    _save(repo, version="18")
    _save(repo, version="19")
    results = repo.check_reference_batch(
        [{"tech": "react", "topic": "server-components"}], now=NOW
    )
    assert results[0]["exists"] is True
    assert results[0]["resolved_version"] == "19"
    assert "version" not in results[0]


# ---- stack_diff ----

def test_stack_diff_missing_tech(repo):
    results = repo.stack_diff([{"tech": "svelte", "version": "5"}], now=NOW)
    assert results == [
        {
            "tech": "svelte",
            "requested_version": "5",
            "status": "missing",
            "cached_versions": [],
        }
    ]


def test_stack_diff_fresh(repo):
    _save(repo, tech="react", version="19", topic="hooks", now=NOW)
    results = repo.stack_diff([{"tech": "react", "version": "19"}], now=NOW)
    assert results[0]["status"] == "fresh"
    assert results[0]["cached_versions"] == ["19"]


def test_stack_diff_stale_by_ttl(repo):
    _save(repo, tech="react", version="19", topic="hooks", now=NOW - timedelta(days=40))
    results = repo.stack_diff([{"tech": "react", "version": "19"}], now=NOW)
    assert results[0]["status"] == "stale"


def test_stack_diff_stale_when_version_not_covered(repo):
    _save(repo, tech="react", version="18", topic="hooks", now=NOW)
    results = repo.stack_diff([{"tech": "react", "version": "20"}], now=NOW)
    assert results[0]["status"] == "stale"
    assert results[0]["cached_versions"] == ["18"]


def test_stack_diff_general_entry_covers_any_version(repo):
    _save(repo, tech="react", version=None, topic="hooks", now=NOW)
    results = repo.stack_diff([{"tech": "react", "version": "99"}], now=NOW)
    assert results[0]["status"] == "fresh"
    assert results[0]["cached_versions"] == [None]


def test_stack_diff_normalizes_tech_and_requested_version(repo):
    _save(repo, tech="go", version="1.23", topic="slices", now=NOW)
    results = repo.stack_diff([{"tech": "Go", "version": " 1.23 "}], now=NOW)
    assert results[0]["status"] == "fresh"


def test_stack_diff_cached_versions_sorted_with_null_last(repo):
    _save(repo, tech="react", version="19", topic="hooks", now=NOW)
    _save(repo, tech="react", version="18", topic="hooks", now=NOW)
    _save(repo, tech="react", version=None, topic="faq", now=NOW)
    results = repo.stack_diff([{"tech": "react"}], now=NOW)
    assert results[0]["requested_version"] is None
    assert results[0]["cached_versions"] == ["18", "19", None]
    assert results[0]["status"] == "fresh"


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


# ---- usage_events logging ----

def test_check_reference_logs_hit(repo):
    _save(repo, version="18")
    repo.check_reference("react", "server-components", version="18", now=NOW)
    rows = repo.conn.execute("SELECT event, tech FROM usage_events").fetchall()
    assert [dict(r) for r in rows] == [{"event": "hit", "tech": "react"}]


def test_check_reference_logs_miss(repo):
    repo.check_reference("vue", "composition-api", now=NOW)
    rows = repo.conn.execute("SELECT event, tech FROM usage_events").fetchall()
    assert [dict(r) for r in rows] == [{"event": "miss", "tech": "vue"}]


def test_resolve_reference_logs_hit(repo):
    _save(repo, version="18")
    repo.resolve_reference("react", "server-components", version="18", now=NOW)
    rows = repo.conn.execute("SELECT event FROM usage_events").fetchall()
    assert [r["event"] for r in rows] == ["hit"]


def test_check_reference_batch_logs_one_event_per_item(repo):
    _save(repo, version="18")
    repo.check_reference_batch(
        [
            {"tech": "react", "topic": "server-components", "version": "18"},
            {"tech": "vue", "topic": "composition-api"},
        ],
        now=NOW,
    )
    rows = repo.conn.execute("SELECT event, tech FROM usage_events ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"event": "hit", "tech": "react"},
        {"event": "miss", "tech": "vue"},
    ]


def test_hit_logs_nonzero_tokens_saved_estimate(repo):
    _save(repo, content="x" * 400)
    repo.check_reference("react", "server-components", version="18", now=NOW)
    row = repo.conn.execute("SELECT tokens_saved FROM usage_events").fetchone()
    assert row["tokens_saved"] == 100  # 400 chars / 4


def test_miss_logs_zero_tokens_saved(repo):
    repo.check_reference("vue", "composition-api", now=NOW)
    row = repo.conn.execute("SELECT tokens_saved FROM usage_events").fetchone()
    assert row["tokens_saved"] == 0


# ---- cache_stats ----

def test_cache_stats_empty(repo):
    assert repo.cache_stats() == {
        "hits": 0,
        "misses": 0,
        "hit_rate": 0.0,
        "tokens_saved_estimate": 0,
        "top_misses": [],
    }


def test_cache_stats_hit_rate(repo):
    _save(repo, version="18")
    repo.check_reference("react", "server-components", version="18", now=NOW)
    repo.check_reference("react", "server-components", version="18", now=NOW)
    repo.check_reference("vue", "composition-api", now=NOW)
    stats = repo.cache_stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(2 / 3)


def test_cache_stats_top_misses_ranked_by_tech(repo):
    repo.check_reference("vue", "x", now=NOW)
    repo.check_reference("vue", "y", now=NOW)
    repo.check_reference("svelte", "z", now=NOW)
    stats = repo.cache_stats(limit=1)
    assert stats["top_misses"] == [{"tech": "vue", "count": 2}]


def test_cache_stats_tokens_saved_estimate(repo):
    _save(repo, content="x" * 400)
    repo.check_reference("react", "server-components", version="18", now=NOW)
    stats = repo.cache_stats()
    assert stats["tokens_saved_estimate"] == 100


# ---- nearby: qué ofrecer cuando no hay acierto ----
#
# Un `exists: false` a secas no distingue «no está cacheado» de «te
# equivocaste de slug». Quien llama no puede saber cuál de las dos es, así que
# ante la duda deja de llamar — y la caché deja de usarse.

def _repo_con(*pares):
    repo = Repository(connect(":memory:"), default_ttl_days=30)
    for tech, topic in pares:
        repo.save_research(
            tech=tech, version=None, topic=topic, summary="s", content="c",
            sources=[], status_tag="current", version_locked=False,
        )
    return repo


def test_nearby_ofrece_los_topics_de_esa_tech():
    repo = _repo_con(("openrouter", "free-models"), ("openrouter", "rate-limits"))

    cerca = repo.nearby("openrouter", "modelos-gratis")

    assert [c["topic"] for c in cerca] == ["free-models", "rate-limits"] or \
           sorted(c["topic"] for c in cerca) == ["free-models", "rate-limits"]
    assert all(c["tech"] == "openrouter" for c in cerca)


def test_nearby_pone_primero_el_topic_mas_parecido():
    repo = _repo_con(("groq", "rate-limits"), ("groq", "algo-sin-relacion"))

    cerca = repo.nearby("groq", "rate-limit")

    assert cerca[0]["topic"] == "rate-limits"


def test_nearby_sugiere_otra_tech_cuando_la_pedida_no_existe():
    repo = _repo_con(("openrouter", "free-models"))

    cerca = repo.nearby("open-router", "free-models")

    assert cerca and cerca[0]["tech"] == "openrouter"


def test_nearby_vacio_con_la_cache_vacia():
    repo = Repository(connect(":memory:"))
    assert repo.nearby("lo-que-sea", "nada") == []


def test_nearby_no_inventa_parecidos_lejanos():
    repo = _repo_con(("openrouter", "free-models"))
    assert repo.nearby("postgres", "vacuum") == []
