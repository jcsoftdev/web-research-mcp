"""Thin MCP tool layer over :class:`Repository`.

Every tool delegates to the repository and shapes the response so the host
model cannot overlook freshness: ``status_tag`` and ``stale`` come first as
structured fields, and a stale entry carries an explicit ``advice`` field.
A cache miss is a flat ``{"exists": False}`` — no auto-exploration, no
suggestion to hit the web. That decision belongs to the user.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..config import load_config
from ..core.models import ResearchEntry
from ..core.repository import Repository
from ..core.staleness import is_stale
from ..core.store import connect
from ..core.updater import update_status

STALE_ADVICE = (
    "This reference may be outdated; confirm with a quick web search before writing code."
)

UPDATE_ADVICE = (
    "Delegate a background agent to run `command`. It updates the server without "
    "blocking the user and takes effect on the next restart."
)

INSTRUCTIONS_BASE = (
    "This server is a cache of previously researched, version-aware technology "
    "references. It never browses the web itself. Before writing code for a "
    "tracked technology, call check_reference(tech, topic) first; if it exists "
    "and is not stale, load it with get_reference(slug). Use list_tree to "
    "discover what is cached and search_reference when you don't know the exact "
    "topic. Only research the web (and then save_research) when the user asks."
)

UPDATE_INSTRUCTION = (
    "\n\nOn connecting, call check_for_update once. If it reports update_available, "
    "delegate a background agent to run the returned command so this server stays "
    "on the latest version — do not block the user or the current task."
)


def _coerce_version(version) -> str | None:
    """Accept a version as string or number (models often pass 19, not "19")."""
    if version is None or version == "":
        return None
    return str(version)


def _coerce_sources(sources) -> list[str]:
    """Accept sources as a list, a single URL string, or nothing."""
    if sources is None:
        return []
    if isinstance(sources, str):
        return [sources]
    return [str(s) for s in sources]


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


def build_server(repo: Repository, update_checker=None, advertise_updates: bool = True) -> FastMCP:
    instructions = INSTRUCTIONS_BASE + (UPDATE_INSTRUCTION if advertise_updates else "")
    mcp = FastMCP("web-research-mcp", instructions=instructions)
    check_update = update_checker or (lambda: update_status(__version__))

    @mcp.tool()
    def list_tree(tech: str | None = None) -> dict:
        """Cached hierarchy tech -> version -> topics (names + flags, no content)."""
        return {"tree": repo.list_tree(tech=tech)}

    @mcp.tool()
    def check_reference(tech: str, topic: str, version: str | int | float | None = None) -> dict:
        """Cheap existence/freshness check. No content. Omit version for the latest."""
        return _shape_check(repo.check_reference(tech, topic, version=_coerce_version(version)))

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
        topic: str,
        summary: str,
        content: str,
        version: str | int | float | None = None,
        sources: list[str] | str | None = None,
        status_tag: str = "current",
        version_locked: bool = False,
        tags: list[str] | None = None,
    ) -> dict:
        """Store host-researched docs. Supersedes older versions atomically.

        Only tech/topic/summary/content are required. Omit version for a general
        (not version-bound) reference; status_tag defaults to "current".
        """
        entry = repo.save_research(
            tech=tech,
            version=_coerce_version(version),
            topic=topic,
            summary=summary,
            content=content,
            sources=_coerce_sources(sources),
            status_tag=status_tag,
            version_locked=version_locked,
            tags=tags,
        )
        return {"slug": entry.slug, "is_latest": entry.is_latest, "saved": True}

    @mcp.tool()
    def invalidate_reference(slug: str) -> dict:
        """Force a reference stale so the next check advises re-research."""
        return {"invalidated": repo.invalidate_reference(slug)}

    @mcp.tool()
    def check_for_update() -> dict:
        """Whether a newer server version exists on GitHub.

        If update_available, the host should delegate a background agent to run
        the returned `command` so the server stays current.
        """
        status = check_update()
        if status.get("update_available"):
            status["advice"] = UPDATE_ADVICE
        return status

    return mcp


def main() -> None:
    cfg = load_config()
    repo = Repository(connect(cfg.db_path), default_ttl_days=cfg.default_ttl_days)
    build_server(repo, advertise_updates=cfg.auto_update).run()


if __name__ == "__main__":
    main()
