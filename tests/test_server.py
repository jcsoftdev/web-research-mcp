import json

import pytest

from web_research_mcp.core.models import ResearchEntry
from web_research_mcp.core.repository import Repository
from web_research_mcp.core.store import connect
from web_research_mcp.mcp_server import server as server_mod
from web_research_mcp.mcp_server.server import (
    HELP_TEXT,
    STALE_ADVICE,
    _serve,
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


def test_server_exposes_all_tools(repo):
    server = build_server(repo)
    names = {t.name for t in _run(server.list_tools())}
    assert names == {
        "list_tree",
        "check_reference",
        "get_reference",
        "search_reference",
        "save_research",
        "invalidate_reference",
        "check_for_update",
    }


def test_instructions_tell_model_to_check_first(repo):
    server = build_server(repo)
    assert "check_reference" in (server.instructions or "")


def test_instructions_tell_host_to_delegate_update_agent(repo):
    server = build_server(repo)
    text = (server.instructions or "").lower()
    assert "check_for_update" in text
    assert "agent" in text  # host delegates the update to an agent


def test_check_for_update_tool_reports_status(repo):
    # inject a checker so no network is touched
    server = build_server(
        repo,
        update_checker=lambda: {
            "update_available": True,
            "current": "0.1.0",
            "latest": "0.2.0",
            "command": "uv tool install ... --force",
        },
    )
    out = _call(server, "check_for_update", {})
    assert out["update_available"] is True
    assert out["latest"] == "0.2.0"
    assert "command" in out
    assert "agent" in out["advice"].lower()  # tells host to delegate an agent


def test_check_for_update_no_update_has_no_advice(repo):
    server = build_server(
        repo, update_checker=lambda: {"update_available": False, "current": "0.1.0"}
    )
    out = _call(server, "check_for_update", {})
    assert out == {"update_available": False, "current": "0.1.0"}


def test_check_miss_through_tool_is_flat(repo):
    server = build_server(repo)
    out = _call(server, "check_reference", {"tech": "svelte", "topic": "runes"})
    assert out == {"exists": False}


def test_check_reference_accepts_numeric_version(repo):
    server = build_server(repo)
    _call(server, "save_research", {
        "tech": "react", "version": "18", "topic": "hooks",
        "summary": "s", "content": "c", "sources": [], "status_tag": "current",
    })
    # model passes 18 as a number, not "18"
    out = _call(server, "check_reference", {"tech": "react", "topic": "hooks", "version": 18})
    assert out["exists"] is True
    assert out["resolved_version"] == "18"


def test_save_research_minimal_call(repo):
    server = build_server(repo)
    # only the truly essential fields — version/sources/status_tag omitted
    out = _call(server, "save_research", {
        "tech": "react", "topic": "hooks", "summary": "s", "content": "c",
    })
    assert out["saved"] is True
    chk = _call(server, "check_reference", {"tech": "react", "topic": "hooks"})
    assert chk["exists"] is True
    assert chk["status_tag"] == "current"  # default
    assert chk["resolved_version"] is None


def test_save_research_coerces_numeric_version(repo):
    server = build_server(repo)
    out = _call(server, "save_research", {
        "tech": "react", "version": 18, "topic": "hooks", "summary": "s", "content": "c",
    })
    assert out["slug"] == "react/18/hooks"


def test_save_research_accepts_string_sources(repo):
    server = build_server(repo)
    out = _call(server, "save_research", {
        "tech": "react", "topic": "hooks", "summary": "s", "content": "c",
        "sources": "https://react.dev",
    })
    got = _call(server, "get_reference", {"slug": out["slug"]})
    assert got["sources"] == ["https://react.dev"]


def test_save_flags_possible_duplicate(repo):
    server = build_server(repo)
    _call(server, "save_research", {
        "tech": "react", "version": "19", "topic": "server-components",
        "summary": "s", "content": "c",
    })
    # a second save under a near-duplicate topic name
    out = _call(server, "save_research", {
        "tech": "react", "version": "19", "topic": "server-component",
        "summary": "s", "content": "c",
    })
    assert out["saved"] is True  # non-blocking
    dups = out["possible_duplicates"]
    assert any(d["slug"] == "react/19/server-components" for d in dups)


def test_save_without_duplicate_has_no_key(repo):
    server = build_server(repo)
    out = _call(server, "save_research", {
        "tech": "react", "topic": "hooks", "summary": "s", "content": "c",
    })
    assert "possible_duplicates" not in out


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


# ---- CLI: help text and clean Ctrl+C shutdown ----

def test_help_text_lists_every_tool():
    for tool in (
        "list_tree", "check_reference", "get_reference", "search_reference",
        "save_research", "invalidate_reference", "check_for_update",
    ):
        assert tool in HELP_TEXT


def test_help_text_documents_hook_and_help_subcommands():
    assert "hook --host" in HELP_TEXT
    assert "web-research-mcp help" in HELP_TEXT


def test_main_help_arg_prints_usage_without_starting_server(monkeypatch, capsys):
    monkeypatch.setattr(server_mod.sys, "argv", ["web-research-mcp", "help"])
    monkeypatch.setattr(
        server_mod, "load_config", lambda: (_ for _ in ()).throw(AssertionError("must not run server"))
    )
    server_mod.main()
    assert HELP_TEXT.strip() in capsys.readouterr().out


def test_main_dash_dash_help_prints_usage(monkeypatch, capsys):
    monkeypatch.setattr(server_mod.sys, "argv", ["web-research-mcp", "--help"])
    server_mod.main()
    assert "Usage:" in capsys.readouterr().out


def test_serve_exits_cleanly_on_keyboard_interrupt(monkeypatch, repo, capsys):
    class _Boom:
        def run(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server_mod, "build_server", lambda *a, **k: _Boom())
    exited = {}
    monkeypatch.setattr(server_mod.os, "_exit", lambda code: exited.setdefault("code", code))

    _serve(repo, advertise_updates=False)

    assert exited["code"] == 0
    assert "stopped" in capsys.readouterr().err


# ---- async helpers ----

def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _call(server, name, args):
    content = _run(server.call_tool(name, args))
    return json.loads(content[0].text)
