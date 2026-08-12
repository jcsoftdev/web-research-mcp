"""Pre-edit enforcement hook: host adapters over :mod:`web_research_mcp.core.gate`.

Invoked as ``web-research-mcp hook --host {claude|codex|gemini|cursor}``. Each
host pipes its pre-edit event as JSON on stdin; the adapter normalizes it, asks
the gate for a verdict, and emits the deny in that host's own protocol.

Deny protocols differ per host:
- claude: exit code 2 + reason on stderr (Claude surfaces stderr to the model).
- codex:  stdout JSON ``{"decision": "block", "reason": ...}`` (deny-only hook).
- gemini: stdout JSON ``{"decision": "block", "reason": ...}`` from BeforeTool.
- cursor: stdout JSON ``{"permission": "deny", "agentMessage": ..., "userMessage": ...}``.

Input parsing is deliberately tolerant (hosts nest fields differently and their
schemas evolve): we pull ``file_path`` / content / ``transcript_path`` out of
whatever shape arrives, falling back to a deep scan.
"""

from __future__ import annotations

import os
import json
import sqlite3
import sys
from dataclasses import dataclass

from ..core import gate

HOSTS = ("claude", "codex", "gemini", "cursor")


@dataclass(frozen=True)
class HookInput:
    tool_name: str
    file_path: str
    text: str
    transcript_path: str


def _first(d: dict, *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _tool_input(data: dict) -> dict:
    for key in ("tool_input", "toolInput", "input", "args", "arguments"):
        v = data.get(key)
        if isinstance(v, dict):
            return v
    tool = data.get("tool")
    if isinstance(tool, dict):
        for key in ("args", "arguments", "input", "tool_input"):
            v = tool.get(key)
            if isinstance(v, dict):
                return v
    return {}


def _tool_name(data: dict) -> str:
    name = _first(data, "tool_name", "toolName", "tool")
    if name:
        return name
    tool = data.get("tool")
    if isinstance(tool, dict):
        return _first(tool, "name")
    return ""


def normalize_input(host: str, data: dict) -> HookInput:
    """Extract the edit's file path, written text, and transcript path."""
    ti = _tool_input(data)
    # written text: Write.content, Edit.new_string, or a shell/patch command
    text = _first(ti, "content", "new_string", "newString", "command", "patch")
    file_path = _first(ti, "file_path", "filePath", "path")
    transcript = _first(data, "transcript_path", "transcriptPath", "transcript")
    return HookInput(
        tool_name=_tool_name(data),
        file_path=file_path,
        text=text,
        transcript_path=transcript,
    )


def _read_transcript(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _reason(missing: frozenset[str]) -> str:
    techs = ", ".join(sorted(missing))
    return (
        f"web-research: cached reference(s) for [{techs}] exist but were not "
        f"consulted this session. Call check_reference(tech, topic) and "
        f"get_reference(slug) to load current, non-deprecated guidance, then "
        f"retry this edit."
    )


def _reason_search(missing: frozenset[str]) -> str:
    techs = ", ".join(sorted(missing))
    return (
        f"web-research: fresh cached reference(s) already exist for [{techs}]. "
        f"Call resolve_reference(tech, topic) instead of searching the web again."
    )


def _reason_debt(missing: frozenset[str]) -> str:
    count = next(iter(missing), "several")
    return (
        f"web-research: {count} earlier web searches this session were never "
        f"cached. Call save_research(tech, topic, summary, content) for what "
        f"they turned up, then search again."
    )


def _emit_deny(
    host: str, missing: frozenset[str], reason_fn=_reason
) -> tuple[int, str, str]:
    reason = reason_fn(missing)
    if host == "claude":
        return 2, "", reason
    if host == "cursor":
        return 0, json.dumps({
            "permission": "deny",
            "agentMessage": reason,
            "userMessage": reason,
        }), ""
    # codex and gemini: block via stdout JSON
    return 0, json.dumps({"decision": "block", "reason": reason}), ""


_SEARCH_TOOLS = ("WebSearch", "WebFetch")

# Off unless asked for, like the rest of the deny machinery: a gate that
# interrupts work is opt-in. 3 is a chain, not a lookup.
_DEBT_ENV = "WEB_RESEARCH_SEARCH_DEBT"


def _debt_threshold() -> int:
    try:
        return int(os.environ.get(_DEBT_ENV, "0"))
    except ValueError:
        return 0  # a typo in the env must not wedge searching

POST_SEARCH_ADVICE = (
    "web-research: if this search surfaced reusable technical knowledge, call "
    "save_research(tech, topic, summary, content, ...) now to cache it before "
    "continuing."
)

# Labels that name the site's role, not its technology. Stripping them turns
# docs.python.org into "python" instead of "docs" — and "docs" as a tech would
# collide across every project that ever gets cached.
_ROLE_LABELS = {
    "www", "docs", "doc", "api", "developer", "developers", "blog", "learn",
    "help", "support", "en", "guide", "guides", "reference", "wiki",
}


# Second-level registry labels (example.co.uk). Never a technology name.
_REGISTRY_LABELS = {"co", "com", "org", "net", "ac", "gov", "edu"}


def _tech_from_url(url: str) -> str:
    """The technology a URL is about, guessed from its host.

    A guess, deliberately: a wrong `tech=` costs one correction by the caller,
    while no suggestion at all costs the save entirely — which is the failure
    actually observed.
    """
    host = url.split("//", 1)[-1].split("/", 1)[0].split("@")[-1].split(":")[0]
    labels = [label for label in host.lower().split(".") if label]
    if len(labels) < 2:
        return ""
    meaningful = [
        label
        for label in labels[:-1]  # the TLD itself is never the technology
        if label not in _ROLE_LABELS and label not in _REGISTRY_LABELS
    ]
    # Last, not first: on pkg.go.dev the technology is `go`, and on
    # docs.python.org the role label is already gone by here.
    return meaningful[-1] if meaningful else ""


def _post_search_advice(tool_name: str, text: str) -> str:
    """The generic reminder, plus whatever of the call we can already fill in."""
    tech = _tech_from_url(text) if tool_name == "WebFetch" else ""
    if tech:
        return (
            f"web-research: cache what this page taught you — "
            f'save_research(tech="{tech}", topic=..., summary=..., content=...) '
            f"— before continuing. Skip only if it held nothing reusable."
        )
    return (
        f'web-research: the search "{text}" is not cached. If it surfaced '
        f"reusable technical knowledge, call save_research(tech, topic, "
        f"summary, content) now, before continuing."
    )


def _search_text(tool_name: str, tool_input: dict) -> str:
    if tool_name == "WebSearch":
        return _first(tool_input, "query")
    if tool_name == "WebFetch":
        return _first(tool_input, "url")
    return ""


def _emit_post_context(host: str, reason: str) -> tuple[int, str, str]:
    # Only Claude Code's PostToolUse schema is verified to carry
    # additionalContext back to the model; other hosts stay silent rather
    # than risk an unsupported/ignored payload.
    if host != "claude":
        return 0, "", ""
    return 0, json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reason,
        }
    }), ""


def run(host: str, raw_stdin: str, conn: sqlite3.Connection) -> tuple[int, str, str]:
    """Return ``(exit_code, stdout, stderr)`` for the given host event."""
    try:
        data = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except (json.JSONDecodeError, AttributeError):
        return 0, "", ""  # unparseable event: never block on our own bug
    if not isinstance(data, dict):
        return 0, "", ""

    tool_name = _tool_name(data)
    is_post = "tool_response" in data

    if tool_name in _SEARCH_TOOLS:
        text = _search_text(tool_name, _tool_input(data))
        if not text:
            return 0, "", ""
        if is_post:
            return _emit_post_context(host, _post_search_advice(tool_name, text))
        transcript = _read_transcript(
            _first(data, "transcript_path", "transcriptPath", "transcript")
        )
        verdict = gate.evaluate_search(text, transcript, conn)
        if not verdict.allow:
            return _emit_deny(host, verdict.missing, reason_fn=_reason_search)

        # Redundancy is about this search; debt is about the ones before it.
        # Checked second so "you already have this cached" wins when both fire:
        # it names the specific tech and is the more actionable of the two.
        debt = gate.evaluate_debt(transcript, _debt_threshold())
        if not debt.allow:
            return _emit_deny(host, debt.missing, reason_fn=_reason_debt)
        return 0, "", ""

    if is_post:
        return 0, "", ""  # no post-edit action defined yet

    inp = normalize_input(host, data)
    if not inp.text and not inp.file_path:
        return 0, "", ""

    transcript = _read_transcript(inp.transcript_path)
    verdict = gate.evaluate(inp.file_path, inp.text, transcript, conn)
    if verdict.allow:
        return 0, "", ""
    return _emit_deny(host, verdict.missing)


def main(argv: list[str]) -> int:
    """CLI: ``hook --host <name>``. Reads the event from stdin."""
    from ..config import load_config
    from ..core.store import connect

    host = "claude"
    if "--host" in argv:
        i = argv.index("--host")
        if i + 1 < len(argv):
            host = argv[i + 1]
    if host not in HOSTS:
        sys.stderr.write(f"unknown host '{host}'; expected one of {HOSTS}\n")
        return 0  # fail open: a misconfigured hook must not block edits

    raw = sys.stdin.read()
    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        code, out, err = run(host, raw, conn)
    finally:
        conn.close()
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return code
