"""App writes against a fake Google: one atomic Mutate with temp ids, plans, refusals, read-back."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp import plans
from ads_mcp.tools import adnero_app_campaign_update as cu
from ads_mcp.tools import adnero_app_ad_group as group
from ads_mcp.tools import adnero_app_create as create
from ads_mcp.tools import adnero_app_update as update
from ads_mcp.tools import adnero_campaigns as camp
from ads_mcp.tools.search import SearchResult

APP = "com.example.app"


def page(rows):
    return SearchResult(
        rows=rows, row_count=len(rows), stopped_by="END", query="q"
    )


class FakeAccount:
    """GAQL answers by the FROM resource; the campaign row is configurable."""

    EU = {
        "2276",
        "2380",
        "2250",
    }  # what Google flags as needing the EU declaration, in this fake

    def __init__(
        self,
        sub_type="APP_CAMPAIGN",
        goal="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
        ad=None,
        criteria=(),
        geo_fails=False,
    ):
        self.geo_fails = geo_fails
        self.sub_type, self.goal, self.ad, self.criteria = (
            sub_type,
            goal,
            ad or {},
            list(criteria),
        )
        self.queries = []

    def __call__(self, cid, query, max_rows, token, login):
        self.queries.append(query)
        if "FROM conversion_action" in query:
            return page(
                [
                    {
                        "conversion_action.id": 2,
                        "conversion_action.name": "purchase",
                        "conversion_action.status": "ENABLED",
                        "conversion_action.type": "FIREBASE_ANDROID_IN_APP_PURCHASE",
                        "conversion_action.category": "PURCHASE",
                        "conversion_action.app_id": APP,
                    },
                ]
            )
        if "FROM asset" in query:
            return page(
                [
                    {
                        "asset.id": 10,
                        "asset.type": "IMAGE",
                        "asset.image_asset.full_size.width_pixels": 1200,
                        "asset.image_asset.full_size.height_pixels": 628,
                        "asset.image_asset.file_size": 9,
                        "asset.image_asset.mime_type": "IMAGE_PNG",
                    },
                    {
                        "asset.id": 11,
                        "asset.type": "IMAGE",
                        "asset.image_asset.full_size.width_pixels": 2048,
                        "asset.image_asset.full_size.height_pixels": 2002,
                        "asset.image_asset.file_size": 9,
                        "asset.image_asset.mime_type": "IMAGE_PNG",
                    },
                    {"asset.id": 20, "asset.type": "YOUTUBE_VIDEO"},
                ]
            )
        if "FROM geo_target_constant" in query:
            if self.geo_fails:
                raise ToolError("Google Ads refused the request")
            ids = query.split("IN (")[1].split(")")[0].split(",")
            return page(
                [
                    {
                        "geo_target_constant.id": int(i),
                        "geo_target_constant.require_eu_political_advertising_declaration": i
                        in self.EU,
                    }
                    for i in ids
                ]
            )
        if "FROM user_list" in query:
            return page([{"user_list.id": 50, "user_list.name": "payers"}])
        if "FROM campaign_criterion" in query:
            return page(self.criteria)
        if "FROM campaign " in query:
            return page(
                [
                    {
                        "campaign.id": 7,
                        "campaign.advertising_channel_sub_type": self.sub_type,
                        "campaign.app_campaign_setting.app_id": APP,
                        "campaign.app_campaign_setting.app_store": "GOOGLE_APP_STORE",
                        "campaign.app_campaign_setting.bidding_strategy_goal_type": self.goal,
                        "campaign.start_date_time": "2026-11-01 00:00:00",
                        "campaign_budget.resource_name": "customers/1/campaignBudgets/9",
                        "campaign_budget.amount_micros": "5000000",
                        "campaign_budget.explicitly_shared": False,
                    }
                ]
            )
        if "FROM ad_group_ad" in query and "primary_status" in query:
            return page(
                [
                    {
                        "campaign.status": "PAUSED",
                        "ad_group_ad.status": "ENABLED",
                        "ad_group_ad.policy_summary.review_status": "REVIEW_IN_PROGRESS",
                    }
                ]
            )
        if "FROM ad_group_ad" in query:
            return page(
                [
                    {
                        "campaign.advertising_channel_sub_type": self.sub_type,
                        "campaign.app_campaign_setting.app_id": APP,
                        "campaign.app_campaign_setting.app_store": "GOOGLE_APP_STORE",
                        "campaign.app_campaign_setting.bidding_strategy_goal_type": self.goal,
                        **self.ad,
                    }
                ]
            )
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
                content=request.response_content_type.name,
            )
        )
        names = ["customers/1/campaigns/7", "customers/1/adGroupAds/8~9"]
        return SimpleNamespace(
            mutate_operation_responses=[
                SimpleNamespace(
                    _pb=SimpleNamespace(WhichOneof=lambda _: "x"),
                    x=SimpleNamespace(resource_name=n),
                )
                for n in names
            ]
        )

    service = MagicMock()
    service.mutate.side_effect = mutate
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


BASE = dict(
    customer_id="1",
    kind="install",
    name="ITS Android",
    app_id=APP,
    store="GOOGLE_APP_STORE",
    goal="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
    daily_budget_micros=50_000_000,
    target_cpa_micros=2_000_000,
    geo_target_ids=["2124"],
    excluded_geo_target_ids=["2840"],
    language_ids=["1000"],
    headlines=["Play now", "Win big"],
    descriptions=["Download today."],
    image_asset_ids=["10"],
    youtube_video_asset_ids=["20"],
)


def kinds(ops):
    return [op._pb.WhichOneof("operation") for op in ops]


def test_create_is_one_atomic_request_with_temp_ids(google):
    _, p = account()
    with p:
        proposal = create.campaign_create(**BASE)
        assert proposal.status == "PROPOSED" and proposal.plan_id.startswith(
            "p1."
        )
        applied = create.campaign_create(
            **BASE, validate_only=False, plan_id=proposal.plan_id
        )
    preview, apply = google
    assert preview.validate_only and not apply.validate_only
    assert apply.content == "MUTABLE_RESOURCE"
    assert kinds(apply.ops) == [
        "campaign_budget_operation",
        "campaign_operation",
        "campaign_criterion_operation",
        "campaign_criterion_operation",
        "campaign_criterion_operation",
        "ad_group_operation",
        "ad_group_ad_operation",
    ]
    budget, c = (
        apply.ops[0].campaign_budget_operation.create,
        apply.ops[1].campaign_operation.create,
    )
    assert (
        budget.resource_name.endswith("/-1")
        and budget.explicitly_shared is False
    )
    assert (
        c.campaign_budget == budget.resource_name and c.status.name == "PAUSED"
    )
    assert (
        c.advertising_channel_type.name,
        c.advertising_channel_sub_type.name,
    ) == ("MULTI_CHANNEL", "APP_CAMPAIGN")
    assert (
        c.app_campaign_setting.app_id == APP
        and c.target_cpa.target_cpa_micros == 2_000_000
    )
    assert (
        c.contains_eu_political_advertising.name
        == "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    )
    assert [
        cc.campaign_criterion_operation.create.negative for cc in apply.ops[2:4]
    ] == [False, True]
    group = apply.ops[5].ad_group_operation.create
    ad = apply.ops[6].ad_group_ad_operation.create
    assert ad.ad_group == group.resource_name and ad.status.name == "ENABLED"
    assert [h.text for h in ad.ad.app_ad.headlines] == ["Play now", "Win big"]
    assert [i.asset for i in ad.ad.app_ad.images] == ["customers/1/assets/10"]
    assert (
        applied.read_back["ad_group_ad.policy_summary.review_status"]
        == "REVIEW_IN_PROGRESS"
    )
    assert "customers/1/campaigns/7" in applied.resource_names


@pytest.mark.parametrize(
    "changes, field, check",
    [
        (
            {
                "goal": "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST",
                "target_cpa_micros": None,
            },
            "maximize_conversions",
            None,
        ),
        (
            {
                "goal": "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
                "target_cpa_micros": None,
                "target_roas": 3.0,
                "conversion_action_ids": ["2"],
            },
            "target_roas",
            lambda c: c.target_roas.target_roas == 3.0
            and list(c.selective_optimization.conversion_actions)
            == ["customers/1/conversionActions/2"],
        ),
        (
            {
                "goal": "OPTIMIZE_TOTAL_VALUE_WITHOUT_TARGET_ROAS",
                "target_cpa_micros": None,
                "conversion_action_ids": ["2"],
            },
            "maximize_conversion_value",
            None,
        ),
        (
            {
                "kind": "pre_registration",
                "goal": "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
            },
            "target_cpa",
            lambda c: [
                g.name
                for g in c.optimization_goal_setting.optimization_goal_types
            ]
            == ["APP_PRE_REGISTRATION"],
        ),
    ],
)
def test_goal_sets_the_right_bidding(google, changes, field, check):
    _, p = account()
    with p:
        create.campaign_create(**{**BASE, **changes})
    c = google[-1].ops[1].campaign_operation.create
    assert c._pb.WhichOneof("campaign_bidding_strategy") == field
    assert check is None or check(c)


def test_engagement_adds_the_user_list_and_engagement_ad(google):
    _, p = account()
    with p:
        create.campaign_create(
            **{
                **BASE,
                "kind": "engagement",
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ["2"],
                "user_list_ids": ["50"],
            }
        )
    ops = google[-1].ops
    assert kinds(ops)[-2:] == [
        "ad_group_criterion_operation",
        "ad_group_ad_operation",
    ]
    assert (
        ops[-2].ad_group_criterion_operation.create.user_list.user_list
        == "customers/1/userLists/50"
    )
    ad = ops[-1].ad_group_ad_operation.create.ad
    assert ad._pb.WhichOneof("ad_data") == "app_engagement_ad"
    assert [v.asset for v in ad.app_engagement_ad.videos] == [
        "customers/1/assets/20"
    ]


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"image_asset_ids": ["11"]}, "ASPECT_RATIO_NOT_ALLOWED"),
        (
            {
                "kind": "engagement",
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ["2"],
            },
            "USER_LIST_REQUIRED",
        ),
        ({"target_cpa_micros": None}, "TARGET_REQUIRED"),
        ({"daily_budget_micros": 0}, "above 0"),
        ({"start_date": "2026-13-01"}, "real date"),
        (
            {
                "store": "APPLE_APP_STORE",
                "kind": "pre_registration",
                "goal": "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
            },
            "ANDROID_ONLY",
        ),
    ],
)
def test_create_refused_before_google(google, changes, error):
    _, p = account()
    with p, pytest.raises(ToolError, match=error):
        create.campaign_create(**{**BASE, **changes})
    assert google == []


def test_plan_binds_args_and_expires(google, monkeypatch):
    _, p = account()
    with p:
        proposal = create.campaign_create(**BASE)
        with pytest.raises(ToolError, match="does not match"):
            create.campaign_create(
                **{**BASE, "daily_budget_micros": 90_000_000},
                validate_only=False,
                plan_id=proposal.plan_id,
            )
        with pytest.raises(ToolError, match="not issued by this server"):
            create.campaign_create(
                **BASE, validate_only=False, plan_id="0" * 64
            )
        monkeypatch.setattr(plans, "now", lambda: 10**10)
        with pytest.raises(ToolError, match="expired"):
            create.campaign_create(
                **BASE, validate_only=False, plan_id=proposal.plan_id
            )
    assert len(google) == 1, "only the preview reached Google"


def test_ad_group_create_is_paused_with_an_enabled_ad(google):
    _, p = account(
        sub_type="APP_CAMPAIGN_FOR_PRE_REGISTRATION",
        goal="OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
    )
    with p:
        group.ad_group_create(
            customer_id="1",
            campaign_id="7",
            name="AG2",
            headlines=["a", "b"],
            descriptions=["d."],
            image_asset_ids=["10"],
        )
    ops = google[-1].ops
    assert kinds(ops) == ["ad_group_operation", "ad_group_ad_operation"]
    assert ops[0].ad_group_operation.create.status.name == "PAUSED"
    assert (
        ops[0].ad_group_operation.create.campaign == "customers/1/campaigns/7"
    )
    assert (
        ops[1].ad_group_ad_operation.create.ad._pb.WhichOneof("ad_data")
        == "app_pre_registration_ad"
    )


def test_ad_group_create_refuses_html5_outside_install(google):
    _, p = account(
        sub_type="APP_CAMPAIGN_FOR_ENGAGEMENT",
        goal="OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
    )
    with p, pytest.raises(ToolError, match="USER_LIST_REQUIRED"):
        group.ad_group_create(
            customer_id="1",
            campaign_id="7",
            name="AG",
            headlines=["a", "b"],
            descriptions=["d."],
        )


AD_NOW = {
    "ad_group_ad.ad.app_ad.headlines": [
        {"text": "Play now"},
        {"text": "Win big"},
    ],
    "ad_group_ad.ad.app_ad.descriptions": [{"text": "Download today."}],
    "ad_group_ad.ad.app_ad.images": [{"asset": "customers/1/assets/10"}],
    "ad_group_ad.ad.app_ad.youtube_videos": [],
}


def test_ad_update_masks_only_changed_fields(google):
    _, p = account(ad=AD_NOW)
    with p:
        out = update.ad_update(
            customer_id="1",
            ad="8~9",
            add_headlines=["Spin today"],
            remove_image_asset_ids=["10"],
            add_image_asset_ids=["10"],
        )
    (op,) = google[-1].ops
    assert op.ad_operation.update.resource_name == "customers/1/ads/9"
    assert list(op.ad_operation.update_mask.paths) == ["app_ad.headlines"]
    assert [h.text for h in op.ad_operation.update.app_ad.headlines] == [
        "Play now",
        "Win big",
        "Spin today",
    ]
    (item,) = out.change_items
    assert item.before == "Play now | Win big" and item.value.endswith(
        "Spin today"
    )


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"remove_headlines": ["Nope"]}, "NOT_IN_AD"),
        ({"remove_headlines": ["Play now"]}, "TEXT_COUNT"),
        ({"add_image_asset_ids": ["11"]}, "ASPECT_RATIO_NOT_ALLOWED"),
        ({"add_html5_asset_ids": ["10"]}, "ASSET_TYPE_MISMATCH"),
        ({}, "NOTHING_TO_CHANGE"),
    ],
)
def test_ad_update_refusals(google, kwargs, error):
    _, p = account(ad=AD_NOW)
    with p, pytest.raises(ToolError, match=error):
        update.ad_update(customer_id="1", ad="8~9", **kwargs)
    assert google == []


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"app_id": "com.other"}, "IMMUTABLE"),
        ({"kind": "engagement"}, "IMMUTABLE"),
        ({"start_date": "2026-12-01"}, "started"),
        ({"target_roas": 2.0}, "TARGET_NOT_USED"),
        ({"conversion_action_ids": ["99"]}, "CONVERSION_ACTION_NOT_FOUND"),
        ({"remove_language_ids": ["1001"]}, "not targeted"),
        ({}, "at least one change"),
    ],
)
def test_campaign_update_refusals(google, kwargs, error):
    _, p = account()
    with p, pytest.raises(ToolError, match=error):
        cu.campaign_update(customer_id="1", campaign_id="7", **kwargs)
    assert google == []


def test_campaign_update_ops(google):
    crit = [
        {
            "campaign_criterion.resource_name": "customers/1/campaignCriteria/7~1000",
            "campaign_criterion.language.language_constant": "languageConstants/1000",
            "campaign_criterion.negative": False,
        }
    ]
    _, p = account(criteria=crit)
    with p:
        cu.campaign_update(
            customer_id="1",
            campaign_id="7",
            daily_budget_micros=80_000_000,
            target_cpa_micros=3_000_000,
            clear_end_date=True,
            add_geo_target_ids=["2276"],
            remove_language_ids=["1000"],
        )
    ops = google[-1].ops
    assert kinds(ops) == [
        "campaign_budget_operation",
        "campaign_operation",
        "campaign_criterion_operation",
        "campaign_criterion_operation",
    ]
    assert ops[0].campaign_budget_operation.update.amount_micros == 80_000_000
    assert sorted(ops[1].campaign_operation.update_mask.paths) == [
        "end_date_time",
        "target_cpa.target_cpa_micros",
    ]
    assert (
        ops[3].campaign_criterion_operation.remove
        == "customers/1/campaignCriteria/7~1000"
    )


def test_set_status_explains_app_ads(google):
    rows = [
        SimpleNamespace(
            ad_group=SimpleNamespace(id=8),
            ad_group_ad=SimpleNamespace(
                ad=SimpleNamespace(id=9, type_=SimpleNamespace(name="APP_AD"))
            ),
        )
    ]
    with (
        patch("ads_mcp.adnero_mutate.search_rows", return_value=rows),
        pytest.raises(ToolError, match="cannot be paused"),
    ):
        camp.set_status(
            customer_id="1", level="AD", ids=["8~9"], status="PAUSED"
        )
    assert google == []


@pytest.mark.parametrize(
    "geos, declared, sent",
    [
        (["2124"], None, "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"),
        (["2276"], False, "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"),
        (["2276"], True, "CONTAINS_EU_POLITICAL_ADVERTISING"),
    ],
)
def test_eu_declaration_sent(google, geos, declared, sent):
    _, p = account()
    with p:
        create.campaign_create(
            **{
                **BASE,
                "geo_target_ids": geos,
                "excluded_geo_target_ids": [],
                "contains_eu_political_advertising": declared,
            }
        )
    assert (
        google[-1]
        .ops[1]
        .campaign_operation.create.contains_eu_political_advertising.name
        == sent
    )


def test_eu_read_failure_leaves_the_field_for_google(google):
    _, p = account(geo_fails=True)
    with p:
        create.campaign_create(
            **{
                **BASE,
                "geo_target_ids": ["2276"],
                "excluded_geo_target_ids": [],
            }
        )
    c = google[-1].ops[1].campaign_operation.create
    assert c.contains_eu_political_advertising.name == "UNSPECIFIED"


@pytest.mark.parametrize("geos", [["2276"], ["2124", "2380"], []])
def test_eu_targeting_needs_a_statement(google, geos):
    _, p = account()
    with (
        p,
        pytest.raises(
            ToolError, match="EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED"
        ),
    ):
        create.campaign_create(
            **{**BASE, "geo_target_ids": geos, "excluded_geo_target_ids": []}
        )
    assert google == []


def test_a_plan_creates_once(google):
    _, p = account()
    with p:
        proposal = create.campaign_create(**BASE)
        create.campaign_create(
            **BASE, validate_only=False, plan_id=proposal.plan_id
        )
        with pytest.raises(ToolError, match="already applied"):
            create.campaign_create(
                **BASE, validate_only=False, plan_id=proposal.plan_id
            )
    assert [r.validate_only for r in google] == [
        True,
        False,
    ], "one apply reached Google"


def test_the_plan_signs_the_request_sent(google, monkeypatch):
    """A plan from one request does not apply a different request built from the same arguments."""
    from ads_mcp.tools import adnero_app_build as B

    _, p = account()
    with p:
        proposal = create.campaign_create(**BASE)
        real = B.budget_op
        monkeypatch.setattr(
            B,
            "budget_op",
            lambda client, cid, name, micros: real(
                client, cid, name, micros * 10
            ),
        )
        with pytest.raises(ToolError, match="does not match"):
            create.campaign_create(
                **BASE, validate_only=False, plan_id=proposal.plan_id
            )
