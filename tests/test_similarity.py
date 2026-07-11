from web_research_mcp.core.similarity import find_duplicates, topic_similarity


def test_identical_topics_score_one():
    assert topic_similarity("hooks", "hooks") == 1.0


def test_near_spelling_is_high():
    # plural / trailing char — same concept
    assert topic_similarity("server-components", "server-component") >= 0.6


def test_spacing_variant_is_high():
    assert topic_similarity("server-components", "servercomponents") >= 0.6


def test_truncated_abbreviation_via_containment():
    # "auth" is a truncation of "authentication"
    assert topic_similarity("auth", "authentication") >= 0.6


def test_unrelated_topics_are_low():
    assert topic_similarity("hooks", "routing") < 0.4


def test_symmetric():
    assert topic_similarity("auth", "authentication") == topic_similarity("authentication", "auth")


def test_find_duplicates_returns_over_threshold_sorted():
    cands = [
        {"slug": "react/19/server-components", "topic": "server-components"},
        {"slug": "react/19/routing", "topic": "routing"},
        {"slug": "react/19/server-component", "topic": "server-component"},
    ]
    dups = find_duplicates("servercomponents", cands, threshold=0.6)
    topics = [d["topic"] for d in dups]
    assert "routing" not in topics
    assert set(topics) == {"server-components", "server-component"}
    # sorted by similarity descending
    assert dups[0]["similarity"] >= dups[-1]["similarity"]


def test_find_duplicates_excludes_exact_match():
    cands = [{"slug": "react/19/hooks", "topic": "hooks"}]
    assert find_duplicates("hooks", cands, threshold=0.6) == []
