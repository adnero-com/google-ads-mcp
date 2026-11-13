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

"""Shared edge helpers of the App write tools: refusals, read-back, the campaign as it is now."""

from typing import Any

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac
from ads_mcp.rules.app_build import kind_of
from ads_mcp.rules.app_spec import AppSpec
from ads_mcp.tools import adnero_app_build as B

_CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def refuse_errors(problems) -> list[str]:
    """Raises on ERROR problems (before Google); returns the warnings as notes."""
    errors = [p for p in problems if p.severity == "ERROR"]
    if errors:
        raise ToolError(
            "Error: refused before Google (App rules):\n"
            + "\n".join(
                f"- {p.code} ({p.field}): {p.message}"
                + (f" Fix: {p.fix}" if p.fix else "")
                for p in errors
            )
        )
    return [f"{p.code}: {p.message}" for p in problems]


def read_back(login) -> Any:
    def run(names: list[str]) -> dict[str, Any] | None:
        from ads_mcp.tools.search import search_page

        ad = next((n for n in names if "/adGroupAds/" in n), None)
        if ad is None:
            return None
        cid = ad.split("/")[1]
        rows = search_page(cid, B.read_back_query(ad), 1, None, login).rows
        return (
            rows[0]
            if rows
            else {
                "note": "not readable yet; Google shows new objects within minutes"
            }
        )

    return run


def _items(
    fields: dict[str, Any], entity: str, key: str
) -> list[pac.ChangeItem]:
    return pac.build_change_items(
        operation="CREATE",
        entity_readable=entity,
        primary_key_readable=key,
        fields=fields,
    )


def campaign_now(cid: str, campaign_id: str, login) -> dict[str, Any]:
    from ads_mcp.tools.search import search_page

    rows = search_page(
        cid,
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_sub_type, "
        "campaign.app_campaign_setting.app_id, campaign.app_campaign_setting.app_store, "
        "campaign.app_campaign_setting.bidding_strategy_goal_type, campaign.start_date_time, "
        "campaign_budget.resource_name, campaign_budget.amount_micros, campaign_budget.explicitly_shared "
        f"FROM campaign WHERE campaign.id = {campaign_id}",
        1,
        None,
        login,
    ).rows
    if not rows:
        raise ToolError(f"Error: campaign {campaign_id} not found in {cid}.")
    row = rows[0]
    if (
        kind_of(str(row.get("campaign.advertising_channel_sub_type", "")))
        is None
    ):
        raise ToolError(
            f"Error: campaign {campaign_id} is not an App campaign "
            f"({row.get('campaign.advertising_channel_sub_type')})."
        )
    return row


def spec_from_campaign(row: dict[str, Any], **creative) -> AppSpec:
    return AppSpec(
        kind=kind_of(str(row["campaign.advertising_channel_sub_type"])),
        app_id=str(row.get("campaign.app_campaign_setting.app_id", "")),
        store=str(
            row.get(
                "campaign.app_campaign_setting.app_store", "GOOGLE_APP_STORE"
            )
        ),
        goal=str(
            row.get(
                "campaign.app_campaign_setting.bidding_strategy_goal_type", ""
            )
        ),
        **creative,
    )
