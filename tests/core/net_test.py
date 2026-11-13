"""core/net.py: https-only URLs and public-address judgement (pure, table-driven)."""

import pytest

from ads_mcp.core.net import (
    Target,
    judge_addresses,
    non_public_reason,
    parse_https_url,
)
from ads_mcp.core.result import Err, Ok


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://cdn.example.com/a/b.png?x=1",
            Target("cdn.example.com", 443, "/a/b.png?x=1"),
        ),
        ("https://cdn.example.com:8443", Target("cdn.example.com", 8443, "/")),
        ("http://cdn.example.com/a.png", None),
        ("file:///etc/passwd", None),
        ("https://user:pw@example.com/", None),
        ("https:///nohost", None),
        ("https://example.com:99999/", None),
        ("https://example.com:0/", None),
        ("https://" + "a" * 3000 + ".com/", None),
    ],
)
def test_parse(url, expected):
    got = parse_https_url(url)
    assert got == Ok(expected) if expected else isinstance(got, Err)


@pytest.mark.parametrize(
    "address, public",
    [
        ("8.8.8.8", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.1.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier NAT
        ("0.0.0.0", False),
        ("::1", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:8.8.8.8", True),
        ("64:ff9b::a00:1", False),  # NAT64 of 10.0.0.1
        ("2002:7f00:1::", False),  # 6to4 of 127.0.0.1
        ("224.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_public(address, public):
    assert (non_public_reason(address) is None) is public


@pytest.mark.parametrize(
    "addresses, ok",
    [
        (("8.8.8.8",), "8.8.8.8"),
        (("8.8.8.8", "1.1.1.1"), "8.8.8.8"),
        (
            ("8.8.8.8", "10.0.0.1"),
            None,
        ),  # one private record: refuse, never pick
        ((), None),
    ],
)
def test_judge(addresses, ok):
    got = judge_addresses("h", addresses)
    assert got == Ok(ok) if ok else isinstance(got, Err)
