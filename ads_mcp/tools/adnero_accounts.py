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


@accounts_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_clients(
    manager_id: str | int | None = None, depth: int = 1
) -> list[dict]:
    """Lists the accounts under a manager (MCC) account: id, name, currency, time zone, status.

    Use it to find the `customer_id` for every other tool. One operation.

    Args:
        manager_id: The manager account. None: the manager this connection signed in through.
        depth: 1 (default) lists direct clients; up to 3 goes deeper.
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
            "Error: no manager account. Pass 'manager_id', or pick a manager "
            "for this Google login in AdHive (Connections)."
        )
    if not 1 <= int(depth) <= 3:
        raise ToolError("Error: 'depth' is 1 to 3.")
    rows = m.search_rows(manager, _QUERY.format(depth=int(depth)))
    return [
        {
            "customer_id": str(r.customer_client.id),
            "name": r.customer_client.descriptive_name,
            "currency": r.customer_client.currency_code,
            "time_zone": r.customer_client.time_zone,
            "manager": bool(r.customer_client.manager),
            "status": r.customer_client.status.name,
            "level": int(r.customer_client.level),
        }
        for r in rows
        if not r.customer_client.hidden and int(r.customer_client.level) > 0
    ]
