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

from .slug import normalize_segment

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
