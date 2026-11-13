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

"""Schema tools (adnero fork; see ADNERO.md): JSON Schemas of every v26 resource, operation and enum.

The schemas are generated from the googleapis protos and checked in (ads_mcp/schemas/<API_VERSION>/); only
schema_get_resource's query metadata and schema_check_query ask Google (GoogleAdsFieldService, cached per process).
"""

import difflib
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import rules
from ads_mcp.api_version import API_VERSION
from ads_mcp.schema import assemble, fieldmeta, gaql, proto2js, store

schema_mcp = FastMCP("schema")

MAX_PAGE = 200
OFFLINE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ONLINE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


# ---------------------------------------------------------------- pure helpers


def resolve_resource(data: Any, name: str) -> str:
    """'campaign', 'Campaign', 'campaigns' (the REST collection), 'ad_group_ad' -> the GAQL name, else ToolError."""
    resources = data["index"]["resources"]
    raw = str(name or "").strip()
    candidates = [raw, raw.lower(), proto2js.snake(raw)]
    by_collection = {
        r["mutate"]["collection"]: g
        for g, r in resources.items()
        if r.get("mutate", {}).get("collection")
    }
    for c in candidates:
        if c in resources:
            return c
        if c in by_collection:
            return by_collection[c]
    if raw in by_collection:
        return by_collection[raw]
    close = difflib.get_close_matches(proto2js.snake(raw), resources, n=5)
    raise ToolError(
        f"Error: no resource '{raw}' in {API_VERSION}."
        + (f" Close: {', '.join(close)}." if close else "")
        + " schema_list_resources lists them."
    )


def resolve_enum(data: Any, name: str) -> str:
    enums = data["enums"]
    raw = str(name or "").strip()
    tail = raw.rsplit(".", 1)[-1]
    by_lower = {k.lower(): k for k in enums}
    for c in (raw, tail, tail.removesuffix("Enum")):
        if c in enums:
            return c
        key = c.replace("_", "").lower()
        if key in by_lower:
            return by_lower[key]
    close = difflib.get_close_matches(tail, enums, n=5)
    raise ToolError(
        f"Error: no enum '{raw}' in {API_VERSION}."
        + (f" Close: {', '.join(close)}." if close else "")
        + " A resource schema names each field's enum in `x-enum`."
    )


def resource_lines(data: Any, text: str, mutable_only: bool) -> list[dict]:
    needle = str(text or "").strip().lower()
    out = []
    for gaql_name, r in data["index"]["resources"].items():
        mutate = r.get("mutate")
        line = {
            "name": gaql_name,
            "description": r["description"],
            **(
                {
                    "mutate": mutate["path"],
                    "operations": mutate["operations"],
                }
                if mutate
                else {}
            ),
        }
        hay = f"{gaql_name} {r['message']} {r['description']}".lower()
        if mutable_only and not mutate:
            continue
        if needle and needle not in hay:
            continue
        out.append(line)
    return out


def service_lines(data: Any, text: str) -> list[dict]:
    needle = str(text or "").strip().lower()
    return [
        {
            "name": name,
            "description": s["description"],
            "methods": sorted(s["methods"]),
        }
        for name, s in data["index"]["services"].items()
        if not needle
        or needle
        in f"{name} {s['description']} {' '.join(s['methods'])}".lower()
    ]


def google_ads_key(data: Any, operation_def: str) -> str | None:
    return next(
        (
            k
            for k, v in data["index"]["googleAdsMutate"].items()
            if v == operation_def
        ),
        None,
    )


def _page(items: list, page_size: int, page_token: str | None):
    if not 1 <= int(page_size) <= MAX_PAGE:
        raise ToolError(f"Error: page_size is 1 to {MAX_PAGE}.")
    start = int(page_token) if page_token and str(page_token).isdigit() else 0
    end = start + int(page_size)
    return items[start:end], (str(end) if end < len(items) else None)


# ---------------------------------------------------------------- tools


class ResourceList(BaseModel):
    version: str
    resources: list[dict] = Field(
        description="name (GAQL / schema_get_resource), description, and for mutable ones the REST `mutate` path "
        "(api_mutate) and its operations."
    )
    services: list[dict] | None = Field(
        default=None,
        description="With include_services: every service and its methods (RPC names).",
    )
    total: int
    next_page_token: str | None = None


@schema_mcp.tool(
    title="List Google Ads resources (and services) of the pinned API version",
    annotations=OFFLINE,
)
def list_resources(
    filter: str | None = None,
    mutable_only: bool = False,
    include_services: bool = False,
    page_size: int = 100,
    page_token: str | None = None,
) -> ResourceList:
    """Lists every Google Ads resource of the pinned API version, one line each, with its mutate path.

    Start here to find the resource a task needs, then schema_get_resource (fields, types, enums, query
    metadata) or schema_get_mutate (the exact body api_mutate takes). Free: no Google call.

    Args:
        filter: Text to look for in the name or description, e.g. 'asset', 'app', 'criterion'.
        mutable_only: Only resources that have a mutate (create/update/remove).
        include_services: Also list the services and their methods (e.g. GeoTargetConstantService).
        page_size: Resources per page, 1 to 200 (default 100).
        page_token: The next_page_token of the previous page.
    """
    data = store.load()
    lines = resource_lines(data, filter or "", mutable_only)
    page, nxt = _page(lines, page_size, page_token)
    return ResourceList(
        version=API_VERSION,
        resources=page,
        services=(
            service_lines(data, filter or "") if include_services else None
        ),
        total=len(lines),
        next_page_token=nxt,
    )


class ResourceSchema(BaseModel):
    resource: str = Field(description="The GAQL name (FROM <resource>).")
    pattern: str | None = Field(
        default=None, description="Its resource name pattern."
    )
    mutate: dict | None = Field(
        default=None,
        description="REST path and operations when it is mutable: schema_get_mutate gives each operation's body.",
    )
    json_schema: dict = Field(
        description="JSON Schema 2020-12 of the resource as read: proto3 JSON names (lowerCamelCase), readOnly = "
        "output only, x-immutable = fixed after create, required, oneOf groups, x-enum = the enum's name "
        "(schema_get_enum for value descriptions), x-resource = the resource a name points to."
    )
    query: dict | None = Field(
        default=None,
        description="GoogleAdsFieldService metadata for GAQL: fields {name: {type, can: 'select filter sort', "
        "enum, repeated}} (GAQL names are snake_case dotted, e.g. campaign.app_campaign_setting.app_id), "
        "attribute_resources, and selectable metrics / segments (names with query='all', else counts).",
    )
    query_error: str | None = Field(
        default=None,
        description="Why the query metadata is missing (the schema is still complete).",
    )


def query_section(
    resource: str,
    head: gaql.FieldMeta | None,
    own: dict,
    fields: list[str],
    full: bool,
) -> dict:
    """The answer's `query` block (pure): own attributes, narrowed to `fields` when given."""
    prefixes = tuple(f"{resource}.{proto2js.snake(f)}" for f in fields)
    picked = {
        n: m.compact()
        for n, m in own.items()
        if not prefixes
        or any(n == p or n.startswith(p + ".") for p in prefixes)
    }
    head = head or gaql.FieldMeta(name=resource, category="RESOURCE")
    return {
        "fields": picked,
        "attribute_resources": list(head.attribute_resources),
        "metrics": list(head.metrics) if full else len(head.metrics),
        "segments": list(head.segments) if full else len(head.segments),
    }


@schema_mcp.tool(
    title="JSON Schema and GAQL metadata of one Google Ads resource",
    annotations=ONLINE,
)
def get_resource(
    resource: str,
    fields: list[str] | None = None,
    brief: bool = False,
    query: Literal["fields", "all", "none"] = "fields",
    login_customer_id: str | int | None = None,
) -> ResourceSchema:
    """The JSON Schema of one resource (every field, type, enum names, required, immutable, output-only, oneofs)
    plus its GAQL metadata from GoogleAdsFieldService (data type, enum values, selectable / filterable / sortable).

    Big resources (campaign, ad_group_ad, asset) run to 60 KB: pass `fields` to keep only what you need, e.g.
    ['appCampaignSetting', 'targetCpa', 'status'] or ['ad.appAd']; oneof branches you do not name are pruned.
    The schema is free (checked in); the query metadata costs 1-2 Google requests the first time a resource is
    asked for in this server process, then it is cached (query='none': no Google call).

    Args:
        resource: The GAQL name ('campaign', 'ad_group_ad'), message name ('AdGroupAd') or REST collection
            ('adGroupAds').
        fields: Dotted lowerCamelCase paths to keep (default: every field).
        brief: Drop descriptions (smaller answer).
        query: 'fields' (default) the resource's own GAQL fields; 'all' also every selectable metric and segment
            name; 'none' no Google call.
        login_customer_id: The manager (MCC) account to call through, if your login needs one.
    """
    data = store.load()
    name = resolve_resource(data, resource)
    entry = data["index"]["resources"][name]
    try:
        schema = assemble.resource_schema(data, name, fields or (), brief)
    except assemble.SchemaPathError as exc:
        raise ToolError(f"Error: {exc}") from None
    out = ResourceSchema(
        resource=name,
        pattern=data["pool"][entry["message"]].get("x-pattern"),
        mutate=(
            {
                "path": entry["mutate"]["path"],
                "operations": entry["mutate"]["operations"],
            }
            if "mutate" in entry
            else None
        ),
        json_schema=schema,
    )
    if query == "none":
        return out
    try:
        head, own, _ = fieldmeta.resource_fields(name, login_customer_id)
    except Exception as exc:  # the schema is still worth returning
        return out.model_copy(update={"query_error": _why(exc)})
    return out.model_copy(
        update={
            "query": query_section(
                name, head, own, list(fields or []), query == "all"
            )
        }
    )


class MutateSchema(BaseModel):
    resource: str
    operation: str
    path: str = Field(
        description="api_mutate's path, customer id filled in by you."
    )
    operations_key: str = Field(
        description='Where this goes in the body: {"operations": [<schema>]} (or {"operation": <schema>}).'
    )
    google_ads_key: str | None = Field(
        default=None,
        description='In a googleAds:mutate (several resources at once): {"mutateOperations": [{<key>: <schema>}]}.',
    )
    json_schema: dict = Field(
        description="JSON Schema 2020-12 of ONE operation exactly as api_mutate takes it (lowerCamelCase); "
        "x-rules: limits the protos do not carry (text lengths, ratios, counts...)."
    )


@schema_mcp.tool(
    title="JSON Schema of one Google Ads mutate operation (create / update / remove)",
    annotations=OFFLINE,
)
def get_mutate(
    resource: str,
    operation: Literal["create", "update", "remove"],
    fields: list[str] | None = None,
    brief: bool = False,
) -> MutateSchema:
    """The JSON Schema of one operation body for api_mutate: output-only fields left out, required fields listed,
    immutable fields marked (refused in an update), oneofs as oneOf, enums as names, int64 as digit strings, plus
    the rules layer's limits in `x-rules`. api_mutate checks every body against this before Google sees it.

    Pass `fields` to keep only part of a big resource, e.g. resource='ad_group_ad', fields=['ad.appAd', 'status',
    'adGroup']. Free: no Google call.

    Args:
        resource: The GAQL name ('campaign'), message name ('Campaign') or REST collection ('campaigns').
        operation: 'create', 'update' (needs resourceName and updateMask) or 'remove'.
        fields: Dotted lowerCamelCase paths to keep (default: every writable field).
        brief: Drop descriptions (smaller answer).
    """
    data = store.load()
    name = resolve_resource(data, resource)
    mutate = data["index"]["resources"][name].get("mutate")
    if not mutate:
        raise ToolError(f"Error: {name} is read-only (no mutate service).")
    if operation not in mutate["operations"]:
        raise ToolError(
            f"Error: {name} supports {', '.join(mutate['operations'])}, not {operation}."
        )
    try:
        schema = assemble.operation_schema(
            data, name, operation, fields or (), brief
        )
    except assemble.SchemaPathError as exc:
        raise ToolError(f"Error: {exc}") from None
    return MutateSchema(
        resource=name,
        operation=operation,
        path=mutate["path"],
        operations_key=mutate["operations_key"],
        google_ads_key=google_ads_key(data, mutate["operation"]),
        json_schema={**schema, "x-rules": rules.rules_for(name)},
    )


class EnumSchema(BaseModel):
    enum: str
    json_schema: dict = Field(
        description="{type: string, enum: [names], x-enumDescriptions: {name: meaning}}."
    )


@schema_mcp.tool(
    title="Values and meanings of one Google Ads enum",
    annotations=OFFLINE,
)
def get_enum(enum: str) -> EnumSchema:
    """An enum's values with their descriptions (UNSPECIFIED / UNKNOWN left out: never send them).

    Schemas name each field's enum in `x-enum`, e.g. 'AppCampaignBiddingStrategyGoalType'. Free: no Google call.

    Args:
        enum: The enum name ('CampaignStatus'; 'CampaignStatusEnum.CampaignStatus' and 'campaign_status' work).
    """
    data = store.load()
    name = resolve_enum(data, enum)
    return EnumSchema(enum=name, json_schema=assemble.enum_schema(data, name))


class QueryCheck(BaseModel):
    valid: bool
    resource: str | None = None
    problems: list[dict] = Field(
        description="{clause, field, code, message}: UNKNOWN_FIELD, NOT_WITH_RESOURCE, NOT_SELECTABLE, "
        "NOT_FILTERABLE, NOT_SORTABLE, BAD_ENUM, DATE_FILTER_NEEDED, INCOMPATIBLE, BAD_LIMIT, UNKNOWN_RESOURCE, SYNTAX."
    )
    fields: dict = Field(
        default_factory=dict,
        description="Metadata of every field the query names: type, can, enum values.",
    )


@schema_mcp.tool(
    title="Check a GAQL query without running it",
    annotations=ONLINE,
)
def check_query(
    query: str, login_customer_id: str | int | None = None
) -> QueryCheck:
    """Checks a GAQL query against GoogleAdsFieldService metadata without running it: fields exist, are selectable /
    filterable / sortable, belong to the FROM resource (or its attribute resources), metrics and segments fit it,
    enum literals are real values, date segments have a date filter.

    Costs 1 Google request when a field is not cached yet in this server process, else nothing; it reads no
    account data.

    Args:
        query: The GAQL, e.g. "SELECT campaign.id, metrics.clicks FROM campaign WHERE segments.date DURING
            LAST_7_DAYS".
        login_customer_id: The manager (MCC) account to call through, if your login needs one.
    """
    parsed = gaql.parse(query)
    if isinstance(parsed, gaql.QueryProblem):
        return QueryCheck(valid=False, problems=[parsed.as_dict()])
    try:
        meta, _ = fieldmeta.fields(gaql.names_in(parsed), login_customer_id)
    except Exception as exc:
        raise ToolError(
            f"Google Ads field metadata could not be read: {_why(exc)}"
        ) from None
    problems = gaql.check(parsed, meta)
    return QueryCheck(
        valid=not problems,
        resource=parsed.resource,
        problems=[p.as_dict() for p in problems],
        fields={
            n: m.compact() for n, m in meta.items() if m.category != "RESOURCE"
        },
    )


def _why(exc: Exception) -> str:
    failure = getattr(exc, "failure", None)
    errors = getattr(failure, "errors", None) or []
    messages = [e.message for e in errors if getattr(e, "message", "")]
    return "; ".join(messages) or str(exc)[:300] or type(exc).__name__
