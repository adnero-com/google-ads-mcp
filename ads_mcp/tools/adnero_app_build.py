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

"""MutateOperation builders for App campaigns: deterministic, no reads, no writes.

Temp ids: budget -1, campaign -2, ad group -3 (GoogleAdsService.Mutate resolves them in one
atomic request). The rules they follow are data in rules/app.py.
"""

from typing import Any

from ads_mcp.api_version import API_VERSION
from ads_mcp.rules import app as R
from ads_mcp.rules.app_build import AdContent
from ads_mcp.rules.app_spec import AppSpec


def rn(cid: str, collection: str, id_: str | int) -> str:
    return f"customers/{cid}/{collection}/{id_}"


def _op(client) -> Any:
    return client.get_type("MutateOperation", version=API_VERSION)


def budget_op(client, cid: str, name: str, micros: int) -> Any:
    op = _op(client)
    b = op.campaign_budget_operation.create
    b.resource_name = rn(cid, "campaignBudgets", -1)
    b.name = f"{name} budget"
    b.amount_micros = micros
    b.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    b.explicitly_shared = False  # App campaigns cannot share a budget
    return op


def set_bidding(
    client, campaign, goal: str, cpa: int | None, roas: float | None
):
    bidding = R.GOALS[goal].bidding
    if bidding == "target_cpa":
        campaign.target_cpa.target_cpa_micros = int(cpa or 0)
    elif bidding == "target_roas":
        campaign.target_roas.target_roas = float(roas or 0)
    elif bidding == "maximize_conversions":
        client.copy_from(
            campaign.maximize_conversions,
            client.get_type("MaximizeConversions", version=API_VERSION),
        )
    else:
        client.copy_from(
            campaign.maximize_conversion_value,
            client.get_type("MaximizeConversionValue", version=API_VERSION),
        )


def campaign_op(
    client,
    cid: str,
    spec: AppSpec,
    name: str,
    start: str | None,
    end: str | None,
    eu_political: bool,
) -> Any:
    e = client.enums
    op = _op(client)
    c = op.campaign_operation.create
    c.resource_name = rn(cid, "campaigns", -2)
    c.name = name
    c.status = (
        e.CampaignStatusEnum.PAUSED
    )  # always: turning it on is campaigns_set_status
    c.advertising_channel_type = e.AdvertisingChannelTypeEnum.MULTI_CHANNEL
    c.advertising_channel_sub_type = getattr(
        e.AdvertisingChannelSubTypeEnum, R.AD_SHAPES[spec.kind].sub_type
    )
    c.campaign_budget = rn(cid, "campaignBudgets", -1)
    c.app_campaign_setting.app_id = spec.app_id
    c.app_campaign_setting.app_store = getattr(
        e.AppCampaignAppStoreEnum, spec.store
    )
    c.app_campaign_setting.bidding_strategy_goal_type = getattr(
        e.AppCampaignBiddingStrategyGoalTypeEnum, spec.goal
    )
    set_bidding(client, c, spec.goal, spec.target_cpa_micros, spec.target_roas)
    c.selective_optimization.conversion_actions.extend(
        rn(cid, "conversionActions", i) for i in spec.conversion_action_ids
    )
    if spec.kind == "pre_registration":
        c.optimization_goal_setting.optimization_goal_types.append(
            e.OptimizationGoalTypeEnum.APP_PRE_REGISTRATION
        )
    c.contains_eu_political_advertising = (
        e.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
        if eu_political
        else e.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    if start:
        c.start_date_time = start
    if end:
        c.end_date_time = end
    return op


def criterion_ops(
    client,
    cid: str,
    campaign: str,
    geos: tuple[str, ...],
    excluded: tuple[str, ...],
    languages: tuple[str, ...],
) -> list[Any]:
    def one(fill) -> Any:
        op = _op(client)
        cc = op.campaign_criterion_operation.create
        cc.campaign = campaign
        fill(cc)
        return op

    def geo(id_, negative):
        def fill(cc):
            cc.location.geo_target_constant = f"geoTargetConstants/{id_}"
            cc.negative = negative

        return fill

    def lang(id_):
        def fill(cc):
            cc.language.language_constant = f"languageConstants/{id_}"

        return fill

    return (
        [one(geo(g, False)) for g in geos]
        + [one(geo(g, True)) for g in excluded]
        + [one(lang(lg)) for lg in languages]
    )


def ad_group_op(
    client, cid: str, campaign: str, name: str, temp_id: int, paused: bool
) -> Any:
    op = _op(client)
    g = op.ad_group_operation.create
    g.resource_name = rn(cid, "adGroups", temp_id)
    g.campaign = campaign
    g.name = name
    # App ad groups take no `type`.
    g.status = (
        client.enums.AdGroupStatusEnum.PAUSED
        if paused
        else client.enums.AdGroupStatusEnum.ENABLED
    )
    return op


def user_list_ops(
    client, cid: str, ad_group: str, ids: tuple[str, ...]
) -> list[Any]:
    def one(id_) -> Any:
        op = _op(client)
        c = op.ad_group_criterion_operation.create
        c.ad_group = ad_group
        c.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        c.user_list.user_list = rn(cid, "userLists", id_)
        return op

    return [one(i) for i in ids]


def fill_ad_info(client, info, kind: R.Kind, content: AdContent) -> None:
    """Writes the content into an AppAdInfo / AppEngagementAdInfo / AppPreRegistrationAdInfo."""

    def typed(name: str, **fields):
        value = client.get_type(name, version=API_VERSION)
        for k, v in fields.items():
            setattr(value, k, v)
        return value

    shape = R.AD_SHAPES[kind]
    info.headlines.extend(
        typed("AdTextAsset", text=t) for t in content.headlines
    )
    info.descriptions.extend(
        typed("AdTextAsset", text=t) for t in content.descriptions
    )
    info.images.extend(typed("AdImageAsset", asset=a) for a in content.images)
    getattr(info, shape.video_field).extend(
        typed("AdVideoAsset", asset=a) for a in content.videos
    )
    if shape.html5:
        info.html5_media_bundles.extend(
            typed("AdMediaBundleAsset", asset=a) for a in content.html5
        )


def ad_op(client, ad_group: str, kind: R.Kind, content: AdContent) -> Any:
    op = _op(client)
    aga = op.ad_group_ad_operation.create
    aga.ad_group = ad_group
    # App ads cannot be paused (AD_TYPE_CANNOT_BE_PAUSED): ENABLED, served by the ad group / campaign status.
    aga.status = client.enums.AdGroupAdStatusEnum.ENABLED
    fill_ad_info(
        client, getattr(aga.ad, R.AD_SHAPES[kind].ad_field), kind, content
    )
    return op


def content_of(cid: str, spec: AppSpec) -> AdContent:
    return AdContent(
        headlines=spec.headlines,
        descriptions=spec.descriptions,
        images=tuple(rn(cid, "assets", i) for i in spec.image_asset_ids),
        videos=tuple(
            rn(cid, "assets", i) for i in spec.youtube_video_asset_ids
        ),
        html5=tuple(rn(cid, "assets", i) for i in spec.html5_asset_ids),
    )


READ_BACK_FIELDS = (
    "campaign.resource_name",
    "campaign.status",
    "campaign.primary_status",
    "campaign.primary_status_reasons",
    "ad_group.resource_name",
    "ad_group.status",
    "ad_group.primary_status",
    "ad_group_ad.resource_name",
    "ad_group_ad.status",
    "ad_group_ad.primary_status",
    "ad_group_ad.primary_status_reasons",
    "ad_group_ad.policy_summary.approval_status",
    "ad_group_ad.policy_summary.review_status",
    "ad_group_ad.policy_summary.policy_topic_entries",
)


def read_back_query(ad_group_ad: str) -> str:
    return (
        f"SELECT {', '.join(READ_BACK_FIELDS)} FROM ad_group_ad "
        f"WHERE ad_group_ad.resource_name = '{ad_group_ad}'"
    )
