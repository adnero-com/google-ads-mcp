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

"""Manager (MCC) reads (adnero fork; see ADNERO.md)."""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import adnero_auth
from ads_mcp import adnero_mutate as m
import ads_mcp.utils as utils

accounts_mcp = FastMCP("accounts")

_QUERY = (
    "SELECT customer_client.id, customer_client.descriptive_name, "
    "customer_client.currency_code, customer_client.time_zone, "
    "customer_client.manager, customer_client.status, customer_client.level, "
    "customer_client.hidden FROM customer_client "
    "WHERE customer_client.level <= {depth}"
)

MAX_PAGE = 200


class ClientAccount(BaseModel):
    customer_id: str = Field(
        description="Pass it as customer_id to every other tool."
    )
    name: str
    currency: str = Field(
        description="ISO 4217; money fields are micros of it."
    )
    time_zone: str
    manager: bool = Field(
        description="A sub-manager (its own clients are one level deeper)."
    )
    status: str = Field(description="ENABLED, CANCELED, SUSPENDED, CLOSED.")
    level: int = Field(description="1: a direct client of the manager.")


class ClientAccounts(BaseModel):
    manager_id: str
    accounts: list[ClientAccount]
    next_page_token: str | None = Field(
        default=None,
        description="More accounts: call again with this page_token (free: no new operation is counted by us).",
    )


@accounts_mcp.tool(
    title="List the client accounts under the manager (MCC)",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def list_clients(
    manager_id: str | int | None = None,
    depth: int = 1,
    page_size: int = 100,
    page_token: str | None = None,
) -> ClientAccounts:
    """Lists the Google Ads accounts under a manager (MCC): id, name, currency, time zone, status.

    Start here: every other tool needs a `customer_id` from this list. Costs 1 Google Ads operation per call.

    Args:
        manager_id: The manager account. Omit it: the manager this connection signed in through.
        depth: 1 (default) lists direct clients; up to 3 goes deeper into sub-managers.
        page_size: Accounts per page, 1 to 200 (default 100).
        page_token: The next_page_token of the previous page.
    """
    manager = (
        utils.clean_customer_id(manager_id)
        if manager_id is not None
        else (
            adnero_auth.login_customer_id() if adnero_auth.enabled() else None
        )
    )
    if not manager:
        raise ToolError(
            "No manager account: pass manager_id, or pick a manager for this "
            "Google login in AdHive (Connections → Google)."
        )
    if not 1 <= int(depth) <= 3:
        raise ToolError("depth must be 1, 2 or 3.")
    if not 1 <= int(page_size) <= MAX_PAGE:
        raise ToolError(f"page_size must be 1 to {MAX_PAGE}.")
    start = int(page_token) if page_token and page_token.isdigit() else 0
    rows = m.search_rows(manager, _QUERY.format(depth=int(depth)))
    accounts = [
        ClientAccount(
            customer_id=str(r.customer_client.id),
            name=r.customer_client.descriptive_name,
            currency=r.customer_client.currency_code,
            time_zone=r.customer_client.time_zone,
            manager=bool(r.customer_client.manager),
            status=r.customer_client.status.name,
            level=int(r.customer_client.level),
        )
        for r in rows
        if not r.customer_client.hidden and int(r.customer_client.level) > 0
    ]
    end = start + int(page_size)
    return ClientAccounts(
        manager_id=manager,
        accounts=accounts[start:end],
        next_page_token=str(end) if end < len(accounts) else None,
    )
