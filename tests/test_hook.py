"""Host-specific hook adapters: parse each host's event JSON, emit its deny protocol."""

import json

from web_research_mcp.core.repository import Repository
from web_research_mcp.core.store import connect
from web_research_mcp.mcp_server import hook


def _repo_with(*techs):
    repo = Repository(connect(":memory:"), default_ttl_days=30)
    for tech in techs:
        repo.save_research(
            tech=tech, version="1", topic="latest", summary="s", content="c",
            sources=[], status_tag="current", version_locked=False,
        )
    return repo


# ---- input normalization ----

def test_normalize_claude_edit():
    data = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "app/page.tsx", "new_string": "import 'next/link'"},
        "transcript_path": "/t.jsonl",
    }
    inp = hook.normalize_input("claude", data)
    assert inp.file_path == "app/page.tsx"
    assert "next/link" in inp.text
    assert inp.transcript_path == "/t.jsonl"


def test_normalize_claude_write_uses_content():
    data = {
        "tool_name": "Write",
        "tool_input": {"file_path": "x.ts", "content": "import 'react'"},
    }
    inp = hook.normalize_input("claude", data)
    assert inp.file_path == "x.ts"
    assert "react" in inp.text


def test_normalize_cursor_tolerant():
    data = {"tool_input": {"file_path": "y.tsx", "content": "import 'next/link'"}}
    inp = hook.normalize_input("cursor", data)
    assert inp.file_path == "y.tsx"
    assert "next/link" in inp.text


# ---- run: end to end per host ----

def test_run_claude_denies_with_exit_2(tmp_path):
    repo = _repo_with("nextjs")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("just a user prompt about pages\n")
    raw = json.dumps({
        "tool_name": "Edit",
        "tool_input": {"file_path": "app/page.tsx", "new_string": "import Link from 'next/link'"},
        "transcript_path": str(transcript),
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 2
    assert "nextjs" in err
    assert "check_reference" in err


def test_run_claude_allows_after_consult(tmp_path):
    repo = _repo_with("nextjs")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"name":"check_reference","input":{"tech":"nextjs"}}\n')
    raw = json.dumps({
        "tool_name": "Edit",
        "tool_input": {"file_path": "app/page.tsx", "new_string": "import Link from 'next/link'"},
        "transcript_path": str(transcript),
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0
    assert err == ""


def test_run_allows_when_no_tracked_tech():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "x.ts", "content": "import x from 'vue'"},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0


def test_run_cursor_denies_with_permission_json(tmp_path):
    repo = _repo_with("react")
    raw = json.dumps({"tool_input": {"file_path": "x.tsx", "content": "import 'react'"}})
    code, out, err = hook.run("cursor", raw, repo.conn)
    payload = json.loads(out)
    assert payload["permission"] == "deny"
    assert "react" in payload["agentMessage"]


def test_run_gemini_denies_with_block_json(tmp_path):
    repo = _repo_with("react")
    raw = json.dumps({"tool": {"name": "write_file",
                               "args": {"file_path": "x.tsx", "content": "import 'react'"}}})
    code, out, err = hook.run("gemini", raw, repo.conn)
    payload = json.loads(out)
    assert payload.get("decision") == "block" or payload.get("block") is True


def test_run_handles_empty_stdin():
    repo = _repo_with("react")
    code, out, err = hook.run("claude", "", repo.conn)
    assert code == 0


def test_run_missing_transcript_file_is_treated_as_empty(tmp_path):
    repo = _repo_with("react")
    raw = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "x.tsx", "content": "import 'react'"},
        "transcript_path": str(tmp_path / "does-not-exist.jsonl"),
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 2  # can't prove consultation -> block


# ---- WebSearch/WebFetch: pre-search redundancy gate ----

def test_run_pre_websearch_denies_redundant_fresh_tech():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "nextjs app router tutorial"},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 2
    assert "nextjs" in err
    assert "resolve_reference" in err


def test_run_pre_webfetch_denies_redundant_fresh_tech_by_url():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://nextjs.org/docs/app-router"},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 2
    assert "nextjs" in err


def test_run_pre_websearch_allows_when_consulted(tmp_path):
    repo = _repo_with("nextjs")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"name":"check_reference","input":{"tech":"nextjs"}}\n')
    raw = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "nextjs app router tutorial"},
        "transcript_path": str(transcript),
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0
    assert err == ""


def test_run_pre_websearch_allows_untracked_tech():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "svelte tutorial"},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0


def test_run_pre_websearch_cursor_denies_with_permission_json():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "nextjs app router tutorial"},
    })
    code, out, err = hook.run("cursor", raw, repo.conn)
    payload = json.loads(out)
    assert payload["permission"] == "deny"
    assert "nextjs" in payload["agentMessage"]


# ---- WebSearch/WebFetch: post-search save reminder ----

def test_run_post_websearch_injects_save_reminder_claude():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "svelte tutorial"},
        "tool_response": {"results": []},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "save_research" in payload["hookSpecificOutput"]["additionalContext"]


def test_run_post_webfetch_injects_save_reminder_claude():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://svelte.dev/docs"},
        "tool_response": {"content": "..."},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0
    payload = json.loads(out)
    assert "save_research" in payload["hookSpecificOutput"]["additionalContext"]


def test_run_post_websearch_noop_on_unsupported_host():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "svelte tutorial"},
        "tool_response": {"results": []},
    })
    code, out, err = hook.run("cursor", raw, repo.conn)
    assert code == 0
    assert out == ""


def test_run_post_edit_is_noop():
    repo = _repo_with("nextjs")
    raw = json.dumps({
        "tool_name": "Edit",
        "tool_input": {"file_path": "x.tsx", "new_string": "import 'next/link'"},
        "tool_response": {"success": True},
    })
    code, out, err = hook.run("claude", raw, repo.conn)
    assert code == 0
    assert out == ""
    assert err == ""
