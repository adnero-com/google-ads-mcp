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
    login_customer_id: str | int | None = None,
) -> ClientAccounts:
    """Lists the Google Ads accounts under a manager (MCC): id, name, currency, time zone, status.

    Every other tool needs a `customer_id` from this list (no manager known? call accounts_list_accessible first).
    Costs 1 Google Ads operation per call.

    Args:
        manager_id: The manager account. Omit it: login_customer_id, else the manager this connection signed in through.
        depth: 1 (default) lists direct clients; up to 3 goes deeper into sub-managers.
        page_size: Accounts per page, 1 to 200 (default 100).
        page_token: The next_page_token of the previous page.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager. `manager_id` defaults to it when given.
    """
    login = utils.clean_customer_id(login_customer_id or "") or None
    manager = (
        utils.clean_customer_id(manager_id)
        if manager_id is not None
        else (
            (adnero_auth.login_customer_id() if adnero_auth.enabled() else None)
            or login
        )
    )
    if not manager:
        raise ToolError(
            "No manager account: pass manager_id or login_customer_id (your "
            "manager account), or pick a manager for this Google login in "
            "AdHive (Connections → Google). Not sure which? Call "
            "accounts_list_accessible."
        )
    # Listing a manager's clients goes through that manager unless told otherwise.
    m.use_login(login or manager)
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


MAX_ACCESSIBLE = 50

_ACCESSIBLE_QUERY = (
    "SELECT customer.id, customer.descriptive_name, customer.manager, "
    "customer.currency_code, customer.time_zone, customer.status "
    "FROM customer LIMIT 1"
)


class AccessibleAccount(BaseModel):
    customer_id: str = Field(
        description="Pass it as customer_id (or, for a manager, as login_customer_id / manager_id)."
    )
    name: str | None = None
    manager: bool | None = Field(
        default=None, description="A manager (MCC) account."
    )
    currency: str | None = Field(
        default=None, description="ISO 4217; money fields are micros of it."
    )
    time_zone: str | None = None
    status: str | None = Field(
        default=None, description="ENABLED, CANCELED, SUSPENDED, CLOSED."
    )
    usable: bool = Field(
        description="False: Google refused a read of this account (see why)."
    )
    why: str | None = Field(
        default=None, description="Google's message when usable is false."
    )


class AccessibleAccounts(BaseModel):
    accounts: list[AccessibleAccount]
    truncated: bool = Field(
        default=False,
        description=f"More than {MAX_ACCESSIBLE} accounts are reachable; only the first {MAX_ACCESSIBLE} are checked.",
    )


def _why(exc: Exception) -> str:
    """Google's own words for a refusal (an authorization error such as CUSTOMER_NOT_ENABLED, PERMISSION_DENIED)."""
    failure = getattr(exc, "failure", None)
    errors = getattr(failure, "errors", None) or []
    messages = [e.message for e in errors if getattr(e, "message", "")]
    return "; ".join(messages) or str(exc)[:300] or type(exc).__name__


def _check_account(customer_id: str) -> AccessibleAccount:
    try:
        rows = m.search_rows(customer_id, _ACCESSIBLE_QUERY, login=customer_id)
    except Exception as exc:  # one account never fails the whole call
        return AccessibleAccount(
            customer_id=customer_id, usable=False, why=_why(exc)
        )
    if not rows:
        return AccessibleAccount(
            customer_id=customer_id, usable=False, why="Google returned no row."
        )
    c = rows[0].customer
    return AccessibleAccount(
        customer_id=customer_id,
        name=c.descriptive_name,
        manager=bool(c.manager),
        currency=c.currency_code,
        time_zone=c.time_zone,
        status=c.status.name,
        usable=True,
    )


@accounts_mcp.tool(
    title="List every Google Ads account this login can reach",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def list_accessible() -> AccessibleAccounts:
    """Lists every Google Ads account the signed-in Google login can reach, and which ones work.

    Start here when no manager (MCC) account is known. For each account it reads the name, currency, time zone and
    status, and says whether it is usable; an account Google refuses (for example one that is not enabled) is listed
    with usable=false and Google's reason, never an error. Then use a manager's id as `manager_id` for
    accounts_list_clients, or as `login_customer_id` on the other tools. Costs 1 Google Ads operation per account
    (up to 50 accounts), plus one for the list itself.
    """
    m.use_login(None)
    service = utils.get_googleads_service("CustomerService")
    names = list(service.list_accessible_customers().resource_names)
    ids = [utils.extract_id(n) for n in names]
    return AccessibleAccounts(
        accounts=[_check_account(i) for i in ids[:MAX_ACCESSIBLE]],
        truncated=len(ids) > MAX_ACCESSIBLE,
    )
