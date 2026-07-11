"""Embedding provider interface.

The MVP ships with :class:`NullProvider` only (search = FTS5). A real provider
(sentence-transformers -> sqlite-vec) can be swapped in post-MVP without
touching the MCP server — only ``embeddings.py`` and ``repository.search``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Turns text into a vector, or declares itself disabled."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether vector search should be attempted."""

    @abstractmethod
    def embed(self, text: str) -> list[float] | None:
        """Return an embedding for ``text``, or ``None`` when disabled."""


class NullProvider(EmbeddingProvider):
    """Default: no embeddings. Search falls back to FTS5 only."""

    @property
    def enabled(self) -> bool:
        return False

    def embed(self, text: str) -> list[float] | None:
        return None
