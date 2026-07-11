from web_research_mcp.core.versioning import is_newer


def test_newer_major():
    assert is_newer("19", "18") is True


def test_older_major():
    assert is_newer("18", "19") is False


def test_not_lexical_compare():
    # "9" > "18" lexically, but 9 < 18 numerically. This is the whole point.
    assert is_newer("9", "18") is False


def test_minor_versions_numeric():
    assert is_newer("18.10", "18.2") is True
    assert is_newer("18.2", "18.10") is False


def test_equal_is_not_newer():
    assert is_newer("19", "19") is False


def test_real_version_beats_none():
    assert is_newer("19", None) is True


def test_none_never_beats_real():
    assert is_newer(None, "19") is False


def test_none_vs_none_is_not_newer():
    assert is_newer(None, None) is False


def test_prerelease_ordering():
    assert is_newer("19.0.0", "19.0.0rc1") is True


def test_invalid_versions_do_not_raise():
    # A non-PEP440 string is coerced to lowest, never crashes the cache.
    assert is_newer("garbage", "1.0") is False
    assert is_newer("1.0", "garbage") is True
