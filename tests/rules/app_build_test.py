"""rules/app_build.py: dates, ad edits, update refusals, pause refusal (pure, table-driven)."""

import pytest

from ads_mcp.core.result import Err, Ok
from ads_mcp.rules.app_build import (
    AdContent,
    AdEdit,
    day_bounds,
    edit_ad,
    kind_of,
    pause_refusal,
)
from ads_mcp.rules.app_update import (
    CampaignChange,
    CampaignNow,
    update_problems,
)


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (None, None, Ok((None, None))),
        ("2026-12-01", None, Ok(("2026-12-01 00:00:00", None))),
        (
            "2026-12-01",
            "2026-12-31",
            Ok(("2026-12-01 00:00:00", "2026-12-31 23:59:59")),
        ),
        (
            "2026-12-01",
            "2026-12-01",
            Ok(("2026-12-01 00:00:00", "2026-12-01 23:59:59")),
        ),
        ("2026-12-02", "2026-12-01", Err),
        ("20261201", None, Err),
        ("2026-02-30", None, Err),
        (None, "tomorrow", Err),
    ],
)
def test_day_bounds(start, end, expected):
    got = day_bounds(start, end)
    assert isinstance(got, Err) if expected is Err else got == expected


AD = AdContent(
    headlines=("A", "B"), descriptions=("D.",), images=("img1",), videos=("v1",)
)


@pytest.mark.parametrize(
    "edit, after, changed, error",
    [
        (
            AdEdit(add_headlines=("C",)),
            AD.model_copy(update={"headlines": ("A", "B", "C")}),
            ("headlines",),
            None,
        ),
        (AdEdit(add_headlines=("A",)), None, None, "NOTHING_TO_CHANGE"),
        (AdEdit(remove_headlines=("Z",)), None, None, "NOT_IN_AD"),
        (
            AdEdit(remove_images=("img1",), add_images=("img2", "img2")),
            AD.model_copy(update={"images": ("img2",)}),
            ("images",),
            None,
        ),
        (
            AdEdit(remove_videos=("v1",), add_descriptions=("E.",)),
            AD.model_copy(update={"videos": (), "descriptions": ("D.", "E.")}),
            ("descriptions", "videos"),
            None,
        ),
        (AdEdit(), None, None, "NOTHING_TO_CHANGE"),
    ],
)
def test_edit_ad(edit, after, changed, error):
    got = edit_ad(AD, edit)
    if error:
        assert isinstance(got, Err) and got.error[0].code == error
    else:
        assert got == Ok((after, changed))


NOW = CampaignNow(
    kind="install",
    goal="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
    app_id="com.a.b",
    store="GOOGLE_APP_STORE",
    start_date_time="2026-11-01 00:00:00",
)


@pytest.mark.parametrize(
    "now, change, expected",
    [
        (NOW, CampaignChange(target_cpa_micros=1), []),
        (NOW, CampaignChange(app_id="com.a.b"), []),
        (NOW, CampaignChange(app_id="com.other"), ["IMMUTABLE"]),
        (NOW, CampaignChange(store="APPLE_APP_STORE"), ["IMMUTABLE"]),
        (NOW, CampaignChange(kind="engagement"), ["IMMUTABLE"]),
        (NOW, CampaignChange(start_date="2026-12-01"), ["IMMUTABLE"]),
        (
            NOW.model_copy(update={"start_date_time": "2026-12-20 00:00:00"}),
            CampaignChange(start_date="2026-12-21"),
            [],
        ),
        (NOW, CampaignChange(target_roas=3.0), ["TARGET_NOT_USED"]),
        (
            NOW.model_copy(
                update={"goal": "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND"}
            ),
            CampaignChange(target_cpa_micros=5),
            ["TARGET_NOT_USED"],
        ),
        (
            NOW.model_copy(
                update={
                    "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST"
                }
            ),
            CampaignChange(conversion_action_ids=()),
            ["CONVERSION_ACTIONS_REQUIRED"],
        ),
        (
            NOW.model_copy(
                update={
                    "kind": "pre_registration",
                    "goal": "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
                }
            ),
            CampaignChange(conversion_action_ids=("1",)),
            ["IMMUTABLE"],
        ),
        (
            NOW.model_copy(update={"budget_shared": True}),
            CampaignChange(daily_budget_micros=5),
            ["SHARED_BUDGET"],
        ),
    ],
)
def test_update_problems(now, change, expected):
    assert [
        p.code for p in update_problems(now, change, "2026-11-13")
    ] == expected


@pytest.mark.parametrize(
    "sub_type, kind",
    [
        ("APP_CAMPAIGN", "install"),
        ("APP_CAMPAIGN_FOR_ENGAGEMENT", "engagement"),
        ("APP_CAMPAIGN_FOR_PRE_REGISTRATION", "pre_registration"),
        ("SEARCH_EXPRESS", None),
        ("", None),
    ],
)
def test_kind_of(sub_type, kind):
    assert kind_of(sub_type) == kind


@pytest.mark.parametrize(
    "types, refused",
    [
        ([("1~2", "RESPONSIVE_SEARCH_AD")], False),
        ([("1~2", "APP_AD")], True),
        ([("1~2", "RESPONSIVE_SEARCH_AD"), ("1~3", "APP_ENGAGEMENT_AD")], True),
        ([], False),
    ],
)
def test_pause_refusal(types, refused):
    got = pause_refusal(types)
    assert (got is not None) is refused
    if got:
        assert "AD_GROUP" in got and "cannot be paused" in got
