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

"""Reports (adnero fork; see ADNERO.md): policy_report (approval tree) and app_report (App campaign results).
The pure parts live in ads_mcp/reads/."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import adnero_mutate as m
from ads_mcp.reads import app_report as ar
from ads_mcp.reads import lookup as lk
from ads_mcp.reads import policy as pol
from ads_mcp.reads.common import date_condition, digits

policy_mcp = FastMCP("policy")
# One FastMCP per namespace: app_report joins app_check and the App writes (adnero_app.py) in `app`.
from ads_mcp.tools.adnero_app import app_mcp  # noqa: E402

READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _search(customer_id: str, query: str) -> list:
    """One GAQL search = one Google Ads operation (patched in tests)."""
    return m.search_rows(customer_id, query)


def _optional_id(value, what: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    d = digits(value)
    if not d:
        raise ToolError(f"Error: '{what}' is digits only.")
    return d


# ---------------------------------------------------------------- policy_report


class PolicyReport(BaseModel):
    report: dict = Field(
        description="customer_id, name, campaigns[]: id, name, status, primary_status, reasons, channel, sub_type, "
        "geo {targeted, excluded: [{id, name, type, country_code}]}, ad_groups[] (same status fields), ads[]: "
        "approval, review, policy_topics [{topic, type, countries, evidence}], exemption_candidates "
        "{ignorable_policy_topics}, assets[] (per asset in the ad: field_type, approval, review, policy_topics)."
    )
    truncated: list[str] = Field(
        default_factory=list,
        description="Levels cut at their cap (rows per level: "
        + ", ".join(f"{k} {v}" for k, v in pol.CAPS.items())
        + "): narrow with campaign_id or ad_group_id.",
    )
    operations: int = Field(
        description="Google Ads operations this report used."
    )
    exemption_note: str = Field(
        default=pol.EXEMPTION_NOTE,
        description="How to read exemption_candidates.",
    )


@policy_mcp.tool(
    name="report",
    title="Approval and policy status: account, campaigns, ad groups, ads, assets",
    annotations=READ,
)
def policy_report(
    customer_id: str | int,
    campaign_id: str | int | None = None,
    ad_group_id: str | int | None = None,
    problems_only: bool = False,
    login_customer_id: str | int | None = None,
) -> PolicyReport:
    """Why something does or does not serve: every campaign, ad group, ad and asset of the account (or of one
    campaign / ad group) with its primary status and reasons, approval and review status, policy topics with their
    type (PROHIBITED, LIMITED...) and the countries they restrict, and the topics a person could ask Google to
    exempt (exemption_candidates: Google decides at submit time; never exempted here). Location targeting is listed with geo names.

    problems_only=true keeps only what is not ELIGIBLE or carries a restricting finding (and its parents). Costs 5 Google
    Ads operations (one per level), plus one per 100 geo targets to name.

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        campaign_id: Only this campaign.
        ad_group_id: Only this ad group.
        problems_only: Leave out everything that serves without a finding.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    scope = pol.Scope(
        _optional_id(campaign_id, "campaign_id"),
        _optional_id(ad_group_id, "ad_group_id"),
    )
    q = pol.queries(scope)
    levels, truncated = pol.cut(
        {
            "campaigns": _search(cid, q["campaigns"]),
            "ad_groups": [
                pol.ad_group(r) for r in _search(cid, q["ad_groups"])
            ],
            "ads": [pol.ad(r) for r in _search(cid, q["ads"])],
            "assets": [pol.asset(r) for r in _search(cid, q["assets"])],
            "locations": [
                pol.location(r) for r in _search(cid, q["locations"])
            ],
        }
    )
    campaign_rows, ad_groups, ads, assets, locations = (
        levels[k]
        for k in ("campaigns", "ad_groups", "ads", "assets", "locations")
    )
    operations = 5
    campaigns = [pol.campaign(r) for r in campaign_rows]
    if scope.ad_group_id:
        owners = {g["campaign_id"] for g in ad_groups}
        campaigns = [c for c in campaigns if c["id"] in owners]
    geo_queries = lk.geo_by_id_queries(pol.geo_ids(locations, ads, assets))
    geo = {
        g["id"]: g
        for query in geo_queries
        for g in (lk.geo(r.geo_target_constant) for r in _search(cid, query))
    }
    operations += len(geo_queries)
    account = {
        "customer_id": cid,
        "name": (
            getattr(
                getattr(campaign_rows[0], "customer", None),
                "descriptive_name",
                None,
            )
            if campaign_rows
            else None
        ),
    }
    tree = pol.tree(
        account,
        campaigns,
        ad_groups,
        ads,
        assets,
        locations,
        geo,
        problems_only,
    )
    return PolicyReport(report=tree, truncated=truncated, operations=operations)


# ---------------------------------------------------------------- app_report


class AppReport(BaseModel):
    level: str = Field(description="campaign or ad_group.")
    by: str
    dates: str = Field(
        description="The date condition used (none for combination: Google ranks those itself)."
    )
    rows: list[dict] = Field(
        description="day / network: impressions, clicks, cost (account currency), conversions (installs for an "
        "install campaign), conversions_value, all_conversions, cpa, roas. asset: + asset id, type, field_type, "
        "performance_label, text / video id. conversion_action: per action. combination: per ad group, Google's "
        "top asset combinations (asset id, field_type, type, text)."
    )
    totals: dict | None = Field(
        default=None,
        description="Sums for day / network (assets overlap, so no total there).",
    )
    truncated: bool = Field(
        default=False, description=f"Cut at {ar.MAX_ROWS} rows."
    )
    operations: int


@app_mcp.tool(
    name="report",
    title="App campaign results by day, network, asset, combination or conversion action",
    annotations=READ,
)
def app_report(
    customer_id: str | int,
    campaign_id: str | int | None = None,
    ad_group_id: str | int | None = None,
    by: Literal[
        "day", "network", "asset", "combination", "conversion_action"
    ] = "day",
    date_preset: str | None = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
    login_customer_id: str | int | None = None,
) -> AppReport:
    """Results of one App campaign or ad group: cost, installs / in-app conversions, value, CPA and ROAS, by day,
    by network, by asset (with Google's performance label: BEST, GOOD, LOW, LEARNING...), by top asset combination
    (app_top_combination_view) or by conversion action.

    Give campaign_id or ad_group_id, and a date_preset (default LAST_30_DAYS) or start + end (YYYY-MM-DD, at most
    400 days). Costs 1 Google Ads operation (2 for combination: the assets are named too).

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        campaign_id: The App campaign.
        ad_group_id: Or one of its ad groups.
        by: day (default), network, asset, combination or conversion_action.
        date_preset: LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, LAST_MONTH... Ignored when start and end are given.
        start: First day, YYYY-MM-DD.
        end: Last day, YYYY-MM-DD.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    target = ar.Target(
        _optional_id(campaign_id, "campaign_id"),
        _optional_id(ad_group_id, "ad_group_id"),
    )
    if bool(target.campaign_id) == bool(target.ad_group_id):
        raise ToolError("Error: give campaign_id or ad_group_id (one of them).")
    dates = date_condition(start, end, None if start or end else date_preset)
    if isinstance(dates, Exception):
        raise ToolError(f"Error: {dates}")
    rows = [ar.row(by, r) for r in _search(cid, ar.query(by, target, dates))]
    operations = 1
    if by == "combination":
        ids = ar.combination_asset_ids(rows)
        if ids:
            labels = {
                str(r.asset.id): ar.asset_label(r)
                for r in _search(
                    cid, ar.ASSETS_BY_ID.format(ids=", ".join(ids))
                )
            }
            rows = ar.with_asset_labels(rows, labels)
            operations += 1
    return AppReport(
        level=target.level,
        by=by,
        dates="" if by == "combination" else dates,
        rows=rows,
        totals=ar.totals(by, rows),
        truncated=len(rows) >= ar.MAX_ROWS,
        operations=operations,
    )
