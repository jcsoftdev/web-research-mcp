"""Slug helpers: ``tech/version/topic`` <-> normalized slug.

A slug is the stable, model-facing key for a reference. It has two or three
``/``-separated segments:

- ``tech/version/topic`` when the entry is version-bound.
- ``tech/topic`` when the version is general / not version-bound (NULL).

Segments are normalized (lowercased, collapsed) so callers that spell a topic
differently still land on the same key.
"""

from __future__ import annotations

import re

# Keep alphanumerics, dots (semver: "18.2.0", "next.js") and hyphens.
_ALLOWED = re.compile(r"[^a-z0-9.]+")
_MULTI_HYPHEN = re.compile(r"-{2,}")


def normalize_segment(value: str) -> str:
    """Lowercase, trim, and collapse a single slug segment.

    Whitespace and disallowed characters become hyphens; runs of hyphens
    collapse to one; leading/trailing hyphens are stripped. Dots survive so
    version strings stay intact.
    """
    lowered = value.strip().lower()
    replaced = _ALLOWED.sub("-", lowered)
    collapsed = _MULTI_HYPHEN.sub("-", replaced)
    return collapsed.strip("-")


def make_slug(tech: str, version: str | None, topic: str) -> str:
    """Build a normalized slug from its parts.

    An empty or ``None`` version yields a two-segment ``tech/topic`` slug.
    """
    tech_n = normalize_segment(tech)
    topic_n = normalize_segment(topic)
    if version:
        version_n = normalize_segment(version)
        if version_n:
            return f"{tech_n}/{version_n}/{topic_n}"
    return f"{tech_n}/{topic_n}"


def parse_slug(slug: str) -> tuple[str, str | None, str]:
    """Split a slug back into ``(tech, version, topic)``.

    Two segments -> version is ``None``. Raises ``ValueError`` for anything
    that is not two or three segments.
    """
    parts = slug.split("/")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], None, parts[1]
    raise ValueError(f"slug must have 2 or 3 segments, got: {slug!r}")
