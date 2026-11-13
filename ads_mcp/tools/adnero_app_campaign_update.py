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

"""app_campaign_update (edge): budget, targets, conversion actions, dates, targeting; immutables refused."""

from datetime import datetime, timezone
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp.api_version import API_VERSION
from ads_mcp.core.result import Err
from ads_mcp.rules.app_build import day_bounds, kind_of
from ads_mcp.rules.app_update import (
    CampaignChange,
    CampaignNow,
    update_problems,
)
from ads_mcp.rules.app_check import conversion_problems
from ads_mcp.tools import adnero_app_build as B
from ads_mcp.tools.adnero_app import _ids, app_mcp, read_facts
from ads_mcp.tools.adnero_app_common import (
    campaign_now,
    refuse_errors,
    spec_from_campaign,
)


def _criteria(cid: str, camp: str, login) -> dict[tuple[str, bool], str]:
    """(geo/language constant, negative) -> the criterion's resource name."""
    from ads_mcp.tools.search import search_page

    rows = search_page(
        cid,
        "SELECT campaign_criterion.resource_name, campaign_criterion.negative, "
        "campaign_criterion.location.geo_target_constant, campaign_criterion.language.language_constant "
        f"FROM campaign_criterion WHERE campaign.id = {camp} AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE')",
        10_000,
        None,
        login,
    ).rows
    return {
        (
            str(
                r.get("campaign_criterion.location.geo_target_constant")
                or r.get("campaign_criterion.language.language_constant")
            ),
            bool(r.get("campaign_criterion.negative")),
        ): str(r["campaign_criterion.resource_name"])
        for r in rows
    }


@app_mcp.tool(
    title="Change an App campaign's budget, targets, conversion actions, dates or targeting",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def campaign_update(
    customer_id: str | int,
    campaign_id: str,
    name: str | None = None,
    daily_budget_micros: int | None = None,
    target_cpa_micros: int | None = None,
    target_roas: float | None = None,
    conversion_action_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    clear_end_date: bool = False,
    add_geo_target_ids: list[str] | None = None,
    remove_geo_target_ids: list[str] | None = None,
    add_excluded_geo_target_ids: list[str] | None = None,
    remove_excluded_geo_target_ids: list[str] | None = None,
    add_language_ids: list[str] | None = None,
    remove_language_ids: list[str] | None = None,
    app_id: str | None = None,
    store: str | None = None,
    kind: str | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Changes an App campaign in one atomic request. What cannot change is refused before Google, naming the
    rule: app_id, store and kind (install / engagement / pre_registration) never change after create; the start
    date cannot change once started; a target the goal does not use (target_roas on a CPA goal...); conversion
    actions of a pre-registration campaign; a shared budget. Conversion actions replace the current list.

    Costs 1 operation to read the campaign, +1 to check new conversion actions, +1 to find criteria to remove,
    plus 1 per changed object on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED,
    a plan_id). Show change_items to the person; once they approve, call again with the SAME arguments,
    validate_only=false and the plan_id (valid 15 minutes).

    Args:
        customer_id: The Google Ads account.
        campaign_id: The App campaign.
        name: New name.
        daily_budget_micros: New daily budget in micros of the account currency.
        target_cpa_micros: New target CPI/CPA in micros (CPA goals).
        target_roas: New target ROAS ratio (ROAS goal).
        conversion_action_ids: The conversion actions to optimize for (replaces the list).
        start_date: YYYY-MM-DD (only before the campaign starts).
        end_date: YYYY-MM-DD.
        clear_end_date: True: run with no end date.
        add_geo_target_ids: Locations to add.
        remove_geo_target_ids: Locations to stop targeting.
        add_excluded_geo_target_ids: Locations to exclude.
        remove_excluded_geo_target_ids: Exclusions to lift.
        add_language_ids: Languages to add.
        remove_language_ids: Languages to remove.
        app_id: Refused unless unchanged (immutable); here so the refusal can say why.
        store: Refused unless unchanged (immutable).
        kind: Refused unless unchanged (immutable).
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    camp = m.plain_id(campaign_id, "campaign_id")
    budget = m.non_negative_micros(daily_budget_micros, "daily_budget_micros")
    m.non_negative_micros(target_cpa_micros, "target_cpa_micros")
    if end_date and clear_end_date:
        raise ToolError("Error: pass end_date or clear_end_date, not both.")
    dates = day_bounds(start_date, end_date)
    if isinstance(dates, Err):
        raise ToolError(f"Error: {dates.error}")
    start, end = dates.value
    row = campaign_now(cid, camp, login_customer_id)
    now = CampaignNow(
        kind=kind_of(str(row["campaign.advertising_channel_sub_type"])),
        goal=str(
            row.get(
                "campaign.app_campaign_setting.bidding_strategy_goal_type", ""
            )
        ),
        app_id=str(row.get("campaign.app_campaign_setting.app_id", "")),
        store=str(row.get("campaign.app_campaign_setting.app_store", "")),
        start_date_time=str(row.get("campaign.start_date_time", "") or ""),
        budget_shared=bool(row.get("campaign_budget.explicitly_shared")),
    )
    actions = (
        None
        if conversion_action_ids is None
        else _ids(conversion_action_ids, "conversion_action_ids")
    )
    change = CampaignChange(
        target_cpa_micros=target_cpa_micros,
        target_roas=target_roas,
        conversion_action_ids=actions,
        app_id=app_id,
        store=store,
        kind=kind,
        start_date=start_date,
        daily_budget_micros=budget,
    )
    today = datetime.now(timezone.utc).date().isoformat()
    problems = update_problems(now, change, today)
    if actions:
        spec = spec_from_campaign(row, conversion_action_ids=actions)
        problems = problems + [
            p
            for p in conversion_problems(
                spec, read_facts(cid, spec, login_customer_id)
            )
            if p.severity == "ERROR"
        ]
    notes = refuse_errors(problems)
    adds = {
        "geo": _ids(add_geo_target_ids, "add_geo_target_ids"),
        "excl": _ids(
            add_excluded_geo_target_ids, "add_excluded_geo_target_ids"
        ),
        "lang": _ids(add_language_ids, "add_language_ids"),
    }
    removal_keys = (
        [
            (f"geoTargetConstants/{g}", False)
            for g in _ids(remove_geo_target_ids, "remove_geo_target_ids")
        ]
        + [
            (f"geoTargetConstants/{g}", True)
            for g in _ids(
                remove_excluded_geo_target_ids, "remove_excluded_geo_target_ids"
            )
        ]
        + [
            (f"languageConstants/{lg}", False)
            for lg in _ids(remove_language_ids, "remove_language_ids")
        ]
    )
    existing = _criteria(cid, camp, login_customer_id) if removal_keys else {}
    missing = [k for k, _ in removal_keys if (k, _) not in existing]
    if missing:
        raise ToolError(
            f"Error: not targeted by this campaign now: {', '.join(missing)}."
        )
    removals = [existing[k] for k in removal_keys]
    campaign_rn = B.rn(cid, "campaigns", camp)
    fields: dict[str, Any] = {
        "name": name.strip() if name else None,
        "target_cpa.target_cpa_micros": target_cpa_micros,
        "target_roas.target_roas": target_roas,
        "selective_optimization.conversion_actions": (
            [B.rn(cid, "conversionActions", a) for a in actions]
            if actions is not None
            else None
        ),
        "start_date_time": start,
        "end_date_time": "" if clear_end_date else end,
    }
    chosen = {k: v for k, v in fields.items() if v is not None}
    if not (chosen or budget is not None or any(adds.values()) or removals):
        raise ToolError("Error: name at least one change.")

    def operations(client):
        ops = []
        if budget is not None:
            op = client.get_type("MutateOperation", version=API_VERSION)
            op.campaign_budget_operation.update.resource_name = str(
                row["campaign_budget.resource_name"]
            )
            op.campaign_budget_operation.update.amount_micros = budget
            op.campaign_budget_operation.update_mask.paths.append(
                "amount_micros"
            )
            ops.append(op)
        if chosen:
            op = client.get_type("MutateOperation", version=API_VERSION)
            c = op.campaign_operation.update
            c.resource_name = campaign_rn
            for path, value in chosen.items():
                if path == "selective_optimization.conversion_actions":
                    c.selective_optimization.conversion_actions.extend(value)
                elif path == "target_cpa.target_cpa_micros":
                    c.target_cpa.target_cpa_micros = value
                elif path == "target_roas.target_roas":
                    c.target_roas.target_roas = value
                elif value != "":
                    setattr(c, path, value)
                op.campaign_operation.update_mask.paths.append(path)
            ops.append(op)
        ops += B.criterion_ops(
            client, cid, campaign_rn, adds["geo"], adds["excl"], adds["lang"]
        )
        for criterion in removals:
            op = client.get_type("MutateOperation", version=API_VERSION)
            op.campaign_criterion_operation.remove = criterion
            ops.append(op)
        return ops

    items = pac.build_change_items(
        operation="UPDATE",
        entity_readable=f"App campaign ({now.kind})",
        primary_key_readable=camp,
        fields={
            "Daily budget (micros)": budget,
            **{k: ("(no end)" if v == "" else v) for k, v in chosen.items()},
            "Add locations": list(adds["geo"]) or None,
            "Add exclusions": list(adds["excl"]) or None,
            "Add languages": list(adds["lang"]) or None,
            "Remove targeting": [k for k, _ in removal_keys] or None,
        },
        original_fields={
            "Daily budget (micros)": row.get("campaign_budget.amount_micros")
        },
    )
    payload = {
        "campaign": camp,
        "budget": budget,
        "budget_rn": (
            str(row.get("campaign_budget.resource_name", ""))
            if budget is not None
            else None
        ),
        "fields": chosen,
        "adds": {k: list(v) for k, v in adds.items()},
        "removals": removals,
    }
    return m.mutate(
        tool="app_campaign_update",
        customer_id=cid,
        payload=payload,
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
        notes=notes,
    )
