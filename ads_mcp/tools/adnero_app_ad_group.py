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

"""app_ad_group_create (edge): an ad group (PAUSED) with its App ad, in one atomic Mutate."""

from fastmcp import Context
from fastmcp.exceptions import ToolError

from ads_mcp import adnero_mutate as m
from ads_mcp.rules import app as R
from ads_mcp.rules.app_check import ad_problems
from ads_mcp.tools import adnero_app_build as B
from ads_mcp.tools.adnero_app import app_mcp, read_facts

from ads_mcp.tools.adnero_app_common import (
    _CREATE,
    _items,
    campaign_now,
    read_back,
    refuse_errors,
    spec_from_campaign,
)


@app_mcp.tool(
    title="Add an ad group with its App ad to an App campaign (paused)",
    annotations=_CREATE,
)
def ad_group_create(
    customer_id: str | int,
    campaign_id: str,
    name: str,
    headlines: list[str],
    descriptions: list[str],
    image_asset_ids: list[str] | None = None,
    youtube_video_asset_ids: list[str] | None = None,
    html5_asset_ids: list[str] | None = None,
    user_list_ids: list[str] | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Adds an ad group (PAUSED) with its App ad (ENABLED: App ads cannot be paused) to an App campaign, in one
    atomic request. The ad type follows the campaign (install: app_ad, engagement: app_engagement_ad,
    pre-registration: app_pre_registration_ad). Texts and assets are checked first (limits, types, image
    ratios); engagement campaigns need a user list. Turn the ad group on with campaigns_set_status AD_GROUP.

    Costs 1 operation for the campaign, 1-2 for the asset and user list checks, plus 1 per created object on each
    call (the preview counts too), plus 1 for the read-back on apply.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED,
    a plan_id). Show change_items to the person; once they approve, call again with the SAME arguments,
    validate_only=false and the plan_id (valid 15 minutes).

    Args:
        customer_id: The Google Ads account.
        campaign_id: The App campaign.
        name: Ad group name.
        headlines: 2-5 headlines, 30 characters each.
        descriptions: 1-5 descriptions, 90 characters each.
        image_asset_ids: IMAGE asset ids.
        youtube_video_asset_ids: YOUTUBE_VIDEO asset ids.
        html5_asset_ids: MEDIA_BUNDLE asset ids (install campaigns only).
        user_list_ids: User lists (engagement: required).
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    from ads_mcp.tools.adnero_app import _ids

    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    camp = m.plain_id(campaign_id, "campaign_id")
    if not name.strip():
        raise ToolError("Error: 'name' is required.")
    row = campaign_now(cid, camp, login_customer_id)
    spec = spec_from_campaign(
        row,
        headlines=tuple(headlines),
        descriptions=tuple(descriptions),
        image_asset_ids=_ids(image_asset_ids, "image_asset_ids"),
        youtube_video_asset_ids=_ids(
            youtube_video_asset_ids, "youtube_video_asset_ids"
        ),
        html5_asset_ids=_ids(html5_asset_ids, "html5_asset_ids"),
        user_list_ids=_ids(user_list_ids, "user_list_ids"),
    )
    facts = read_facts(cid, spec, login_customer_id, conversions=False)
    notes = refuse_errors(ad_problems(spec, facts))
    campaign = B.rn(cid, "campaigns", camp)
    group = B.rn(cid, "adGroups", -1)

    def operations(client):
        return (
            [
                B.ad_group_op(
                    client, cid, campaign, name.strip(), -1, paused=True
                )
            ]
            + B.user_list_ops(client, cid, group, spec.user_list_ids)
            + [B.ad_op(client, group, spec.kind, B.content_of(cid, spec))]
        )

    shape = R.AD_SHAPES[spec.kind]
    items = _items(
        {
            "Campaign": camp,
            "Status": "PAUSED",
            "User lists": list(spec.user_list_ids) or None,
        },
        "Ad group",
        name.strip(),
    ) + _items(
        {
            "Status": "ENABLED (App ads cannot be paused)",
            "Headlines": list(spec.headlines),
            "Descriptions": list(spec.descriptions),
            "Images": list(spec.image_asset_ids) or None,
            "Videos": list(spec.youtube_video_asset_ids) or None,
            "HTML5": list(spec.html5_asset_ids) or None,
        },
        f"App ad ({shape.ad_field})",
        name.strip(),
    )
    return m.mutate(
        tool="app_ad_group_create",
        customer_id=cid,
        payload={
            "campaign_id": camp,
            "name": name.strip(),
            "spec": spec.model_dump(mode="json"),
        },
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
        mutable_resource=True,
        read_back=read_back(login_customer_id),
        notes=notes,
    )
