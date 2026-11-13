"""rules/app_check.py: spec + account facts -> problems (pure, table-driven)."""

import pytest

from ads_mcp.rules.app_check import check, needed_ids
from ads_mcp.rules.app_spec import (
    AccountFacts,
    AppSpec,
    AssetFact,
    ConversionActionFact,
    UserListFact,
)

APP = "com.example.app"
FACTS = AccountFacts(
    conversion_actions=(
        ConversionActionFact(
            id="1",
            name="first_open",
            status="ENABLED",
            type="FIREBASE_ANDROID_FIRST_OPEN",
            category="DOWNLOAD",
            app_id=APP,
        ),
        ConversionActionFact(
            id="2",
            name="purchase",
            status="ENABLED",
            type="FIREBASE_ANDROID_IN_APP_PURCHASE",
            category="PURCHASE",
            app_id=APP,
        ),
        ConversionActionFact(
            id="3",
            name="old",
            status="HIDDEN",
            type="FIREBASE_ANDROID_CUSTOM",
            category="DEFAULT",
            app_id=APP,
        ),
        ConversionActionFact(
            id="4",
            name="other app",
            status="ENABLED",
            type="GOOGLE_PLAY_IN_APP_PURCHASE",
            category="PURCHASE",
            app_id="com.other",
        ),
    ),
    assets=(
        AssetFact(
            id="10",
            type="IMAGE",
            width=1200,
            height=628,
            size_bytes=1000,
            mime="IMAGE_PNG",
        ),
        AssetFact(
            id="11",
            type="IMAGE",
            width=2048,
            height=2002,
            size_bytes=1000,
            mime="IMAGE_PNG",
        ),
        AssetFact(id="20", type="YOUTUBE_VIDEO"),
        AssetFact(id="30", type="MEDIA_BUNDLE"),
    ),
    user_lists=(UserListFact(id="50", name="payers"),),
)
BASE = dict(
    kind="install",
    app_id=APP,
    store="GOOGLE_APP_STORE",
    goal="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
    target_cpa_micros=2_000_000,
    geo_target_ids=("2840",),
    headlines=("Play now", "Win big"),
    descriptions=("Download today.",),
    image_asset_ids=("10",),
    youtube_video_asset_ids=("20",),
)


def run(**changes):
    report = check(AppSpec(**{**BASE, **changes}), FACTS)
    return report, sorted(p.code for p in report.problems)


@pytest.mark.parametrize(
    "changes, expected, ok",
    [
        ({}, [], True),
        ({"image_asset_ids": ("11",)}, ["ASPECT_RATIO_NOT_ALLOWED"], False),
        ({"image_asset_ids": ("20",)}, ["ASSET_TYPE_MISMATCH"], False),
        ({"image_asset_ids": ("99",)}, ["ASSET_NOT_FOUND"], False),
        ({"html5_asset_ids": ("30",)}, [], True),
        (
            {
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ("2",),
            },
            [],
            True,
        ),
        (
            {
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ("1",),
            },
            ["CONVERSION_ACTION_WITH_DOWNLOAD_CATEGORY_NOT_ALLOWED"],
            False,
        ),
        (
            {
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ("3", "4", "9"),
            },
            [
                "APP_ID_MUST_MATCH_CONVERSION_ACTION_APP_ID",
                "CONVERSION_ACTION_NOT_ENABLED",
                "CONVERSION_ACTION_NOT_FOUND",
            ],
            False,
        ),
        (
            {
                "goal": "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
                "target_cpa_micros": None,
                "target_roas": 2.0,
                "conversion_action_ids": ("4",),
            },
            [
                "APP_ID_MUST_MATCH_CONVERSION_ACTION_APP_ID",
                "VALUE_GOAL_NEEDS_FIREBASE",
            ],
            False,
        ),
        ({"app_id": "com.untracked"}, ["APP_NOT_TRACKED"], True),
        (
            {"geo_target_ids": ()},
            ["EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED", "NO_GEO"],
            False,
        ),
        (
            {"geo_target_ids": (), "contains_eu_political_advertising": False},
            ["NO_GEO"],
            True,
        ),
        (
            {"excluded_geo_target_ids": ("2840",)},
            ["GEO_BOTH_INCLUDED_AND_EXCLUDED"],
            False,
        ),
        (
            {
                "kind": "engagement",
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ("2",),
            },
            ["USER_LIST_REQUIRED"],
            False,
        ),
        (
            {
                "kind": "engagement",
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ("2",),
                "user_list_ids": ("50", "51"),
            },
            ["USER_LIST_NOT_FOUND"],
            False,
        ),
        (
            {
                "kind": "engagement",
                "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                "conversion_action_ids": ("2",),
                "user_list_ids": ("50",),
                "html5_asset_ids": ("30",),
            },
            ["HTML5_NOT_ALLOWED"],
            False,
        ),
        ({"headlines": ("x" * 31, "b")}, ["TEXT_TOO_LONG"], False),
    ],
)
def test_check(changes, expected, ok):
    report, got = run(**changes)
    assert got == expected
    assert report.ok is ok


@pytest.mark.parametrize(
    "kind, sub_type, ad_field",
    [
        ("install", "APP_CAMPAIGN", "app_ad"),
        ("engagement", "APP_CAMPAIGN_FOR_ENGAGEMENT", "app_engagement_ad"),
        (
            "pre_registration",
            "APP_CAMPAIGN_FOR_PRE_REGISTRATION",
            "app_pre_registration_ad",
        ),
    ],
)
def test_shape(kind, sub_type, ad_field):
    report, _ = run(kind=kind)
    assert (report.campaign_sub_type, report.ad_field) == (sub_type, ad_field)


def test_needed_ids_dedupes_in_order():
    spec = AppSpec(
        **{
            **BASE,
            "image_asset_ids": ("10", "11", "10"),
            "html5_asset_ids": ("30",),
        }
    )
    assert needed_ids(spec) == ("10", "11", "20", "30")


@pytest.mark.parametrize(
    "declared, geos, requiring, expected",
    [
        (
            None,
            ("2124",),
            (),
            "DOES_NOT_CONTAIN",
        ),  # Google says no location needs it
        (
            None,
            ("2276",),
            ("2276",),
            "REQUIRED",
        ),  # Google says Germany needs it
        (
            None,
            ("1007339",),
            ("1007339",),
            "REQUIRED",
        ),  # a city: Google flags it too
        (None, ("2840", "2250"), ("2250",), "REQUIRED"),
        (None, (), (), "REQUIRED"),  # no location: everywhere, the EU included
        (None, ("2124",), None, "UNSET"),  # the read failed: Google enforces it
        (False, ("2276",), ("2276",), "DOES_NOT_CONTAIN"),
        (True, ("2276",), ("2276",), "CONTAINS"),
        (True, (), None, "CONTAINS"),
    ],
)
def test_eu_decide(declared, geos, requiring, expected):
    from ads_mcp.core.result import Err
    from ads_mcp.rules.eu import decide

    got = decide(declared, geos, requiring)
    if expected == "REQUIRED":
        assert isinstance(got, Err)
        assert got.error.code == "EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED"
        assert "ask the person" in got.error.fix.lower()
    else:
        assert got.value == expected


@pytest.mark.parametrize(
    "requiring, expected",
    [
        ((), []),
        (("2840",), ["EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED"]),
        (None, []),
    ],
)
def test_check_uses_googles_eu_answer(requiring, expected):
    facts = FACTS.model_copy(update={"eu_requiring_geo_ids": requiring})
    report = check(AppSpec(**BASE), facts)
    assert [p.code for p in report.problems] == expected


def test_missing_ids_on_an_incomplete_read_are_warnings():
    facts = FACTS.model_copy(update={"complete": False})
    report = check(AppSpec(**{**BASE, "image_asset_ids": ("99",)}), facts)
    assert [(p.code, p.severity) for p in report.problems] == [
        ("ASSET_NOT_FOUND", "WARNING")
    ]
    assert report.ok


@pytest.mark.parametrize(
    "tracked, warned", [(True, False), (False, True), (None, False)]
)
def test_app_tracked_fact(tracked, warned):
    facts = FACTS.model_copy(update={"app_tracked": tracked})
    report = check(AppSpec(**BASE), facts)
    assert ("APP_NOT_TRACKED" in [p.code for p in report.problems]) is warned
