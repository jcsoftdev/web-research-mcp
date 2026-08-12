"""Enforcement gate for the pre-edit hook.

Pure logic, no I/O beyond the passed-in DB connection. The host-specific hook
adapters (see :mod:`web_research_mcp.mcp_server.hook`) call :func:`evaluate` and
translate the verdict into their own deny protocol.

The gate answers one question: *is the model about to write code for a tracked
technology without having consulted its cached reference this session?* If so,
the edit is denied so the model loads the reference first.

Detection is deliberately conservative — a false deny blocks a legitimate edit
and enrages the developer, so we only fire on strong signals (real JS/TS imports
or ``package.json`` dependency keys), never on prose mentions.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .slug import normalize_segment
from .staleness import is_stale

# Map a tracked tech (normalized slug segment) to the identifiers it appears as
# in source code. The tech's own name is always included implicitly, so only
# aliases that differ from the slug need an entry here.
TECH_ALIASES: dict[str, list[str]] = {
    "nextjs": ["next"],
    "react": ["react", "react-dom"],
}

# Files where an import/require signal is meaningful. Prose files (.md, .txt)
# are excluded so documentation mentions never trigger a deny.
_CODE_EXTENSIONS = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
}

# Reference tools that count as "consulting the cache".
_REFERENCE_TOOLS = (
    "check_reference",
    "resolve_reference",
    "get_reference",
    "search_reference",
    "list_tree",
)


@dataclass(frozen=True)
class GateVerdict:
    allow: bool
    missing: frozenset[str]


def tracked_techs(conn: sqlite3.Connection) -> set[str]:
    """Distinct techs currently cached, as normalized slug segments."""
    rows = conn.execute("SELECT DISTINCT tech FROM research_entries").fetchall()
    return {row[0] for row in rows}


def _aliases_for(tech: str) -> list[str]:
    aliases = TECH_ALIASES.get(tech, [])
    return list(dict.fromkeys([tech, *aliases]))  # tech first, de-duplicated


def _is_package_json(file_path: str) -> bool:
    return file_path.rsplit("/", 1)[-1] == "package.json"


def _extension(file_path: str) -> str:
    name = file_path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def detect_techs(
    file_path: str,
    content: str,
    tracked: set[str],
    aliases: dict[str, list[str]] | None = None,
) -> set[str]:
    """Which tracked techs this edit actually pulls in, via strong signals."""
    if aliases is not None:
        # allow callers/tests to override the alias table wholesale
        resolve = lambda t: list(dict.fromkeys([t, *aliases.get(t, [])]))
    else:
        resolve = _aliases_for

    package_json = _is_package_json(file_path)
    if not package_json and _extension(file_path) not in _CODE_EXTENSIONS:
        return set()

    hits: set[str] = set()
    for tech in tracked:
        for alias in resolve(tech):
            escaped = re.escape(alias)
            if package_json:
                # dependency key: "next": "..."
                pattern = rf'"{escaped}"\s*:'
            else:
                # import ... from 'alias' | 'alias/sub' | require('alias')
                pattern = rf"""['"]{escaped}(?:/[^'"]*)?['"]"""
            if re.search(pattern, content):
                hits.add(tech)
                break
    return hits


def consulted_techs(transcript_text: str, tracked: set[str]) -> set[str]:
    """Techs whose reference was loaded this session, read from the transcript.

    A tech counts as consulted when a reference tool call appears in the
    transcript *and* the tech name (or an alias) co-occurs with it.
    """
    if not transcript_text:
        return set()
    if not any(tool in transcript_text for tool in _REFERENCE_TOOLS):
        return set()

    consulted: set[str] = set()
    for tech in tracked:
        for alias in _aliases_for(tech):
            if re.search(rf"\b{re.escape(alias)}\b", transcript_text):
                consulted.add(tech)
                break
    return consulted


def evaluate(
    file_path: str,
    content: str,
    transcript_text: str,
    conn: sqlite3.Connection,
) -> GateVerdict:
    """Decide whether to allow the edit."""
    tracked = tracked_techs(conn)
    if not tracked:
        return GateVerdict(allow=True, missing=frozenset())

    detected = detect_techs(file_path, content, tracked)
    if not detected:
        return GateVerdict(allow=True, missing=frozenset())

    consulted = consulted_techs(transcript_text, tracked)
    missing = detected - consulted
    return GateVerdict(allow=not missing, missing=frozenset(missing))


def query_techs(text: str, tracked: set[str]) -> set[str]:
    """Tracked techs named as a whole word in free text (a WebSearch query or
    WebFetch URL) — same word-boundary matching as :func:`detect_techs`, but
    without the code-file gate since search text is never source code.
    """
    if not text:
        return set()
    hits: set[str] = set()
    for tech in tracked:
        for alias in _aliases_for(tech):
            if re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
                hits.add(tech)
                break
    return hits


# The other end of the cycle. `evaluate_search` guards the way in — don't
# re-research what is already cached. This guards the way out: don't keep
# searching without having saved what the previous searches turned up.
#
# It exists because the PostToolUse reminder demonstrably does not work. A
# reminder competes with whatever the model is actually trying to do and loses;
# a deny is obeyed. Measured in a real session: four consecutive searches, four
# reminders, zero `save_research` calls.
_SAVE_TOOL = "save_research"


def unsaved_search_debt(transcript_text: str) -> int:
    """How many web searches happened since the last `save_research`.

    Counted off tool-use names in the transcript rather than parsed as JSONL:
    the transcript is append-only and its per-host shape varies, while the
    ordering of these two names within it does not.
    """
    if not transcript_text:
        return 0

    last_save = transcript_text.rfind(f'"{_SAVE_TOOL}"')
    tail = transcript_text[last_save + 1 :] if last_save != -1 else transcript_text

    return sum(
        len(re.findall(rf'"name"\s*:\s*"{tool}"', tail)) for tool in _SEARCH_TOOL_NAMES
    )


_SEARCH_TOOL_NAMES = ("WebSearch", "WebFetch")


def evaluate_debt(transcript_text: str, threshold: int) -> GateVerdict:
    """Deny a further search while earlier ones remain uncached.

    `threshold` is the number of unsaved searches tolerated before denying, so
    a one-off lookup is never interrupted — only a chain of them. Zero disables
    the gate entirely.

    Deliberately blunt: it counts searches without judging whether each one was
    worth caching, because that judgement cannot be made from a query string.
    The cost of a false deny is one `save_research` call the model would have
    skipped; the cost of missing is a cache that never fills.
    """
    if threshold <= 0:
        return GateVerdict(allow=True, missing=frozenset())
    debt = unsaved_search_debt(transcript_text)
    if debt < threshold:
        return GateVerdict(allow=True, missing=frozenset())
    return GateVerdict(allow=False, missing=frozenset({str(debt)}))


def _has_fresh_entry(tech: str, conn: sqlite3.Connection, now: datetime | None) -> bool:
    rows = conn.execute(
        "SELECT version_locked, updated_at, ttl_days FROM research_entries WHERE tech = ?",
        (tech,),
    ).fetchall()
    return any(
        not is_stale(
            version_locked=bool(r[0]), updated_at=r[1], ttl_days=r[2], now=now
        )
        for r in rows
    )


def evaluate_search(
    text: str,
    transcript_text: str,
    conn: sqlite3.Connection,
    now: datetime | None = None,
) -> GateVerdict:
    """Decide whether to allow a WebSearch/WebFetch call.

    Fires only when the query/URL names a tracked tech that already has a
    FRESH cached entry and that tech wasn't already consulted this session —
    a redundant search that would waste tokens re-researching what's already
    cached. A brand-new tech, or one whose cache is stale, is always allowed
    (that's legitimate research).
    """
    tracked = tracked_techs(conn)
    if not tracked:
        return GateVerdict(allow=True, missing=frozenset())

    named = query_techs(text, tracked)
    if not named:
        return GateVerdict(allow=True, missing=frozenset())

    fresh = {t for t in named if _has_fresh_entry(t, conn, now)}
    if not fresh:
        return GateVerdict(allow=True, missing=frozenset())

    consulted = consulted_techs(transcript_text, tracked)
    missing = fresh - consulted
    return GateVerdict(allow=not missing, missing=frozenset(missing))
