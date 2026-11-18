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

"""Audience signals (edge): audiences_signal_attach / audiences_signal_detach on App install ad groups
(AdGroupCriterion.audience) and Performance Max asset groups (AssetGroupSignal.audience). Reads first (the
audience, the targets, the signals there now), then one atomic Mutate through the plan gate.
"""

from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp.api_version import API_VERSION
from ads_mcp.rules import audience_signal as S
from ads_mcp.tools.adnero_app_common import _CREATE
from ads_mcp.tools.adnero_audiences import (
    audience_row,
    audiences_mcp,
    signals,
    targets,
)

_REMOVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

_ENTITY = {
    "AD_GROUP": "Ad group audience signal",
    "ASSET_GROUP": "Asset group audience signal",
}


def _remove_op(client, signal: S.Signal, kind: str):
    op = client.get_type("MutateOperation", version=API_VERSION)
    if kind == "AD_GROUP":
        op.ad_group_criterion_operation.remove = signal.resource_name
    else:
        op.asset_group_signal_operation.remove = signal.resource_name
    return op


def _create_op(client, cid: str, target: S.Target, audience: str):
    op = client.get_type("MutateOperation", version=API_VERSION)
    if target.kind == "AD_GROUP":
        c = op.ad_group_criterion_operation.create
        c.ad_group = f"customers/{cid}/adGroups/{target.id}"
        c.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        c.audience.audience = audience
    else:
        s = op.asset_group_signal_operation.create
        s.asset_group = f"customers/{cid}/assetGroups/{target.id}"
        s.audience.audience = audience
    return op


@audiences_mcp.tool(
    name="signal_attach",
    title="Attach an audience as the audience signal of an App campaign or Performance Max asset group",
    annotations=_CREATE,
)
def signal_attach(
    customer_id: str | int,
    audience_id: str,
    campaign_id: str | None = None,
    ad_group_id: str | None = None,
    asset_group_id: str | None = None,
    replace: bool = False,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Attaches an Audience (audiences_create / audiences_list) as an audience signal: on App install campaigns to
    ad groups (an ad group criterion; at most ONE audience per ad group), on Performance Max to asset groups (an
    asset group signal; several allowed). With campaign_id it goes on every ad group / asset group of that campaign.
    This is how an App campaign gets ages, genders, interests or lists: the campaign cannot target them, the signal
    tells Google whom to look for first (not a limit). App engagement campaigns take user lists instead
    (app_ad_group_create), pre-registration takes none.

    Name exactly one of campaign_id, ad_group_id, asset_group_id. Targets that have this audience already are
    skipped. An App ad group with another audience is refused unless replace=true (then the old one is removed in
    the same atomic request); on asset groups replace=true removes their other audience signals.

    Costs 3-4 Google Ads operations for the reads, plus 1 per signal added or removed on each call (the preview
    counts too). Preview, show change_items, then apply with the SAME arguments, validate_only=false and plan_id.

    Args:
        customer_id: The Google Ads account.
        audience_id: The audience.
        campaign_id: An App install or Performance Max campaign (all its ad groups / asset groups).
        ad_group_id: One App install ad group.
        asset_group_id: One Performance Max asset group.
        replace: Swap out the audience signals there now.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    aid = m.plain_id(audience_id, "audience_id")
    aud = audience_row(cid, aid, login_customer_id)
    if aud.get("audience.status") != "ENABLED":
        raise ToolError(
            f"Error: audience {aid} is {aud.get('audience.status')}, not ENABLED."
        )
    found = targets(
        cid, login_customer_id, campaign_id, ad_group_id, asset_group_id
    )
    now = signals(cid, login_customer_id, found)
    plan = S.plan_attach(found, now, aid, bool(replace))
    if plan.problems:
        raise ToolError(
            "Error: refused before Google:\n"
            + "\n".join(f"- {p}" for p in plan.problems)
        )
    if not plan.create and not plan.remove:
        raise ToolError(
            f"Error: nothing to do: audience {aid} is already on {', '.join(plan.already)}."
        )
    kind = found[0].kind
    audience = f"customers/{cid}/audiences/{aid}"
    name = str(aud.get("audience.name", aid))

    def operations(client):
        return [_remove_op(client, s, kind) for s in plan.remove] + [
            _create_op(client, cid, t, audience) for t in plan.create
        ]

    items = [
        pac.ChangeItem(
            operation="REMOVE",
            entity_readable=_ENTITY[kind],
            primary_key_readable=s.target_id,
            field_readable="Audience",
            value_readable=s.audience_id,
        )
        for s in plan.remove
    ] + [
        pac.ChangeItem(
            operation="CREATE",
            entity_readable=_ENTITY[kind],
            primary_key_readable=f"{t.id} {t.name}".strip(),
            field_readable="Audience",
            value_readable=f"{aid} {name}",
        )
        for t in plan.create
    ]
    notes = (
        [f"Already there, skipped: {', '.join(plan.already)}."]
        if plan.already
        else []
    )
    return m.mutate(
        tool="audiences_signal_attach",
        customer_id=cid,
        payload={
            "audience_id": aid,
            "create": [t.model_dump() for t in plan.create],
            "remove": [s.model_dump() for s in plan.remove],
        },
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
        notes=notes,
    )


@audiences_mcp.tool(
    name="signal_detach",
    title="Remove an audience signal from an App campaign or Performance Max asset group",
    annotations=_REMOVE,
)
def signal_detach(
    customer_id: str | int,
    audience_id: str,
    campaign_id: str | None = None,
    ad_group_id: str | None = None,
    asset_group_id: str | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Removes an Audience's signal from App install ad groups or Performance Max asset groups (the audience itself
    stays and can be attached again). Name exactly one of campaign_id, ad_group_id, asset_group_id.

    Costs 2-3 Google Ads operations for the reads, plus 1 per signal removed on each call (the preview counts too).
    Preview, show change_items, then apply with the SAME arguments, validate_only=false and plan_id.

    Args:
        customer_id: The Google Ads account.
        audience_id: The audience whose signal goes.
        campaign_id: An App install or Performance Max campaign (all its ad groups / asset groups).
        ad_group_id: One App install ad group.
        asset_group_id: One Performance Max asset group.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    aid = m.plain_id(audience_id, "audience_id")
    found = targets(
        cid, login_customer_id, campaign_id, ad_group_id, asset_group_id
    )
    gone = S.plan_detach(signals(cid, login_customer_id, found), aid)
    if not gone:
        raise ToolError(
            f"Error: audience {aid} is not a signal on {', '.join(t.id for t in found)}."
        )
    kind = found[0].kind
    items = [
        pac.ChangeItem(
            operation="REMOVE",
            entity_readable=_ENTITY[kind],
            primary_key_readable=s.target_id,
            field_readable="Audience",
            value_readable=aid,
        )
        for s in gone
    ]
    return m.mutate(
        tool="audiences_signal_detach",
        customer_id=cid,
        payload={"audience_id": aid, "remove": [s.model_dump() for s in gone]},
        operations=lambda client: [_remove_op(client, s, kind) for s in gone],
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
    )
