"""What an App campaign can no longer change (pure): refusals that name the rule."""

from pydantic import BaseModel, ConfigDict

from ads_mcp.rules import app as R


class CampaignNow(BaseModel):
    """What app_campaign_update reads before changing anything."""

    model_config = ConfigDict(frozen=True)

    kind: R.Kind
    goal: str
    app_id: str
    store: str
    start_date_time: str = ""
    budget_shared: bool = False


class CampaignChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_cpa_micros: int | None = None
    target_roas: float | None = None
    conversion_action_ids: tuple[str, ...] | None = None
    app_id: str | None = None
    store: str | None = None
    kind: str | None = None
    start_date: str | None = None
    daily_budget_micros: int | None = None


def update_problems(
    now: CampaignNow, change: CampaignChange, today: str
) -> list[R.Problem]:
    """Refuse what App campaigns cannot change after create, naming the rule."""
    goal = R.GOALS.get(now.goal)
    immut = [
        R.Problem(
            code="IMMUTABLE",
            field=arg,
            message=R.IMMUTABLE_CAMPAIGN_FIELDS[rule],
            fix="Create a new App campaign instead.",
        )
        for arg, rule, new, old in (
            (
                "app_id",
                "app_campaign_setting.app_id",
                change.app_id,
                now.app_id,
            ),
            (
                "store",
                "app_campaign_setting.app_store",
                change.store,
                now.store,
            ),
            ("kind", "advertising_channel_sub_type", change.kind, now.kind),
        )
        if new is not None and new != old
    ]
    started = (
        [
            R.Problem(
                code="IMMUTABLE",
                field="start_date",
                message=R.START_AFTER_START,
            )
        ]
        if change.start_date is not None
        and now.start_date_time
        and now.start_date_time[:10] <= today
        else []
    )
    bidding = goal.bidding if goal else ""
    targets = [
        R.Problem(
            code="TARGET_NOT_USED",
            field=arg,
            message=f"The campaign's goal is {now.goal} ({bidding}); it has no {arg}.",
            fix="Change the target the goal uses, or create a campaign with another goal.",
        )
        for arg, value, needs in (
            ("target_cpa_micros", change.target_cpa_micros, "target_cpa"),
            ("target_roas", change.target_roas, "target_roas"),
        )
        if value is not None and bidding != needs
    ]
    selective = (
        [
            R.Problem(
                code="IMMUTABLE",
                field="conversion_action_ids",
                message=R.PRE_REGISTRATION_SELECTIVE,
            )
        ]
        if change.conversion_action_ids is not None
        and now.kind == "pre_registration"
        else (
            [
                R.Problem(
                    code="CONVERSION_ACTIONS_REQUIRED",
                    field="conversion_action_ids",
                    message=f"{now.goal} needs at least one conversion action.",
                )
            ]
            if change.conversion_action_ids == ()
            and goal is not None
            and goal.selective_optimization == "required"
            else []
        )
    )
    shared = (
        [
            R.Problem(
                code="SHARED_BUDGET",
                field="daily_budget_micros",
                message="This campaign's budget is shared; changing it changes every campaign on it.",
            )
        ]
        if change.daily_budget_micros is not None and now.budget_shared
        else []
    )
    return immut + started + targets + selective + shared
