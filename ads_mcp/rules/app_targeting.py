"""App campaign targeting checks (pure): engagement user lists, locations."""

from ads_mcp.core.result import Err, Result
from ads_mcp.rules import app as R
from ads_mcp.rules.eu import EuStatus, decide
from ads_mcp.rules.app_spec import AccountFacts, AppSpec


def eu_status(
    spec: AppSpec, facts: AccountFacts
) -> Result[EuStatus, R.Problem]:
    return decide(
        spec.contains_eu_political_advertising,
        spec.geo_target_ids,
        facts.eu_requiring_geo_ids,
    )


def eu_problems(spec: AppSpec, facts: AccountFacts) -> list[R.Problem]:
    status = eu_status(spec, facts)
    return [status.error] if isinstance(status, Err) else []


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
    # Docs (app-campaigns/create-ad-group): "Don't attach UserListInfo criteria to APP_CAMPAIGN ad groups";
    # pre-registration ad groups take no criteria at all. Lists for installs go inside an Audience (signal).
    not_allowed = (
        [
            R.Problem(
                code="USER_LIST_NOT_ALLOWED",
                field="user_list_ids",
                message=(
                    "Install campaigns take no user list on the ad group: put the lists in an audience "
                    "(audiences_create user_list_ids) and attach it with audiences_signal_attach."
                    if spec.kind == "install"
                    else "Pre-registration ad groups take no criteria: leave user_list_ids out."
                ),
                fix="Remove user_list_ids.",
            )
        ]
        if spec.kind != "engagement" and spec.user_list_ids
        else []
    )
    missing = [
        R.Problem(
            code="USER_LIST_NOT_FOUND",
            field=f"user_list_ids[{i}]",
            message=f"User list {u} is not in this account.",
            severity="ERROR" if facts.complete else "WARNING",
        )
        for i, u in enumerate(spec.user_list_ids)
        if u not in known
    ]
    return required + not_allowed + missing


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
