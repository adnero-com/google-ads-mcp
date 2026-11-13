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

"""Raw Google Ads REST passthrough (adnero fork; see ADNERO.md).

For the fields no typed tool covers. Every call is held to ONE account: the path must sit under
``customers/{customer_id}`` of the same customer_id (``contained_path``). Reads are GETs, or the search POSTs;
writes are ``<resource>:mutate`` bodies through the same propose and confirm as the typed tools, and never remove.
The Google token is the request's own (never logged, nor is any body).
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import adnero_auth
from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp.core import plan as plan_core
from ads_mcp.schema import normalize as schema_normalize
from ads_mcp.schema import store as schema_store
from ads_mcp.schema import validate as schema_validate
import ads_mcp.utils as utils

api_mcp = FastMCP("api")

REST_BASE_ENV = "GOOGLE_ADS_REST_BASE"
DEFAULT_REST_BASE = "https://googleads.googleapis.com"
TIMEOUT_S = 30
MAX_PATH = 300
MAX_RESPONSE_CHARS = 200_000
MAX_BODY_CHARS = 60_000
MAX_OPERATIONS = 100

# Keys we set ourselves: the caller's are dropped (from the checksum and from what is sent; a write is atomic).
_CONTROL_KEYS = (
    "validateOnly",
    "validate_only",
    "partialFailure",
    "partial_failure",
)
_OPERATION_KEYS = ("operations", "mutateOperations", "mutate_operations")

_VERSION_SEGMENT = re.compile(r"(^|/)v\d+(/|$)")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_~.,:=/-]+$")
_MUTATE_PATH = re.compile(r"^customers/(\d+)/([a-z][A-Za-z]*):mutate$")
_SEARCH_SUFFIXES = ("googleAds:search", "googleAds:searchStream")

NO_REMOVE = "Agents never remove through the raw API: pause it (status PAUSED), or use keywords_remove for keywords."


# ---------------------------------------------------------------- pure rules


def contained_path(customer_id: str, path: str) -> str:
    """The path, if it stays inside customers/{customer_id}; otherwise a ToolError that says what to fix."""
    p = str(path or "").strip()
    if not p:
        raise ToolError(
            f"Error: 'path' is required, e.g. 'customers/{customer_id}/googleAds:search'."
        )
    if len(p) > MAX_PATH:
        raise ToolError(f"Error: 'path' is at most {MAX_PATH} characters.")
    if "://" in p or p.lower().startswith(("http:", "https:")):
        raise ToolError(
            f"Error: 'path' is relative, not a URL: 'customers/{customer_id}/...' (the server adds the host and version)."
        )
    if p.startswith("/"):
        raise ToolError(
            f"Error: 'path' starts without a slash: 'customers/{customer_id}/...'."
        )
    if _VERSION_SEGMENT.search(p):
        raise ToolError(
            f"Error: leave the API version out of 'path' (the server pins {adnero_auth.api_version()}): "
            f"'customers/{customer_id}/...'."
        )
    if ".." in p or "." in p.split("/"):
        raise ToolError("Error: 'path' may not contain '..' or '.' segments.")
    if "?" in p or "#" in p:
        raise ToolError(
            "Error: 'path' takes no query string or fragment; put parameters in 'body' (search: {'query': ...})."
        )
    if "//" in p or not _SAFE_PATH.match(p):
        raise ToolError(
            "Error: 'path' holds only letters, digits and _ ~ . , : = - / (a resource name), with no empty segment."
        )
    root = f"customers/{customer_id}"
    if p != root and not p.startswith(root + "/"):
        raise ToolError(
            f"Error: 'path' must be under '{root}' (the customer_id of this call). Another account: call again with "
            "its customer_id."
        )
    return p


def read_route(customer_id: str, path: str, method: str) -> str:
    """The method, if a read may take it here: GET anywhere under the customer, POST only to the searches."""
    verb = str(method or "GET").strip().upper()
    if verb == "GET":
        return verb
    if verb != "POST":
        raise ToolError("Error: 'method' is 'GET' or 'POST'.")
    if path not in (f"customers/{customer_id}/{s}" for s in _SEARCH_SUFFIXES):
        raise ToolError(
            f"Error: api_read POSTs only to 'customers/{customer_id}/googleAds:search' or ':searchStream'; "
            "changes go through api_mutate."
        )
    return verb


def mutate_resource(customer_id: str, path: str) -> str:
    """The resource a mutate path names ('campaigns', 'googleAds'...), or a ToolError."""
    match = _MUTATE_PATH.match(path)
    if not match or match.group(1) != customer_id:
        raise ToolError(
            f"Error: api_mutate takes 'customers/{customer_id}/<resource>:mutate', with a camelCase resource such "
            "as campaigns, adGroups, campaignBudgets or googleAds."
        )
    return match.group(2)


def has_remove(value: Any) -> bool:
    """True when a 'remove' key, or a status set to REMOVED, sits anywhere inside value (any depth)."""
    if isinstance(value, dict):
        return (
            "remove" in value
            or str(value.get("status", "")).upper() == "REMOVED"
            or any(has_remove(v) for v in value.values())
        )
    if isinstance(value, list):
        return any(has_remove(v) for v in value)
    return False


def operations_of(body: dict) -> list:
    """The body's operation list (operations or mutateOperations), checked: no remove, 1 to MAX_OPERATIONS."""
    present = [k for k in _OPERATION_KEYS if k in body]
    if len(present) != 1 or not isinstance(body[present[0]], list):
        raise ToolError(
            "Error: 'body' needs one list of operations: 'operations' (a <resource>:mutate) or 'mutateOperations' "
            "(googleAds:mutate)."
        )
    ops = body[present[0]]
    if any(has_remove(op) for op in ops):
        raise ToolError(NO_REMOVE)
    if not 1 <= len(ops) <= MAX_OPERATIONS:
        raise ToolError(
            f"Error: 1 to {MAX_OPERATIONS} operations per call; split the change into several calls."
        )
    return ops


def clean_body(body: Any) -> dict:
    """The caller's body without the keys we set (validateOnly, partialFailure), size-checked."""
    if not isinstance(body, dict):
        raise ToolError(
            "Error: 'body' is a JSON object, e.g. {'operations': [...]}."
        )
    text = json.dumps(body, ensure_ascii=False)
    if len(text) > MAX_BODY_CHARS:
        raise ToolError(
            f"Error: 'body' is at most {MAX_BODY_CHARS:,} characters of JSON; split the change."
        )
    return {k: v for k, v in body.items() if k not in _CONTROL_KEYS}


def schema_normalize_body(resource: str, body: dict) -> tuple[dict, str | None]:
    """The body in canonical REST form (camelCase keys, int64 digit strings), and one line saying what changed."""
    fixed, notes = schema_normalize.normalize_body(
        schema_store.load(), resource, body
    )
    return fixed, schema_normalize.summary(notes)


def schema_check(resource: str, body: dict, customer_id: str) -> None:
    """The body against the generated schemas (schema_get_mutate); a ToolError with JSON-pointer problems."""
    problems = schema_validate.check_body(
        schema_store.load(), resource, body, customer_id
    )
    if problems:
        raise ToolError(
            json.dumps(
                {
                    "error": "SCHEMA_INVALID",
                    "message": "Google was not called: fix these fields (schema_get_mutate shows the body).",
                    "problems": [p.as_dict() for p in problems],
                },
                ensure_ascii=False,
            )
        )


def checksum_of(customer_id: str, path: str, body: dict) -> str:
    """sha256 hex of the canonical JSON of {customer_id, path, body} (sorted keys, no spaces, UTF-8)."""
    canonical = json.dumps(
        {"customer_id": customer_id, "path": path, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def change_items(resource: str, ops: list) -> list[pac.ChangeItem]:
    """One reviewable line per operation: its kind, its resource name, its mask and its fields (compact)."""
    items = []
    for op in ops:
        # googleAds:mutate wraps each one: {"campaignOperation": {"update": {...}}}
        entity, inner = resource, op
        if isinstance(op, dict) and len(op) == 1:
            ((key, value),) = op.items()
            if key.endswith("Operation") and isinstance(value, dict):
                entity, inner = key[: -len("Operation")], value
        inner = inner if isinstance(inner, dict) else {}
        kind = next((k for k in ("create", "update") if k in inner), "OTHER")
        fields = inner.get(kind, {}) if kind != "OTHER" else inner
        name = fields.get("resourceName") if isinstance(fields, dict) else None
        value = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        items.append(
            pac.ChangeItem(
                operation=kind.upper(),
                entity_readable=entity,
                primary_key_readable=str(name or "new"),
                field_readable=str(inner.get("updateMask", "")),
                value_readable=(
                    value if len(value) <= 300 else value[:300] + "..."
                ),
            )
        )
    return items


def resource_names_of(data: Any) -> list[str]:
    """Google's resource names from a mutate answer (results[], or mutateOperationResponses[] of googleAds)."""
    if not isinstance(data, dict):
        return []
    names = [
        r.get("resourceName")
        for r in data.get("results") or []
        if isinstance(r, dict)
    ]
    for response in data.get("mutateOperationResponses") or []:
        if isinstance(response, dict):
            names += [
                v.get("resourceName")
                for v in response.values()
                if isinstance(v, dict)
            ]
    return [n for n in names if n]


def google_error(
    status: int, raw: bytes, request_id: str | None = None
) -> ToolError:
    """Google's own message, status and first errorCode (e.g. AuthorizationError.CUSTOMER_NOT_ENABLED)."""
    try:
        error = json.loads(raw.decode("utf-8")).get("error") or {}
    except (ValueError, AttributeError, UnicodeDecodeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    message = str(error.get("message") or "no message")
    label = error.get("status") or "HTTP"
    code = error.get("code") or status
    first = None
    for detail in error.get("details") or []:
        for e in (
            (detail.get("errors") or []) if isinstance(detail, dict) else []
        ):
            ec = e.get("errorCode") if isinstance(e, dict) else None
            if isinstance(ec, dict) and ec:
                kind, value = next(iter(ec.items()))
                first = f"{kind[:1].upper()}{kind[1:]}.{value}"
                message = e.get("message") or message
                break
        if first:
            break
    text = f"Google Ads refused it ({code} {label}"
    text += f", {first}): " if first else "): "
    text += message
    if request_id:
        text += f" (request id {request_id})"
    return ToolError(text)


# ---------------------------------------------------------------- the edge


def _url(path: str) -> str:
    base = (
        os.environ.get(REST_BASE_ENV, "").strip().rstrip("/")
        or DEFAULT_REST_BASE
    )
    return f"{base}/{adnero_auth.api_version()}/{path}"


def _token() -> str:
    creds = utils._create_credentials()
    if not getattr(
        creds, "token", None
    ):  # ADC outside multi-tenant mode: mint one
        import google.auth.transport.requests

        creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _call(
    method: str,
    path: str,
    body: dict | None,
    login_customer_id: str | int | None,
) -> tuple[int, Any]:
    """(HTTP status, Google's JSON). Google's errors become a ToolError. Never logs the token or a body."""
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }
    login = utils._get_login_customer_id(login_customer_id)
    if login:
        headers["login-customer-id"] = login
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _url(path), data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        raise google_error(
            exc.code, exc.read() or b"", exc.headers.get("request-id")
        ) from None
    except (
        Exception
    ):  # timeout, DNS, refused: say nothing that could echo a header
        raise ToolError(
            "Google Ads did not answer within 30 s (network or timeout); try again."
        ) from None
    if not raw:
        return status, {}
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ToolError(
            f"Google Ads answered {status} with something that is not JSON; check 'path'."
        ) from None


class ApiReadResult(BaseModel):
    status: int = Field(description="Google's HTTP status.")
    data: Any = Field(
        description="Google's JSON answer; the start of its JSON text when truncated."
    )
    truncated: bool = Field(
        default=False,
        description=f"The answer was over {MAX_RESPONSE_CHARS:,} characters.",
    )
    hint: str | None = Field(
        default=None, description="What to do when truncated."
    )


@api_mcp.tool(
    title="Read any Google Ads resource of the account (raw REST)",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def read(
    customer_id: str | int,
    path: str,
    method: Literal["GET", "POST"] = "GET",
    body: dict | None = None,
    login_customer_id: str | int | None = None,
) -> ApiReadResult:
    """Reads the raw Google Ads REST API under one account, for fields no typed tool covers.

    GET any path under customers/{customer_id}, or POST a GAQL search to customers/{customer_id}/googleAds:search
    (or :searchStream) with body {"query": "SELECT ... FROM ..."}. Any other POST is refused: changes go through
    api_mutate. Prefer search_search for ordinary GAQL. Costs 1 Google Ads operation per call.

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        path: Relative to the API version, e.g. 'customers/1234567890/googleAds:search'. No host, no version, no
            '..', no query string; the same customer as customer_id.
        method: 'GET' (default) or 'POST' (searches only).
        body: POST only: the JSON request, e.g. {"query": "SELECT campaign.id FROM campaign", "pageSize": ...}.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    p = contained_path(cid, path)
    verb = read_route(cid, p, method)
    if verb == "GET" and body is not None:
        raise ToolError(
            "Error: a GET takes no 'body'; a search is method='POST' to googleAds:search."
        )
    if verb == "POST" and not isinstance(body, dict):
        raise ToolError(
            "Error: a search POST needs 'body', e.g. {'query': 'SELECT campaign.id FROM campaign'}."
        )
    status, data = _call(
        verb, p, body if verb == "POST" else None, login_customer_id
    )
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= MAX_RESPONSE_CHARS:
        return ApiReadResult(status=status, data=data)
    return ApiReadResult(
        status=status,
        data=text[:MAX_RESPONSE_CHARS],
        truncated=True,
        hint=(
            "The answer was cut. Narrow the query (fewer fields, a WHERE, a LIMIT, a shorter date range) or page it "
            "(googleAds:search with pageToken)."
        ),
    )


@api_mcp.tool(
    title="Preview, then apply, a raw Google Ads mutate",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def mutate(
    customer_id: str | int,
    path: str,
    body: dict,
    validate_only: bool = True,
    checksum: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
    plan_id: str | None = None,
) -> m.MutationResult:
    """Sends a raw Google Ads REST mutate for one account, for changes no typed tool covers.

    Prefer the typed campaigns_* and keywords_* tools when they cover the change. New campaigns, ad groups and ads
    should be created PAUSED. Nothing is ever removed here (pause it, or keywords_remove for keywords). Money and
    status changes made here wait for a person's approval in AdHive. The request is atomic: every operation applies
    or none (partialFailure and validateOnly in the body are ignored). Costs 1 Google Ads operation per entry in
    operations on each call (the preview counts too); at most 100 entries.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same arguments,
    validate_only=false and the plan_id. A client with form elicitation may call once with validate_only=false:
    the person is asked in the client.

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        path: 'customers/{customer_id}/<resource>:mutate', resource in camelCase: campaigns, adGroups,
            campaignBudgets, adGroupAds, campaignCriteria... or googleAds (mutateOperations across resources).
        body: The REST request: {"operations": [{"update": {...}, "updateMask": "..."}]}, or for googleAds
            {"mutateOperations": [{"campaignOperation": {"create": {...}}}]}. Temporary ids are negative.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    p = contained_path(cid, path)
    resource = mutate_resource(cid, p)
    clean, normalized = schema_normalize_body(resource, clean_body(body))
    ops = operations_of(clean)
    schema_check(resource, clean, cid)
    items = change_items(resource, ops)

    def noted(text: str) -> str:
        return f"{normalized} {text}" if normalized else text

    items_out = m._items_out(items)

    def _validate() -> None:
        _call("POST", p, {**clean, "validateOnly": True}, login_customer_id)

    def _commit() -> m.MutationResult:
        _, data = _call("POST", p, clean, login_customer_id)
        return m.MutationResult(
            status="APPLIED",
            customer_id=cid,
            change_items=items_out,
            resource_names=resource_names_of(data),
            next_step=noted(
                "Done. Read it back with search_search or api_read to confirm, or report the change to the person."
            ),
        )

    # The plan binds the customer, the path and the cleaned body (plans.py: HMAC with the server secret).
    return m.gate(
        tool="api_mutate",
        customer_id=cid,
        digest=plan_core.digest_of(
            "api_mutate", cid, {"path": p, "body": clean}
        ),
        change_items=items,
        validate_only=validate_only,
        plan_id=plan_id or checksum,
        ctx=ctx,
        validate=_validate,
        commit=_commit,
        notes=[normalized] if normalized else (),
    )
