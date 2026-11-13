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

"""An update's updateMask checked against the schema (pure).

Google changes exactly the fields the mask names, so the mask decides: every path must exist in the resource
(snake_case or camelCase segments), and a path that names an immutable field (or one inside an immutable field,
or a message whose body here sets an immutable field) is refused, whether the body carries a value or not. A
field in the body that the mask does not name is ignored by Google, and so here.
"""

from typing import Any, Mapping

from ads_mcp.schema.problems import Problem, camel, pointer, resolve


def paths(mask: Any) -> list[str]:
    return [p.strip() for p in str(mask or "").split(",") if p.strip()]


def _immutables_in(
    value: Any, schema: Mapping, defs: Mapping, at: str
) -> list[str]:
    """Dotted paths of immutable fields that `value` (a message under the mask) sets."""
    s = resolve(schema, defs)
    props = s.get("properties", {})
    if not isinstance(value, dict) or not isinstance(props, Mapping):
        return []
    found = []
    for k, v in value.items():
        field = props.get(k, {})
        here = f"{at}.{k}"
        if field.get("x-immutable"):
            found.append(here)
        elif field.get("type") != "array":
            found += _immutables_in(v, field, defs, here)
    return found


def check(operation: Mapping, schema: Mapping, prefix: tuple) -> list[Problem]:
    """Problems of one update operation's mask (`schema` is the served update-operation schema)."""
    defs = schema.get("$defs", {})
    root = schema["properties"]["update"]
    body = (
        operation.get("update")
        if isinstance(operation.get("update"), dict)
        else {}
    )
    where = pointer(prefix + ("updateMask",))
    out = []
    for path in paths(operation.get("updateMask")):
        node, value, why = root, body, None
        segments = [camel(s) for s in path.split(".")]
        for i, seg in enumerate(segments):
            props = resolve(node, defs).get("properties", {})
            if seg not in props:
                why = Problem(
                    where,
                    "BAD_MASK",
                    f"'{path}': '{seg}' is not a field of {'.'.join(segments[:i]) or 'the resource'}.",
                )
                break
            node = props[seg]
            if node.get("x-immutable") and seg != "resourceName":
                why = Problem(
                    where,
                    "IMMUTABLE",
                    f"'{path}' names an immutable field: it is set at creation and cannot change; drop it from the mask.",
                )
                break
            value = value.get(seg) if isinstance(value, dict) else None
            if node.get("type") == "array" and i < len(segments) - 1:
                why = Problem(
                    where,
                    "BAD_MASK",
                    f"'{path}': a mask stops at the repeated field '{seg}'.",
                )
                break
        if why is None and isinstance(value, dict):
            inner = _immutables_in(value, node, defs, ".".join(segments))
            if inner:
                why = Problem(
                    where,
                    "IMMUTABLE",
                    f"'{path}' would change immutable {', '.join(inner)}; leave them out of the body or the mask.",
                )
        if why:
            out.append(why)
    return out
