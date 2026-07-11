import json

import pytest

from web_research_mcp.core.models import ResearchEntry
from web_research_mcp.core.repository import Repository
from web_research_mcp.core.store import connect
from web_research_mcp.mcp_server.server import (
    STALE_ADVICE,
    _shape_check,
    _shape_get,
    build_server,
)


def _entry(**over):
    base = dict(
        id=1,
        slug="react/19/rsc",
        tech="react",
        version="19",
        topic="rsc",
        summary="s",
        content="# Doc\nbody",
        status_tag="current",
        sources=[],
        tags=[],
        version_locked=False,
        is_latest=True,
        superseded_by=None,
        ttl_days=30,
        created_at="2026-07-10T12:00:00+00:00",
        updated_at="2026-07-10T12:00:00+00:00",
    )
    base.update(over)
    return ResearchEntry(**base)


# ---- shaping: structured status first, advice on stale, flat on miss ----

def test_shape_check_miss_is_flat():
    assert _shape_check({"exists": False}) == {"exists": False}


def test_shape_check_hit_puts_status_and_stale_first():
    res = {
        "exists": True,
        "slug": "react/19/rsc",
        "is_latest": True,
        "resolved_version": "19",
        "status_tag": "current",
        "stale": False,
    }
    shaped = _shape_check(res)
    assert list(shaped)[:2] == ["status_tag", "stale"]
    assert "advice" not in shaped
    assert "content" not in shaped


def test_shape_check_stale_includes_advice():
    res = {
        "exists": True,
        "slug": "react/19/rsc",
        "is_latest": True,
        "resolved_version": "19",
        "status_tag": "deprecated",
        "stale": True,
    }
    shaped = _shape_check(res)
    assert shaped["advice"] == STALE_ADVICE


def test_shape_get_miss_is_flat():
    assert _shape_get(None) == {"exists": False}


def test_shape_get_hit_status_first_and_has_content():
    shaped = _shape_get(_entry(version_locked=True, ttl_days=None))
    assert list(shaped)[:2] == ["status_tag", "stale"]
    assert shaped["content"] == "# Doc\nbody"
    assert shaped["stale"] is False
    assert "advice" not in shaped


def test_shape_get_stale_includes_advice():
    shaped = _shape_get(_entry(ttl_days=-1))  # negative ttl forces stale
    assert shaped["stale"] is True
    assert shaped["advice"] == STALE_ADVICE


# ---- server wiring ----

@pytest.fixture
def repo():
    return Repository(connect(":memory:"), default_ttl_days=30)


def test_server_exposes_all_six_tools(repo):
    server = build_server(repo)
    names = {t.name for t in _run(server.list_tools())}
    assert names == {
        "list_tree",
        "check_reference",
        "get_reference",
        "search_reference",
        "save_research",
        "invalidate_reference",
    }


def test_instructions_tell_model_to_check_first(repo):
    server = build_server(repo)
    assert "check_reference" in (server.instructions or "")


def test_check_miss_through_tool_is_flat(repo):
    server = build_server(repo)
    out = _call(server, "check_reference", {"tech": "svelte", "topic": "runes"})
    assert out == {"exists": False}


def test_save_then_check_hit_through_tools(repo):
    server = build_server(repo)
    _call(
        server,
        "save_research",
        {
            "tech": "react",
            "version": "19",
            "topic": "server-components",
            "summary": "sum",
            "content": "# Doc\nrender on server",
            "sources": ["https://react.dev"],
            "status_tag": "current",
            "version_locked": False,
        },
    )
    out = _call(server, "check_reference", {"tech": "react", "topic": "server-components"})
    assert out["status_tag"] == "current"
    assert out["resolved_version"] == "19"
    assert out["stale"] is False


# ---- async helpers ----

def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _call(server, name, args):
    content = _run(server.call_tool(name, args))
    return json.loads(content[0].text)
