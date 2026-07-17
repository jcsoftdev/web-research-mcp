"""Server-side topic canonicalization.

Hosts invent many spellings for the same recurring topic ("whats-new",
"latest-changes", "new-features"...). Without a canonical form each spelling
becomes a separate cache entry and the redundant-save guard never fires. This
module maps known aliases to one canonical topic before slugs are built or
looked up.

v1 is a static seed map (KISS). DB-backed alias management — letting the host
teach new aliases at runtime — is a deliberate v2.
"""

from __future__ import annotations

from .slug import normalize_segment

TOPIC_ALIASES = {
    "latest-version-features": "latest-version",
    "latest-version-and-changes": "latest-version",
    "version-and-changes": "latest-version",
    "latest-changes": "latest-version",
    "whats-new": "latest-version",
    "what-s-new": "latest-version",
    "new-features": "latest-version",
}


def canonical_topic(topic: str) -> str:
    """Return the canonical form of ``topic``.

    Normalizes first (so "Latest Version Features" matches the alias table),
    then applies the alias lookup. Unknown topics pass through normalized.
    """
    normalized = normalize_segment(topic)
    return TOPIC_ALIASES.get(normalized, normalized)


_ALIASES_BY_CANONICAL: dict[str, list[str]] = {}
for _alias, _canonical in TOPIC_ALIASES.items():
    _ALIASES_BY_CANONICAL.setdefault(_canonical, []).append(_alias)


def aliases_for(canonical: str) -> list[str]:
    """Alias spellings that canonicalize to ``canonical``.

    Used to find rows saved under an alias spelling before canonicalization
    existed — a lookup on the canonical form alone can't see them, since
    their ``topic`` column still holds the literal alias string.
    """
    return list(_ALIASES_BY_CANONICAL.get(canonical, []))
