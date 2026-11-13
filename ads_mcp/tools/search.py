# Copyright 2026 Google LLC.
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

"""Tools for exposing the API Search method to the MCP server.

adnero: `search` takes a GAQL `query` string OR resource + fields (+ conditions, orderings, limit),
and every answer is bounded (max_rows, a byte cap) with a page token for the rest. Enums come back
as names at every depth. The query and paging rules are pure (ads_mcp/core/gaql.py); this module is
the edge that calls Google.
"""

import json
from typing import Any, Dict, List, Literal

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

search_mcp = FastMCP("search")

import ads_mcp.utils as utils
from ads_mcp.core import gaql
from ads_mcp.core.result import Err
from google.ads.googleads.errors import GoogleAdsException
from fastmcp.exceptions import ToolError


class SearchResult(BaseModel):
    """One bounded page of GAQL rows."""

    rows: List[Dict[str, Any]] = Field(
        description="One object per row, keyed by the selected field paths; enums as names."
    )
    row_count: int
    next_page_token: str | None = Field(
        default=None,
        description="More rows exist: call again with the same query, customer_id and this page_token.",
    )
    stopped_by: Literal["END", "MAX_ROWS", "BYTE_CAP"] = Field(
        description="END: no more rows on this page. MAX_ROWS / BYTE_CAP: the answer was cut; page on."
    )
    query: str = Field(description="The GAQL that ran.")


def _fail(message: str) -> ToolError:
    return ToolError(f"Error: {message}")


def _unwrap(result):
    if isinstance(result, Err):
        raise _fail(result.error)
    return result.value


def search(
    customer_id: str | int,
    query: str | None = None,
    fields: List[str] | None = None,
    resource: str | None = None,
    conditions: List[str] = [],
    orderings: List[str] = [],
    limit: int | None = None,
    max_rows: int | None = None,
    page_token: str | None = None,
    login_customer_id: str | int | None = None,
) -> SearchResult:
    """Fetches data from the Google Ads API with GAQL, one bounded page per call.

    Pass EITHER `query` (a full GAQL string, e.g. "SELECT campaign.id, campaign.name FROM campaign WHERE
    campaign.status = 'ENABLED'") OR `resource` + `fields` (+ `conditions`, `orderings`, `limit`).
    One call is one Google Ads operation. At most `max_rows` rows (default 500, at most 10000) and about
    200 KB come back; when more exist, `next_page_token` is set: call again with the same query and it.

    Args:
        customer_id: The id of the customer
        query: A full GAQL SELECT statement (instead of resource + fields)
        fields: The fields to fetch
        resource: The resource to return fields from
        conditions: List of conditions to filter the data, combined using AND clauses
        orderings: How the data is ordered
        limit: The GAQL LIMIT (parts form only)
        max_rows: Rows per answer (default 500, max 10000)
        page_token: From a previous answer of the same query, to get the next rows
        login_customer_id: Optional manager customer ID to use as the login-customer-id header.

    """

    customer_id = utils.clean_customer_id(customer_id)
    text = _unwrap(
        gaql.choose_query(
            query, resource, fields, conditions or (), orderings or (), limit
        )
    )
    rows_cap = _unwrap(gaql.check_max_rows(max_rows))
    fp = gaql.fingerprint(customer_id, text)
    cursor = (
        _unwrap(gaql.decode_cursor(page_token, fp))
        if page_token
        else gaql.Cursor(fp, "", 0)
    )

    ga_service = utils.get_googleads_service(
        "GoogleAdsService", login_customer_id=login_customer_id
    )
    utils.logger.info(f"ads_mcp.search query {text}")
    request: Dict[str, Any] = {"customer_id": customer_id, "query": text}
    if cursor.google_token:
        request["page_token"] = cursor.google_token

    try:
        # Exactly one Google page per call: one operation.
        first = next(iter(ga_service.search(request=request).pages))
        paths = list(first.field_mask.paths)
        formatted = (
            utils.format_output_row(row, paths) for row in first.results
        )
        page = gaql.take_rows(
            formatted,
            cursor.offset,
            rows_cap,
            size_of=lambda r: len(json.dumps(r, default=str)),
            byte_cap=gaql.BYTE_CAP,
        )
        following = gaql.next_cursor(
            cursor, page, str(first.next_page_token or "")
        )
    except GoogleAdsException as ex:
        raise ToolError(utils.format_google_ads_error(ex)) from ex

    return SearchResult(
        rows=list(page.rows),
        row_count=len(page.rows),
        next_page_token=gaql.encode_cursor(following) if following else None,
        stopped_by=page.stopped_by,
        query=text,
    )


def _search_tool_description() -> str:
    """Returns the description for the `search` tool."""
    # Add a warning that will be part of the description
    file_content = (
        "WARNING: The list of valid resources is missing. "
        "Tool may not function correctly."
    )

    try:
        with open(utils.get_gaql_resources_filepath(), "r") as file:
            file_content = file.read()
    except FileNotFoundError:
        utils.logger.error("The specified file was not found.")

    return f"""
{search.__doc__}

### Hints
    Language Grammar can be found at https://developers.google.com/google-ads/api/docs/query/grammar
    All resources and descriptions are found at https://developers.google.com/google-ads/api/fields/latest/overview
    If the query fails, a ToolError will be raised with the error details.

    For Conversion issues try looking in offline_conversion_upload_conversion_action_summary

### Hint for customer_id
    should be a string of numbers without punctuation
    if presented in the form 123-456-7890 remove the hyphens and use 1234567890

### Hints for Dates
    All dates should be in the form YYYY-MM-DD and must include the dashes (-)
    Date ranges must be finite and must include a start and end date

### Hints for historical metrics
    Status fields (campaign.status, ad_group.status, ad_group_criterion.status, ad_group_ad.status) hold the current status, not the status during the date range
    When selecting metrics over a date range, do not filter on status (e.g. campaign.status = 'ENABLED' or campaign.status != 'REMOVED')
    Entities paused or removed during the range would drop out and totals would understate cost and conversions
    To leave out removed entities with no activity, remove rows with zero impressions after the query instead
    Filter on status only when asking what is live now

### Hints for limits
    Requests to resource change_event must specify a LIMIT of less than or equal to 10000

### Hints for conversions questions
    https://developers.google.com/google-ads/api/docs/conversions/upload-summaries 


### Hints for all resources
    To find out which specific fields (including compatible metrics and segments) you can select, filter by, or sort by for a given resource, you MUST use the `get_resource_metadata` tool.
    Do not guess the fields. Use the tool to look them up.
    Once you have the fields, ensure the whole field name is used (e.g., 'campaign.id', not just 'id'). Wildcards and partial fields are not allowed.

### Valid resources
    What follows is a list of valid resources that can be queried.
    {file_content}
"""


# The `search` tool requires a more complex description that's generated at
# runtime. Uses the `add_tool` method instead of an annnotation since `add_tool`
# provides the flexibility needed to generate the description while also
# including the `search` method's docstring.
search.__doc__ = _search_tool_description()
search_mcp.add_tool(
    Tool.from_function(search, annotations=ToolAnnotations(readOnlyHint=True))
)
