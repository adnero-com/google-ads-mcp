"""App campaign targeting checks (pure): engagement user lists, locations."""

from ads_mcp.rules import app as R
from ads_mcp.rules.app_spec import AccountFacts, AppSpec


def eu_targeted(spec: AppSpec) -> bool:
    """True when the campaign can reach the EU: an EU country targeted, or no location (everywhere)."""
    return not spec.geo_target_ids or any(
        g in R.EU_COUNTRY_GEO_IDS for g in spec.geo_target_ids
    )


def eu_problems(spec: AppSpec) -> list[R.Problem]:
    if spec.contains_eu_political_advertising is not None or not eu_targeted(
        spec
    ):
        return []
    return [
        R.Problem(
            code="EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED",
            field="contains_eu_political_advertising",
            message="The campaign targets the EU (an EU country, or no location: everywhere), so Google needs "
            "the EU political advertising self-declaration.",
            fix="Ask the person, then pass contains_eu_political_advertising=false (the ads are not EU political "
            "advertising) or true.",
        )
    ]


def eu_declaration(spec: AppSpec) -> bool:
    """What is sent: the caller's statement, else false (allowed only when no EU location is targeted)."""
    return bool(spec.contains_eu_political_advertising)


def audience_problems(spec: AppSpec, facts: AccountFacts) -> list[R.Problem]:
    known = {u.id for u in facts.user_lists}
    required = (
        [
            R.Problem(
                code="USER_LIST_REQUIRED",
                field="user_list_ids",
                message="Engagement campaigns target existing users: name at least one user list.",
            )
        ]
        if spec.kind == "engagement" and not spec.user_list_ids
        else []
    )
    missing = [
        R.Problem(
            code="USER_LIST_NOT_FOUND",
            field=f"user_list_ids[{i}]",
            message=f"User list {u} is not in this account.",
        )
        for i, u in enumerate(spec.user_list_ids)
        if u not in known
    ]
    return required + missing


def geo_problems(spec: AppSpec) -> list[R.Problem]:
    both = sorted(set(spec.geo_target_ids) & set(spec.excluded_geo_target_ids))
    clash = (
        [
            R.Problem(
                code="GEO_BOTH_INCLUDED_AND_EXCLUDED",
                field="excluded_geo_target_ids",
                message=f"{', '.join(both)} both targeted and excluded.",
            )
        ]
        if both
        else []
    )
    everywhere = (
        []
        if spec.geo_target_ids
        else [
            R.Problem(
                code="NO_GEO",
                field="geo_target_ids",
                message="No location: the campaign targets every country.",
                severity="WARNING",
            )
        ]
    )
    return clash + everywhere
