"""Enforcement gate: block editing tracked-tech code before a reference was consulted."""

from web_research_mcp.core import gate
from web_research_mcp.core.repository import Repository
from web_research_mcp.core.store import connect

NOW = None


def _repo_with(*techs):
    repo = Repository(connect(":memory:"), default_ttl_days=30)
    for tech in techs:
        repo.save_research(
            tech=tech,
            version="1",
            topic="latest",
            summary="s",
            content="c",
            sources=[],
            status_tag="current",
            version_locked=False,
        )
    return repo


# ---- tracked_techs ----

def test_tracked_techs_returns_distinct_normalized():
    repo = _repo_with("nextjs", "react")
    assert gate.tracked_techs(repo.conn) == {"nextjs", "react"}


def test_tracked_techs_empty_when_no_entries():
    repo = Repository(connect(":memory:"))
    assert gate.tracked_techs(repo.conn) == set()


# ---- detect_techs ----

def test_detect_js_import_by_alias():
    tracked = {"nextjs", "react"}
    content = "import { useState } from 'react';\nimport Link from 'next/link';\n"
    assert gate.detect_techs("app/page.tsx", content, tracked) == {"react", "nextjs"}


def test_detect_require_form():
    tracked = {"react"}
    content = "const React = require(\"react\");\n"
    assert gate.detect_techs("x.js", content, tracked) == {"react"}


def test_detect_package_json_dependency():
    tracked = {"nextjs", "react"}
    content = '{\n  "dependencies": {\n    "next": "16.0.0",\n    "react": "19.2.0"\n  }\n}\n'
    assert gate.detect_techs("package.json", content, tracked) == {"nextjs", "react"}


def test_detect_ignores_substring_false_positive():
    # "nextauth" must not match tech "nextjs" (alias "next") — word boundary
    tracked = {"nextjs"}
    content = "import x from 'nextauth-helper';\n"
    assert gate.detect_techs("x.ts", content, tracked) == set()


def test_detect_skips_unrelated_file_types():
    tracked = {"react"}
    content = "react is a great library, said the README about react\n"
    assert gate.detect_techs("README.md", content, tracked) == set()


def test_detect_untracked_tech_ignored():
    tracked = {"react"}
    content = "import x from 'vue';\n"
    assert gate.detect_techs("x.ts", content, tracked) == set()


# ---- consulted_techs ----

def test_consulted_from_check_reference_call():
    tracked = {"nextjs", "react"}
    transcript = '{"name":"mcp__web-research__check_reference","input":{"tech":"nextjs","topic":"latest"}}\n'
    assert gate.consulted_techs(transcript, tracked) == {"nextjs"}


def test_consulted_from_get_reference_slug():
    tracked = {"react"}
    transcript = '{"name":"get_reference","input":{"slug":"react/19.2/latest-version-features"}}\n'
    assert gate.consulted_techs(transcript, tracked) == {"react"}


def test_not_consulted_when_tech_mentioned_without_reference_tool():
    tracked = {"react"}
    transcript = 'user: please write some react code for me\n'
    assert gate.consulted_techs(transcript, tracked) == set()


def test_consulted_empty_transcript():
    assert gate.consulted_techs("", {"react"}) == set()


def test_consulted_from_resolve_reference_call():
    # resolve_reference is the recommended check+fetch tool — its own docstring
    # says "call this before any web research". A transcript that only calls
    # it must still count as consulted, or the gate contradicts its own advice.
    tracked = {"nextjs"}
    transcript = '{"name":"resolve_reference","input":{"tech":"nextjs","topic":"latest"}}\n'
    assert gate.consulted_techs(transcript, tracked) == {"nextjs"}


# ---- evaluate ----

def test_evaluate_denies_when_detected_but_not_consulted():
    repo = _repo_with("nextjs")
    v = gate.evaluate("app/page.tsx", "import Link from 'next/link'\n", "", repo.conn)
    assert v.allow is False
    assert v.missing == {"nextjs"}


def test_evaluate_allows_when_consulted():
    repo = _repo_with("nextjs")
    transcript = '{"name":"check_reference","input":{"tech":"nextjs"}}'
    v = gate.evaluate("app/page.tsx", "import Link from 'next/link'\n", transcript, repo.conn)
    assert v.allow is True
    assert v.missing == set()


def test_evaluate_allows_when_no_tracked_tech_detected():
    repo = _repo_with("nextjs")
    v = gate.evaluate("x.ts", "import x from 'vue'\n", "", repo.conn)
    assert v.allow is True
    assert v.missing == set()


def test_evaluate_partial_consult_denies_only_missing():
    repo = _repo_with("nextjs", "react")
    content = "import 'react'\nimport 'next/link'\n"
    transcript = '{"name":"check_reference","input":{"tech":"react"}}'
    v = gate.evaluate("x.tsx", content, transcript, repo.conn)
    assert v.allow is False
    assert v.missing == {"nextjs"}


# ---- query_techs ----

def test_query_techs_matches_word_boundary():
    assert gate.query_techs("react hooks best practices", {"react", "nextjs"}) == {"react"}


def test_query_techs_matches_in_url():
    assert gate.query_techs("https://nextjs.org/docs/app", {"nextjs"}) == {"nextjs"}


def test_query_techs_ignores_substring_false_positive():
    assert gate.query_techs("nextauth setup guide", {"nextjs"}) == set()


def test_query_techs_empty_text():
    assert gate.query_techs("", {"react"}) == set()


# ---- evaluate_search ----

def test_evaluate_search_denies_redundant_search_of_fresh_tech():
    repo = _repo_with("nextjs")
    v = gate.evaluate_search("nextjs app router tutorial", "", repo.conn)
    assert v.allow is False
    assert v.missing == {"nextjs"}


def test_evaluate_search_allows_when_already_consulted():
    repo = _repo_with("nextjs")
    transcript = '{"name":"check_reference","input":{"tech":"nextjs"}}'
    v = gate.evaluate_search("nextjs app router tutorial", transcript, repo.conn)
    assert v.allow is True


def test_evaluate_search_allows_untracked_tech():
    repo = _repo_with("nextjs")
    v = gate.evaluate_search("svelte tutorial", "", repo.conn)
    assert v.allow is True


def test_evaluate_search_allows_stale_entry():
    from datetime import datetime, timedelta, timezone

    repo = _repo_with("nextjs")
    future = datetime.now(timezone.utc) + timedelta(days=60)
    v = gate.evaluate_search("nextjs app router tutorial", "", repo.conn, now=future)
    assert v.allow is True


def test_evaluate_search_allows_when_no_tracked_techs():
    repo = Repository(connect(":memory:"))
    v = gate.evaluate_search("nextjs tutorial", "", repo.conn)
    assert v.allow is True


# ---- unsaved_search_debt ----
#
# El otro extremo del ciclo. El gate de redundancia protege la ENTRADA (no
# vuelvas a buscar lo que ya está cacheado); esto protege la SALIDA (no sigas
# buscando sin haber guardado lo anterior). Medido en la práctica: un recordatorio
# en PostToolUse se ignora cuando el modelo persigue otro objetivo, mientras que
# una denegación se obedece siempre.

def _busqueda(query="groq free tier limits"):
    return (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        f'"name":"WebSearch","input":{{"query":"{query}"}}}}]}}}}\n'
    )


def _guardado():
    return (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"save_research","input":{"tech":"groq"}}]}}\n'
    )


def test_debt_zero_without_searches():
    assert gate.unsaved_search_debt("") == 0
    assert gate.unsaved_search_debt('{"type":"user"}\n') == 0


def test_debt_counts_searches_since_last_save():
    transcript = _busqueda() + _busqueda() + _busqueda()
    assert gate.unsaved_search_debt(transcript) == 3


def test_saving_clears_the_debt():
    transcript = _busqueda() + _busqueda() + _guardado()
    assert gate.unsaved_search_debt(transcript) == 0


def test_only_searches_after_the_last_save_count():
    transcript = _busqueda() + _guardado() + _busqueda() + _busqueda()
    assert gate.unsaved_search_debt(transcript) == 2


def test_webfetch_counts_as_a_search():
    transcript = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"WebFetch","input":{"url":"https://ai.google.dev/x"}}]}}\n'
    )
    assert gate.unsaved_search_debt(transcript) == 1


# ---- evaluate_debt ----

def test_debt_allows_below_the_threshold():
    transcript = _busqueda() + _busqueda()
    verdict = gate.evaluate_debt(transcript, threshold=3)
    assert verdict.allow


def test_debt_denies_at_the_threshold():
    transcript = _busqueda() + _busqueda() + _busqueda()
    verdict = gate.evaluate_debt(transcript, threshold=3)
    assert not verdict.allow


def test_debt_never_denies_when_disabled():
    transcript = _busqueda() * 9
    assert gate.evaluate_debt(transcript, threshold=0).allow


def test_debt_allows_an_unreadable_transcript():
    # Falla abierto: un fallo del propio gate no puede atascar el trabajo.
    assert gate.evaluate_debt("{no es json\n", threshold=1).allow
