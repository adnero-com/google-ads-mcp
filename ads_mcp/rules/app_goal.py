"""App campaign goal, store and bidding checks (pure), from the goal table in rules/app.py."""

from collections.abc import Sequence

from ads_mcp.rules.app import GOALS, Kind, Problem, Store


def check_app_id(app_id: str, store: Store) -> list[Problem]:
    text = app_id.strip()
    if store == "APPLE_APP_STORE" and not text.isdigit():
        return [
            Problem(
                code="APP_ID_FORMAT",
                field="app_id",
                message=f"'{app_id}': an App Store app id is the number only (e.g. 1234567890).",
                fix="Drop the 'id' prefix and the URL.",
            )
        ]
    if store == "GOOGLE_APP_STORE" and (
        "." not in text or " " in text or "/" in text
    ):
        return [
            Problem(
                code="APP_ID_FORMAT",
                field="app_id",
                message=f"'{app_id}': a Google Play app id is the package name (com.example.app).",
            )
        ]
    return []


def check_goal(
    kind: Kind,
    store: Store,
    goal: str,
    target_cpa_micros: int | None,
    target_roas: float | None,
    conversion_action_ids: Sequence[str],
) -> list[Problem]:
    rule = GOALS.get(goal)
    if rule is None:
        return [
            Problem(
                code="UNKNOWN_GOAL",
                field="goal",
                message=f"'{goal}' is not an App bidding goal.",
                fix="One of: " + ", ".join(sorted(GOALS)),
            )
        ]
    kind_ok = (
        []
        if kind in rule.kinds
        else [
            Problem(
                code="GOAL_KIND_MISMATCH",
                field="goal",
                message=f"{goal} is for {', '.join(rule.kinds)} campaigns, not {kind}.",
                fix="Pick a goal of this campaign kind: "
                + ", ".join(
                    sorted(g for g, r in GOALS.items() if kind in r.kinds)
                ),
            )
        ]
    )
    android = (
        [
            Problem(
                code="PRE_REGISTRATION_ANDROID_ONLY",
                field="store",
                message="Pre-registration campaigns are for Google Play apps only.",
            )
        ]
        if kind == "pre_registration" and store != "GOOGLE_APP_STORE"
        else []
    )
    targets = _target_problems(
        goal, rule.target, target_cpa_micros, target_roas
    )
    selective = _selective_problems(
        goal, rule.selective_optimization, conversion_action_ids
    )
    return kind_ok + android + targets + selective


def _target_problems(
    goal: str, needs: str, cpa: int | None, roas: float | None
) -> list[Problem]:
    missing = {
        "cpa": cpa is None or cpa <= 0,
        "roas": roas is None or roas <= 0,
        "none": False,
    }[needs]
    stray = [
        Problem(
            code="TARGET_NOT_USED",
            field=name,
            message=f"{goal} takes no {name}.",
            fix=f"Leave {name} out.",
        )
        for name, value, kind in (
            ("target_cpa_micros", cpa, "cpa"),
            ("target_roas", roas, "roas"),
        )
        if value is not None and kind != needs
    ]
    need = (
        [
            Problem(
                code="TARGET_REQUIRED",
                field="target_cpa_micros" if needs == "cpa" else "target_roas",
                message=f"{goal} needs a {'target CPA/CPI in micros' if needs == 'cpa' else 'target ROAS (3.0 = 300%)'} above 0.",
            )
        ]
        if missing
        else []
    )
    return need + stray


def _selective_problems(
    goal: str, mode: str, ids: Sequence[str]
) -> list[Problem]:
    if mode == "required" and not ids:
        return [
            Problem(
                code="CONVERSION_ACTIONS_REQUIRED",
                field="conversion_action_ids",
                message=f"{goal} optimizes for in-app actions: name the conversion actions.",
                fix="List the app's conversion actions (search conversion_action WHERE conversion_action.app_id = ...).",
            )
        ]
    if mode == "not_allowed" and ids:
        return [
            Problem(
                code="CONVERSION_ACTIONS_NOT_ALLOWED",
                field="conversion_action_ids",
                message="Pre-registration campaigns get their conversion actions from Google.",
                fix="Leave conversion_action_ids out.",
            )
        ]
    return []
