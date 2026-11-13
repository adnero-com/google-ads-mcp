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

"""assets_list: image, video and HTML5 assets with sizes, App ratio class and where used (edge)."""

from typing import Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import adnero_mutate as m
from ads_mcp.core import assets as A
from ads_mcp.tools import assets_mcp


class AssetList(BaseModel):
    assets: list[A.AssetOut]
    next_page_token: str | None = Field(
        default=None,
        description="More assets: call again with this page_token.",
    )


@assets_mcp.tool(
    name="list",
    title="List image, video and HTML5 assets with sizes and where they are used",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def list_assets(
    customer_id: str | int,
    asset_type: Literal["IMAGE", "YOUTUBE_VIDEO", "MEDIA_BUNDLE"] | None = None,
    unused: bool = False,
    max_rows: int = 200,
    page_token: str | None = None,
    login_customer_id: str | int | None = None,
) -> AssetList:
    """Lists image, YouTube video and HTML5 assets: pixel size, App ratio class (1:1, 1.91:1, 4:5 or none),
    file size, and where each is used (in ads, linked to campaigns or ad groups).

    Costs 4 Google Ads operations per call (the assets, then three usage searches for that page).

    Args:
        customer_id: The Google Ads account.
        asset_type: IMAGE, YOUTUBE_VIDEO or MEDIA_BUNDLE; None: all three.
        unused: Only assets used nowhere (filtered per page: a page may hold fewer rows).
        max_rows: Assets per page (default 200, max 10000).
        page_token: From the previous answer.
        login_customer_id: The manager (MCC) account to call through.
    """
    from ads_mcp.tools.search import search_page

    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    page = search_page(
        cid, A.list_query(asset_type), max_rows, page_token, login_customer_id
    )
    names = [str(r.get("asset.resource_name", "")) for r in page.rows]
    usages = [
        row
        for q in A.usage_queries(names)
        for row in search_page(cid, q, 10_000, None, login_customer_id).rows
    ]
    return AssetList(
        assets=list(A.assets_of(page.rows, usages, unused)),
        next_page_token=page.next_page_token,
    )
