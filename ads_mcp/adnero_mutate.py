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

"""Shared plumbing for the adnero write tools (adnero fork; see ADNERO.md).

Every write goes out as ONE GoogleAdsService.Mutate request (atomic: all its
operations apply or none), through upstream's propose-and-confirm workflow:
``validate_only=True`` (the default) asks Google to validate the request and
returns change items and a checksum; ``validate_only=False`` with that checksum
and the same arguments applies it.
"""

from collections.abc import Callable, Sequence
import re
from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

MAX_ITEMS = 200

_COMPOSITE = re.compile(r"^\d+~\d+$")
_DIGITS = re.compile(r"^\d+$")


def customer_id_of(customer_id: str | int) -> str:
    cleaned = utils.clean_customer_id(customer_id)
    if not cleaned:
        raise ToolError("Error: 'customer_id' must contain digits.")
    return cleaned


def plain_id(value: str | int, what: str) -> str:
    text = str(value).strip()
    if not _DIGITS.match(text):
        raise ToolError(f"Error: '{what}' must be digits only, got '{value}'.")
    return text


def composite_id(value: str, what: str, shape: str) -> str:
    text = str(value).strip()
    if not _COMPOSITE.match(text):
        raise ToolError(
            f"Error: '{what}' must be '{shape}' (two ids joined by '~'), "
            f"got '{value}'."
        )
    return text


def bounded(items: Sequence[Any], what: str, low: int = 1) -> Sequence[Any]:
    if len(items) < low:
        raise ToolError(f"Error: '{what}' needs at least {low} item(s).")
    if len(items) > MAX_ITEMS:
        raise ToolError(
            f"Error: '{what}' takes at most {MAX_ITEMS} items per call."
        )
    return items


def non_negative_micros(value: int | None, what: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolError(
            f"Error: '{what}' must be a whole number of micros (1 unit of "
            "the account currency = 1,000,000 micros), zero or more."
        )
    return value


def client():
    return utils.get_googleads_client()


def search_rows(customer_id: str, query: str) -> list[Any]:
    """One GAQL search (one operation), every row."""
    service = utils.get_googleads_service("GoogleAdsService")
    return list(service.search(customer_id=customer_id, query=query))


def mutate(
    *,
    tool: str,
    customer_id: str,
    payload: dict[str, Any],
    operations: Callable[[Any], list[Any]],
    change_items: Sequence[pac.ChangeItem],
    describe: Callable[[Any], str],
    validate_only: bool,
    checksum: str | None,
) -> str:
    """Builds the Mutate request and runs it through propose and confirm.

    ``operations(client)`` returns the MutateOperation list; the checksum binds
    ``customer_id`` and ``payload`` (the tool's normalised arguments).
    """
    ga_client = client()
    service = utils.get_googleads_service_from_client(
        ga_client, "GoogleAdsService"
    )
    request = ga_client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.mutate_operations.extend(operations(ga_client))

    def _validate() -> None:
        request.validate_only = True
        service.mutate(request=request)

    def _commit() -> str:
        request.validate_only = False
        return describe(service.mutate(request=request))

    return pac.propose_and_confirm(
        customer_id=customer_id,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload={"tool": tool, **payload},
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix=f"Failed to run {tool}",
    )


def resource_names(response: Any) -> list[str]:
    """Every resource name a Mutate response returned, in order."""
    names: list[str] = []
    for result in response.mutate_operation_responses:
        kind = result._pb.WhichOneof("response")
        if kind is None:
            continue
        inner = getattr(result, kind)
        name = getattr(inner, "resource_name", "")
        if name:
            names.append(name)
    return names


def applied(response: Any, verb: str) -> str:
    names = resource_names(response)
    listed = "\n".join(f"- {n}" for n in names)
    return f"Applied: {verb} ({len(names)}).\n{listed}".rstrip()
