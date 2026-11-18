"""Audience tools against a fake Google: reads first, one atomic Mutate, plans, refusals."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp import plans
from ads_mcp.tools import adnero_audience_signals as sig
from ads_mcp.tools import adnero_audience_writes as w
from ads_mcp.tools import adnero_audiences as r
from ads_mcp.tools.search import SearchResult


def page(rows):
    return SearchResult(
        rows=rows, row_count=len(rows), stopped_by="END", query="q"
    )


AUDIENCE = {
    "audience.id": 1,
    "audience.name": "Slots 25-54",
    "audience.status": "ENABLED",
    "audience.scope": "CUSTOMER",
    "audience.dimensions": [
        {"age": {"age_ranges": [{"min_age": 25, "max_age": 54}]}},
        {
            "audience_segments": {
                "segments": [
                    {
                        "user_interest": {
                            "user_interest_category": "customers/1/userInterests/80"
                        }
                    }
                ]
            }
        },
    ],
}


class FakeAccount:
    def __init__(
        self,
        channel="MULTI_CHANNEL",
        sub="APP_CAMPAIGN",
        existing=(),
        audience=None,
    ):
        self.channel, self.sub, self.existing = channel, sub, list(existing)
        self.audience = audience or AUDIENCE
        self.queries = []

    def __call__(self, cid, query, max_rows, token, login):
        self.queries.append(query)
        camp = {
            "campaign.id": 7,
            "campaign.advertising_channel_type": self.channel,
            "campaign.advertising_channel_sub_type": self.sub,
        }
        if "FROM audience" in query:
            return page([self.audience])
        if "FROM campaign " in query:
            return page([camp])
        if "FROM ad_group " in query:
            groups = [
                {"ad_group.id": 10, "ad_group.name": "AG A", **camp},
                {"ad_group.id": 11, "ad_group.name": "AG B", **camp},
            ]
            return page(
                [
                    g
                    for g in groups
                    if f"ad_group.id = {g['ad_group.id']}" in query
                    or "ad_group.id =" not in query
                ]
            )
        if "FROM asset_group " in query:
            return page(
                [{"asset_group.id": 20, "asset_group.name": "PG", **camp}]
            )
        if (
            "FROM ad_group_criterion" in query
            or "FROM asset_group_signal" in query
        ):
            return page(self.existing)
        if "FROM user_interest" in query:
            return page(
                [
                    {
                        "user_interest.user_interest_id": 80,
                        "user_interest.name": "Casino & Slots Fans",
                        "user_interest.taxonomy_type": "AFFINITY",
                        "user_interest.user_interest_parent": "customers/1/userInterests/3",
                    }
                ]
            )
        if "FROM " in query:
            return page([])
        raise AssertionError(query)


@pytest.fixture(scope="module")
def gads():
    return GoogleAdsClient(
        credentials=AnonymousCredentials(), use_proto_plus=True, version="v26"
    )


@pytest.fixture
def google(gads, monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_PLAN_SECRET", "s" * 40)
    plans.reset()
    sent = []

    def mutate(request):
        sent.append(
            SimpleNamespace(
                validate_only=request.validate_only,
                ops=list(request.mutate_operations),
            )
        )
        return SimpleNamespace(mutate_operation_responses=[])

    def mutate_custom(request):
        sent.append(
            SimpleNamespace(
                validate_only=request.validate_only,
                ops=list(request.operations),
            )
        )
        return SimpleNamespace(
            results=[
                SimpleNamespace(resource_name="customers/1/customAudiences/5")
            ]
        )

    service = MagicMock()
    service.mutate.side_effect = mutate
    service.mutate_custom_audiences.side_effect = mutate_custom
    with (
        patch("ads_mcp.adnero_mutate.client", return_value=gads),
        patch(
            "ads_mcp.utils.get_googleads_service_from_client",
            return_value=service,
        ),
    ):
        yield sent
    plans.reset()


def account(**kw):
    fake = FakeAccount(**kw)
    return fake, patch("ads_mcp.tools.search.search_page", side_effect=fake)


def kinds(ops):
    return [op._pb.WhichOneof("operation") for op in ops]


def test_create_previews_then_applies_one_audience(google):
    args = dict(
        customer_id="1",
        name="Slots 25-54",
        age_ranges=["25-54"],
        interest_ids=["80"],
    )
    proposal = w.create(**args)
    assert proposal.status == "PROPOSED"
    assert any(
        i.field == "Ages" and i.value == "25-54" for i in proposal.change_items
    )
    applied = w.create(**args, validate_only=False, plan_id=proposal.plan_id)
    assert applied.status == "APPLIED"
    preview, apply = google
    assert preview.validate_only and not apply.validate_only
    assert kinds(apply.ops) == ["audience_operation"]
    a = apply.ops[0].audience_operation.create
    assert a.name == "Slots 25-54" and len(a.dimensions) == 2
    age = a.dimensions[0].age.age_ranges[0]
    assert (age.min_age, age.max_age) == (25, 54)
    seg = a.dimensions[1].audience_segments.segments[0]
    assert (
        seg.user_interest.user_interest_category
        == "customers/1/userInterests/80"
    )
    assert not a.exclusion_dimension.exclusions


def test_create_open_age_and_demographics(google):
    w.create(
        customer_id="1",
        name="x",
        age_ranges=["65+"],
        genders=["female"],
        parental_statuses=["PARENT"],
        income_ranges=["INCOME_RANGE_90_UP"],
        include_undetermined=True,
        exclude_user_list_ids=["9"],
    )
    a = google[0].ops[0].audience_operation.create
    age = a.dimensions[0].age
    assert (
        age.age_ranges[0].min_age == 65 and "max_age" not in age.age_ranges[0]
    )
    assert age.include_undetermined is True
    assert [
        d.gender.genders[0].name for d in a.dimensions if "gender" in d
    ] == ["FEMALE"]
    assert (
        a.exclusion_dimension.exclusions[0].user_list.user_list
        == "customers/1/userLists/9"
    )


def test_create_refuses_bad_input_before_google(google):
    with pytest.raises(ToolError, match="age range"):
        w.create(customer_id="1", name="x", age_ranges=["20-30"])
    with pytest.raises(ToolError, match="at least one dimension"):
        w.create(customer_id="1", name="x")
    assert google == []


def test_a_changed_argument_voids_the_plan(google):
    proposal = w.create(customer_id="1", name="x", age_ranges=["25-54"])
    with pytest.raises(ToolError):
        w.create(
            customer_id="1",
            name="x",
            age_ranges=["25-34"],
            validate_only=False,
            plan_id=proposal.plan_id,
        )


def test_update_keeps_what_is_not_passed(google):
    _, p = account()
    with p:
        proposal = w.update(customer_id="1", audience_id="1", genders=["MALE"])
    op = google[0].ops[0].audience_operation
    assert list(op.update_mask.paths) == [
        "name",
        "description",
        "dimensions",
        "exclusion_dimension",
    ]
    assert op.update.resource_name == "customers/1/audiences/1"
    present = [d._pb.WhichOneof("dimension") for d in op.update.dimensions]
    assert present == ["age", "gender", "audience_segments"]
    assert [(i.field, i.before, i.value) for i in proposal.change_items] == [
        ("Genders", "(none)", "MALE")
    ]


def test_update_replaces_one_segment_kind_and_clears_ages(google):
    _, p = account()
    with p:
        w.update(
            customer_id="1",
            audience_id="1",
            age_ranges=[],
            interest_ids=["81", "82"],
        )
    dims = google[0].ops[0].audience_operation.update.dimensions
    assert [d._pb.WhichOneof("dimension") for d in dims] == [
        "audience_segments"
    ]
    assert [
        s.user_interest.user_interest_category
        for s in dims[0].audience_segments.segments
    ] == ["customers/1/userInterests/81", "customers/1/userInterests/82"]


def test_update_with_nothing_new_is_refused(google):
    _, p = account()
    with p, pytest.raises(ToolError, match="nothing to change"):
        w.update(customer_id="1", audience_id="1", age_ranges=["25-54"])


def test_attach_to_every_app_install_ad_group(google):
    fake, p = account()
    with p:
        proposal = sig.signal_attach(
            customer_id="1", audience_id="1", campaign_id="7"
        )
        sig.signal_attach(
            customer_id="1",
            audience_id="1",
            campaign_id="7",
            validate_only=False,
            plan_id=proposal.plan_id,
        )
    apply = google[1]
    assert kinds(apply.ops) == ["ad_group_criterion_operation"] * 2
    c = apply.ops[0].ad_group_criterion_operation.create
    assert (
        c.ad_group == "customers/1/adGroups/10"
        and c.audience.audience == "customers/1/audiences/1"
    )
    assert not c.negative and not c.bid_modifier
    assert any("FROM audience" in q for q in fake.queries)


def test_attach_refuses_a_second_audience_unless_replace(google):
    existing = [
        {
            "ad_group_criterion.resource_name": "customers/1/adGroupCriteria/10~99",
            "ad_group_criterion.ad_group": "customers/1/adGroups/10",
            "ad_group_criterion.audience.audience": "customers/1/audiences/2",
        }
    ]
    _, p = account(existing=existing)
    with p:
        with pytest.raises(ToolError, match="replace=true"):
            sig.signal_attach(
                customer_id="1", audience_id="1", ad_group_id="10"
            )
        sig.signal_attach(
            customer_id="1", audience_id="1", ad_group_id="10", replace=True
        )
    ops = google[0].ops
    assert kinds(ops) == [
        "ad_group_criterion_operation",
        "ad_group_criterion_operation",
    ]
    assert (
        ops[0].ad_group_criterion_operation.remove
        == "customers/1/adGroupCriteria/10~99"
    )


def test_attach_to_performance_max_asset_groups(google):
    _, p = account(channel="PERFORMANCE_MAX", sub="")
    with p:
        sig.signal_attach(customer_id="1", audience_id="1", campaign_id="7")
    op = google[0].ops[0]
    assert kinds([op]) == ["asset_group_signal_operation"]
    s = op.asset_group_signal_operation.create
    assert (
        s.asset_group == "customers/1/assetGroups/20"
        and s.audience.audience == "customers/1/audiences/1"
    )


@pytest.mark.parametrize(
    "sub, words",
    [
        ("APP_CAMPAIGN_FOR_ENGAGEMENT", "user list"),
        ("APP_CAMPAIGN_FOR_PRE_REGISTRATION", "no criteria"),
    ],
)
def test_attach_refuses_other_app_kinds(google, sub, words):
    _, p = account(sub=sub)
    with p, pytest.raises(ToolError, match=words):
        sig.signal_attach(customer_id="1", audience_id="1", campaign_id="7")
    assert google == []


def test_attach_needs_exactly_one_target(google):
    _, p = account()
    with p, pytest.raises(ToolError, match="exactly one"):
        sig.signal_attach(
            customer_id="1", audience_id="1", campaign_id="7", ad_group_id="10"
        )


def test_detach_removes_only_that_audience(google):
    existing = [
        {
            "asset_group_signal.resource_name": "customers/1/assetGroupSignals/20~5",
            "asset_group_signal.asset_group": "customers/1/assetGroups/20",
            "asset_group_signal.audience.audience": "customers/1/audiences/1",
        },
        {
            "asset_group_signal.resource_name": "customers/1/assetGroupSignals/20~6",
            "asset_group_signal.asset_group": "customers/1/assetGroups/20",
            "asset_group_signal.audience.audience": "customers/1/audiences/2",
        },
    ]
    _, p = account(channel="PERFORMANCE_MAX", sub="", existing=existing)
    with p:
        sig.signal_detach(customer_id="1", audience_id="1", asset_group_id="20")
    ops = google[0].ops
    assert [o.asset_group_signal_operation.remove for o in ops] == [
        "customers/1/assetGroupSignals/20~5"
    ]


def test_signals_list_shows_each_target(google):
    existing = [
        {
            "ad_group_criterion.resource_name": "customers/1/adGroupCriteria/11~3",
            "ad_group_criterion.ad_group": "customers/1/adGroups/11",
            "ad_group_criterion.audience.audience": "customers/1/audiences/1",
        }
    ]
    _, p = account(existing=existing)
    with p:
        out = r.signals_list(customer_id="1", campaign_id="7")
    assert [
        (t["id"], [a["audience_id"] for a in t["audiences"]])
        for t in out.targets
    ] == [("10", []), ("11", ["1"])]


def test_segments_search_says_where_each_id_goes(google):
    fake, p = account()
    with p:
        out = r.segments_search(
            customer_id="1", text="slot", kinds=["AFFINITY", "USER_LIST"]
        )
    hit = out.segments[0]
    assert (
        hit["kind"],
        hit["id"],
        hit["pass_as"],
        hit["user_interest_parent"],
    ) == ("AFFINITY", "80", "interest_ids", "3")
    assert (
        "REGEXP_MATCH '(?i).*slot.*'" in fake.queries[0]
        and "taxonomy_type = 'AFFINITY'" in fake.queries[0]
    )
    assert len(fake.queries) == 2


def test_audiences_list(google):
    fake, p = account()
    with p:
        out = r.list_audiences(customer_id="1", name_contains="slots")
    assert out.audiences[0]["name"] == "Slots 25-54"
    assert "audience.status = 'ENABLED'" in fake.queries[0]


def test_custom_segment_goes_through_its_own_service(google):
    args = dict(
        customer_id="1",
        name="Slot players",
        keywords=["online slots", "free spins"],
        apps=["com.playtika.slotomania"],
    )
    proposal = w.custom_segment_create(**args)
    applied = w.custom_segment_create(
        **args, validate_only=False, plan_id=proposal.plan_id
    )
    assert applied.resource_names == ["customers/1/customAudiences/5"]
    ca = google[1].ops[0].create
    assert ca.type_.name == "AUTO"
    assert [(m.member_type.name, m.keyword or m.app) for m in ca.members] == [
        ("KEYWORD", "online slots"),
        ("KEYWORD", "free spins"),
        ("APP", "com.playtika.slotomania"),
    ]


def test_custom_segment_refuses_legacy_types_and_long_keywords(google):
    with pytest.raises(ToolError, match="AUTO or SEARCH"):
        w.custom_segment_create(
            customer_id="1", name="x", keywords=["a"], type="INTEREST"
        )
    with pytest.raises(ToolError, match="10 words"):
        w.custom_segment_create(customer_id="1", name="x", keywords=["a " * 11])
    with pytest.raises(ToolError, match="at least 1"):
        w.custom_segment_create(customer_id="1", name="x")
