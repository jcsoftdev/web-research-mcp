"""Lexical topic similarity for the save-time dedup gate.

Catches near-spellings, spacing/plural variants and truncated abbreviations
(``auth`` ~ ``authentication``). It does NOT catch synonyms or non-truncation
abbreviations (``rsc`` ~ ``server-components``) — that needs embeddings, which
plug in later via ``EmbeddingProvider`` without touching this call site.
"""

from __future__ import annotations

from difflib import SequenceMatcher

_CONTAINMENT_SCORE = 0.85


def topic_similarity(a: str, b: str) -> float:
    """Return a 0..1 similarity between two normalized topic strings."""
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # A short topic fully contained in a longer one is usually the same concept
    # spelled shorter (truncated abbreviation), which raw ratio underrates.
    if a and b and (a in b or b in a):
        return max(ratio, _CONTAINMENT_SCORE)
    # Token overlap (Jaccard) catches reordered / partial multi-word topics.
    ta, tb = set(a.split("-")), set(b.split("-"))
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
        return max(ratio, jaccard)
    return ratio


def find_duplicates(topic: str, candidates: list[dict], threshold: float) -> list[dict]:
    """Return candidates whose ``topic`` is similar to ``topic`` (exact excluded).

    Each returned dict is the candidate plus a ``similarity`` score, sorted
    most-similar first.
    """
    out = []
    for c in candidates:
        cand_topic = c["topic"]
        if cand_topic == topic:
            continue
        score = topic_similarity(topic, cand_topic)
        if score >= threshold:
            out.append({**c, "similarity": round(score, 3)})
    out.sort(key=lambda d: d["similarity"], reverse=True)
    return out
