"""Version comparison via ``packaging.version`` (PEP 440).

Uses ``packaging`` — never string compare (``"9" > "18"`` lexical bug) and
never strict ``semver`` (chokes on ``"18.2"`` and other non-strict inputs).

A ``None`` version (general / not version-bound) and any non-PEP440 string
coerce to the lowest possible version, so a real version always wins and the
cache never crashes on odd input.
"""

from __future__ import annotations

from packaging.version import InvalidVersion, Version

_LOWEST = Version("0")


def _coerce(value: str | None) -> Version:
    if not value:
        return _LOWEST
    try:
        return Version(value)
    except InvalidVersion:
        return _LOWEST


def is_newer(candidate: str | None, current: str | None) -> bool:
    """Return True iff ``candidate`` is a strictly greater version than ``current``."""
    return _coerce(candidate) > _coerce(current)
