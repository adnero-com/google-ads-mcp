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

from typing import Literal

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from pydantic import BaseModel, Field

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


class ChangeItemOut(BaseModel):
    """One field a write changes, as the person reviews it."""

    operation: str = Field(description="CREATE, UPDATE or REMOVE.")
    entity: str = Field(
        description="What changes (Campaign, Ad group, Keyword...)."
    )
    key: str = Field(description="Which one: its id or name.")
    field: str = Field(default="", description="The field that changes.")
    value: str = Field(default="", description="Its new value.")
    before: str | None = Field(
        default=None, description="Its value now, when known."
    )


class MutationResult(BaseModel):
    """The answer of every write tool: a validated proposal, or what was applied."""

    status: Literal["PROPOSED", "APPLIED"] = Field(
        description="PROPOSED: Google validated it, nothing changed yet. APPLIED: it is live."
    )
    customer_id: str
    checksum: str | None = Field(
        default=None,
        description="PROPOSED only: pass it back with validate_only=false and the same arguments to apply.",
    )
    change_items: list[ChangeItemOut] = Field(default_factory=list)
    resource_names: list[str] = Field(
        default_factory=list,
        description="APPLIED only: the Google Ads resources created or changed.",
    )
    next_step: str = Field(description="What to do next.")


ELICIT_KEY = "confirm"
_CAPS_META = "io.modelcontextprotocol/clientCapabilities"


def _items_out(items: Sequence[pac.ChangeItem]) -> list[ChangeItemOut]:
    return [
        ChangeItemOut(
            operation=i.operation,
            entity=i.entity_readable,
            key=i.primary_key_readable,
            field=i.field_readable,
            value=i.value_readable,
            before=i.original_value_readable or None,
        )
        for i in items
    ]


def client_can_elicit(ctx: Any) -> bool:
    """The 2026-07-28 client declared form elicitation on this request (multi round-trip, SEP-2322)."""
    if ctx is None:
        return False
    try:
        rc = ctx.request_context
        if rc is None or not ctx._is_modern_protocol():
            return False
        meta = rc.meta
        meta = (
            meta.model_dump(by_alias=True)
            if hasattr(meta, "model_dump")
            else (meta or {})
        )
        caps = meta.get(_CAPS_META) or {}
        return bool(caps.get("elicitation"))
    except Exception:  # an older FastMCP or no request: no elicitation
        return False


def _accepted(ctx: Any, expected: str) -> bool:
    """The person accepted this exact change in the client's form (its state is our checksum)."""
    try:
        responses = ctx.input_responses or {}
        answer = responses.get(ELICIT_KEY)
        content = getattr(answer, "content", None) or {}
        return (
            getattr(answer, "action", None) == "accept"
            and content.get("confirm") is True
            and ctx.request_state == expected
        )
    except Exception:
        return False


def _ask(summary: str, checksum: str):
    from fastmcp.tools import InputRequiredToolResult
    from mcp import types as mcp_types

    request = mcp_types.ElicitRequest(
        method="elicitation/create",
        params=mcp_types.ElicitRequestFormParams(
            mode="form",
            message=summary,
            requested_schema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "title": "Apply this change in Google Ads",
                    }
                },
                "required": ["confirm"],
            },
        ),
    )
    return InputRequiredToolResult(
        mcp_types.InputRequiredResult(
            input_requests={ELICIT_KEY: request}, request_state=checksum
        )
    )


def _summary(
    tool: str, customer_id: str, items: Sequence[pac.ChangeItem]
) -> str:
    lines = [
        f"{i.operation} {i.entity_readable} {i.primary_key_readable}"
        + (
            f": {i.field_readable} = {i.value_readable}"
            if i.field_readable
            else ""
        )
        for i in items[:20]
    ]
    more = f"\n... and {len(items) - 20} more" if len(items) > 20 else ""
    return (
        f"{tool} in Google Ads account {customer_id}:\n"
        + "\n".join(lines)
        + more
    )


def _fail(tool: str, exc: Exception) -> ToolError:
    if isinstance(exc, GoogleAdsException):
        return ToolError(
            f"{tool} was refused by Google:\n{utils.format_google_ads_error(exc)}"
        )
    return ToolError(f"{tool} failed: {exc}")


def mutate(
    *,
    tool: str,
    customer_id: str,
    payload: dict[str, Any],
    operations: Callable[[Any], list[Any]],
    change_items: Sequence[pac.ChangeItem],
    validate_only: bool,
    checksum: str | None,
    ctx: Any = None,
):
    """Builds ONE atomic Mutate request and runs it through propose and confirm.

    - validate_only=True (default): Google validates it (nothing changes); the answer is PROPOSED with a checksum.
    - validate_only=False + the checksum: applied.
    - validate_only=False without a checksum, on a client that declared form elicitation: Google validates it, the
      person is asked in the client's form (an input_required result), and the retry with their acceptance applies
      it. Any other client gets the two-step above.
    The checksum binds ``customer_id`` and ``payload`` (the tool's normalised arguments).
    """
    ga_client = client()
    service = utils.get_googleads_service_from_client(
        ga_client, "GoogleAdsService"
    )
    request = ga_client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.mutate_operations.extend(operations(ga_client))
    expected = pac.compute_checksum(customer_id, {"tool": tool, **payload})
    items = _items_out(change_items)

    def _validate() -> None:
        request.validate_only = True
        try:
            service.mutate(request=request)
        except Exception as exc:
            raise _fail(tool, exc) from exc

    def _commit() -> MutationResult:
        request.validate_only = False
        try:
            response = service.mutate(request=request)
        except Exception as exc:
            raise _fail(tool, exc) from exc
        return MutationResult(
            status="APPLIED",
            customer_id=customer_id,
            change_items=items,
            resource_names=resource_names(response),
            next_step="Done. Read it back with search to confirm, or report the change to the person.",
        )

    if validate_only:
        _validate()
        return MutationResult(
            status="PROPOSED",
            customer_id=customer_id,
            checksum=expected,
            change_items=items,
            next_step=(
                "Nothing changed yet. Show change_items to the person; after they approve, call this tool again "
                "with the same arguments, validate_only=false and this checksum."
            ),
        )
    if checksum:
        problem = pac.verify_checksum(
            customer_id, None, checksum, expected_checksum=expected
        )
        if problem:
            raise ToolError(problem)
        return _commit()
    if _accepted(ctx, expected):
        return _commit()
    if client_can_elicit(ctx):
        _validate()
        return _ask(_summary(tool, customer_id, change_items), expected)
    raise ToolError(
        "A change needs the person's approval first: call with validate_only=true, show the change_items, then "
        "call again with validate_only=false and the checksum."
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
