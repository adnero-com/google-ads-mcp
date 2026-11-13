"""rules/app.py and rules/app_goal.py: limits and the goal table (pure, table-driven)."""

import pytest

from ads_mcp.rules import app as R
from ads_mcp.rules import app_creative as C
from ads_mcp.rules import rules_for
from ads_mcp.rules.app_goal import check_app_id, check_goal


def codes(problems):
    return sorted(p.code for p in problems)


@pytest.mark.parametrize(
    "headlines, descriptions, expected",
    [
        (["Play now", "Win big"], ["Download today."], []),
        (["Only one"], ["d"], ["TEXT_COUNT"]),
        (["a", "b", "c", "d", "e", "f"], ["d"], ["TEXT_COUNT"]),
        (["a", "b"], [], ["TEXT_COUNT"]),
        (["x" * 31, "b"], ["d"], ["TEXT_TOO_LONG"]),
        (["x" * 30, "b"], ["y" * 90], []),
        (["a", "b"], ["y" * 91], ["TEXT_TOO_LONG"]),
        (["Same", "same "], ["d"], ["TEXT_DUPLICATE"]),
        (["a", " "], ["d"], ["TEXT_EMPTY"]),
    ],
)
def test_texts(headlines, descriptions, expected):
    assert codes(C.check_texts(headlines, descriptions)) == expected


@pytest.mark.parametrize(
    "w, h, ratio",
    [
        (1200, 1200, "1:1"),
        (1200, 628, "1.91:1"),
        (1200, 1500, "4:5"),
        (1205, 1200, "1:1"),  # 0.42% off: allowed
        (1210, 1200, None),  # 0.83% off: Google refused this much live
        (600, 314, "1.91:1"),  # the minimum size, 0.03% off
        (2048, 2002, None),  # seen live: refused
        (2048, 1081, None),
        (2048, 1092, None),
        (881, 1092, None),
        (1080, 1920, None),  # 9:16: video only
        (1920, 1080, None),
        (0, 100, None),
    ],
)
def test_ratio_class(w, h, ratio):
    assert C.ratio_class(w, h) == ratio


@pytest.mark.parametrize(
    "w, h, size, mime, expected",
    [
        (1200, 628, 100_000, "IMAGE_JPEG", []),
        (600, 314, 100_000, "IMAGE_PNG", []),
        (599, 314, 100_000, "IMAGE_PNG", ["IMAGE_TOO_SMALL"]),
        (199, 199, 1, "IMAGE_PNG", ["IMAGE_TOO_SMALL"]),
        (2048, 2002, 1, "IMAGE_PNG", ["ASPECT_RATIO_NOT_ALLOWED"]),
        (1200, 1200, 5 * 1024 * 1024 + 1, "IMAGE_PNG", ["IMAGE_TOO_LARGE"]),
        (1200, 1200, 1, "IMAGE_GIF", ["IMAGE_TYPE"]),
    ],
)
def test_image(w, h, size, mime, expected):
    assert codes(C.check_image("img", w, h, size, mime)) == expected


@pytest.mark.parametrize(
    "images, videos, html5, kind, expected",
    [
        (20, 20, 20, "install", []),
        (21, 0, 0, "install", ["TOO_MANY_ASSETS"]),
        (0, 21, 0, "engagement", ["TOO_MANY_ASSETS"]),
        (0, 0, 1, "engagement", ["HTML5_NOT_ALLOWED"]),
        (0, 0, 1, "pre_registration", ["HTML5_NOT_ALLOWED"]),
    ],
)
def test_counts(images, videos, html5, kind, expected):
    assert codes(C.check_counts(images, videos, html5, kind)) == expected


@pytest.mark.parametrize(
    "app_id, store, expected",
    [
        ("com.example.app", "GOOGLE_APP_STORE", []),
        ("1234567890", "APPLE_APP_STORE", []),
        ("id1234567890", "APPLE_APP_STORE", ["APP_ID_FORMAT"]),
        ("https://play.google.com/x", "GOOGLE_APP_STORE", ["APP_ID_FORMAT"]),
        ("example", "GOOGLE_APP_STORE", ["APP_ID_FORMAT"]),
    ],
)
def test_app_id(app_id, store, expected):
    assert codes(check_app_id(app_id, store)) == expected


G = "GOOGLE_APP_STORE"


@pytest.mark.parametrize(
    "kind, store, goal, cpa, roas, actions, expected",
    [
        (
            "install",
            G,
            "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
            2_000_000,
            None,
            [],
            [],
        ),
        (
            "install",
            G,
            "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
            None,
            None,
            [],
            ["TARGET_REQUIRED"],
        ),
        (
            "install",
            G,
            "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST",
            None,
            None,
            [],
            [],
        ),
        (
            "install",
            G,
            "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST",
            1,
            None,
            [],
            ["TARGET_NOT_USED"],
        ),
        (
            "install",
            G,
            "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
            5,
            None,
            [],
            ["CONVERSION_ACTIONS_REQUIRED"],
        ),
        (
            "install",
            G,
            "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
            5,
            None,
            ["1"],
            [],
        ),
        (
            "install",
            G,
            "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
            None,
            3.0,
            ["1"],
            [],
        ),
        (
            "install",
            G,
            "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
            5,
            None,
            ["1"],
            ["TARGET_NOT_USED", "TARGET_REQUIRED"],
        ),
        (
            "install",
            G,
            "OPTIMIZE_TOTAL_VALUE_WITHOUT_TARGET_ROAS",
            None,
            None,
            ["1"],
            [],
        ),
        (
            "engagement",
            G,
            "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
            5,
            None,
            ["1"],
            [],
        ),
        (
            "engagement",
            G,
            "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
            5,
            None,
            [],
            ["GOAL_KIND_MISMATCH"],
        ),
        (
            "pre_registration",
            G,
            "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
            5,
            None,
            [],
            [],
        ),
        (
            "pre_registration",
            "APPLE_APP_STORE",
            "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
            5,
            None,
            [],
            ["PRE_REGISTRATION_ANDROID_ONLY"],
        ),
        (
            "pre_registration",
            G,
            "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
            5,
            None,
            ["1"],
            ["CONVERSION_ACTIONS_NOT_ALLOWED"],
        ),
        ("install", G, "MAXIMIZE_EVERYTHING", None, None, [], ["UNKNOWN_GOAL"]),
    ],
)
def test_goals(kind, store, goal, cpa, roas, actions, expected):
    assert codes(check_goal(kind, store, goal, cpa, roas, actions)) == expected


@pytest.mark.parametrize(
    "resource, ids",
    [
        (
            "ad_group_ad",
            {
                "app_ad.headlines",
                "app_ad.descriptions",
                "app_ad.no_pause",
                "app_image.ratio",
            },
        ),
        ("campaign", {"app_campaign.goal_bidding", "app_campaign.immutable"}),
        ("asset", {"app_image.ratio", "app_html5.bundle"}),
        ("AD_GROUP_AD", {"app_ad.headlines"}),
        ("keyword_view", set()),
    ],
)
def test_rules_for(resource, ids):
    got = {r["id"] for r in rules_for(resource)}
    assert ids <= got
    if not ids:
        assert got == set()


def test_rules_are_copies():
    rules_for("campaign")[0]["id"] = "changed"
    assert rules_for("campaign")[0]["id"] == "app_campaign.goal_bidding"
