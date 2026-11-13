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

"""app_ad_update and app_campaign_update (edge): read the current state, refuse by the rules, one Mutate."""

from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp.api_version import API_VERSION
from ads_mcp.core.result import Err
from ads_mcp.rules import app as R
from ads_mcp.rules.app_build import AdContent, AdEdit, edit_ad, kind_of
from ads_mcp.rules.app_check import asset_problems
from ads_mcp.rules.app_creative import check_counts, check_texts
from ads_mcp.tools import adnero_app_build as B
from ads_mcp.tools.adnero_app import _ids, app_mcp, read_facts
from ads_mcp.tools.adnero_app_common import refuse_errors, spec_from_campaign

_UPDATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)
# AdContent field -> the ad info's field name (videos differ per ad type).
_MASK = {
    "headlines": "headlines",
    "descriptions": "descriptions",
    "images": "images",
    "html5": "html5_media_bundles",
}


def _search(cid: str, query: str, login) -> list[dict[str, Any]]:
    from ads_mcp.tools.search import search_page

    return search_page(cid, query, 10_000, None, login).rows


def _ad_now(cid: str, ad: str, login) -> tuple[dict[str, Any], R.Kind]:
    infos = [
        f"ad_group_ad.ad.{shape.ad_field}.{f}"
        for shape in R.AD_SHAPES.values()
        for f in ("headlines", "descriptions", "images", shape.video_field)
    ] + ["ad_group_ad.ad.app_ad.html5_media_bundles"]
    rows = _search(
        cid,
        "SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, campaign.advertising_channel_sub_type, "
        "campaign.app_campaign_setting.app_id, campaign.app_campaign_setting.app_store, "
        f"campaign.app_campaign_setting.bidding_strategy_goal_type, {', '.join(infos)} "
        f"FROM ad_group_ad WHERE ad_group_ad.resource_name = '{B.rn(cid, 'adGroupAds', ad)}'",
        login,
    )
    if not rows:
        raise ToolError(f"Error: ad {ad} not found in {cid}.")
    kind = kind_of(
        str(rows[0].get("campaign.advertising_channel_sub_type", ""))
    )
    if kind is None:
        raise ToolError(
            f"Error: ad {ad} is not in an App campaign; use the other ad tools."
        )
    return rows[0], kind


def _content(row: dict[str, Any], kind: R.Kind) -> AdContent:
    shape = R.AD_SHAPES[kind]

    def values(field: str, key: str) -> tuple[str, ...]:
        items = row.get(f"ad_group_ad.ad.{shape.ad_field}.{field}") or []
        return tuple(str(i.get(key, "")) for i in items if isinstance(i, dict))

    return AdContent(
        headlines=values("headlines", "text"),
        descriptions=values("descriptions", "text"),
        images=values("images", "asset"),
        videos=values(shape.video_field, "asset"),
        html5=values("html5_media_bundles", "asset") if shape.html5 else (),
    )


@app_mcp.tool(
    title="Change an App ad's texts, images, videos or HTML5",
    annotations=_UPDATE,
)
def ad_update(
    customer_id: str | int,
    ad: str,
    add_headlines: list[str] | None = None,
    remove_headlines: list[str] | None = None,
    add_descriptions: list[str] | None = None,
    remove_descriptions: list[str] | None = None,
    add_image_asset_ids: list[str] | None = None,
    remove_image_asset_ids: list[str] | None = None,
    add_video_asset_ids: list[str] | None = None,
    remove_video_asset_ids: list[str] | None = None,
    add_html5_asset_ids: list[str] | None = None,
    remove_html5_asset_ids: list[str] | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Adds or removes an App ad's headlines, descriptions, images, videos or HTML5 bundles, in place (the ad
    keeps its stats; Google calls the ad's content through AdService, here inside one atomic Mutate). The result
    must still fit the App limits (2-5 headlines of 30, 1-5 descriptions of 90, <=20 images/videos/HTML5, image
    ratios); added assets are checked first. change_items show each changed field before and after.

    Costs 1 operation to read the ad, +1 when assets are added (their check), +1 for the update on each call.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED,
    a plan_id). Show change_items to the person; once they approve, call again with the SAME arguments,
    validate_only=false and the plan_id (valid 15 minutes).

    Args:
        customer_id: The Google Ads account.
        ad: 'adGroupId~adId'.
        add_headlines: Headlines to add.
        remove_headlines: Headlines to remove (exact text).
        add_descriptions: Descriptions to add.
        remove_descriptions: Descriptions to remove (exact text).
        add_image_asset_ids: IMAGE asset ids to add.
        remove_image_asset_ids: IMAGE asset ids to remove.
        add_video_asset_ids: YOUTUBE_VIDEO asset ids to add.
        remove_video_asset_ids: YOUTUBE_VIDEO asset ids to remove.
        add_html5_asset_ids: MEDIA_BUNDLE asset ids to add (install ads only).
        remove_html5_asset_ids: MEDIA_BUNDLE asset ids to remove.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    target = m.composite_id(ad, "ad", "adGroupId~adId")
    row, kind = _ad_now(cid, target, login_customer_id)
    before = _content(row, kind)

    def names(ids, what):
        return tuple(B.rn(cid, "assets", i) for i in _ids(ids, what))

    edit = AdEdit(
        add_headlines=tuple(add_headlines or ()),
        remove_headlines=tuple(remove_headlines or ()),
        add_descriptions=tuple(add_descriptions or ()),
        remove_descriptions=tuple(remove_descriptions or ()),
        add_images=names(add_image_asset_ids, "add_image_asset_ids"),
        remove_images=names(remove_image_asset_ids, "remove_image_asset_ids"),
        add_videos=names(add_video_asset_ids, "add_video_asset_ids"),
        remove_videos=names(remove_video_asset_ids, "remove_video_asset_ids"),
        add_html5=names(add_html5_asset_ids, "add_html5_asset_ids"),
        remove_html5=names(remove_html5_asset_ids, "remove_html5_asset_ids"),
    )
    edited = edit_ad(before, edit)
    if isinstance(edited, Err):
        refuse_errors(edited.error)
    after, changed = edited.value
    added = spec_from_campaign(
        row,
        image_asset_ids=_ids(add_image_asset_ids, "add_image_asset_ids"),
        youtube_video_asset_ids=_ids(
            add_video_asset_ids, "add_video_asset_ids"
        ),
        html5_asset_ids=_ids(add_html5_asset_ids, "add_html5_asset_ids"),
    )
    facts = read_facts(cid, added, login_customer_id, conversions=False)
    notes = refuse_errors(
        check_texts(after.headlines, after.descriptions)
        + check_counts(
            len(after.images), len(after.videos), len(after.html5), kind
        )
        + asset_problems(added, facts)
    )
    shape = R.AD_SHAPES[kind]
    ad_id = target.split("~")[1]

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        update = op.ad_operation.update
        update.resource_name = B.rn(cid, "ads", ad_id)
        B.fill_ad_info(client, getattr(update, shape.ad_field), kind, after)
        op.ad_operation.update_mask.paths.extend(
            f"{shape.ad_field}.{_MASK.get(f, shape.video_field)}"
            for f in changed
        )
        return [op]

    items = [
        pac.ChangeItem(
            operation="UPDATE",
            entity_readable=f"App ad ({shape.ad_field})",
            primary_key_readable=target,
            field_readable=f,
            value_readable=" | ".join(getattr(after, f)),
            original_value_readable=" | ".join(getattr(before, f)) or "(none)",
        )
        for f in changed
    ]
    return m.mutate(
        tool="app_ad_update",
        customer_id=cid,
        payload={
            "ad": target,
            "before": before.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
        },
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
        notes=notes,
    )
