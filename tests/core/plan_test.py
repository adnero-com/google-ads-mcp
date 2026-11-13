"""core/plan.py: HMAC plans bound to the digest, expiring, unforgeable (pure, table-driven)."""

import pytest

from ads_mcp.core import plan
from ads_mcp.core.result import Err, Ok

KEY = b"k" * 32
D = plan.digest_of(
    "set_budget", "123", {"campaign_id": "9", "daily_budget_micros": 5}
)
NOW = 1_800_000_000
GOOD = plan.issue(KEY, D, NOW)


@pytest.mark.parametrize(
    "plan_id, digest, now, key, expected",
    [
        (GOOD, D, NOW, KEY, Ok(NOW + 900)),
        (GOOD.upper(), D, NOW, KEY, Ok(NOW + 900)),
        (f"  {GOOD} ", D, NOW + 900, KEY, Ok(NOW + 900)),
        (GOOD, D, NOW + 901, KEY, Err("EXPIRED")),
        (
            GOOD,
            plan.digest_of(
                "set_budget",
                "123",
                {"campaign_id": "9", "daily_budget_micros": 6},
            ),
            NOW,
            KEY,
            Err("MISMATCH"),
        ),
        (
            GOOD,
            plan.digest_of(
                "set_budget",
                "124",
                {"campaign_id": "9", "daily_budget_micros": 5},
            ),
            NOW,
            KEY,
            Err("MISMATCH"),
        ),
        (
            GOOD,
            plan.digest_of(
                "set_bids",
                "123",
                {"campaign_id": "9", "daily_budget_micros": 5},
            ),
            NOW,
            KEY,
            Err("MISMATCH"),
        ),
        (GOOD, D, NOW, b"other-key" * 4, Err("MISMATCH")),
        # a model that recomputes the old unkeyed sha256 cannot pass it off as a plan
        (D, D, NOW, KEY, Err("MALFORMED")),
        # nor move the expiry: the MAC covers it
        (
            GOOD.replace(str(NOW + 900), str(NOW + 99_999)),
            D,
            NOW + 1000,
            KEY,
            Err("MISMATCH"),
        ),
        (None, D, NOW, KEY, Err("MISSING")),
        ("", D, NOW, KEY, Err("MISSING")),
        ("p1.x.abc", D, NOW, KEY, Err("MALFORMED")),
        ("p2.1.abc", D, NOW, KEY, Err("MALFORMED")),
    ],
)
def test_verify(plan_id, digest, now, key, expected):
    assert plan.verify(key, plan_id, digest, now) == expected


@pytest.mark.parametrize(
    "a, b, same",
    [
        ({"x": 1, "y": [1, 2]}, {"y": [1, 2], "x": 1}, True),
        ({"x": 1}, {"x": 1.0}, False),
        ({"x": [1, 2]}, {"x": [2, 1]}, False),
        ({"x": "é"}, {"x": "é"}, True),
    ],
)
def test_digest_canonical(a, b, same):
    assert (plan.digest_of("t", "1", a) == plan.digest_of("t", "1", b)) is same


def test_every_error_has_a_message():
    assert set(plan.MESSAGES) == {"MISSING", "MALFORMED", "EXPIRED", "MISMATCH"}
