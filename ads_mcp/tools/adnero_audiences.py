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

"""Audiences (reads; adnero fork, see ADNERO.md): audiences_list, audiences_segments_search, audiences_signals_list,
plus the reads the audience writes share. The pure parts live in ads_mcp/rules/audience*.py.
"""

from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import adnero_mutate as m
from ads_mcp.reads.common import quote, re2_escape
from ads_mcp.rules import audience_signal as S

audiences_mcp = FastMCP("audiences")

READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
ROWS = 10_000

# ---------------------------------------------------------------- shared reads (edges)


def rows(cid: str, query: str, login) -> list[dict[str, Any]]:
    """One GAQL page (1 operation), plain rows."""
    from ads_mcp.tools.search import search_page

    return search_page(cid, query, ROWS, None, login).rows


def _tail(name: Any) -> str:
    return str(name or "").rsplit("/", 1)[-1]


def audience_row(cid: str, audience_id: str, login) -> dict[str, Any]:
    found = rows(
        cid,
        "SELECT audience.id, audience.name, audience.description, audience.status, audience.scope, "
        f"audience.dimensions, audience.exclusion_dimension FROM audience WHERE audience.id = {audience_id}",
        login,
    )
    if not found:
        raise ToolError(
            f"Error: audience {audience_id} not found in {cid} (audiences_list lists them)."
        )
    return found[0]


def targets(
    cid: str,
    login,
    campaign_id: str | None,
    ad_group_id: str | None,
    asset_group_id: str | None,
) -> tuple[S.Target, ...]:
    """The ad groups (App install) or asset groups (Performance Max) a signal goes on; refuses other campaigns."""
    given = [
        v for v in (campaign_id, ad_group_id, asset_group_id) if v is not None
    ]
    if len(given) != 1:
        raise ToolError(
            "Error: name exactly one of campaign_id, ad_group_id, asset_group_id."
        )
    if asset_group_id is not None:
        where = (
            f"asset_group.id = {m.plain_id(asset_group_id, 'asset_group_id')}"
        )
        return _asset_groups(cid, where, login)
    if ad_group_id is not None:
        where = f"ad_group.id = {m.plain_id(ad_group_id, 'ad_group_id')}"
        return _ad_groups(cid, where, login)
    camp = m.plain_id(campaign_id, "campaign_id")
    found = rows(
        cid,
        "SELECT campaign.id, campaign.advertising_channel_type, campaign.advertising_channel_sub_type "
        f"FROM campaign WHERE campaign.id = {camp}",
        login,
    )
    if not found:
        raise ToolError(f"Error: campaign {camp} not found in {cid}.")
    channel = str(found[0].get("campaign.advertising_channel_type", ""))
    _refuse(
        channel, str(found[0].get("campaign.advertising_channel_sub_type", ""))
    )
    where = f"campaign.id = {camp}"
    return (
        _asset_groups(cid, where, login)
        if channel == "PERFORMANCE_MAX"
        else _ad_groups(cid, where, login)
    )


def _refuse(channel: str, sub_type: str) -> None:
    problem = S.target_problem(channel, sub_type)
    if problem:
        raise ToolError(f"Error: {problem}")


def _ad_groups(cid: str, where: str, login) -> tuple[S.Target, ...]:
    found = rows(
        cid,
        "SELECT ad_group.id, ad_group.name, ad_group.status, campaign.id, campaign.advertising_channel_type, "
        f"campaign.advertising_channel_sub_type FROM ad_group WHERE {where} AND ad_group.status != 'REMOVED'",
        login,
    )
    if not found:
        raise ToolError(f"Error: no ad group found ({where}).")
    for r in found:
        _refuse(
            str(r.get("campaign.advertising_channel_type", "")),
            str(r.get("campaign.advertising_channel_sub_type", "")),
        )
    return tuple(
        S.Target(
            kind="AD_GROUP",
            id=str(r["ad_group.id"]),
            name=str(r.get("ad_group.name", "")),
            campaign_id=str(r.get("campaign.id", "")),
        )
        for r in found
    )


def _asset_groups(cid: str, where: str, login) -> tuple[S.Target, ...]:
    found = rows(
        cid,
        "SELECT asset_group.id, asset_group.name, asset_group.status, campaign.id, campaign.advertising_channel_type "
        f"FROM asset_group WHERE {where} AND asset_group.status != 'REMOVED'",
        login,
    )
    if not found:
        raise ToolError(f"Error: no asset group found ({where}).")
    for r in found:
        _refuse(str(r.get("campaign.advertising_channel_type", "")), "")
    return tuple(
        S.Target(
            kind="ASSET_GROUP",
            id=str(r["asset_group.id"]),
            name=str(r.get("asset_group.name", "")),
            campaign_id=str(r.get("campaign.id", "")),
        )
        for r in found
    )


def signals(
    cid: str, login, found: tuple[S.Target, ...]
) -> tuple[S.Signal, ...]:
    """The audience signals on these targets now (1 operation)."""
    if not found:
        return ()
    if found[0].kind == "AD_GROUP":
        names = ", ".join(
            quote(f"customers/{cid}/adGroups/{t.id}") for t in found
        )
        hits = rows(
            cid,
            "SELECT ad_group_criterion.resource_name, ad_group_criterion.ad_group, ad_group_criterion.audience.audience "
            f"FROM ad_group_criterion WHERE ad_group_criterion.ad_group IN ({names}) "
            "AND ad_group_criterion.type = 'AUDIENCE' AND ad_group_criterion.status != 'REMOVED'",
            login,
        )
        key = "ad_group_criterion"
        owner, audience = f"{key}.ad_group", f"{key}.audience.audience"
    else:
        names = ", ".join(
            quote(f"customers/{cid}/assetGroups/{t.id}") for t in found
        )
        hits = rows(
            cid,
            "SELECT asset_group_signal.resource_name, asset_group_signal.asset_group, "
            "asset_group_signal.audience.audience FROM asset_group_signal "
            f"WHERE asset_group_signal.asset_group IN ({names})",
            login,
        )
        key = "asset_group_signal"
        owner, audience = f"{key}.asset_group", f"{key}.audience.audience"
    return tuple(
        S.Signal(
            target_id=_tail(h.get(owner)),
            resource_name=str(h[f"{key}.resource_name"]),
            audience_id=_tail(h.get(audience)),
        )
        for h in hits
        if h.get(audience)
    )


# ---------------------------------------------------------------- audiences_list


class Audiences(BaseModel):
    audiences: list[dict] = Field(
        description="id, name, description, status, scope, dimensions (age_ranges {min_age, max_age}, genders, "
        "parental_statuses, income_ranges, segments as resource names), exclusion_dimension."
    )
    row_count: int


@audiences_mcp.tool(
    name="list",
    title="List the account's audiences (for audience signals)",
    annotations=READ,
)
def list_audiences(
    customer_id: str | int,
    name_contains: str | None = None,
    include_removed: bool = False,
    login_customer_id: str | int | None = None,
) -> Audiences:
    """Lists the account's Audiences: reusable sets of age ranges, genders, parental status, household income and
    segments (user lists, interests, life events, detailed demographics, custom segments). An Audience is what an
    App install ad group or a Performance Max asset group takes as its audience signal (audiences_signal_attach).

    Costs 1 Google Ads operation.

    Args:
        customer_id: The Google Ads account.
        name_contains: Only audiences whose name contains this text (any case).
        include_removed: Also list REMOVED audiences.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    conditions = (
        [] if include_removed else ["audience.status = 'ENABLED'"]
    ) + (
        [
            f"audience.name REGEXP_MATCH {quote('(?i).*' + re2_escape(name_contains.strip()) + '.*')}"
        ]
        if name_contains and name_contains.strip()
        else []
    )
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    found = rows(
        cid,
        "SELECT audience.id, audience.name, audience.description, audience.status, audience.scope, "
        f"audience.dimensions, audience.exclusion_dimension FROM audience{where} ORDER BY audience.id",
        login_customer_id,
    )
    return Audiences(
        audiences=[
            {k.split(".", 1)[1]: v for k, v in r.items()} for r in found
        ],
        row_count=len(found),
    )


# ---------------------------------------------------------------- audiences_signals_list


class Signals(BaseModel):
    targets: list[dict] = Field(
        description="One per ad group (App install) or asset group (Performance Max): kind, id, name, campaign_id, "
        "audiences [{audience_id, resource_name}]."
    )


@audiences_mcp.tool(
    title="Audience signals on an App campaign or Performance Max campaign",
    annotations=READ,
)
def signals_list(
    customer_id: str | int,
    campaign_id: str | None = None,
    ad_group_id: str | None = None,
    asset_group_id: str | None = None,
    login_customer_id: str | int | None = None,
) -> Signals:
    """Shows which Audience is attached as an audience signal to each ad group of an App install campaign, or to each
    asset group of a Performance Max campaign. Name exactly one of campaign_id, ad_group_id, asset_group_id.

    Costs 2-3 Google Ads operations.

    Args:
        customer_id: The Google Ads account.
        campaign_id: An App install or Performance Max campaign (all its ad groups / asset groups).
        ad_group_id: One App install ad group.
        asset_group_id: One Performance Max asset group.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    found = targets(
        cid, login_customer_id, campaign_id, ad_group_id, asset_group_id
    )
    now = signals(cid, login_customer_id, found)
    return Signals(
        targets=[
            {
                **t.model_dump(),
                "audiences": [
                    {
                        "audience_id": s.audience_id,
                        "resource_name": s.resource_name,
                    }
                    for s in now
                    if s.target_id == t.id
                ],
            }
            for t in found
        ]
    )


# ---------------------------------------------------------------- audiences_segments_search

SegmentKind = Literal[
    "AFFINITY",
    "IN_MARKET",
    "LIFE_EVENT",
    "DETAILED_DEMOGRAPHIC",
    "USER_LIST",
    "CUSTOM_SEGMENT",
]
_KIND_FIELD = {
    "AFFINITY": "interest_ids",
    "IN_MARKET": "interest_ids",
    "LIFE_EVENT": "life_event_ids",
    "DETAILED_DEMOGRAPHIC": "detailed_demographic_ids",
    "USER_LIST": "user_list_ids",
    "CUSTOM_SEGMENT": "custom_segment_ids",
}


def _segment_query(kind: str, text: str) -> tuple[str, str]:
    """(GAQL, the resource prefix of its fields) for one kind of segment."""
    match = (
        f" REGEXP_MATCH {quote('(?i).*' + re2_escape(text) + '.*')}"
        if text
        else ""
    )
    if kind in ("AFFINITY", "IN_MARKET"):
        name = f" AND user_interest.name{match}" if match else ""
        return (
            "SELECT user_interest.user_interest_id, user_interest.name, user_interest.taxonomy_type, "
            "user_interest.user_interest_parent, user_interest.launched_to_all FROM user_interest "
            f"WHERE user_interest.taxonomy_type = '{kind}'{name}",
            "user_interest",
        )
    resource, extra, status = {
        "LIFE_EVENT": (
            "life_event",
            "life_event.parent, life_event.launched_to_all",
            "",
        ),
        "DETAILED_DEMOGRAPHIC": (
            "detailed_demographic",
            "detailed_demographic.parent, detailed_demographic.launched_to_all",
            "",
        ),
        "USER_LIST": (
            "user_list",
            "user_list.type, user_list.membership_status, user_list.size_for_display, user_list.size_for_search",
            "",
        ),
        "CUSTOM_SEGMENT": (
            "custom_audience",
            "custom_audience.type, custom_audience.status",
            "custom_audience.status = 'ENABLED'",
        ),
    }[kind]
    conditions = [
        c for c in (status, f"{resource}.name{match}" if match else "") if c
    ]
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return (
        f"SELECT {resource}.id, {resource}.name, {extra} FROM {resource}{where}",
        resource,
    )


class Segments(BaseModel):
    segments: list[dict] = Field(
        description="kind, id, name, pass_as (the audiences_create argument that takes this id), plus parent, "
        "launched_to_all, taxonomy_type, sizes/type/status where the kind has them."
    )
    notes: list[str] = Field(default_factory=list)


@audiences_mcp.tool(
    title="Find audience segments: interests, in-market, life events, demographics, lists",
    annotations=READ,
)
def segments_search(
    customer_id: str | int,
    text: str | None = None,
    kinds: list[SegmentKind] | None = None,
    max_per_kind: int = 50,
    login_customer_id: str | int | None = None,
) -> Segments:
    """Finds the segments an Audience can hold, by name: AFFINITY and IN_MARKET interests (user_interest), LIFE_EVENT,
    DETAILED_DEMOGRAPHIC (e.g. parents of toddlers, homeowners), the account's USER_LISTs and CUSTOM_SEGMENTs. Each
    hit says which audiences_create argument takes its id (`pass_as`). Search short words in English ('casino',
    'slot', 'games', 'travel'): Google's names are matched as text, any case.

    Costs 1 Google Ads operation per kind searched (default: all six).

    Args:
        customer_id: The Google Ads account.
        text: Words the segment name contains; empty lists everything of the kinds asked (up to max_per_kind).
        kinds: Which kinds to search (default all).
        max_per_kind: At most this many hits per kind (1-500, default 50).
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    words = (text or "").strip()
    if not 1 <= max_per_kind <= 500:
        raise ToolError("Error: max_per_kind is 1 to 500.")
    chosen = tuple(
        dict.fromkeys(
            str(getattr(k, "value", k)).upper() for k in (kinds or _KIND_FIELD)
        )
    )
    bad = [k for k in chosen if k not in _KIND_FIELD]
    if bad:
        raise ToolError(
            f"Error: unknown kinds {bad}; use {sorted(_KIND_FIELD)}."
        )
    hits: list[dict] = []
    notes: list[str] = []
    for kind in chosen:
        query, prefix = _segment_query(kind, words)
        found = rows(
            cid, query + f" LIMIT {max_per_kind + 1}", login_customer_id
        )
        if len(found) > max_per_kind:
            notes.append(
                f"{kind}: more than {max_per_kind} hits; narrow `text` or raise max_per_kind."
            )
        hits.extend(_segment(kind, prefix, r) for r in found[:max_per_kind])
    return Segments(segments=hits, notes=notes)


def _segment(kind: str, prefix: str, row: dict) -> dict:
    fields = {
        k.split(".", 1)[1]: v
        for k, v in row.items()
        if k.startswith(prefix + ".")
    }
    seg_id = fields.pop("user_interest_id", None) or fields.pop("id", None)
    for ref in ("parent", "user_interest_parent"):
        if fields.get(ref):
            fields[ref] = _tail(fields[ref])
    return {
        "kind": kind,
        "id": str(seg_id),
        "name": fields.pop("name", ""),
        "pass_as": _KIND_FIELD[kind],
        **fields,
    }
