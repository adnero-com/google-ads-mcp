"""ads_mcp.core.gaql: query forms, cursors, bounded pages (pure, table-driven)."""

import pytest

from ads_mcp.core import gaql
from ads_mcp.core.result import Err, Ok


@pytest.mark.parametrize(
    "args, expected",
    [
        (("SELECT a.b FROM a", None, None), Ok("SELECT a.b FROM a")),
        (("  SELECT a.b FROM a  ", None, None), Ok("SELECT a.b FROM a")),
        (
            (None, "a", ["a.b", " ", "a.c"]),
            Ok(
                "SELECT a.b,a.c FROM a PARAMETERS omit_unselected_resource_names=true"
            ),
        ),
        ((None, None, None), Err),
        (("SELECT a.b FROM a", "a", ["a.b"]), Err),
        (("select_x", None, None), Err),
        ((None, "a", []), Err),
        ((None, "", ["a.b"]), Err),
    ],
)
def test_choose_query(args, expected):
    got = gaql.choose_query(*args)
    if expected is Err:
        assert isinstance(got, Err)
    else:
        assert got == expected


@pytest.mark.parametrize(
    "value, ok",
    [
        (None, 500),
        (1, 1),
        (10_000, 10_000),
        (0, None),
        (10_001, None),
        (True, None),
    ],
)
def test_check_max_rows(value, ok):
    got = gaql.check_max_rows(value)
    assert (got.value if isinstance(got, Ok) else None) == ok


@pytest.mark.parametrize(
    "cursor",
    [
        gaql.Cursor("abc", "", 0),
        gaql.Cursor("abc", "CAESg/=+", 9_999),
    ],
)
def test_cursor_round_trip(cursor):
    assert gaql.decode_cursor(gaql.encode_cursor(cursor), "abc") == Ok(cursor)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "!!!",
        "e30",
        gaql.encode_cursor(gaql.Cursor("zzz", "", 0)),
        "WyJhYmMiLCIiLC0xXQ",
    ],
)
def test_bad_cursors(token):
    assert isinstance(gaql.decode_cursor(token, "abc"), Err)


def test_fingerprint_binds_customer_and_query():
    assert gaql.fingerprint("1", "q") == gaql.fingerprint("1", "q")
    assert gaql.fingerprint("1", "q") != gaql.fingerprint("2", "q")
    assert gaql.fingerprint("1", "q") != gaql.fingerprint("1", "q2")


@pytest.mark.parametrize(
    "n, offset, max_rows, cap, rows, stop",
    [
        (5, 0, 10, 1000, [0, 1, 2, 3, 4], "END"),
        (5, 0, 5, 1000, [0, 1, 2, 3, 4], "END"),
        (5, 0, 2, 1000, [0, 1], "MAX_ROWS"),
        (5, 3, 10, 1000, [3, 4], "END"),
        (5, 5, 10, 1000, [], "END"),
        (5, 0, 10, 25, [0, 1], "BYTE_CAP"),
        (5, 0, 10, 1, [0], "BYTE_CAP"),  # always at least one row
    ],
)
def test_take_rows(n, offset, max_rows, cap, rows, stop):
    page = gaql.take_rows(
        range(n), offset, max_rows, size_of=lambda r: 10, byte_cap=cap
    )
    assert (list(page.rows), page.stopped_by, page.consumed) == (
        rows,
        stop,
        len(rows),
    )


@pytest.mark.parametrize(
    "stop, consumed, google_next, expected",
    [
        ("MAX_ROWS", 2, "G2", gaql.Cursor("f", "G1", 7)),
        ("BYTE_CAP", 1, "", gaql.Cursor("f", "G1", 6)),
        ("END", 3, "G2", gaql.Cursor("f", "G2", 0)),
        ("END", 3, "", None),
    ],
)
def test_next_cursor(stop, consumed, google_next, expected):
    page = gaql.Page(tuple(range(consumed)), consumed, stop)
    assert (
        gaql.next_cursor(gaql.Cursor("f", "G1", 5), page, google_next)
        == expected
    )


@pytest.mark.parametrize(
    "query, expected",
    [
        (
            "SELECT campaign.id, campaign.name FROM campaign",
            "SELECT campaign.id, campaign.name FROM campaign ORDER BY campaign.id",
        ),
        (
            "SELECT campaign.id FROM campaign WHERE campaign.status = 'ENABLED' LIMIT 5",
            "SELECT campaign.id FROM campaign WHERE campaign.status = 'ENABLED' ORDER BY campaign.id LIMIT 5",
        ),
        (
            "SELECT ad_group.id FROM ad_group PARAMETERS omit_unselected_resource_names=true",
            "SELECT ad_group.id FROM ad_group ORDER BY ad_group.id PARAMETERS omit_unselected_resource_names=true",
        ),
        (
            "SELECT campaign.id FROM campaign ORDER BY campaign.name",
            "SELECT campaign.id FROM campaign ORDER BY campaign.name",
        ),
        (
            "SELECT campaign.name FROM campaign",
            "SELECT campaign.name FROM campaign",
        ),  # no id selected: unchanged
        (
            "SELECT ad_group.id FROM campaign",
            "SELECT ad_group.id FROM campaign",
        ),
    ],
)
def test_stable_order(query, expected):
    assert gaql.stable_order(query) == expected
    assert gaql.is_ordered(gaql.stable_order(query)) is ("ORDER BY" in expected)


def test_unordered_cut_has_no_cursor():
    page = gaql.Page((1, 2), 2, "MAX_ROWS")
    assert (
        gaql.next_cursor(gaql.Cursor("f", "", 0), page, "G2", ordered=False)
        is None
    )
    end = gaql.Page((1,), 1, "END")
    assert gaql.next_cursor(
        gaql.Cursor("f", "", 0), end, "G2", ordered=False
    ) == gaql.Cursor("f", "G2", 0)


@pytest.mark.parametrize(
    "query, expected",
    [
        # 'order by' / 'limit' inside a literal are not clauses
        (
            "SELECT campaign.id FROM campaign WHERE campaign.name = 'order by me'",
            "SELECT campaign.id FROM campaign WHERE campaign.name = 'order by me' ORDER BY campaign.id",
        ),
        (
            "SELECT campaign.id FROM campaign WHERE campaign.name = 'x LIMIT 5' LIMIT 3",
            "SELECT campaign.id FROM campaign WHERE campaign.name = 'x LIMIT 5' ORDER BY campaign.id LIMIT 3",
        ),
        (
            'SELECT campaign.id FROM campaign WHERE campaign.name LIKE "%ORDER BY%"',
            'SELECT campaign.id FROM campaign WHERE campaign.name LIKE "%ORDER BY%" ORDER BY campaign.id',
        ),
    ],
)
def test_literals_are_ignored(query, expected):
    assert gaql.stable_order(query) == expected


@pytest.mark.parametrize(
    "query, within",
    [
        ("SELECT campaign.id FROM campaign ORDER BY campaign.id", True),
        (
            "SELECT campaign.id, segments.date FROM campaign ORDER BY campaign.id",
            False,
        ),
        ("SELECT campaign.id, segments.device FROM campaign", False),
        ("SELECT campaign.name FROM campaign", False),
        (
            "SELECT campaign.name FROM campaign WHERE campaign.name = 'order by'",
            False,
        ),
    ],
)
def test_pages_within(query, within):
    assert gaql.pages_within(gaql.stable_order(query)) is within
