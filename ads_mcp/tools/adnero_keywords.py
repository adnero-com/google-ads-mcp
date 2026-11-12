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

"""Keyword writes (adnero fork; see ADNERO.md): add and remove, positive and negative."""

from enum import Enum

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac

keywords_mcp = FastMCP("keywords")


class MatchType(str, Enum):
    EXACT = "EXACT"
    PHRASE = "PHRASE"
    BROAD = "BROAD"


class KeywordLevel(str, Enum):
    AD_GROUP = "AD_GROUP"
    CAMPAIGN = "CAMPAIGN"


class Keyword(BaseModel):
    text: str
    match_type: MatchType = MatchType.EXACT


def _keywords(items: list[Keyword]) -> list[tuple[str, str]]:
    out = []
    for k in m.bounded(items, "keywords"):
        kw = k if isinstance(k, Keyword) else Keyword.model_validate(k)
        text = kw.text.strip()
        if not text or len(text) > 80:
            raise ToolError("Error: a keyword is 1 to 80 characters.")
        out.append((text, kw.match_type.value))
    return out


@keywords_mcp.tool(
    title="Add keywords or negative keywords",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def add(
    customer_id: str | int,
    keywords: list[Keyword],
    ad_group_id: str | None = None,
    campaign_id: str | None = None,
    negative: bool = False,
    cpc_bid_micros: int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Adds keywords to an ad group, or negative keywords to an ad group or a campaign.

    Costs 1 Google Ads operation per item on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a
    checksum). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the checksum. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        keywords: [{"text": "...", "match_type": "EXACT" | "PHRASE" | "BROAD"}].
        ad_group_id: The ad group (positive keywords need one).
        campaign_id: The campaign, for campaign-level negatives (then no ad_group_id).
        negative: True adds negative keywords.
        cpc_bid_micros: Optional max CPC for positive keywords.
        validate_only: True (default) previews; False applies (needs `checksum`).
        checksum: From the preview.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    words = _keywords(keywords)
    if (ad_group_id is None) == (campaign_id is None):
        raise ToolError("Error: name exactly one of ad_group_id, campaign_id.")
    if campaign_id is not None and not negative:
        raise ToolError("Error: campaign-level keywords are negatives only.")
    cpc = m.non_negative_micros(cpc_bid_micros, "cpc_bid_micros")
    if cpc is not None and negative:
        raise ToolError("Error: negative keywords take no bid.")
    group = (
        None if ad_group_id is None else m.plain_id(ad_group_id, "ad_group_id")
    )
    camp = (
        None if campaign_id is None else m.plain_id(campaign_id, "campaign_id")
    )

    def operations(client):
        ops = []
        for text, match in words:
            op = client.get_type("MutateOperation")
            if group is not None:
                crit = op.ad_group_criterion_operation.create
                crit.ad_group = f"customers/{cid}/adGroups/{group}"
                if negative:
                    crit.negative = True
                else:
                    crit.status = (
                        client.enums.AdGroupCriterionStatusEnum.ENABLED
                    )
                    if cpc is not None:
                        crit.cpc_bid_micros = cpc
            else:
                crit = op.campaign_criterion_operation.create
                crit.campaign = f"customers/{cid}/campaigns/{camp}"
                crit.negative = True
            crit.keyword.text = text
            crit.keyword.match_type = getattr(
                client.enums.KeywordMatchTypeEnum, match
            )
            ops.append(op)
        return ops

    where = f"ad group {group}" if group is not None else f"campaign {camp}"
    items = [
        pac.ChangeItem(
            operation="CREATE",
            entity_readable="Negative keyword" if negative else "Keyword",
            primary_key_readable=where,
            field_readable=match,
            value_readable=text,
        )
        for text, match in words
    ]
    return m.mutate(
        tool="add_keywords",
        customer_id=cid,
        payload={
            "keywords": words,
            "ad_group_id": group,
            "campaign_id": camp,
            "negative": bool(negative),
            "cpc": cpc,
        },
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=checksum,
        ctx=ctx,
    )


@keywords_mcp.tool(
    title="Remove keywords",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def remove(
    customer_id: str | int,
    level: KeywordLevel,
    ids: list[str],
    validate_only: bool = True,
    checksum: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Removes keywords (positive or negative) from ad groups, or negatives from campaigns.

    Costs 1 Google Ads operation per item on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a
    checksum). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the checksum. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account.
        level: AD_GROUP ('adGroupId~criterionId' ids) or CAMPAIGN ('campaignId~criterionId', negatives).
        ids: The keywords' composite ids.
        validate_only: True (default) previews; False applies (needs `checksum`).
        checksum: From the preview.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    lvl = level.value if isinstance(level, Enum) else str(level).upper()
    if lvl not in KeywordLevel.__members__:
        raise ToolError("Error: level is AD_GROUP or CAMPAIGN.")
    shape = (
        "adGroupId~criterionId"
        if lvl == "AD_GROUP"
        else "campaignId~criterionId"
    )
    targets = [m.composite_id(i, "ids", shape) for i in m.bounded(ids, "ids")]
    collection = "adGroupCriteria" if lvl == "AD_GROUP" else "campaignCriteria"

    def operations(client):
        ops = []
        for t in targets:
            op = client.get_type("MutateOperation")
            rn = f"customers/{cid}/{collection}/{t}"
            if lvl == "AD_GROUP":
                op.ad_group_criterion_operation.remove = rn
            else:
                op.campaign_criterion_operation.remove = rn
            ops.append(op)
        return ops

    items = [
        pac.ChangeItem(
            operation="REMOVE",
            entity_readable="Keyword",
            primary_key_readable=t,
        )
        for t in targets
    ]
    return m.mutate(
        tool="remove_keywords",
        customer_id=cid,
        payload={"level": lvl, "ids": targets},
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=checksum,
        ctx=ctx,
    )
