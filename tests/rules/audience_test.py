"""Audiences as data: age parsing, checks, read-back parsing, signal planning (pure)."""

import pytest

from ads_mcp.rules import audience as A
from ads_mcp.rules import audience_signal as S


@pytest.mark.parametrize(
    "text, expected",
    [
        ("25-54", (25, 54)),
        ("18-24", (18, 24)),
        ("65+", (65, None)),
        ("55", (55, None)),
        (" 35 - 44 ", (35, 44)),
    ],
)
def test_ages_parse(text, expected):
    assert A.parse_age(text) == expected


@pytest.mark.parametrize(
    "text", ["20-30", "25-25", "25-60", "17+", "abc", "25-34-44"]
)
def test_bad_ages_are_problems(text):
    assert isinstance(A.parse_age(text), str)


def spec(**kw):
    return A.AudienceSpec(name=kw.pop("name", "Slots 25-54"), **kw)


def test_a_good_audience_has_no_problems():
    s = spec(
        ages=A.demo_of(["25-54"], False, upper=False),
        genders=A.demo_of(["male"], True),
        segments=(("user_interest", "80"),),
    )
    assert A.problems(s) == []
    assert s.genders.values == ("MALE",)


@pytest.mark.parametrize(
    "kw, fragment",
    [
        ({}, "at least one dimension"),
        ({"name": " ", "segments": (("user_list", "1"),)}, "name is 1-255"),
        ({"ages": A.Demo(values=("20-30",))}, "age range"),
        ({"genders": A.Demo(values=("OTHER",))}, "genders: OTHER"),
        (
            {"parental_statuses": A.Demo(values=("UNDETERMINED",))},
            "parental_statuses",
        ),
        (
            {"income_ranges": A.Demo(values=("INCOME_RANGE_1",))},
            "income_ranges",
        ),
        ({"genders": A.Demo(values=())}, "given but empty"),
        ({"segments": (("topic", "1"),)}, "unknown"),
    ],
)
def test_problems(kw, fragment):
    assert any(fragment in p for p in A.problems(spec(**kw)))


def test_read_back_round_trips():
    dims = [
        {
            "age": {
                "age_ranges": [{"min_age": 25, "max_age": 54}, {"min_age": 65}],
                "include_undetermined": True,
            }
        },
        {"gender": {"genders": ["FEMALE"]}},
        {"parental_status": {"parental_statuses": ["PARENT"]}},
        {"household_income": {"income_ranges": ["INCOME_RANGE_90_UP"]}},
        {
            "audience_segments": {
                "segments": [
                    {
                        "user_interest": {
                            "user_interest_category": "customers/1/userInterests/80"
                        }
                    },
                    {"user_list": {"user_list": "customers/1/userLists/5"}},
                    {"life_event": {"life_event": "customers/1/lifeEvents/6"}},
                    {
                        "detailed_demographic": {
                            "detailed_demographic": "customers/1/detailedDemographics/7"
                        }
                    },
                    {
                        "custom_audience": {
                            "custom_audience": "customers/1/customAudiences/8"
                        }
                    },
                ]
            }
        },
    ]
    s = A.spec_from_plain(
        "n",
        "d",
        dims,
        {
            "exclusions": [
                {"user_list": {"user_list": "customers/1/userLists/9"}}
            ]
        },
    )
    assert s.ages == A.Demo(values=("25-54", "65+"), include_undetermined=True)
    assert s.genders.values == ("FEMALE",) and s.parental_statuses.values == (
        "PARENT",
    )
    assert s.income_ranges.values == ("INCOME_RANGE_90_UP",)
    assert s.segments == (
        ("user_interest", "80"),
        ("user_list", "5"),
        ("life_event", "6"),
        ("detailed_demographic", "7"),
        ("custom_audience", "8"),
    )
    assert s.exclude_user_list_ids == ("9",)
    assert A.problems(s) == []


@pytest.mark.parametrize(
    "channel, sub, ok",
    [
        ("PERFORMANCE_MAX", "", True),
        ("MULTI_CHANNEL", "APP_CAMPAIGN", True),
        ("MULTI_CHANNEL", "APP_CAMPAIGN_FOR_ENGAGEMENT", False),
        ("MULTI_CHANNEL", "APP_CAMPAIGN_FOR_PRE_REGISTRATION", False),
        ("SEARCH", "", False),
        ("DEMAND_GEN", "", False),
    ],
)
def test_where_signals_go(channel, sub, ok):
    assert (S.target_problem(channel, sub) is None) == ok


AG = S.Target(kind="AD_GROUP", id="10")
AG2 = S.Target(kind="AD_GROUP", id="11")
PG = S.Target(kind="ASSET_GROUP", id="20")


def sig(target, audience):
    return S.Signal(
        target_id=target,
        resource_name=f"r/{target}~{audience}",
        audience_id=audience,
    )


def test_attach_to_empty_ad_groups():
    plan = S.plan_attach((AG, AG2), (), "1", False)
    assert plan.create == (AG, AG2) and not plan.remove and not plan.problems


def test_attach_skips_where_it_is_already():
    plan = S.plan_attach((AG, AG2), (sig("10", "1"),), "1", False)
    assert plan.create == (AG2,) and plan.already == ("10",)


def test_one_audience_per_ad_group_unless_replace():
    blocked = S.plan_attach((AG,), (sig("10", "2"),), "1", False)
    assert blocked.problems and "replace=true" in blocked.problems[0]
    swapped = S.plan_attach((AG,), (sig("10", "2"),), "1", True)
    assert swapped.create == (AG,) and swapped.remove == (sig("10", "2"),)


def test_asset_groups_take_several():
    plan = S.plan_attach((PG,), (sig("20", "2"),), "1", False)
    assert plan.create == (PG,) and not plan.problems and not plan.remove


def test_detach_only_that_audience():
    assert S.plan_detach((sig("10", "1"), sig("11", "2")), "1") == (
        sig("10", "1"),
    )


@pytest.mark.parametrize(
    "kind, lists, code",
    [
        ("install", ("50",), "USER_LIST_NOT_ALLOWED"),
        ("pre_registration", ("50",), "USER_LIST_NOT_ALLOWED"),
        ("engagement", (), "USER_LIST_REQUIRED"),
        ("install", (), None),
        ("engagement", ("50",), None),
    ],
)
def test_app_ad_group_user_lists(kind, lists, code):
    from ads_mcp.rules.app_spec import AccountFacts, AppSpec, UserListFact
    from ads_mcp.rules.app_targeting import audience_problems

    spec = AppSpec.model_construct(kind=kind, user_list_ids=lists)
    facts = AccountFacts.model_construct(
        user_lists=(UserListFact.model_construct(id="50"),), complete=True
    )
    codes = [p.code for p in audience_problems(spec, facts)]
    assert codes == ([code] if code else [])
