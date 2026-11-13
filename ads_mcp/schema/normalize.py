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

"""Mutate bodies put in the canonical REST form before they are checked and sent (pure).

Google's JSON parser accepts proto field names (snake_case) and int64 as JSON numbers; the schemas use the
canonical form (lowerCamelCase, int64 as digit strings). So, guided by the operation's schema, a snake_case key
whose camelCase name is a field there is renamed, and an int64 field given as an integer becomes its digit string.
Nothing else changes: whatever Google would refuse is still refused by the validator.
"""

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ads_mcp.schema.validate import KINDS, operation_schema, pointer

MAX_EXAMPLES = 3


@dataclass(frozen=True)
class Note:
    kind: str  # "key" | "int64"
    path: str  # JSON pointer of the value, after renaming
    detail: str  # "campaign_budget→campaignBudget" | "5000000"


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _resolve(schema: Mapping, defs: Mapping) -> Mapping:
    ref = schema.get("$ref", "")
    return (
        defs.get(ref.rsplit("/", 1)[-1], {})
        if ref.startswith("#/$defs/")
        else schema
    )


def _is_int64(schema: Mapping) -> bool:
    return schema.get("type") == "string" and schema.get("pattern") in (
        "^-?[0-9]+$",
        "^[0-9]+$",
    )


def walk(
    value: Any, schema: Mapping, defs: Mapping, path: tuple
) -> tuple[Any, tuple]:
    """(value in canonical form, notes) for one value against its schema."""
    s = _resolve(schema, defs)
    if isinstance(value, bool):
        return value, ()
    if isinstance(value, int) and _is_int64(s):
        return str(value), (Note("int64", pointer(path), str(value)),)
    if isinstance(value, list) and isinstance(s.get("items"), Mapping):
        parts = [
            walk(v, s["items"], defs, path + (i,)) for i, v in enumerate(value)
        ]
        return [v for v, _ in parts], tuple(n for _, ns in parts for n in ns)
    if not isinstance(value, dict):
        return value, ()
    props = s.get("properties")
    if isinstance(props, Mapping):
        return _object(value, props, defs, path)
    extra = s.get("additionalProperties")
    if isinstance(extra, Mapping):  # a map: keys are data, never renamed
        parts = {k: walk(v, extra, defs, path + (k,)) for k, v in value.items()}
        return {k: v for k, (v, _) in parts.items()}, tuple(
            n for _, ns in parts.values() for n in ns
        )
    return value, ()


def _object(
    value: dict, props: Mapping, defs: Mapping, path: tuple
) -> tuple[dict, tuple]:
    def target(key: str) -> str:
        camel = _camel(key)
        renamable = key not in props and camel in props and camel not in value
        return camel if renamable else key

    out: dict = {}
    notes: list = []
    for key, v in value.items():
        name = target(key)
        if name != key:
            notes.append(Note("key", pointer(path + (name,)), f"{key}→{name}"))
        if name in props:
            v, inner = walk(v, props[name], defs, path + (name,))
            notes += inner
        out[name] = v
    return out, tuple(notes)


Schemas = Callable[[str, str], Mapping]


def normalize_body(
    data: Mapping,
    collection: str,
    body: Mapping,
    schema_of: Schemas | None = None,
) -> tuple[dict, tuple]:
    """(the body in canonical form, notes). Unknown resources and malformed operations pass through untouched:
    the validator reports them."""
    schema_of = schema_of or (
        lambda gaql, kind: operation_schema(data, gaql, kind)
    )
    resources = data["index"]["resources"]

    def op(gaql: str, operation: Any, prefix: tuple) -> tuple[Any, tuple]:
        kinds = [
            k for k in KINDS if isinstance(operation, dict) and k in operation
        ]
        if len(kinds) != 1 or not gaql:
            return operation, ()
        schema = schema_of(gaql, kinds[0])
        return walk(operation, schema, schema.get("$defs", {}), prefix)

    if collection == "googleAds":
        wrappers = data["index"]["googleAdsMutate"]
        by_op = {
            r["mutate"]["operation"]: g
            for g, r in resources.items()
            if "mutate" in r
        }
        notes: list = []
        key = "mutateOperations"
        if key not in body and "mutate_operations" in body:
            notes.append(
                Note(
                    "key",
                    "/mutateOperations",
                    "mutate_operations→mutateOperations",
                )
            )
        entries = body.get(key, body.get("mutate_operations")) or []
        fixed = []
        for i, entry in enumerate(entries):
            if not (isinstance(entry, dict) and len(entry) == 1):
                fixed.append(entry)
                continue
            ((wrapper, inner),) = entry.items()
            name = wrapper if wrapper in wrappers else _camel(wrapper)
            if name != wrapper and name in wrappers:
                notes.append(
                    Note("key", pointer((key, i, name)), f"{wrapper}→{name}")
                )
            else:
                name = wrapper
            new, inner_notes = op(
                by_op.get(wrappers.get(name, ""), ""), inner, (key, i, name)
            )
            notes += inner_notes
            fixed.append({name: new})
        rest = {
            k: v for k, v in body.items() if k not in (key, "mutate_operations")
        }
        return {**rest, key: fixed}, tuple(notes)
    gaql = next(
        (
            g
            for g, r in resources.items()
            if r.get("mutate", {}).get("collection") == collection
        ),
        "",
    )
    if not gaql:
        return dict(body), ()
    key = resources[gaql]["mutate"]["operations_key"]
    if key == "operation":
        new, notes = op(gaql, body.get(key), (key,))
        return {**body, key: new}, notes
    parts = [op(gaql, o, (key, i)) for i, o in enumerate(body.get(key) or [])]
    return {**body, key: [v for v, _ in parts]}, tuple(
        n for _, ns in parts for n in ns
    )


def summary(notes: tuple) -> str | None:
    """One short line: what was normalized, with a few examples."""
    keys = [n for n in notes if n.kind == "key"]
    numbers = [n for n in notes if n.kind == "int64"]
    parts = []
    if keys:
        shown = ", ".join(n.detail for n in keys[:MAX_EXAMPLES])
        parts.append(
            f"{len(keys)} snake_case key(s) → camelCase ({shown}{', ...' if len(keys) > MAX_EXAMPLES else ''})"
        )
    if numbers:
        shown = ", ".join(n.path for n in numbers[:MAX_EXAMPLES])
        parts.append(
            f"{len(numbers)} int64 number(s) → digit strings ({shown}{', ...' if len(numbers) > MAX_EXAMPLES else ''})"
        )
    return f"Normalized: {'; '.join(parts)}." if parts else None
