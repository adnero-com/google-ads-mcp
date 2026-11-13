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

"""GoogleAdsFieldService metadata (the edge), kept per process: it only changes with the API version.

Every field keeps data_type, enum_values, is_repeated, selectable/filterable/sortable, selectable_with,
attribute_resources, metrics and segments (upstream's get_resource_metadata drops most of them).
"""

import re
import threading
from typing import Any, Callable, Iterable

import ads_mcp.utils as utils
from ads_mcp.schema.gaql import FieldMeta

_SELECT = (
    "SELECT name, category, data_type, enum_values, is_repeated, selectable, filterable, sortable, "
    "selectable_with, attribute_resources, metrics, segments, type_url"
)
_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_lock = threading.Lock()
_fields: dict[str, FieldMeta] = {}
_absent: set[str] = set()
_resources_done: set[str] = set()


def _enum_name(value: Any) -> str:
    return getattr(value, "name", None) or str(value)


def to_meta(row: Any) -> FieldMeta:
    """A GoogleAdsField row (proto-plus) -> FieldMeta (pure)."""
    return FieldMeta(
        name=row.name,
        category=_enum_name(row.category),
        data_type=_enum_name(row.data_type),
        enum_values=tuple(row.enum_values),
        is_repeated=bool(row.is_repeated),
        selectable=bool(row.selectable),
        filterable=bool(row.filterable),
        sortable=bool(row.sortable),
        selectable_with=tuple(row.selectable_with),
        attribute_resources=tuple(row.attribute_resources),
        metrics=tuple(row.metrics),
        segments=tuple(row.segments),
        type_url=row.type_url,
    )


def google_search(query: str, login_customer_id: str | int | None) -> list:
    """One SearchGoogleAdsFields call (all pages)."""
    service = utils.get_googleads_service(
        "GoogleAdsFieldService", login_customer_id=login_customer_id
    )
    request = utils.get_googleads_type("SearchGoogleAdsFieldsRequest")
    request.query = query
    return list(service.search_google_ads_fields(request=request))


Search = Callable[[str, "str | int | None"], list]


def _store(rows: Iterable) -> None:
    with _lock:
        for row in rows:
            meta = to_meta(row)
            _fields[meta.name] = meta


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def fields(
    names: Iterable[str],
    login_customer_id: str | int | None = None,
    search: Search | None = None,
) -> tuple[dict[str, FieldMeta], int]:
    """(metadata of the names Google knows, Google requests made: 0 when all were cached)."""
    search = search or google_search
    wanted = [n for n in dict.fromkeys(names) if valid_name(n)]
    missing = [n for n in wanted if n not in _fields and n not in _absent]
    calls = 0
    if missing:
        quoted = ", ".join(f"'{n}'" for n in missing)
        _store(search(f"{_SELECT} WHERE name IN ({quoted})", login_customer_id))
        calls = 1
        with _lock:
            _absent.update(n for n in missing if n not in _fields)
    return {n: _fields[n] for n in wanted if n in _fields}, calls


def resource_fields(
    resource: str,
    login_customer_id: str | int | None = None,
    search: Search | None = None,
) -> tuple[FieldMeta | None, dict[str, FieldMeta], int]:
    """(the RESOURCE entry, its own attributes 'resource.*', Google requests made)."""
    search = search or google_search
    if not valid_name(resource) or "." in resource:
        return None, {}, 0
    head, calls = fields([resource], login_customer_id, search)
    if resource in head and resource not in _resources_done:
        _store(
            search(
                f"{_SELECT} WHERE name LIKE '{resource}.%'", login_customer_id
            )
        )
        calls += 1
        with _lock:
            _resources_done.add(resource)
    prefix = resource + "."
    own = {n: m for n, m in sorted(_fields.items()) if n.startswith(prefix)}
    return head.get(resource), own, calls


def clear() -> None:
    """Tests only."""
    with _lock:
        _fields.clear()
        _absent.clear()
        _resources_done.clear()
