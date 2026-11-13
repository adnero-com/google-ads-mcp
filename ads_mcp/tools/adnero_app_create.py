# Copyright 2026 Adnero.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""app_campaign_create (edge): check, then ONE atomic Mutate, then read back."""

from fastmcp import Context
from fastmcp.exceptions import ToolError

from ads_mcp import adnero_mutate as m
from ads_mcp.core.result import Err
from ads_mcp.rules import app as R
from ads_mcp.rules.app_build import day_bounds
from ads_mcp.rules.app_check import check as check_spec
from ads_mcp.rules.app_targeting import eu_declaration
from ads_mcp.tools import adnero_app_build as B
from ads_mcp.tools.adnero_app import app_mcp, read_facts, spec_of

from ads_mcp.tools.adnero_app_common import (
    _CREATE,
    _items,
    read_back,
    refuse_errors,
)


@app_mcp.tool(title="Create an App campaign (paused)", annotations=_CREATE)
def campaign_create(
    customer_id: str | int,
    kind: str,
    name: str,
    app_id: str,
    store: str,
    goal: str,
    daily_budget_micros: int,
    target_cpa_micros: int | None = None,
    target_roas: float | None = None,
    conversion_action_ids: list[str] | None = None,
    geo_target_ids: list[str] | None = None,
    excluded_geo_target_ids: list[str] | None = None,
    language_ids: list[str] | None = None,
    headlines: list[str] | None = None,
    descriptions: list[str] | None = None,
    image_asset_ids: list[str] | None = None,
    youtube_video_asset_ids: list[str] | None = None,
    html5_asset_ids: list[str] | None = None,
    user_list_ids: list[str] | None = None,
    ad_group_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    contains_eu_political_advertising: bool | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Creates a whole App campaign in ONE atomic request: its own budget, the campaign (always PAUSED), location
    and language targeting (with exclusions), an ad group, its user lists (engagement: required), and the App ad.

    The campaign is the one on/off switch: it is created PAUSED, while the ad group inside it is ENABLED and the
    App ad is ENABLED (App ads cannot be paused). Nothing serves until the campaign is turned on with
    campaigns_set_status; after that, pause the campaign or the ad group, never the ad.
    EU: when any targeted location is an EU country, or no location is set (everywhere), the person must state
    contains_eu_political_advertising (refused otherwise); with only non-EU locations it defaults to false.
    Everything app_check checks is checked first; any error refuses before Google. Takes app_check's arguments.
    After the apply, the answer reads back primary_status and the ad's policy review.

    Costs 1-3 operations for the check, plus 1 per created object (budget, campaign, each criterion, ad group,
    each user list, ad) on each call (the preview counts too), plus 1 for the read-back on apply.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED,
    a plan_id). Show change_items to the person; once they approve, call again with the SAME arguments,
    validate_only=false and the plan_id (valid 15 minutes; any changed argument voids it).

    Args:
        customer_id: The Google Ads account.
        kind: install, engagement or pre_registration.
        name: Campaign name (unique in the account).
        app_id: Google Play package name (com.example.app) or App Store numeric id.
        store: GOOGLE_APP_STORE or APPLE_APP_STORE.
        goal: AppCampaignBiddingStrategyGoalType, e.g. OPTIMIZE_INSTALLS_TARGET_INSTALL_COST.
        daily_budget_micros: Average daily budget in micros of the account currency (10 EUR = 10000000).
        target_cpa_micros: Target CPI/CPA in micros (goals with a CPA target).
        target_roas: Target ROAS as a ratio, 3.0 = 300% (OPTIMIZE_RETURN_ON_ADVERTISING_SPEND).
        conversion_action_ids: In-app conversion actions (in-app and value goals: required).
        geo_target_ids: Geo target constant ids to target (2840 = United States).
        excluded_geo_target_ids: Geo target constant ids to exclude.
        language_ids: Language constant ids (1000 = English).
        headlines: 2-5 headlines, 30 characters each.
        descriptions: 1-5 descriptions, 90 characters each.
        image_asset_ids: IMAGE asset ids.
        youtube_video_asset_ids: YOUTUBE_VIDEO asset ids.
        html5_asset_ids: MEDIA_BUNDLE asset ids (install only).
        user_list_ids: User lists for the ad group (engagement: required).
        ad_group_name: Default: '<name> ad group'.
        start_date: YYYY-MM-DD in the account's time zone; default: when it is turned on.
        end_date: YYYY-MM-DD; default: no end.
        contains_eu_political_advertising: Whether the ads are EU political advertising. Required when an EU
            country is targeted or no location is set (ask the person); otherwise defaults to false.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    if not name.strip():
        raise ToolError("Error: 'name' is required.")
    budget = m.non_negative_micros(daily_budget_micros, "daily_budget_micros")
    if not budget:
        raise ToolError("Error: 'daily_budget_micros' must be above 0.")
    dates = day_bounds(start_date, end_date)
    if isinstance(dates, Err):
        raise ToolError(f"Error: {dates.error}")
    start, end = dates.value
    spec = spec_of(
        kind, app_id, store, goal, target_cpa_micros, target_roas,
        conversion_action_ids, geo_target_ids, excluded_geo_target_ids, language_ids,
        headlines, descriptions, image_asset_ids, youtube_video_asset_ids,
        html5_asset_ids, user_list_ids, contains_eu_political_advertising,
    )  # fmt: skip
    report = check_spec(spec, read_facts(cid, spec, login_customer_id))
    notes = refuse_errors(report.problems)
    group_name = (ad_group_name or f"{name.strip()} ad group").strip()
    campaign = B.rn(cid, "campaigns", -2)
    group = B.rn(cid, "adGroups", -3)

    def operations(client):
        return (
            [
                B.budget_op(client, cid, name.strip(), budget),
                B.campaign_op(
                    client, cid, spec, name.strip(), start, end,
                    eu_declaration(spec),
                ),
            ]  # fmt: skip
            + B.criterion_ops(
                client, cid, campaign, spec.geo_target_ids,
                spec.excluded_geo_target_ids, spec.language_ids,
            )  # fmt: skip
            + [
                B.ad_group_op(
                    client, cid, campaign, group_name, -3, paused=False
                )
            ]
            + B.user_list_ops(client, cid, group, spec.user_list_ids)
            + [B.ad_op(client, group, spec.kind, B.content_of(cid, spec))]
        )

    shape = R.AD_SHAPES[spec.kind]
    items = (
        _items(
            {
                "Status": "PAUSED",
                "Type": f"MULTI_CHANNEL / {shape.sub_type}",
                "App": f"{spec.app_id} ({spec.store})",
                "Goal": spec.goal,
                "Bidding": report.bidding,
                "Target CPA (micros)": spec.target_cpa_micros,
                "Target ROAS": spec.target_roas,
                "Daily budget (micros)": budget,
                "Conversion actions": list(spec.conversion_action_ids) or None,
                "Locations": list(spec.geo_target_ids) or "all",
                "Excluded locations": list(spec.excluded_geo_target_ids)
                or None,
                "Languages": list(spec.language_ids) or "all",
                "Start": start,
                "End": end,
                "EU political advertising": eu_declaration(spec),
            },
            "App campaign (PAUSED)",
            name.strip(),
        )
        + _items(
            {
                "Status": "ENABLED (serves when the campaign is on)",
                "User lists": list(spec.user_list_ids) or None,
            },
            "Ad group",
            group_name,
        )
        + _items(
            {
                "Status": "ENABLED (App ads cannot be paused)",
                "Headlines": list(spec.headlines),
                "Descriptions": list(spec.descriptions),
                "Images": list(spec.image_asset_ids) or None,
                "Videos": list(spec.youtube_video_asset_ids) or None,
                "HTML5": list(spec.html5_asset_ids) or None,
            },
            f"App ad ({shape.ad_field})",
            group_name,
        )
    )
    payload = {
        "spec": spec.model_dump(mode="json"),
        "name": name.strip(),
        "daily_budget_micros": budget,
        "start": start,
        "end": end,
        "eu": eu_declaration(spec),
        "ad_group_name": group_name,
    }
    return m.mutate(
        tool="app_campaign_create",
        customer_id=cid,
        payload=payload,
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
        mutable_resource=True,
        read_back=read_back(login_customer_id),
        notes=notes,
    )
