"""ads_mcp.core.plain: enum names at every depth, 'type' never 'type_' (table-driven)."""

import pytest
from google.ads.googleads.v26.common.types.metrics import Metrics
from google.ads.googleads.v26.common.types.policy import PolicyTopicEntry
from google.ads.googleads.v26.enums.types.campaign_status import (
    CampaignStatusEnum,
)
from google.ads.googleads.v26.enums.types.policy_topic_entry_type import (
    PolicyTopicEntryTypeEnum,
)
from google.ads.googleads.v26.resources.types.ad_group_ad import (
    AdGroupAdPolicySummary,
)
from google.protobuf.field_mask_pb2 import FieldMask

from ads_mcp.core.plain import api_key, rename_keys, to_plain


def _summary():
    s = AdGroupAdPolicySummary(review_status=3, approval_status=3)
    s.policy_topic_entries.append(
        PolicyTopicEntry(
            topic="SOCIAL_CASINO_GAMES",
            type_=PolicyTopicEntryTypeEnum.PolicyTopicEntryType.LIMITED,
        )
    )
    return s


@pytest.mark.parametrize(
    "key, expected",
    [
        ("type_", "type"),
        ("type", "type"),
        ("format_", "format"),
        ("__proto__", "__proto__"),
    ],
)
def test_api_key(key, expected):
    assert api_key(key) == expected


def test_rename_keys_nested():
    assert rename_keys({"a": [{"type_": 1}], "b": {"c_": {"d": 2}}}) == {
        "a": [{"type": 1}],
        "b": {"c": {"d": 2}},
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        (CampaignStatusEnum.CampaignStatus.ENABLED, "ENABLED"),
        (123, 123),
        ("abc", "abc"),
        ([1, 2], [1, 2]),
        (FieldMask(paths=["a", "b"]), "a,b"),
        (Metrics(clicks=10), {"clicks": 10}),
    ],
)
def test_scalars_and_messages(value, expected):
    got = to_plain(value)
    if isinstance(expected, dict):
        assert {k: got[k] for k in expected} == expected
    else:
        assert got == expected


def test_nested_policy_summary_has_names_and_type_key():
    # Seen live: {"policy_topic_entries":[{"type_":4,...}],"review_status":3,"approval_status":3}
    got = to_plain(_summary())
    assert got["review_status"] == "REVIEWED"
    assert got["approval_status"] == "APPROVED_LIMITED"
    entry = got["policy_topic_entries"][0]
    assert entry["type"] == "LIMITED" and "type_" not in entry
    assert entry["topic"] == "SOCIAL_CASINO_GAMES"


def test_repeated_messages():
    got = to_plain([_summary(), _summary()])
    assert [g["review_status"] for g in got] == ["REVIEWED", "REVIEWED"]


def test_int64_are_numbers_at_every_depth():
    from google.ads.googleads.v26.services.types.google_ads_service import (
        GoogleAdsRow,
    )

    row = GoogleAdsRow()
    row.campaign.id = 123456789012
    row.campaign_budget.amount_micros = 5_000_000
    row.campaign.selective_optimization.conversion_actions.append(
        "customers/1/conversionActions/2"
    )
    got = to_plain(row)
    assert got["campaign"]["id"] == 123456789012
    assert got["campaign_budget"]["amount_micros"] == 5_000_000
    assert to_plain(row.campaign.id) == 123456789012  # the same at the leaf


def test_no_made_up_defaults():
    got = to_plain(AdGroupAdPolicySummary(review_status=3))
    assert got == {
        "review_status": "REVIEWED"
    }  # approval_status unset: absent, not "UNSPECIFIED"
    assert to_plain(Metrics()) == {}
