"""Pure data-access logic over the SQLite store.

No MCP here — this is testable with an in-memory connection. The MCP server
is a thin wrapper that calls these methods and shapes responses.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .embeddings import EmbeddingProvider, NullProvider
from .models import ResearchEntry
from .similarity import find_duplicates
from .slug import make_slug, normalize_segment
from .staleness import is_stale
from .versioning import is_newer

_MUTABLE_ON_REFRESH = ("summary", "content", "status_tag", "sources", "tags", "ttl_days")


def _iso(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _extract_section(content: str, section: str) -> str | None:
    """Return the markdown section whose heading matches ``section``.

    Includes the heading line through to the next heading of the same or a
    higher level. Case-insensitive. Returns ``None`` if no heading matches.
    """
    target = section.strip().lower()
    lines = content.splitlines()
    out: list[str] = []
    level = 0
    capturing = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[hashes:].strip()
            if capturing and hashes <= level:
                break
            if not capturing and heading.lower() == target:
                capturing = True
                level = hashes
                out.append(line)
                continue
        if capturing:
            out.append(line)
    if not capturing:
        return None
    return "\n".join(out)


class Repository:
    def __init__(
        self,
        conn: sqlite3.Connection,
        default_ttl_days: int = 30,
        embeddings: EmbeddingProvider | None = None,
    ) -> None:
        self.conn = conn
        self.default_ttl_days = default_ttl_days
        self.embeddings = embeddings or NullProvider()

    # ---- reads ----

    def _row_by_slug(self, slug: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM research_entries WHERE slug = ?", (slug,)
        ).fetchone()

    def _latest_row(self, tech: str, topic: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM research_entries WHERE tech = ? AND topic = ? AND is_latest = 1",
            (tech, topic),
        ).fetchone()

    def get_reference(self, slug: str, section: str | None = None) -> ResearchEntry | None:
        row = self._row_by_slug(slug)
        if row is None:
            return None
        entry = ResearchEntry.from_row(row)
        if section is not None:
            extracted = _extract_section(entry.content, section)
            if extracted is not None:
                entry.content = extracted
        return entry

    def check_reference(
        self,
        tech: str,
        topic: str,
        version: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        from .slug import normalize_segment

        tech_n = normalize_segment(tech)
        topic_n = normalize_segment(topic)
        if version:
            row = self._row_by_slug(make_slug(tech, version, topic))
        else:
            row = self._latest_row(tech_n, topic_n)
        if row is None:
            return {"exists": False}
        stale = is_stale(
            version_locked=bool(row["version_locked"]),
            updated_at=row["updated_at"],
            ttl_days=row["ttl_days"],
            now=now,
        )
        return {
            "exists": True,
            "slug": row["slug"],
            "is_latest": bool(row["is_latest"]),
            "resolved_version": row["version"],
            "status_tag": row["status_tag"],
            "stale": stale,
        }

    def search_reference(
        self, query: str, tech: str | None = None, limit: int = 20
    ) -> list[dict]:
        # Quote-wrap and double embedded quotes so FTS5 treats the whole query
        # as a literal phrase — neutralizes MATCH-syntax injection
        # (+ - ~ ( ) : * AND/OR/NOT). The value is also bound as a parameter.
        safe = '"' + query.replace('"', '""') + '"'
        sql = (
            "SELECT e.* FROM research_fts f "
            "JOIN research_entries e ON e.id = f.rowid "
            "WHERE research_fts MATCH ?"
        )
        params: list = [safe]
        if tech:
            from .slug import normalize_segment

            sql += " AND e.tech = ?"
            params.append(normalize_segment(tech))
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "slug": r["slug"],
                "tech": r["tech"],
                "version": r["version"],
                "topic": r["topic"],
                "summary": r["summary"],
                "status_tag": r["status_tag"],
                "is_latest": bool(r["is_latest"]),
            }
            for r in rows
        ]

    def list_tree(self, tech: str | None = None, now: datetime | None = None) -> list[dict]:
        sql = (
            "SELECT tech, version, topic, slug, is_latest, status_tag, "
            "version_locked, updated_at, ttl_days FROM research_entries"
        )
        params: list = []
        if tech:
            from .slug import normalize_segment

            sql += " WHERE tech = ?"
            params.append(normalize_segment(tech))
        sql += " ORDER BY tech, version, topic"
        rows = self.conn.execute(sql, params).fetchall()

        techs: dict[str, dict] = {}
        for r in rows:
            tech_node = techs.setdefault(r["tech"], {})
            version_node = tech_node.setdefault(r["version"], [])
            version_node.append(
                {
                    "topic": r["topic"],
                    "slug": r["slug"],
                    "is_latest": bool(r["is_latest"]),
                    "status_tag": r["status_tag"],
                    "stale": is_stale(
                        version_locked=bool(r["version_locked"]),
                        updated_at=r["updated_at"],
                        ttl_days=r["ttl_days"],
                        now=now,
                    ),
                }
            )
        return [
            {
                "tech": t,
                "versions": [
                    {"version": v, "topics": topics}
                    for v, topics in versions.items()
                ],
            }
            for t, versions in techs.items()
        ]

    def find_similar_topics(
        self, tech: str, topic: str, threshold: float = 0.6
    ) -> list[dict]:
        """Existing topics for ``tech`` that look like duplicates of ``topic``.

        Lexical for now; when embeddings are enabled this is where vector
        similarity swaps in (same signature, same call site).
        """
        tech_n = normalize_segment(tech)
        topic_n = normalize_segment(topic)
        rows = self.conn.execute(
            "SELECT slug, topic, version, is_latest FROM research_entries "
            "WHERE tech = ? ORDER BY is_latest DESC, version DESC",
            (tech_n,),
        ).fetchall()
        candidates: list[dict] = []
        seen: set[str] = set()
        for r in rows:
            if r["topic"] in seen:
                continue
            seen.add(r["topic"])
            candidates.append(
                {
                    "slug": r["slug"],
                    "topic": r["topic"],
                    "version": r["version"],
                    "is_latest": bool(r["is_latest"]),
                }
            )
        return find_duplicates(topic_n, candidates, threshold)

    # ---- writes ----

    def save_research(
        self,
        tech: str,
        version: str | None,
        topic: str,
        summary: str,
        content: str,
        sources: list[str],
        status_tag: str,
        version_locked: bool,
        tags: list[str] | None = None,
        now: datetime | None = None,
    ) -> ResearchEntry:
        slug = make_slug(tech, version, topic)
        from .slug import normalize_segment

        tech_n = normalize_segment(tech)
        topic_n = normalize_segment(topic)
        version_n = normalize_segment(version) if version else None
        ttl_days = None if version_locked else self.default_ttl_days
        ts = _iso(now)
        sources_json = json.dumps(sources or [])
        tags_json = json.dumps(tags or [])

        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._row_by_slug(slug)
            if existing is not None:
                # Same tech/version/topic — refresh in place, keep is_latest.
                conn.execute(
                    "UPDATE research_entries SET summary=?, content=?, status_tag=?, "
                    "sources=?, tags=?, version_locked=?, ttl_days=?, updated_at=? "
                    "WHERE id=?",
                    (
                        summary,
                        content,
                        status_tag,
                        sources_json,
                        tags_json,
                        int(version_locked),
                        ttl_days,
                        ts,
                        existing["id"],
                    ),
                )
                conn.commit()
                return ResearchEntry.from_row(self._row_by_slug(slug))

            current = self._latest_row(tech_n, topic_n)
            new_is_latest = 1
            if current is not None:
                if is_newer(version_n, current["version"]):
                    new_is_latest = 1  # supersede handled after insert
                else:
                    new_is_latest = 0

            cur = conn.execute(
                "INSERT INTO research_entries "
                "(slug, tech, version, topic, summary, content, status_tag, sources, tags, "
                "version_locked, is_latest, superseded_by, ttl_days, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    slug,
                    tech_n,
                    version_n,
                    topic_n,
                    summary,
                    content,
                    status_tag,
                    sources_json,
                    tags_json,
                    int(version_locked),
                    new_is_latest,
                    None,
                    ttl_days,
                    ts,
                    ts,
                ),
            )
            new_id = cur.lastrowid

            if current is not None and new_is_latest == 1:
                conn.execute(
                    "UPDATE research_entries SET is_latest=0, superseded_by=? WHERE id=?",
                    (new_id, current["id"]),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return ResearchEntry.from_row(conn.execute(
            "SELECT * FROM research_entries WHERE id=?", (new_id,)
        ).fetchone())

    def invalidate_reference(self, slug: str) -> bool:
        # Negative ttl forces stale regardless of lock state (see staleness).
        cur = self.conn.execute(
            "UPDATE research_entries SET ttl_days=-1 WHERE slug=?", (slug,)
        )
        self.conn.commit()
        return cur.rowcount > 0
