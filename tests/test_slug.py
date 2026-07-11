from web_research_mcp.core.slug import make_slug, parse_slug, normalize_segment


def test_make_slug_three_segments_normalized():
    assert make_slug("React", "19", "Server Components") == "react/19/server-components"


def test_make_slug_omits_version_when_none():
    assert make_slug("react", None, "hooks") == "react/hooks"


def test_make_slug_treats_empty_version_as_none():
    assert make_slug("react", "", "hooks") == "react/hooks"


def test_parse_slug_three_segments():
    assert parse_slug("react/19/server-components") == ("react", "19", "server-components")


def test_parse_slug_two_segments_has_no_version():
    assert parse_slug("react/hooks") == ("react", None, "hooks")


def test_round_trip_with_version():
    slug = make_slug("Next.js", "15.1", "App Router")
    assert parse_slug(slug) == ("next.js", "15.1", "app-router")


def test_round_trip_without_version():
    slug = make_slug("Vue", None, "Composition API")
    assert parse_slug(slug) == ("vue", None, "composition-api")


def test_normalize_segment_collapses_and_lowercases():
    assert normalize_segment("  Server   Components!! ") == "server-components"


def test_normalize_segment_keeps_dots_for_versions():
    assert normalize_segment("18.2.0") == "18.2.0"


def test_parse_slug_rejects_single_segment():
    import pytest

    with pytest.raises(ValueError):
        parse_slug("react")
