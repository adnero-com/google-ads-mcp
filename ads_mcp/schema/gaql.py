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

"""GAQL checked against GoogleAdsFieldService metadata without running it (pure).

`parse(query)` -> Query (or a syntax problem); `check(query, meta)` -> problems, where `meta` maps field and
resource names to their GoogleAdsField metadata (ads_mcp.schema.fieldmeta.FieldMeta). Rules:
- FROM names a RESOURCE;
- every SELECT field exists and is selectable; an attribute belongs to the FROM resource or one of its
  attribute_resources; a metric / segment is one the FROM resource lists;
- every WHERE field exists and is filterable (the same resource rule); enum literals are values of the field;
- every ORDER BY field exists and is sortable; LIMIT is a positive integer;
- selecting segments.date / week / month / quarter / year needs a finite segments.date filter in WHERE;
- a selected segment and a selected metric must list each other in selectable_with (when Google lists any).
"""

import re
from dataclasses import dataclass, field
from typing import Mapping

_FIELD = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b")
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_CLAUSES = re.compile(
    r"^\s*SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<from>[a-z_]+)"
    r"(?:\s+WHERE\s+(?P<where>.+?))?"
    r"(?:\s+ORDER\s+BY\s+(?P<order>.+?))?"
    r"(?:\s+LIMIT\s+(?P<limit>\S+))?"
    r"(?:\s+PARAMETERS\s+(?P<params>.+?))?\s*;?\s*$",
    re.I | re.S,
)
_ENUM_CONDITION = re.compile(
    r"(?P<field>[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\s*(?P<op>=|!=|NOT\s+IN|IN)\s*"
    r"(?P<value>\([^)]*\)|'[^']*'|\"[^\"]*\"|[A-Z_][A-Z0-9_]*)",
    re.I,
)
_DATE_SEGMENTS = (
    "segments.date",
    "segments.week",
    "segments.month",
    "segments.quarter",
    "segments.year",
)


@dataclass(frozen=True)
class Query:
    select: tuple[str, ...]
    resource: str
    where: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()
    limit: str | None = None
    enum_literals: tuple[tuple[str, tuple[str, ...]], ...] = ()
    where_text: str = ""


@dataclass(frozen=True)
class QueryProblem:
    clause: str  # SELECT, FROM, WHERE, ORDER BY, LIMIT, SYNTAX
    field: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {
            "clause": self.clause,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class FieldMeta:
    """GoogleAdsField, as GoogleAdsFieldService returns it (enum names as strings)."""

    name: str
    category: str  # RESOURCE, ATTRIBUTE, SEGMENT, METRIC
    data_type: str = ""
    enum_values: tuple[str, ...] = ()
    is_repeated: bool = False
    selectable: bool = False
    filterable: bool = False
    sortable: bool = False
    selectable_with: tuple[str, ...] = ()
    attribute_resources: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    segments: tuple[str, ...] = ()
    type_url: str = ""

    def compact(self) -> dict:
        """Small answer form: type, what it can do, enum values (no UNSPECIFIED/UNKNOWN), repeated."""
        can = [
            n
            for n, ok in (
                ("select", self.selectable),
                ("filter", self.filterable),
                ("sort", self.sortable),
            )
            if ok
        ]
        values = [
            v for v in self.enum_values if v not in ("UNSPECIFIED", "UNKNOWN")
        ]
        return {
            "type": self.data_type,
            "can": " ".join(can) or "none",
            **({"enum": values} if values else {}),
            **({"repeated": True} if self.is_repeated else {}),
        }


def _unquote_strings(text: str) -> str:
    return _STRING.sub("''", text)


def _fields_in(text: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for f in _FIELD.findall(_unquote_strings(text)):
        seen.setdefault(f, None)
    return tuple(seen)


def _literals(value: str) -> tuple[str, ...]:
    inner = value.strip()
    inner = inner[1:-1] if inner.startswith("(") else inner
    return tuple(
        v.strip().strip("'\"")
        for v in inner.split(",")
        if v.strip().strip("'\"")
    )


def parse(query: str) -> Query | QueryProblem:
    match = _CLAUSES.match(str(query or ""))
    if not match:
        return QueryProblem(
            "SYNTAX",
            "",
            "SYNTAX",
            "A query is SELECT <fields> FROM <resource> [WHERE ...] [ORDER BY ...] [LIMIT n].",
        )
    where = match.group("where") or ""
    return Query(
        select=tuple(
            s.strip() for s in match.group("select").split(",") if s.strip()
        ),
        resource=match.group("from").lower(),
        where=_fields_in(where),
        order_by=tuple(
            re.sub(r"\s+(ASC|DESC)\s*$", "", o.strip(), flags=re.I)
            for o in (match.group("order") or "").split(",")
            if o.strip()
        ),
        limit=match.group("limit"),
        enum_literals=tuple(
            (m.group("field"), _literals(m.group("value")))
            for m in _ENUM_CONDITION.finditer(where)
        ),
        where_text=where,
    )


def names_in(query: Query) -> tuple[str, ...]:
    """Every field and resource name the check needs metadata for."""
    seen: dict[str, None] = {query.resource: None}
    for f in query.select + query.where + query.order_by:
        seen.setdefault(f, None)
    return tuple(seen)


def _belongs(name: str, meta: FieldMeta, resource: FieldMeta) -> str | None:
    """None when `name` may be used with FROM `resource`, else why not."""
    if meta.category == "ATTRIBUTE":
        owner = name.split(".", 1)[0]
        ok = owner == resource.name or owner in resource.attribute_resources
        return (
            None
            if ok
            else (
                f"'{owner}' fields cannot be used FROM {resource.name}; its attribute resources: "
                + (", ".join(resource.attribute_resources) or "none")
            )
        )
    if meta.category == "METRIC":
        return (
            None
            if name in resource.metrics
            else f"{name} is not a metric of {resource.name}."
        )
    if meta.category == "SEGMENT":
        return (
            None
            if name in resource.segments
            else f"{name} is not a segment of {resource.name}."
        )
    return f"{name} is a resource, not a field: select its fields (e.g. {name}.id)."


def _field_problems(
    clause: str,
    names: tuple[str, ...],
    ability: str,
    meta: Mapping[str, FieldMeta],
    resource: FieldMeta,
) -> list[QueryProblem]:
    out = []
    for name in names:
        m = meta.get(name)
        if m is None:
            out.append(
                QueryProblem(
                    clause,
                    name,
                    "UNKNOWN_FIELD",
                    f"{name} does not exist (check schema_get_resource).",
                )
            )
            continue
        why = _belongs(name, m, resource)
        if why:
            out.append(QueryProblem(clause, name, "NOT_WITH_RESOURCE", why))
        elif not getattr(m, ability):
            out.append(
                QueryProblem(
                    clause,
                    name,
                    f"NOT_{ability.upper()}",
                    f"{name} is not {ability}.",
                )
            )
    return out


def check(query: Query, meta: Mapping[str, FieldMeta]) -> list[QueryProblem]:
    resource = meta.get(query.resource)
    if resource is None or resource.category != "RESOURCE":
        return [
            QueryProblem(
                "FROM",
                query.resource,
                "UNKNOWN_RESOURCE",
                f"{query.resource} is not a resource (schema_list_resources lists them).",
            )
        ]
    problems = (
        _field_problems("SELECT", query.select, "selectable", meta, resource)
        + _field_problems("WHERE", query.where, "filterable", meta, resource)
        + _field_problems(
            "ORDER BY", query.order_by, "sortable", meta, resource
        )
        + _enum_problems(query, meta)
        + _date_problems(query)
        + _compat_problems(query, meta)
    )
    if query.limit is not None and not (
        query.limit.isdigit() and int(query.limit) > 0
    ):
        problems.append(
            QueryProblem(
                "LIMIT", "", "BAD_LIMIT", "LIMIT takes a positive integer."
            )
        )
    return problems


def _enum_problems(
    query: Query, meta: Mapping[str, FieldMeta]
) -> list[QueryProblem]:
    out = []
    for name, values in query.enum_literals:
        m = meta.get(name)
        if not m or m.data_type != "ENUM" or not m.enum_values:
            continue
        bad = [v for v in values if v not in m.enum_values]
        if bad:
            out.append(
                QueryProblem(
                    "WHERE",
                    name,
                    "BAD_ENUM",
                    f"{', '.join(bad)} not a value of {name}: {', '.join(v for v in m.enum_values if v not in ('UNSPECIFIED', 'UNKNOWN'))}.",
                )
            )
    return out


def _date_problems(query: Query) -> list[QueryProblem]:
    dated = [f for f in query.select if f in _DATE_SEGMENTS]
    if dated and "segments.date" not in query.where:
        return [
            QueryProblem(
                "WHERE",
                dated[0],
                "DATE_FILTER_NEEDED",
                f"Selecting {dated[0]} needs a finite segments.date filter, e.g. WHERE segments.date DURING LAST_30_DAYS.",
            )
        ]
    return []


def _says_no(a: FieldMeta, b: FieldMeta, prefix: str) -> bool | None:
    """Whether a's selectable_with rules b out; None when a lists nothing of b's kind (no opinion)."""
    if not any(x.startswith(prefix) for x in a.selectable_with):
        return None
    return b.name not in a.selectable_with


def _compat_problems(
    query: Query, meta: Mapping[str, FieldMeta]
) -> list[QueryProblem]:
    picked = [meta[f] for f in query.select if f in meta]
    segments = [m for m in picked if m.category == "SEGMENT"]
    metrics = [m for m in picked if m.category == "METRIC"]

    def incompatible(s: FieldMeta, m: FieldMeta) -> bool:
        views = [
            v
            for v in (_says_no(s, m, "metrics."), _says_no(m, s, "segments."))
            if v is not None
        ]
        return bool(views) and all(views)

    return [
        QueryProblem(
            "SELECT",
            s.name,
            "INCOMPATIBLE",
            f"{s.name} cannot be selected with {m.name}.",
        )
        for s in segments
        for m in metrics
        if incompatible(s, m)
    ]
