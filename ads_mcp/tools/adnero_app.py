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

"""App campaigns (adnero fork): app_check, the pre-launch check (edge: reads facts, calls rules/app_check)."""

from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import adnero_mutate as m
from ads_mcp.rules import app as R
from ads_mcp.rules.app_check import check as check_spec, needed_ids
from ads_mcp.rules.app_spec import (
    AccountFacts,
    AppSpec,
    AssetFact,
    CheckReport,
    ConversionActionFact,
    UserListFact,
)

app_mcp = FastMCP("app")


def _ids(values: Sequence[str | int] | None, what: str) -> tuple[str, ...]:
    return tuple(m.plain_id(v, what) for v in (values or ()))


def _rows(cid: str, query: str, login) -> list[dict]:
    from ads_mcp.tools.search import search_page

    return search_page(cid, query, 10_000, None, login).rows


def read_facts(
    cid: str, spec: AppSpec, login, conversions: bool = True
) -> AccountFacts:
    """1 search for conversion actions (unless conversions=False), +1 for the named assets, +1 for user lists."""
    actions = (
        _rows(
            cid,
            "SELECT conversion_action.id, conversion_action.name, conversion_action.status, "
            "conversion_action.type, conversion_action.category, conversion_action.app_id "
            "FROM conversion_action WHERE conversion_action.status != 'REMOVED'",
            login,
        )
        if conversions
        else []
    )
    asset_ids = needed_ids(spec)
    assets = (
        _rows(
            cid,
            "SELECT asset.id, asset.type, asset.image_asset.full_size.width_pixels, "
            "asset.image_asset.full_size.height_pixels, asset.image_asset.file_size, "
            f"asset.image_asset.mime_type FROM asset WHERE asset.id IN ({','.join(asset_ids)})",
            login,
        )
        if asset_ids
        else []
    )
    lists = (
        _rows(
            cid,
            "SELECT user_list.id, user_list.name FROM user_list "
            f"WHERE user_list.id IN ({','.join(spec.user_list_ids)})",
            login,
        )
        if spec.user_list_ids
        else []
    )
    return AccountFacts(
        conversion_actions=tuple(
            ConversionActionFact(
                id=str(r.get("conversion_action.id", "")),
                name=str(r.get("conversion_action.name", "")),
                status=str(r.get("conversion_action.status", "")),
                type=str(r.get("conversion_action.type", "")),
                category=str(r.get("conversion_action.category", "")),
                app_id=str(r.get("conversion_action.app_id", "") or ""),
            )
            for r in actions
        ),
        assets=tuple(
            AssetFact(
                id=str(r.get("asset.id", "")),
                type=str(r.get("asset.type", "")),
                width=int(
                    r.get("asset.image_asset.full_size.width_pixels") or 0
                ),
                height=int(
                    r.get("asset.image_asset.full_size.height_pixels") or 0
                ),
                size_bytes=int(r.get("asset.image_asset.file_size") or 0),
                mime=str(r.get("asset.image_asset.mime_type", "") or ""),
            )
            for r in assets
        ),
        user_lists=tuple(
            UserListFact(
                id=str(r.get("user_list.id", "")),
                name=str(r.get("user_list.name", "")),
            )
            for r in lists
        ),
    )


def spec_of(
    kind: str,
    app_id: str,
    store: str,
    goal: str,
    target_cpa_micros: int | None,
    target_roas: float | None,
    conversion_action_ids,
    geo_target_ids,
    excluded_geo_target_ids,
    language_ids,
    headlines,
    descriptions,
    image_asset_ids,
    youtube_video_asset_ids,
    html5_asset_ids,
    user_list_ids,
) -> AppSpec:
    """The tool arguments as an AppSpec (ids checked as digits)."""
    if kind not in R.AD_SHAPES:
        raise ToolError(
            "Error: 'kind' is install, engagement or pre_registration."
        )
    if store not in ("GOOGLE_APP_STORE", "APPLE_APP_STORE"):
        raise ToolError(
            "Error: 'store' is GOOGLE_APP_STORE or APPLE_APP_STORE."
        )
    m.non_negative_micros(target_cpa_micros, "target_cpa_micros")
    return AppSpec(
        kind=kind,
        app_id=app_id.strip(),
        store=store,
        goal=goal.strip().upper(),
        target_cpa_micros=target_cpa_micros,
        target_roas=target_roas,
        conversion_action_ids=_ids(
            conversion_action_ids, "conversion_action_ids"
        ),
        geo_target_ids=_ids(geo_target_ids, "geo_target_ids"),
        excluded_geo_target_ids=_ids(
            excluded_geo_target_ids, "excluded_geo_target_ids"
        ),
        language_ids=_ids(language_ids, "language_ids"),
        headlines=tuple(headlines or ()),
        descriptions=tuple(descriptions or ()),
        image_asset_ids=_ids(image_asset_ids, "image_asset_ids"),
        youtube_video_asset_ids=_ids(
            youtube_video_asset_ids, "youtube_video_asset_ids"
        ),
        html5_asset_ids=_ids(html5_asset_ids, "html5_asset_ids"),
        user_list_ids=_ids(user_list_ids, "user_list_ids"),
    )


@app_mcp.tool(
    title="Check an App campaign before creating it",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def check(
    customer_id: str | int,
    kind: str,
    app_id: str,
    store: str,
    goal: str,
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
    login_customer_id: str | int | None = None,
) -> CheckReport:
    """Checks an App campaign (install, engagement or pre-registration) before it is created. Changes nothing.

    Checks: the app id format; the goal fits the kind and has its target (target CPA/CPI in micros, or ROAS
    as a ratio, 3.0 = 300%); in-app goals name conversion actions that exist, are ENABLED, track this app and
    are not DOWNLOAD actions; pre-registration is Google Play only; 2-5 headlines (30 chars), 1-5 descriptions
    (90 chars); every asset exists, has the right type, images are 1:1 / 1.91:1 / 4:5 within 0.5%, at most 20
    images, 20 videos and 20 HTML5 bundles; engagement names a user list. Returns problems with fixes; `ok`
    means nothing would make Google refuse. Takes the same arguments as app_campaign_create.

    Costs 1 to 3 Google Ads operations (conversion actions, the named assets, the named user lists).

    Args:
        customer_id: The Google Ads account.
        kind: install, engagement or pre_registration.
        app_id: Google Play package name (com.example.app) or App Store numeric id.
        store: GOOGLE_APP_STORE or APPLE_APP_STORE.
        goal: AppCampaignBiddingStrategyGoalType, e.g. OPTIMIZE_INSTALLS_TARGET_INSTALL_COST.
        target_cpa_micros: Target CPI/CPA in micros of the account currency.
        target_roas: Target ROAS as a ratio (3.0 = 300%).
        conversion_action_ids: In-app conversion actions to optimize for.
        geo_target_ids: Geo target constant ids to target (2840 = United States).
        excluded_geo_target_ids: Geo target constant ids to exclude.
        language_ids: Language constant ids (1000 = English).
        headlines: 2-5 headlines, 30 characters each.
        descriptions: 1-5 descriptions, 90 characters each.
        image_asset_ids: IMAGE asset ids (assets_upload_image).
        youtube_video_asset_ids: YOUTUBE_VIDEO asset ids (assets_add_youtube).
        html5_asset_ids: MEDIA_BUNDLE asset ids (assets_upload_html5; install only).
        user_list_ids: User lists (engagement: required).
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    spec = spec_of(
        kind,
        app_id,
        store,
        goal,
        target_cpa_micros,
        target_roas,
        conversion_action_ids,
        geo_target_ids,
        excluded_geo_target_ids,
        language_ids,
        headlines,
        descriptions,
        image_asset_ids,
        youtube_video_asset_ids,
        html5_asset_ids,
        user_list_ids,
    )
    return check_spec(spec, read_facts(cid, spec, login_customer_id))
