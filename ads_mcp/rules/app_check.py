"""app_check's pure core: an App campaign spec + facts read from the account -> problems.

The edge (tools/adnero_app.py) reads the facts (conversion actions, assets, user lists) and
calls `check`. The same spec feeds app_campaign_create, so a spec that passes here is the
one that gets built.
"""

from collections.abc import Sequence


from ads_mcp.rules import app as R
from ads_mcp.rules.app_creative import check_counts, check_image, check_texts
from ads_mcp.rules.app_spec import AccountFacts, AppSpec, CheckReport
from ads_mcp.rules.app_goal import check_app_id, check_goal
from ads_mcp.rules.app_targeting import audience_problems, geo_problems


def conversion_problems(spec: AppSpec, facts: AccountFacts) -> list[R.Problem]:
    by_id = {c.id: c for c in facts.conversion_actions}
    missing = [
        R.Problem(
            code="CONVERSION_ACTION_NOT_FOUND",
            field=f"conversion_action_ids[{i}]",
            message=f"Conversion action {cid} is not in this account.",
        )
        for i, cid in enumerate(spec.conversion_action_ids)
        if cid not in by_id
    ]
    found = [
        (i, by_id[cid])
        for i, cid in enumerate(spec.conversion_action_ids)
        if cid in by_id
    ]
    disabled = [
        R.Problem(
            code="CONVERSION_ACTION_NOT_ENABLED",
            field=f"conversion_action_ids[{i}]",
            message=f"'{c.name}' is {c.status}.",
        )
        for i, c in found
        if c.status != "ENABLED"
    ]
    other_app = [
        R.Problem(
            code="APP_ID_MUST_MATCH_CONVERSION_ACTION_APP_ID",
            field=f"conversion_action_ids[{i}]",
            message=f"'{c.name}' tracks app '{c.app_id or 'none'}', not '{spec.app_id}'.",
        )
        for i, c in found
        if c.app_id != spec.app_id
    ]
    download = [
        R.Problem(
            code="CONVERSION_ACTION_WITH_DOWNLOAD_CATEGORY_NOT_ALLOWED",
            field=f"conversion_action_ids[{i}]",
            message=f"'{c.name}' is a DOWNLOAD action; optimize for in-app actions instead.",
        )
        for i, c in found
        if c.category == "DOWNLOAD"
    ]
    value_goal = R.GOALS.get(spec.goal)
    firebase = [
        R.Problem(
            code="VALUE_GOAL_NEEDS_FIREBASE",
            field=f"conversion_action_ids[{i}]",
            message=f"'{c.name}' is {c.type}; value goals use Firebase SDK conversion actions.",
            severity="WARNING",
        )
        for i, c in found
        if value_goal is not None
        and value_goal.bidding in ("target_roas", "maximize_conversion_value")
        and not c.type.startswith("FIREBASE_")
    ]
    linked = (
        []
        if any(c.app_id == spec.app_id for c in facts.conversion_actions)
        else [
            R.Problem(
                code="APP_NOT_TRACKED",
                field="app_id",
                message=f"No conversion action in this account tracks '{spec.app_id}'.",
                fix="Link the app (Google Play, Firebase or an app analytics partner) so installs are measured.",
                severity="WARNING",
            )
        ]
    )
    return missing + disabled + other_app + download + firebase + linked


_SLOTS: tuple[tuple[str, str], ...] = (
    ("image_asset_ids", "IMAGE"),
    ("youtube_video_asset_ids", "YOUTUBE_VIDEO"),
    ("html5_asset_ids", "MEDIA_BUNDLE"),
)


def asset_problems(spec: AppSpec, facts: AccountFacts) -> list[R.Problem]:
    by_id = {a.id: a for a in facts.assets}
    listed = [
        (f"{slot}[{i}]", aid, want)
        for slot, want in _SLOTS
        for i, aid in enumerate(getattr(spec, slot))
    ]
    missing = [
        R.Problem(
            code="ASSET_NOT_FOUND",
            field=f,
            message=f"Asset {aid} is not in this account.",
        )
        for f, aid, _ in listed
        if aid not in by_id
    ]
    wrong = [
        R.Problem(
            code="ASSET_TYPE_MISMATCH",
            field=f,
            message=f"Asset {aid} is {by_id[aid].type}, not {want}.",
        )
        for f, aid, want in listed
        if aid in by_id and by_id[aid].type != want
    ]
    images = [
        p
        for f, aid, want in listed
        if want == "IMAGE" and aid in by_id and by_id[aid].type == "IMAGE"
        for p in check_image(
            f,
            by_id[aid].width,
            by_id[aid].height,
            by_id[aid].size_bytes,
            by_id[aid].mime,
        )
    ]
    counts = check_counts(
        len(spec.image_asset_ids),
        len(spec.youtube_video_asset_ids),
        len(spec.html5_asset_ids),
        spec.kind,
    )
    return missing + wrong + images + counts


def check(spec: AppSpec, facts: AccountFacts) -> CheckReport:
    shape = R.AD_SHAPES[spec.kind]
    goal = R.GOALS.get(spec.goal)
    problems: tuple[R.Problem, ...] = tuple(
        check_app_id(spec.app_id, spec.store)
        + check_goal(
            spec.kind,
            spec.store,
            spec.goal,
            spec.target_cpa_micros,
            spec.target_roas,
            spec.conversion_action_ids,
        )
        + check_texts(spec.headlines, spec.descriptions)
        + conversion_problems(spec, facts)
        + asset_problems(spec, facts)
        + audience_problems(spec, facts)
        + geo_problems(spec)
    )
    return CheckReport(
        ok=not any(p.severity == "ERROR" for p in problems),
        problems=problems,
        campaign_sub_type=shape.sub_type,
        ad_field=shape.ad_field,
        bidding=goal.bidding if goal else "",
    )


def ad_problems(spec: AppSpec, facts: AccountFacts) -> list[R.Problem]:
    """The ad-level part of `check`: texts, assets, audiences (an ad group added to an existing campaign)."""
    return (
        check_texts(spec.headlines, spec.descriptions)
        + asset_problems(spec, facts)
        + audience_problems(spec, facts)
    )


def needed_ids(spec: AppSpec) -> Sequence[str]:
    """Every asset id the spec names (what the edge must read)."""
    return tuple(
        dict.fromkeys(
            spec.image_asset_ids
            + spec.youtube_video_asset_ids
            + spec.html5_asset_ids
        )
    )
