"""Thin MCP tool layer over :class:`Repository`.

Every tool delegates to the repository and shapes the response so the host
model cannot overlook freshness: ``status_tag`` and ``stale`` come first as
structured fields, and a stale entry carries an explicit ``advice`` field.
A cache miss is a flat ``{"exists": False}`` — no auto-exploration, no
suggestion to hit the web. That decision belongs to the user.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..config import load_config
from ..core.models import ResearchEntry
from ..core.repository import Repository
from ..core.staleness import is_stale
from ..core.store import connect

STALE_ADVICE = (
    "This reference may be outdated; confirm with a quick web search before writing code."
)

INSTRUCTIONS = (
    "This server is a cache of previously researched, version-aware technology "
    "references. It never browses the web itself. Before writing code for a "
    "tracked technology, call check_reference(tech, topic) first; if it exists "
    "and is not stale, load it with get_reference(slug). Use list_tree to "
    "discover what is cached and search_reference when you don't know the exact "
    "topic. Only research the web (and then save_research) when the user asks."
)


def _shape_check(res: dict) -> dict:
    if not res.get("exists"):
        return {"exists": False}
    shaped = {
        "status_tag": res["status_tag"],
        "stale": res["stale"],
        "exists": True,
        "slug": res["slug"],
        "is_latest": res["is_latest"],
        "resolved_version": res["resolved_version"],
    }
    if res["stale"]:
        shaped["advice"] = STALE_ADVICE
    return shaped


def _shape_get(entry: ResearchEntry | None) -> dict:
    if entry is None:
        return {"exists": False}
    stale = is_stale(
        version_locked=entry.version_locked,
        updated_at=entry.updated_at,
        ttl_days=entry.ttl_days,
    )
    shaped = {
        "status_tag": entry.status_tag,
        "stale": stale,
        "exists": True,
        "slug": entry.slug,
        "resolved_version": entry.version,
        "is_latest": entry.is_latest,
        "summary": entry.summary,
        "sources": entry.sources,
        "content": entry.content,
    }
    if stale:
        shaped["advice"] = STALE_ADVICE
    return shaped


def build_server(repo: Repository) -> FastMCP:
    mcp = FastMCP("web-research-mcp", instructions=INSTRUCTIONS)

    @mcp.tool()
    def list_tree(tech: str | None = None) -> dict:
        """Cached hierarchy tech -> version -> topics (names + flags, no content)."""
        return {"tree": repo.list_tree(tech=tech)}

    @mcp.tool()
    def check_reference(tech: str, topic: str, version: str | None = None) -> dict:
        """Cheap existence/freshness check. No content. Omit version for the latest."""
        return _shape_check(repo.check_reference(tech, topic, version=version))

    @mcp.tool()
    def get_reference(slug: str, section: str | None = None) -> dict:
        """Full cached doc for a slug. Optional section returns one heading block."""
        return _shape_get(repo.get_reference(slug, section=section))

    @mcp.tool()
    def search_reference(query: str, tech: str | None = None) -> dict:
        """Full-text search over cached references when the exact topic is unknown."""
        return {"results": repo.search_reference(query, tech=tech)}

    @mcp.tool()
    def save_research(
        tech: str,
        version: str | None,
        topic: str,
        summary: str,
        content: str,
        sources: list[str],
        status_tag: str,
        version_locked: bool = False,
        tags: list[str] | None = None,
    ) -> dict:
        """Store host-researched docs. Supersedes older versions atomically."""
        entry = repo.save_research(
            tech=tech,
            version=version,
            topic=topic,
            summary=summary,
            content=content,
            sources=sources,
            status_tag=status_tag,
            version_locked=version_locked,
            tags=tags,
        )
        return {"slug": entry.slug, "is_latest": entry.is_latest, "saved": True}

    @mcp.tool()
    def invalidate_reference(slug: str) -> dict:
        """Force a reference stale so the next check advises re-research."""
        return {"invalidated": repo.invalidate_reference(slug)}

    return mcp


def main() -> None:
    cfg = load_config()
    repo = Repository(connect(cfg.db_path), default_ttl_days=cfg.default_ttl_days)
    build_server(repo).run()


if __name__ == "__main__":
    main()
