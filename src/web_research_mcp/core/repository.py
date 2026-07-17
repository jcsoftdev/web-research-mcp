"""Pure data-access logic over the SQLite store.

No MCP here — this is testable with an in-memory connection. The MCP server
is a thin wrapper that calls these methods and shapes responses.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .canonical import aliases_for, canonical_topic
from .embeddings import EmbeddingProvider, NullProvider
from .models import ResearchEntry, SaveResult
from .similarity import find_duplicates
from .slug import make_slug, normalize_segment, parse_slug
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
            # The slug's topic segment may be an alias spelling (e.g.
            # "react/18/whats-new") — retry with it canonicalized.
            try:
                tech, version, topic = parse_slug(slug)
            except ValueError:
                return None
            canonical = canonical_topic(topic)
            if canonical == topic:
                return None
            row = self._row_by_slug(make_slug(tech, version, canonical))
            if row is None:
                return None
        entry = ResearchEntry.from_row(row)
        if section is not None:
            extracted = _extract_section(entry.content, section)
            if extracted is not None:
                entry.content = extracted
        return entry

    def _lookup(
        self, tech: str, topic: str, version: str | None
    ) -> tuple[sqlite3.Row | None, str, str]:
        """Resolve tech/topic/version to its row plus the normalized segments.

        Version given -> exact slug; omitted -> the latest row for tech/topic.
        """
        tech_n = normalize_segment(tech)
        topic_n = canonical_topic(topic)
        if version:
            row = self._row_by_slug(make_slug(tech, version, topic_n))
        else:
            row = self._latest_row(tech_n, topic_n)
        if row is None:
            # Rows saved before topic canonicalization existed may still carry
            # a literal alias spelling — try each alias that maps to this
            # canonical topic so they don't become invisible (and get
            # silently re-saved as a "new" canonical entry) after this
            # feature ships. Covers both "input was itself an alias" and
            # "input is canonical but the stored row used a different alias".
            for alias in aliases_for(topic_n):
                if version:
                    row = self._row_by_slug(make_slug(tech, version, alias))
                else:
                    row = self._latest_row(tech_n, alias)
                if row is not None:
                    break
        return row, tech_n, topic_n

    def _check_from_row(self, row: sqlite3.Row | None, now: datetime | None) -> dict:
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

    def _log_usage(
        self,
        tech: str,
        topic: str | None,
        row: sqlite3.Row | None,
        now: datetime | None,
    ) -> None:
        # Insert only — caller commits, so a batch can log N events in one
        # fsync instead of N.
        hit = row is not None
        tokens_saved = len(row["content"]) // 4 if hit else 0
        self.conn.execute(
            "INSERT INTO usage_events (event, tech, topic, tokens_saved, created_at) "
            "VALUES (?,?,?,?,?)",
            ("hit" if hit else "miss", tech, topic, tokens_saved, _iso(now)),
        )

    def check_reference(
        self,
        tech: str,
        topic: str,
        version: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        row, tech_n, topic_n = self._lookup(tech, topic, version)
        self._log_usage(tech_n, topic_n, row, now)
        self.conn.commit()
        return self._check_from_row(row, now)

    def check_reference_batch(
        self, items: list[dict], now: datetime | None = None
    ) -> list[dict]:
        """Loop check_reference over ``{tech, topic, version?}`` items in one call.

        Each result echoes tech/topic (and version when given) before the check
        fields so the caller can correlate without positional assumptions.
        """
        results: list[dict] = []
        for item in items:
            version = item.get("version")
            version = None if version in (None, "") else str(version)
            row, tech_n, topic_n = self._lookup(item["tech"], item["topic"], version)
            self._log_usage(tech_n, topic_n, row, now)
            out: dict = {"tech": item["tech"], "topic": item["topic"]}
            if version is not None:
                out["version"] = version
            out.update(self._check_from_row(row, now))
            results.append(out)
        self.conn.commit()
        return results

    def stack_diff(self, items: list[dict], now: datetime | None = None) -> list[dict]:
        """Audit a stack (``{tech, version?}`` items) against the cache.

        Per item: ``missing`` (never researched), ``stale`` (cached but every
        covering entry is outdated, or the requested version is not covered) or
        ``fresh`` (at least one covering entry is current). A general
        (version-less) entry covers any requested version.
        """
        results: list[dict] = []
        for item in items:
            tech = item["tech"]
            requested = item.get("version")
            requested = None if requested in (None, "") else str(requested)
            tech_n = normalize_segment(tech)
            req_n = normalize_segment(requested) if requested else None
            rows = self.conn.execute(
                "SELECT version, version_locked, updated_at, ttl_days "
                "FROM research_entries WHERE tech = ?",
                (tech_n,),
            ).fetchall()
            if not rows:
                results.append(
                    {
                        "tech": tech,
                        "requested_version": requested,
                        "status": "missing",
                        "cached_versions": [],
                    }
                )
                continue
            cached_versions = sorted(
                {r["version"] for r in rows}, key=lambda v: (v is None, v or "")
            )
            if req_n is None:
                covering = list(rows)
            else:
                covering = [
                    r for r in rows if r["version"] == req_n or r["version"] is None
                ]
            if req_n is not None and not covering:
                status = "stale"
            else:
                any_fresh = any(
                    not is_stale(
                        version_locked=bool(r["version_locked"]),
                        updated_at=r["updated_at"],
                        ttl_days=r["ttl_days"],
                        now=now,
                    )
                    for r in covering
                )
                status = "fresh" if any_fresh else "stale"
            results.append(
                {
                    "tech": tech,
                    "requested_version": requested,
                    "status": status,
                    "cached_versions": cached_versions,
                }
            )
        return results

    def resolve_reference(
        self,
        tech: str,
        topic: str,
        version: str | None = None,
        max_age_days: int | None = None,
        now: datetime | None = None,
    ) -> dict:
        """check_reference + get_reference in one call.

        ``max_age_days`` overrides the entry TTL for this call only, unless the
        entry is version_locked (never stale) or negatively invalidated
        (always stale).
        """
        row, tech_n, topic_n = self._lookup(tech, topic, version)
        self._log_usage(tech_n, topic_n, row, now)
        self.conn.commit()
        if row is None:
            return {"exists": False}
        entry = ResearchEntry.from_row(row)
        ttl = entry.ttl_days
        if (
            max_age_days is not None
            and not entry.version_locked
            and ttl is not None
            and ttl >= 0
        ):
            ttl = max_age_days
        stale = is_stale(
            version_locked=entry.version_locked,
            updated_at=entry.updated_at,
            ttl_days=ttl,
            now=now,
        )
        return {
            "exists": True,
            "slug": entry.slug,
            "is_latest": entry.is_latest,
            "resolved_version": entry.version,
            "status_tag": entry.status_tag,
            "stale": stale,
            "summary": entry.summary,
            "sources": entry.sources,
            "content": entry.content,
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
        topic_n = canonical_topic(topic)
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
        force: bool = False,
        now: datetime | None = None,
    ) -> SaveResult:
        tech_n = normalize_segment(tech)
        topic_n = canonical_topic(topic)
        version_n = normalize_segment(version) if version else None
        slug = make_slug(tech, version, topic_n)
        ttl_days = None if version_locked else self.default_ttl_days
        ts = _iso(now)
        sources_json = json.dumps(sources or [])
        tags_json = json.dumps(tags or [])

        # Redundant-save guard: re-saving over a still-fresh entry means the
        # caller skipped check_reference. Block unless they insist (force).
        # _lookup also falls back to a legacy alias-spelled row so a re-save
        # under the canonical topic doesn't fork a duplicate of it.
        existing, _, _ = self._lookup(tech, topic, version)
        if existing is not None and not force and not is_stale(
            version_locked=bool(existing["version_locked"]),
            updated_at=existing["updated_at"],
            ttl_days=existing["ttl_days"],
            now=now,
        ):
            return SaveResult(entry=None, blocked=True, existing_slug=slug)

        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing, _, _ = self._lookup(tech, topic, version)
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
                return SaveResult(
                    # Fetch by id, not by (possibly canonical) slug — a row
                    # found via the legacy-alias fallback keeps its original
                    # slug on refresh, it isn't migrated.
                    entry=ResearchEntry.from_row(
                        conn.execute(
                            "SELECT * FROM research_entries WHERE id=?",
                            (existing["id"],),
                        ).fetchone()
                    ),
                    blocked=False,
                    existing_slug=None,
                )

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
        return SaveResult(
            entry=ResearchEntry.from_row(conn.execute(
                "SELECT * FROM research_entries WHERE id=?", (new_id,)
            ).fetchone()),
            blocked=False,
            existing_slug=None,
        )

    def cache_stats(self, limit: int = 10) -> dict:
        """Hit-rate, top-missed techs (= the research queue), and an estimate
        of tokens saved by cache hits — sourced from ``usage_events``.
        """
        counts = {
            r["event"]: r["n"]
            for r in self.conn.execute(
                "SELECT event, COUNT(*) as n FROM usage_events GROUP BY event"
            ).fetchall()
        }
        hits = counts.get("hit", 0)
        misses = counts.get("miss", 0)
        total = hits + misses
        tokens_saved = self.conn.execute(
            "SELECT COALESCE(SUM(tokens_saved), 0) as s FROM usage_events WHERE event='hit'"
        ).fetchone()["s"]
        top_misses = self.conn.execute(
            "SELECT tech, COUNT(*) as n FROM usage_events WHERE event='miss' "
            "GROUP BY tech ORDER BY n DESC, tech ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": (hits / total) if total else 0.0,
            "tokens_saved_estimate": tokens_saved,
            "top_misses": [{"tech": r["tech"], "count": r["n"]} for r in top_misses],
        }

    def invalidate_reference(self, slug: str) -> bool:
        # Negative ttl forces stale regardless of lock state (see staleness).
        cur = self.conn.execute(
            "UPDATE research_entries SET ttl_days=-1 WHERE slug=?", (slug,)
        )
        self.conn.commit()
        return cur.rowcount > 0
