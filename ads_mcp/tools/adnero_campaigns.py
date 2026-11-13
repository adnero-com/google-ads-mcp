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

"""Campaign, ad group and ad writes (adnero fork; see ADNERO.md).

Status, budgets, bids and creates. Everything new is created PAUSED: no
argument can make it live; turning it on is a separate `set_status` call.
"""

from enum import Enum
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp.api_version import API_VERSION
from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp import eu_read
from ads_mcp.core.result import Err
from ads_mcp.rules import app_build
from ads_mcp.rules import eu as eu_rule
from ads_mcp.tools.adnero_app_build import set_eu

campaigns_mcp = FastMCP("campaigns")


class Level(str, Enum):
    CAMPAIGN = "CAMPAIGN"
    AD_GROUP = "AD_GROUP"
    AD = "AD"
    KEYWORD = "KEYWORD"


class Status(str, Enum):
    PAUSED = "PAUSED"
    ENABLED = "ENABLED"


class Bidding(str, Enum):
    MANUAL_CPC = "MANUAL_CPC"
    MAXIMIZE_CLICKS = "MAXIMIZE_CLICKS"
    MAXIMIZE_CONVERSIONS = "MAXIMIZE_CONVERSIONS"


# level -> (operation field, resource collection, status enum, id shape)
_LEVELS: dict[str, tuple[str, str, str, str | None]] = {
    "CAMPAIGN": ("campaign_operation", "campaigns", "CampaignStatusEnum", None),
    "AD_GROUP": ("ad_group_operation", "adGroups", "AdGroupStatusEnum", None),
    "AD": (
        "ad_group_ad_operation",
        "adGroupAds",
        "AdGroupAdStatusEnum",
        "adGroupId~adId",
    ),
    "KEYWORD": (
        "ad_group_criterion_operation",
        "adGroupCriteria",
        "AdGroupCriterionStatusEnum",
        "adGroupId~criterionId",
    ),
}


def _value(v: Any) -> str:
    return v.value if isinstance(v, Enum) else str(v).upper()


def _ids_for(level: str, ids: list[str]) -> list[str]:
    shape = _LEVELS[level][3]
    return [
        m.composite_id(i, "ids", shape) if shape else m.plain_id(i, "ids")
        for i in m.bounded(ids, "ids")
    ]


@campaigns_mcp.tool(
    title="Pause or turn on campaigns, ad groups, ads or keywords",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def set_status(
    customer_id: str | int,
    level: Level,
    ids: list[str],
    status: Status,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Pauses or turns on campaigns, ad groups, ads or keywords (one level per call).

    App ads (App campaigns) cannot be paused: pausing one is refused with the reason; pause its ad group
    (level AD_GROUP) or campaign instead. Costs 1 Google Ads operation per item on each call (the preview counts
    too), +1 to read the ad types when pausing ads.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        level: CAMPAIGN, AD_GROUP, AD or KEYWORD.
        ids: Campaign or ad group ids; for AD 'adGroupId~adId'; for KEYWORD 'adGroupId~criterionId'.
        status: PAUSED or ENABLED.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    lvl, st = _value(level), _value(status)
    if lvl not in _LEVELS or st not in ("PAUSED", "ENABLED"):
        raise ToolError("Error: unknown level or status.")
    targets = _ids_for(lvl, ids)
    if lvl == "AD" and st == "PAUSED":
        # App ads cannot be paused (AD_TYPE_CANNOT_BE_PAUSED): say so before Google does (1 search).
        refusal = app_build.pause_refusal(_ad_types(cid, targets))
        if refusal:
            raise ToolError(refusal)
    op_field, collection, enum_name, _ = _LEVELS[lvl]

    def operations(client):
        ops = []
        for t in targets:
            op = client.get_type("MutateOperation", version=API_VERSION)
            inner = getattr(op, op_field)
            inner.update.resource_name = f"customers/{cid}/{collection}/{t}"
            inner.update.status = getattr(getattr(client.enums, enum_name), st)
            inner.update_mask.paths.append("status")
            ops.append(op)
        return ops

    items = [
        pac.ChangeItem(
            operation="UPDATE",
            entity_readable=lvl.replace("_", " ").title(),
            primary_key_readable=t,
            field_readable="Status",
            value_readable=st,
        )
        for t in targets
    ]
    return m.mutate(
        tool="set_status",
        customer_id=cid,
        payload={"level": lvl, "ids": targets, "status": st},
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id or checksum,
        ctx=ctx,
    )


def _ad_types(cid: str, targets: list[str]) -> list[tuple[str, str]]:
    names = ",".join(f"'customers/{cid}/adGroupAds/{t}'" for t in targets)
    rows = m.search_rows(
        cid,
        "SELECT ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.type FROM ad_group_ad "
        f"WHERE ad_group_ad.resource_name IN ({names})",
    )
    return [
        (
            f"{r.ad_group.id}~{r.ad_group_ad.ad.id}",
            getattr(
                r.ad_group_ad.ad.type_, "name", str(r.ad_group_ad.ad.type_)
            ),
        )
        for r in rows
    ]


def _campaign_budget(cid: str, campaign_id: str) -> tuple[str, int, bool]:
    rows = m.search_rows(
        cid,
        "SELECT campaign.id, campaign_budget.resource_name, "
        "campaign_budget.amount_micros, campaign_budget.explicitly_shared "
        f"FROM campaign WHERE campaign.id = {campaign_id}",
    )
    if not rows:
        raise ToolError(f"Error: campaign {campaign_id} not found in {cid}.")
    b = rows[0].campaign_budget
    return b.resource_name, int(b.amount_micros), bool(b.explicitly_shared)


@campaigns_mcp.tool(
    title="Set a campaign's daily budget",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def set_budget(
    customer_id: str | int,
    campaign_id: str,
    daily_budget_micros: int,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Sets a campaign's daily budget (its own budget; a shared budget is refused).

    Costs 1 Google Ads operation per item on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        campaign_id: The campaign.
        daily_budget_micros: New average daily budget in micros of the account currency (10 EUR = 10000000).
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    camp = m.plain_id(campaign_id, "campaign_id")
    amount = m.non_negative_micros(daily_budget_micros, "daily_budget_micros")
    budget_rn, current, shared = _campaign_budget(cid, camp)
    if shared:
        raise ToolError(
            "Error: this campaign uses a shared budget; changing it would "
            "change every campaign on it. Not supported here."
        )

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        op.campaign_budget_operation.update.resource_name = budget_rn
        op.campaign_budget_operation.update.amount_micros = amount
        op.campaign_budget_operation.update_mask.paths.append("amount_micros")
        return [op]

    items = [
        pac.ChangeItem(
            operation="UPDATE",
            entity_readable="Campaign budget",
            primary_key_readable=f"campaign {camp}",
            field_readable="Daily budget (micros)",
            value_readable=str(amount),
            original_value_readable=str(current),
        )
    ]
    return m.mutate(
        tool="set_budget",
        customer_id=cid,
        payload={"campaign_id": camp, "daily_budget_micros": amount},
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id or checksum,
        ctx=ctx,
    )


@campaigns_mcp.tool(
    title="Set ad group or keyword bids",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def set_bids(
    customer_id: str | int,
    level: Level,
    id: str,
    cpc_bid_micros: int | None = None,
    target_cpa_micros: int | None = None,
    target_roas: float | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Sets bids on an ad group (max CPC, target CPA, target ROAS) or a keyword (max CPC).

    Costs 1 Google Ads operation per item on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        level: AD_GROUP or KEYWORD.
        id: Ad group id, or 'adGroupId~criterionId' for a keyword.
        cpc_bid_micros: Max CPC in micros.
        target_cpa_micros: Ad group target CPA in micros (Maximize conversions campaigns).
        target_roas: Ad group target ROAS as a ratio (3.5 = 350%).
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    lvl = _value(level)
    if lvl not in ("AD_GROUP", "KEYWORD"):
        raise ToolError("Error: bids are set on AD_GROUP or KEYWORD.")
    target = _ids_for(lvl, [id])[0]
    fields: dict[str, Any] = {
        "cpc_bid_micros": m.non_negative_micros(
            cpc_bid_micros, "cpc_bid_micros"
        ),
        "target_cpa_micros": m.non_negative_micros(
            target_cpa_micros, "target_cpa_micros"
        ),
        "target_roas": target_roas,
    }
    if lvl == "KEYWORD" and (
        fields["target_cpa_micros"] is not None
        or fields["target_roas"] is not None
    ):
        raise ToolError("Error: a keyword takes only cpc_bid_micros.")
    if target_roas is not None and not target_roas > 0:
        raise ToolError("Error: 'target_roas' must be above 0.")
    chosen = {k: v for k, v in fields.items() if v is not None}
    if not chosen:
        raise ToolError("Error: name at least one bid to set.")
    op_field, collection, _, _ = _LEVELS[lvl]

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        inner = getattr(op, op_field)
        inner.update.resource_name = f"customers/{cid}/{collection}/{target}"
        for k, v in chosen.items():
            setattr(inner.update, k, v)
            inner.update_mask.paths.append(k)
        return [op]

    items = pac.build_change_items(
        operation="UPDATE",
        entity_readable=lvl.replace("_", " ").title(),
        primary_key_readable=target,
        fields=chosen,
    )
    return m.mutate(
        tool="set_bids",
        customer_id=cid,
        payload={"level": lvl, "id": target, **chosen},
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id or checksum,
        ctx=ctx,
    )


@campaigns_mcp.tool(
    title="Create a Search campaign (paused)",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def create(
    customer_id: str | int,
    name: str,
    daily_budget_micros: int,
    bidding: Bidding = Bidding.MAXIMIZE_CLICKS,
    target_cpa_micros: int | None = None,
    location_ids: list[int] | None = None,
    language_ids: list[int] | None = None,
    search_partners: bool = False,
    start_date_time: str | None = None,
    end_date_time: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
    contains_eu_political_advertising: bool | None = None,
) -> m.MutationResult:
    """Creates a Search campaign with its own daily budget, always PAUSED.

    EU: when a targeted location needs the EU political advertising self-declaration (Google says which) or no
    location is set, the person must state contains_eu_political_advertising; otherwise it is "does not
    contain"; if Google's answer cannot be read, the field is left out and Google enforces it.

    Costs 1 Google Ads operation per item on each call (the preview counts too), +1 to read the locations' EU
    requirement when contains_eu_political_advertising is not given.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        name: Campaign name (unique in the account).
        daily_budget_micros: Average daily budget in micros.
        bidding: MANUAL_CPC, MAXIMIZE_CLICKS (default) or MAXIMIZE_CONVERSIONS.
        target_cpa_micros: Optional target CPA for MAXIMIZE_CONVERSIONS.
        location_ids: Geo target constant ids (2840 = United States). None: everywhere.
        language_ids: Language constant ids (1000 = English). None: all languages.
        search_partners: Also show on Google search partners.
        start_date_time: 'yyyy-MM-dd HH:mm:ss' in the account time zone; None: now.
        end_date_time: 'yyyy-MM-dd HH:mm:ss'; None: no end.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    if not name.strip():
        raise ToolError("Error: 'name' is required.")
    amount = m.non_negative_micros(daily_budget_micros, "daily_budget_micros")
    bid = _value(bidding)
    if bid not in Bidding.__members__:
        raise ToolError("Error: unknown bidding.")
    cpa = m.non_negative_micros(target_cpa_micros, "target_cpa_micros")
    if cpa is not None and bid != "MAXIMIZE_CONVERSIONS":
        raise ToolError("Error: target_cpa_micros needs MAXIMIZE_CONVERSIONS.")
    geos = [int(g) for g in (location_ids or [])]
    langs = [int(lg) for lg in (language_ids or [])]
    eu_answer = eu_rule.decide(
        contains_eu_political_advertising,
        [str(g) for g in geos],
        (
            eu_read.eu_requiring(cid, [str(g) for g in geos], login_customer_id)
            if contains_eu_political_advertising is None
            else ()
        ),
    )
    if isinstance(eu_answer, Err):
        p = eu_answer.error
        raise ToolError(f"Error: {p.code}: {p.message} Fix: {p.fix}")
    eu = eu_answer.value
    budget_rn = f"customers/{cid}/campaignBudgets/-1"
    campaign_rn = f"customers/{cid}/campaigns/-2"

    def operations(client):
        e = client.enums
        b_op = client.get_type("MutateOperation", version=API_VERSION)
        budget = b_op.campaign_budget_operation.create
        budget.resource_name = budget_rn
        budget.name = f"{name.strip()} budget"
        budget.amount_micros = amount
        budget.delivery_method = e.BudgetDeliveryMethodEnum.STANDARD
        budget.explicitly_shared = False

        c_op = client.get_type("MutateOperation", version=API_VERSION)
        c = c_op.campaign_operation.create
        c.resource_name = campaign_rn
        c.name = name.strip()
        c.status = e.CampaignStatusEnum.PAUSED
        c.advertising_channel_type = e.AdvertisingChannelTypeEnum.SEARCH
        c.campaign_budget = budget_rn
        c.network_settings.target_google_search = True
        c.network_settings.target_search_network = bool(search_partners)
        c.network_settings.target_content_network = False
        set_eu(client, c, eu)
        if bid == "MANUAL_CPC":
            client.copy_from(
                c.manual_cpc, client.get_type("ManualCpc", version=API_VERSION)
            )
        elif bid == "MAXIMIZE_CLICKS":
            client.copy_from(
                c.target_spend,
                client.get_type("TargetSpend", version=API_VERSION),
            )
        else:
            mc = client.get_type("MaximizeConversions", version=API_VERSION)
            if cpa is not None:
                mc.target_cpa_micros = cpa
            client.copy_from(c.maximize_conversions, mc)
        if start_date_time:
            c.start_date_time = start_date_time
        if end_date_time:
            c.end_date_time = end_date_time

        criteria = []
        for g in geos:
            op = client.get_type("MutateOperation", version=API_VERSION)
            cc = op.campaign_criterion_operation.create
            cc.campaign = campaign_rn
            cc.location.geo_target_constant = f"geoTargetConstants/{g}"
            criteria.append(op)
        for lg in langs:
            op = client.get_type("MutateOperation", version=API_VERSION)
            cc = op.campaign_criterion_operation.create
            cc.campaign = campaign_rn
            cc.language.language_constant = f"languageConstants/{lg}"
            criteria.append(op)
        return [b_op, c_op, *criteria]

    payload = {
        "name": name.strip(),
        "daily_budget_micros": amount,
        "bidding": bid,
        "target_cpa_micros": cpa,
        "location_ids": geos,
        "language_ids": langs,
        "search_partners": bool(search_partners),
        "start_date_time": start_date_time,
        "end_date_time": end_date_time,
        "eu": eu,
    }
    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Campaign (Search, PAUSED)",
        primary_key_readable=name.strip(),
        fields={
            "Status": "PAUSED",
            "Daily budget (micros)": amount,
            "Bidding": bid,
            "Target CPA (micros)": cpa,
            "Locations": geos or None,
            "Languages": langs or None,
            "Search partners": bool(search_partners),
            "Start": start_date_time,
            "End": end_date_time,
            "EU political advertising": eu,
        },
    )
    return m.mutate(
        tool="create_campaign",
        customer_id=cid,
        payload=payload,
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id or checksum,
        ctx=ctx,
    )


@campaigns_mcp.tool(
    title="Create an ad group (paused)",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def create_ad_group(
    customer_id: str | int,
    campaign_id: str,
    name: str,
    cpc_bid_micros: int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Creates a Search ad group in an existing campaign, PAUSED.

    Costs 1 Google Ads operation per item on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        campaign_id: The campaign it goes in.
        name: Ad group name.
        cpc_bid_micros: Optional max CPC in micros.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    camp = m.plain_id(campaign_id, "campaign_id")
    if not name.strip():
        raise ToolError("Error: 'name' is required.")
    cpc = m.non_negative_micros(cpc_bid_micros, "cpc_bid_micros")

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        g = op.ad_group_operation.create
        g.campaign = f"customers/{cid}/campaigns/{camp}"
        g.name = name.strip()
        g.status = client.enums.AdGroupStatusEnum.PAUSED
        g.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        if cpc is not None:
            g.cpc_bid_micros = cpc
        return [op]

    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Ad group (PAUSED)",
        primary_key_readable=name.strip(),
        fields={"Campaign": camp, "Status": "PAUSED", "Max CPC (micros)": cpc},
    )
    return m.mutate(
        tool="create_ad_group",
        customer_id=cid,
        payload={"campaign_id": camp, "name": name.strip(), "cpc": cpc},
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id or checksum,
        ctx=ctx,
    )


@campaigns_mcp.tool(
    title="Create a responsive search ad (paused)",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def create_ad(
    customer_id: str | int,
    ad_group_id: str,
    headlines: list[str],
    descriptions: list[str],
    final_urls: list[str],
    path1: str | None = None,
    path2: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Creates a responsive search ad in an existing ad group, PAUSED.

    Costs 1 Google Ads operation per item on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        ad_group_id: The ad group it goes in.
        headlines: 3 to 15 headlines, 30 characters each at most.
        descriptions: 2 to 4 descriptions, 90 characters each at most.
        final_urls: The landing page URL(s).
        path1: Optional display path part (15 characters).
        path2: Optional second display path part (15 characters).
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    group = m.plain_id(ad_group_id, "ad_group_id")
    if not 3 <= len(headlines) <= 15 or not 2 <= len(descriptions) <= 4:
        raise ToolError("Error: 3-15 headlines and 2-4 descriptions.")
    if not final_urls:
        raise ToolError("Error: 'final_urls' is required.")

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        aga = op.ad_group_ad_operation.create
        aga.ad_group = f"customers/{cid}/adGroups/{group}"
        aga.status = client.enums.AdGroupAdStatusEnum.PAUSED
        aga.ad.final_urls.extend(final_urls)
        rsa = aga.ad.responsive_search_ad
        for text in headlines:
            asset = client.get_type("AdTextAsset", version=API_VERSION)
            asset.text = text
            rsa.headlines.append(asset)
        for text in descriptions:
            asset = client.get_type("AdTextAsset", version=API_VERSION)
            asset.text = text
            rsa.descriptions.append(asset)
        if path1:
            rsa.path1 = path1
        if path2:
            rsa.path2 = path2
        return [op]

    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Responsive search ad (PAUSED)",
        primary_key_readable=f"ad group {group}",
        fields={
            "Headlines": list(headlines),
            "Descriptions": list(descriptions),
            "Final URLs": list(final_urls),
            "Path 1": path1,
            "Path 2": path2,
        },
    )
    return m.mutate(
        tool="create_ad",
        customer_id=cid,
        payload={
            "ad_group_id": group,
            "headlines": list(headlines),
            "descriptions": list(descriptions),
            "final_urls": list(final_urls),
            "path1": path1,
            "path2": path2,
        },
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id or checksum,
        ctx=ctx,
    )
