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

"""Audience writes (edge): audiences_create, audiences_update, audiences_custom_segment_create. Preview, then
apply with the signed plan (adnero_mutate.gate), like every other write."""

from typing import Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError

from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp.api_version import API_VERSION
from ads_mcp.rules import audience as A
from ads_mcp.tools.adnero_app_common import _CREATE
from ads_mcp.tools.adnero_audiences import audience_row, audiences_mcp

from mcp.types import ToolAnnotations

_UPDATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

_SEGMENT_ARGS = {
    "user_list": "user_list_ids",
    "user_interest": "interest_ids",
    "life_event": "life_event_ids",
    "detailed_demographic": "detailed_demographic_ids",
    "custom_audience": "custom_segment_ids",
}

_HOW = (
    "An Audience is a signal: Google uses it as a hint and still reaches people outside it (App install and "
    "Performance Max campaigns cannot be limited to an age or gender). Attach it with audiences_signal_attach."
)


def _ids(values: list[Any] | None, what: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            m.plain_id(v, what) for v in m.bounded(values or [], what, low=0)
        )
    )


def _segments(
    given: dict[str, list[Any] | None],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (kind, i)
        for kind, arg in _SEGMENT_ARGS.items()
        for i in _ids(given.get(arg), arg)
    )


def _refuse(spec: A.AudienceSpec) -> None:
    found = A.problems(spec)
    if found:
        raise ToolError(
            "Error: refused before Google:\n"
            + "\n".join(f"- {p}" for p in found)
        )


def _fill(client, audience, cid: str, spec: A.AudienceSpec) -> None:
    """Writes the spec into an Audience message (all its dimensions and the exclusions)."""

    def dim():
        return client.get_type("AudienceDimension", version=API_VERSION)

    dims = []
    if spec.ages:
        d = dim()
        for text in spec.ages.values:
            low, high = A.parse_age(text)
            seg = client.get_type("AgeSegment", version=API_VERSION)
            seg.min_age = low
            if high is not None:
                seg.max_age = high
            d.age.age_ranges.append(seg)
        d.age.include_undetermined = spec.ages.include_undetermined
        dims.append(d)
    for demo, field, values, enum in (
        (spec.genders, "gender", "genders", client.enums.GenderTypeEnum),
        (
            spec.parental_statuses,
            "parental_status",
            "parental_statuses",
            client.enums.ParentalStatusTypeEnum,
        ),
        (
            spec.income_ranges,
            "household_income",
            "income_ranges",
            client.enums.IncomeRangeTypeEnum,
        ),
    ):
        if demo:
            d = dim()
            inner = getattr(d, field)
            getattr(inner, values).extend(getattr(enum, v) for v in demo.values)
            inner.include_undetermined = demo.include_undetermined
            dims.append(d)
    if spec.segments:
        d = dim()
        for kind, seg_id in spec.segments:
            collection, field, inner = A.SEGMENTS[kind]
            seg = client.get_type("AudienceSegment", version=API_VERSION)
            setattr(
                getattr(seg, field),
                inner,
                f"customers/{cid}/{collection}/{seg_id}",
            )
            d.audience_segments.segments.append(seg)
        dims.append(d)
    del audience.dimensions[:]
    audience.dimensions.extend(dims)
    exclusion = client.get_type(
        "AudienceExclusionDimension", version=API_VERSION
    )
    for list_id in spec.exclude_user_list_ids:
        ex = client.get_type("ExclusionSegment", version=API_VERSION)
        ex.user_list.user_list = f"customers/{cid}/userLists/{list_id}"
        exclusion.exclusions.append(ex)
    if (
        spec.exclude_user_list_ids
    ):  # an update clears it through the mask when none are left
        audience.exclusion_dimension = exclusion
    audience.name = spec.name.strip()
    audience.description = spec.description


def _show(value: Any) -> str:
    if value is None:
        return "(none)"
    return (
        ", ".join(str(v) for v in value)
        if isinstance(value, (list, tuple))
        else str(value)
    )


def _items(op: str, key: str, fields: dict[str, Any]) -> list[pac.ChangeItem]:
    return pac.build_change_items(
        operation=op,
        entity_readable="Audience",
        primary_key_readable=key,
        fields=fields,
    )


@audiences_mcp.tool(
    name="create",
    title="Create an audience (ages, genders, interests, lists, custom segments)",
    annotations=_CREATE,
)
def create(
    customer_id: str | int,
    name: str,
    description: str = "",
    age_ranges: list[str] | None = None,
    genders: list[str] | None = None,
    parental_statuses: list[str] | None = None,
    income_ranges: list[str] | None = None,
    include_undetermined: bool = False,
    user_list_ids: list[str] | None = None,
    interest_ids: list[str] | None = None,
    life_event_ids: list[str] | None = None,
    detailed_demographic_ids: list[str] | None = None,
    custom_segment_ids: list[str] | None = None,
    exclude_user_list_ids: list[str] | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Creates a reusable Audience (account scope): the audience signal for App install ad groups and Performance
    Max asset groups. It combines demographics (age, gender, parental status, household income) with segments
    (user lists, affinity / in-market interests, life events, detailed demographics, custom segments). Find segment
    ids with audiences_segments_search (each hit names the argument it goes in); make a custom segment from keywords,
    URLs or apps with audiences_custom_segment_create. App campaigns cannot TARGET an age, but an audience signal
    CAN carry ages: create the audience here, then audiences_signal_attach.

    Costs 1 Google Ads operation on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it, nothing changes (PROPOSED, a plan_id).
    Show change_items to the person; once they approve, call again with the SAME arguments, validate_only=false and
    the plan_id (valid 15 minutes).

    Args:
        customer_id: The Google Ads account.
        name: Unique name in the account (1-255 characters).
        description: Optional description.
        age_ranges: e.g. ['25-54'] or ['25-34', '35-44', '65+']. Minimums 18/25/35/45/55/65, maximums 24/34/44/54/64.
        genders: MALE, FEMALE.
        parental_statuses: PARENT, NOT_A_PARENT.
        income_ranges: INCOME_RANGE_0_50 (bottom 50%), INCOME_RANGE_50_60 ... INCOME_RANGE_90_UP (top 10%).
        include_undetermined: Also count people Google cannot classify in the demographics given.
        user_list_ids: User lists (your data: customer match, app users, website visitors).
        interest_ids: Affinity and in-market segments (user_interest ids).
        life_event_ids: Life event segments.
        detailed_demographic_ids: Detailed demographic segments.
        custom_segment_ids: Custom segments (custom audiences).
        exclude_user_list_ids: User lists to exclude (only user lists can be excluded).
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    spec = A.AudienceSpec(
        name=name.strip(),
        description=(description or "").strip(),
        ages=A.demo_of(age_ranges, include_undetermined, upper=False),
        genders=A.demo_of(genders, include_undetermined),
        parental_statuses=A.demo_of(parental_statuses, include_undetermined),
        income_ranges=A.demo_of(income_ranges, include_undetermined),
        segments=_segments(locals()),
        exclude_user_list_ids=_ids(
            exclude_user_list_ids, "exclude_user_list_ids"
        ),
    )
    _refuse(spec)

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        _fill(client, op.audience_operation.create, cid, spec)
        return [op]

    return m.mutate(
        tool="audiences_create",
        customer_id=cid,
        payload={"spec": spec.model_dump(mode="json")},
        operations=operations,
        change_items=_items("CREATE", spec.name, A.summary(spec)),
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
        notes=[_HOW],
    )


@audiences_mcp.tool(
    name="update",
    title="Change an audience's name, demographics or segments",
    annotations=_UPDATE,
)
def update(
    customer_id: str | int,
    audience_id: str,
    name: str | None = None,
    description: str | None = None,
    age_ranges: list[str] | None = None,
    genders: list[str] | None = None,
    parental_statuses: list[str] | None = None,
    income_ranges: list[str] | None = None,
    include_undetermined: bool | None = None,
    user_list_ids: list[str] | None = None,
    interest_ids: list[str] | None = None,
    life_event_ids: list[str] | None = None,
    detailed_demographic_ids: list[str] | None = None,
    custom_segment_ids: list[str] | None = None,
    exclude_user_list_ids: list[str] | None = None,
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Changes an Audience in place; every ad group and asset group using it as a signal follows. It reads the
    audience first and keeps what you do not pass: an argument left out (null) keeps its value, an empty list
    clears it, a list replaces it (segment kinds are replaced one kind at a time). include_undetermined applies to
    the demographics you pass (left out: each keeps its own).

    Costs 1 Google Ads operation for the read plus 1 on each call (the preview counts too). The plan binds the
    audience as read: changed by someone else since the preview, the apply is refused (preview again).

    Args:
        customer_id: The Google Ads account.
        audience_id: The audience (audiences_list).
        name: New name.
        description: New description.
        age_ranges: e.g. ['25-54'] ([] removes the age dimension).
        genders: MALE, FEMALE.
        parental_statuses: PARENT, NOT_A_PARENT.
        income_ranges: INCOME_RANGE_0_50 ... INCOME_RANGE_90_UP.
        include_undetermined: For the demographics passed: also count people Google cannot classify.
        user_list_ids: User lists.
        interest_ids: Affinity and in-market segments.
        life_event_ids: Life event segments.
        detailed_demographic_ids: Detailed demographic segments.
        custom_segment_ids: Custom segments.
        exclude_user_list_ids: Excluded user lists.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    aid = m.plain_id(audience_id, "audience_id")
    row = audience_row(cid, aid, login_customer_id)
    if row.get("audience.status") == "REMOVED":
        raise ToolError(f"Error: audience {aid} is REMOVED.")
    now = A.spec_from_plain(
        str(row.get("audience.name", "")),
        str(row.get("audience.description", "")),
        row.get("audience.dimensions") or [],
        row.get("audience.exclusion_dimension"),
    )
    given = locals()

    def demo(
        values, current: A.Demo | None, upper: bool = True
    ) -> A.Demo | None:
        if values is None:
            return current
        undetermined = (
            include_undetermined
            if include_undetermined is not None
            else bool(current and current.include_undetermined)
        )
        return A.demo_of(values, undetermined, upper) if values else None

    replaced = {
        kind for kind, arg in _SEGMENT_ARGS.items() if given[arg] is not None
    }
    segments = tuple(s for s in now.segments if s[0] not in replaced) + tuple(
        (kind, i) for kind, i in _segments(given) if kind in replaced
    )
    new = A.AudienceSpec(
        name=name.strip() if name is not None else now.name,
        description=(
            description.strip() if description is not None else now.description
        ),
        ages=demo(age_ranges, now.ages, upper=False),
        genders=demo(genders, now.genders),
        parental_statuses=demo(parental_statuses, now.parental_statuses),
        income_ranges=demo(income_ranges, now.income_ranges),
        segments=segments,
        exclude_user_list_ids=(
            _ids(exclude_user_list_ids, "exclude_user_list_ids")
            if exclude_user_list_ids is not None
            else now.exclude_user_list_ids
        ),
    )
    _refuse(new)
    if new == now:
        raise ToolError(
            "Error: nothing to change (every value passed equals the audience now)."
        )
    before, after = A.summary(now), A.summary(new)
    items = [
        pac.ChangeItem(
            operation="UPDATE",
            entity_readable="Audience",
            primary_key_readable=aid,
            field_readable=field,
            value_readable=_show(after[field]),
            original_value_readable=_show(before[field]),
        )
        for field in after
        if after[field] != before[field]
    ] + (
        [
            pac.ChangeItem(
                operation="UPDATE",
                entity_readable="Audience",
                primary_key_readable=aid,
                field_readable="Name",
                value_readable=new.name,
                original_value_readable=now.name,
            )
        ]
        if new.name != now.name
        else []
    )

    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        aud = op.audience_operation.update
        aud.resource_name = f"customers/{cid}/audiences/{aid}"
        _fill(client, aud, cid, new)
        op.audience_operation.update_mask.paths.extend(
            ["name", "description", "dimensions", "exclusion_dimension"]
        )
        return [op]

    return m.mutate(
        tool="audiences_update",
        customer_id=cid,
        payload={
            "audience_id": aid,
            "before": now.model_dump(mode="json"),
            "after": new.model_dump(mode="json"),
        },
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
    )


# ---------------------------------------------------------------- audiences_custom_segment_create

KEYWORD_MAX, KEYWORD_WORDS, URL_MAX = 80, 10, 2048


def _members(keywords, urls, apps, place_category_ids) -> list[tuple[str, str]]:
    kws = [k.strip() for k in keywords or [] if k and k.strip()]
    bad_kw = [
        k for k in kws if len(k) > KEYWORD_MAX or len(k.split()) > KEYWORD_WORDS
    ]
    bad_url = [
        u
        for u in urls or []
        if not str(u).startswith(("http://", "https://"))
        or len(str(u)) > URL_MAX
    ]
    if bad_kw:
        raise ToolError(
            f"Error: a keyword is at most {KEYWORD_WORDS} words and {KEYWORD_MAX} characters: {bad_kw[:3]}"
        )
    if bad_url:
        raise ToolError(
            f"Error: a URL starts with http:// or https:// and is at most {URL_MAX} characters: {bad_url[:3]}"
        )
    members = (
        [("KEYWORD", k) for k in kws]
        + [("URL", str(u).strip()) for u in urls or []]
        + [("APP", str(a).strip()) for a in apps or [] if str(a).strip()]
        + [
            ("PLACE_CATEGORY", m.plain_id(p, "place_category_ids"))
            for p in place_category_ids or []
        ]
    )
    return list(
        dict.fromkeys(
            m.bounded(members, "keywords/urls/apps/place_category_ids")
        )
    )


@audiences_mcp.tool(
    name="custom_segment_create",
    title="Create a custom segment from keywords, URLs or apps",
    annotations=_CREATE,
)
def custom_segment_create(
    customer_id: str | int,
    name: str,
    keywords: list[str] | None = None,
    urls: list[str] | None = None,
    apps: list[str] | None = None,
    place_category_ids: list[str] | None = None,
    type: Literal[
        "AUTO", "SEARCH"
    ] = "AUTO",  # noqa: A002 (Google's field name)
    description: str = "",
    validate_only: bool = True,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Creates a custom segment (Google's CustomAudience): people interested in these keywords, browsing pages like
    these URLs, or using apps like these. Put its id in audiences_create custom_segment_ids. New segments are AUTO
    (Google picks interest or purchase intent) or SEARCH (people who searched these words on Google); Google no
    longer allows creating INTEREST or PURCHASE_INTENT ones.

    Costs 1 Google Ads operation on each call (the preview counts too). Preview, then apply with the plan_id.

    Args:
        customer_id: The Google Ads account.
        name: Unique name in the account.
        keywords: Interests or search terms (at most 10 words, 80 characters each), e.g. 'online slots'.
        urls: Web pages like the ones the people visit (with https://).
        apps: Android package names, e.g. 'com.playtika.slotomania'.
        place_category_ids: Google place category ids.
        type: AUTO (default) or SEARCH.
        description: Optional description.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    title = (name or "").strip()
    if not title:
        raise ToolError("Error: 'name' is required.")
    kind = str(getattr(type, "value", type)).upper()
    if kind not in ("AUTO", "SEARCH"):
        raise ToolError(
            "Error: type is AUTO or SEARCH (Google refuses new INTEREST / PURCHASE_INTENT segments)."
        )
    members = _members(keywords, urls, apps, place_category_ids)

    def operations(client):
        op = client.get_type("CustomAudienceOperation", version=API_VERSION)
        ca = op.create
        ca.name = title
        ca.description = (description or "").strip()
        ca.type_ = getattr(client.enums.CustomAudienceTypeEnum, kind)
        for member_type, value in members:
            mem = client.get_type("CustomAudienceMember", version=API_VERSION)
            mem.member_type = getattr(
                client.enums.CustomAudienceMemberTypeEnum, member_type
            )
            if member_type == "PLACE_CATEGORY":
                mem.place_category = int(value)
            else:
                setattr(mem, member_type.lower(), value)
            ca.members.append(mem)
        return [op]

    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Custom segment",
        primary_key_readable=title,
        fields={
            "Type": kind,
            "Keywords": [v for t, v in members if t == "KEYWORD"] or None,
            "URLs": [v for t, v in members if t == "URL"] or None,
            "Apps": [v for t, v in members if t == "APP"] or None,
            "Place categories": [v for t, v in members if t == "PLACE_CATEGORY"]
            or None,
        },
    )
    return m.service_mutate(
        tool="audiences_custom_segment_create",
        customer_id=cid,
        payload={
            "name": title,
            "type": kind,
            "members": members,
            "description": (description or "").strip(),
        },
        service_name="CustomAudienceService",
        request_type="MutateCustomAudiencesRequest",
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=plan_id,
        ctx=ctx,
    )
