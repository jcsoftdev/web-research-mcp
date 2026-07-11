from web_research_mcp.core.updater import check_for_update, parse_remote_version


def test_parse_double_quoted_version():
    assert parse_remote_version('__version__ = "0.2.0"\n') == "0.2.0"


def test_parse_single_quoted_version():
    assert parse_remote_version("__version__ = '1.4.1'") == "1.4.1"


def test_parse_missing_version_is_none():
    assert parse_remote_version("nothing here") is None


def test_update_available_returns_newer():
    assert check_for_update("0.1.0", fetch=lambda: '__version__ = "0.2.0"') == "0.2.0"


def test_no_update_when_same():
    assert check_for_update("0.2.0", fetch=lambda: '__version__ = "0.2.0"') is None


def test_no_update_when_local_is_newer():
    assert check_for_update("0.3.0", fetch=lambda: '__version__ = "0.2.0"') is None


def test_fetch_error_is_swallowed():
    def boom():
        raise OSError("network down")

    assert check_for_update("0.1.0", fetch=boom) is None


def test_unparseable_remote_is_none():
    assert check_for_update("0.1.0", fetch=lambda: "garbage") is None
