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
