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

"""Every resource name in a mutate body must belong to the call's account (pure).

Checked: each `resourceName`, each `remove`, each field the schema marks `x-resource` whose resource lives under a
customer (pattern `customers/{customer_id}/...`; geo and language constants do not), and any other string that
looks like `customers/<id>/...`. They must start with `customers/{customer_id}/` (temporary ids included).
"""

import re
from typing import Any, Mapping

from ads_mcp.schema.problems import Problem, pointer, resolve

_CUSTOMER_NAME = re.compile(r"^customers/\d+(/|$)")


def customer_types(data: Mapping) -> frozenset:
    """Resource types ('Campaign', 'ConversionAction'...) whose names start with customers/{customer_id}/."""
    pool = data["pool"]
    return frozenset(
        r["type"]
        for r in data["index"]["resources"].values()
        if pool.get(r["message"], {})
        .get("x-pattern", "")
        .startswith("customers/{customer_id}")
    )


def _check(
    text: str, key: str, schema: Mapping, prefix: str, types: frozenset
) -> str | None:
    """Why `text` is not a name in this account, or None."""
    if text.startswith(prefix):
        return None
    if _CUSTOMER_NAME.match(text):
        return f"'{text}' belongs to another account; this call may only reference {prefix}..."
    named = (
        key in ("resourceName", "remove") or schema.get("x-resource") in types
    )
    if named:
        return f"'{text}' is not a resource name of this account: expected {prefix}..."
    return None


def foreign(
    value: Any,
    schema: Mapping,
    defs: Mapping,
    customer_id: str,
    types: frozenset,
    path: tuple = (),
    key: str = "",
) -> list[Problem]:
    """Problems for every resource name in `value` outside customers/{customer_id}/."""
    prefix = f"customers/{customer_id}/"
    s = resolve(schema, defs) if isinstance(schema, Mapping) else {}
    marks = schema if isinstance(schema, Mapping) else {}
    if isinstance(value, str):
        why = _check(value, key, {**s, **marks}, prefix, types)
        return [Problem(pointer(path), "FOREIGN_CUSTOMER", why)] if why else []
    if isinstance(value, list):
        items = (
            s.get("items", {}) if isinstance(s.get("items"), Mapping) else {}
        )
        return [
            p
            for i, v in enumerate(value)
            for p in foreign(
                v, items, defs, customer_id, types, path + (i,), key
            )
        ]
    if isinstance(value, dict):
        props = (
            s.get("properties", {})
            if isinstance(s.get("properties"), Mapping)
            else {}
        )
        extra = s.get("additionalProperties")
        return [
            p
            for k, v in value.items()
            for p in foreign(
                v,
                props.get(k, extra if isinstance(extra, Mapping) else {}),
                defs,
                customer_id,
                types,
                path + (k,),
                k,
            )
        ]
    return []
