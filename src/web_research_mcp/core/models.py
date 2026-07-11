"""The ``ResearchEntry`` dataclass and row mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


def _load_json_list(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return json.loads(value)


@dataclass
class ResearchEntry:
    id: int | None
    slug: str
    tech: str
    version: str | None
    topic: str
    summary: str
    content: str
    status_tag: str
    sources: list[str]
    tags: list[str]
    version_locked: bool
    is_latest: bool
    superseded_by: int | None
    ttl_days: int | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ResearchEntry":
        """Build an entry from a SQLite row (or dict), coercing JSON and ints."""
        return cls(
            id=row["id"],
            slug=row["slug"],
            tech=row["tech"],
            version=row["version"],
            topic=row["topic"],
            summary=row["summary"],
            content=row["content"],
            status_tag=row["status_tag"],
            sources=_load_json_list(row["sources"]),
            tags=_load_json_list(row["tags"]),
            version_locked=bool(row["version_locked"]),
            is_latest=bool(row["is_latest"]),
            superseded_by=row["superseded_by"],
            ttl_days=row["ttl_days"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
