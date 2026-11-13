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

"""lookup_geo, lookup_language and resolve, pure part: queries, matching, shaping."""

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from ads_mcp.reads.common import name, quote, re2_escape, tail_id

MAX_GEO = 25
MAX_MATCHES = 50
Match = Literal["exact", "contains"]


def geo(constant: Any, reach: Any = None, search_term: str = "") -> dict:
    """A GeoTargetConstant as the answer shows it (id, name, canonical name, type, country, status, EU flag)."""
    return {
        "id": str(constant.id),
        "name": constant.name,
        "canonical_name": constant.canonical_name,
        "type": constant.target_type,
        "country_code": constant.country_code or None,
        "status": name(constant.status),
        "eu_political_declaration_required": bool(
            getattr(
                constant, "require_eu_political_advertising_declaration", False
            )
        ),
        **({"reach": int(reach)} if reach else {}),
        **({"query": search_term} if search_term else {}),
    }


def suggestions(response: Any) -> list[dict]:
    return [
        geo(s.geo_target_constant, s.reach, s.search_term)
        for s in getattr(response, "geo_target_constant_suggestions", None)
        or []
    ]


GEO_BY_ID = (
    "SELECT geo_target_constant.id, geo_target_constant.name, geo_target_constant.canonical_name, "
    "geo_target_constant.target_type, geo_target_constant.country_code, geo_target_constant.status, "
    "geo_target_constant.require_eu_political_advertising_declaration "
    "FROM geo_target_constant WHERE geo_target_constant.resource_name IN ({names})"
)


GEO_CHUNK = 100


def geo_by_id_queries(ids: Iterable[str]) -> list[str]:
    """One query per GEO_CHUNK ids (an IN list stays short)."""
    ids = list(ids)
    return [
        GEO_BY_ID.format(
            names=", ".join(
                quote(f"geoTargetConstants/{i}") for i in ids[n : n + GEO_CHUNK]
            )
        )
        for n in range(0, len(ids), GEO_CHUNK)
    ]


LANGUAGE_QUERY = (
    "SELECT language_constant.id, language_constant.code, language_constant.name, "
    "language_constant.targetable FROM language_constant"
)


def language(row: Any) -> dict:
    lc = row.language_constant
    return {
        "id": str(lc.id),
        "code": lc.code,
        "name": lc.name,
        "targetable": bool(lc.targetable),
    }


def match_languages(languages: list[dict], text: str) -> list[dict]:
    """Every language whose code, name or id equals or contains `text` (case-insensitive); all when empty."""
    needle = str(text or "").strip().lower()
    if not needle:
        return languages
    exact = [
        l
        for l in languages
        if needle in (l["code"].lower(), l["name"].lower(), l["id"])
    ]
    return exact or [
        l
        for l in languages
        if needle in l["name"].lower() or needle in l["code"].lower()
    ]


@dataclass(frozen=True)
class Kind:
    resource: str
    extra: tuple[str, ...] = ()  # more fields to show (the parent, the type...)
    status: str | None = (
        "status"  # field that says REMOVED, when the resource has one
    )


KINDS = {
    "campaign": Kind(
        "campaign",
        (
            "campaign.advertising_channel_type",
            "campaign.advertising_channel_sub_type",
        ),
    ),
    "ad_group": Kind("ad_group", ("campaign.id", "campaign.name")),
    "asset": Kind("asset", ("asset.type",), status=None),
    "conversion_action": Kind(
        "conversion_action",
        (
            "conversion_action.type",
            "conversion_action.category",
            "conversion_action.app_id",
        ),
    ),
    "user_list": Kind(
        "user_list",
        ("user_list.type", "user_list.size_for_search"),
        status=None,
    ),
    "label": Kind("label"),
    "campaign_budget": Kind(
        "campaign_budget",
        ("campaign_budget.amount_micros", "campaign_budget.explicitly_shared"),
    ),
}


def resolve_query(
    kind: str, text: str, match: Match, include_removed: bool
) -> str:
    k = KINDS[kind]
    r = k.resource
    fields = [f"{r}.id", f"{r}.name", f"{r}.resource_name"]
    fields += [f"{r}.{k.status}"] if k.status else []
    fields += list(k.extra)
    where = (
        f"{r}.name = {quote(text)}"
        if match == "exact"
        else f"{r}.name REGEXP_MATCH {quote('(?i).*' + re2_escape(text) + '.*')}"
    )
    if k.status and not include_removed:
        where += f" AND {r}.{k.status} != 'REMOVED'"
    return (
        f"SELECT {', '.join(fields)} FROM {r} WHERE {where} "
        f"ORDER BY {r}.id LIMIT {MAX_MATCHES + 1}"
    )


def _get(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
    return obj


def _plain(value: Any) -> Any:
    n = (
        name(value)
        if hasattr(value, "name") and not isinstance(value, str)
        else None
    )
    if n is not None:
        return n
    if isinstance(value, (bool, float)):
        return value
    if isinstance(
        value, int
    ):  # ids, micros, sizes: int64, sent as strings like everywhere else
        return str(value)
    return None if value in (None, "") else str(value)


def resolved(kind: str, row: Any) -> dict:
    k = KINDS[kind]
    r = getattr(row, k.resource)
    extra = {
        (
            f.split(".", 1)[0] + "_" + f.rsplit(".", 1)[-1]
            if not f.startswith(k.resource + ".")
            else f.rsplit(".", 1)[-1]
        ): _plain(_get(row, f))
        for f in k.extra
    }
    return {
        "id": str(r.id),
        "name": r.name,
        "resource_name": r.resource_name,
        **({"status": name(getattr(r, k.status))} if k.status else {}),
        **{key: v for key, v in extra.items() if v is not None},
    }


def criterion_geo_ids(resource_names: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tail_id(n) for n in resource_names if n))
