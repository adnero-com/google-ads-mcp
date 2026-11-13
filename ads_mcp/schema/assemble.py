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

"""Served JSON Schemas, assembled from the checked-in fragments (pure).

One resource or one operation at a time, `$defs` pruned to what the root reaches. Three modes:
- read: every field, `readOnly` marked, `required` as the protos say;
- create: output-only fields dropped (and dropped from `required` and oneofs);
- update: as create, no `required` except the root's `resourceName`; `x-immutable` fields stay listed (the
  validator refuses them in an update).
Enums are inlined as their names (`{"type":"string","enum":[...],"x-enum":"CampaignStatus"}`); the values'
descriptions come from schema_get_enum. `x-oneof` groups become standard `oneOf` in the output.
`fields` narrows a schema to dotted camelCase paths ('appCampaignSetting', 'ad.appAd'): every other property
(except required ones) and every oneof branch not named is pruned, so the answer stays small.
"""

from typing import Any, Iterable, Literal, Mapping

Mode = Literal["read", "create", "update"]
OperationKind = Literal["create", "update", "remove"]
DIALECT = "https://json-schema.org/draft/2020-12/schema"
_PREFIX = "#/$defs/"


class SchemaPathError(ValueError):
    """A `fields` path names a property that does not exist (the message lists the ones that do)."""


def ref_name(schema: Mapping) -> str | None:
    ref = schema.get("$ref", "") if isinstance(schema, Mapping) else ""
    return ref[len(_PREFIX) :] if ref.startswith(_PREFIX) else None


def refs_in(value: Any) -> frozenset:
    """Every $defs name referenced anywhere inside value."""
    if isinstance(value, Mapping):
        own = frozenset({ref_name(value)} - {None})
        return own.union(*(refs_in(v) for v in value.values()))
    if isinstance(value, list):
        return frozenset().union(*(refs_in(v) for v in value))
    return frozenset()


def one_of(groups: Mapping[str, list]) -> dict:
    """x-oneof groups -> standard JSON Schema: at most one member of each group may be set.

    Each group is `oneOf: [{required:[a]}, ..., {not:{anyOf:[{required:[a]}, ...]}}]`: with zero or one member
    present exactly one branch holds; two members make two branches hold, which fails.
    """
    rules = [
        {
            "oneOf": [{"required": [m]} for m in members]
            + [{"not": {"anyOf": [{"required": [m]} for m in members]}}]
        }
        for members in groups.values()
    ]
    if not rules:
        return {}
    return rules[0] if len(rules) == 1 else {"allOf": rules}


def expand_oneofs(value: Any) -> Any:
    """Every x-oneof (kept internally while narrowing) -> standard oneOf, recursively."""
    if isinstance(value, Mapping):
        rest = {k: expand_oneofs(v) for k, v in value.items() if k != "x-oneof"}
        return {**rest, **one_of(value.get("x-oneof", {}))}
    if isinstance(value, list):
        return [expand_oneofs(v) for v in value]
    return value


def inline_enums(value: Any, enums: Mapping) -> Any:
    if isinstance(value, Mapping):
        name = ref_name(value)
        if name in enums:
            rest = {k: v for k, v in value.items() if k != "$ref"}
            return {
                "type": "string",
                "enum": enums[name]["enum"],
                "x-enum": name,
                **rest,
            }
        return {k: inline_enums(v, enums) for k, v in value.items()}
    if isinstance(value, list):
        return [inline_enums(v, enums) for v in value]
    return value


def strip_descriptions(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            k: strip_descriptions(v)
            for k, v in value.items()
            if k not in ("description", "x-enumDescriptions")
        }
    if isinstance(value, list):
        return [strip_descriptions(v) for v in value]
    return value


def shape(fragment: Mapping, mode: Mode, enums: Mapping) -> dict:
    """One message fragment in a mode: write modes drop output-only fields, update drops `required`."""
    props = {
        k: v
        for k, v in fragment.get("properties", {}).items()
        if mode == "read" or not v.get("readOnly")
    }
    required = [
        r
        for r in fragment.get("required", [])
        if r in props and mode != "update"
    ]
    groups = {
        g: [m for m in members if m in props]
        for g, members in fragment.get("x-oneof", {}).items()
    }
    base = {
        k: v
        for k, v in fragment.items()
        if k not in ("properties", "required", "x-oneof")
    }
    return {
        **base,
        "properties": inline_enums(props, enums),
        **({"required": required} if required else {}),
        **_groups(groups),
    }


def _groups(groups: Mapping[str, list]) -> dict:
    kept = {g: m for g, m in groups.items() if len(m) > 1}
    return {"x-oneof": kept} if kept else {}


def _split(paths: Iterable[str]) -> dict:
    """['ad.appAd', 'status', 'ad.finalUrls'] -> {'ad': {'appAd', 'finalUrls'}, 'status': {''}}."""
    out: dict[str, set] = {}
    for p in paths:
        head, _, rest = p.strip().strip(".").partition(".")
        if head:
            out.setdefault(head, set()).add(rest)
    return out


def narrow(
    obj: Mapping,
    paths: Iterable[str],
    pool: Mapping,
    enums: Mapping,
    mode: Mode,
    where: str = "",
) -> dict:
    """obj (a shaped object schema) keeping only the named properties (+ required ones), recursively inlined."""
    heads = _split(paths)
    props = obj.get("properties", {})
    unknown = sorted(set(heads) - set(props))
    if unknown:
        raise SchemaPathError(
            f"'{where + unknown[0]}' is not a field here; fields at '{where or '(root)'}': "
            + ", ".join(sorted(props))
        )
    keep = [k for k in props if k in heads or k in obj.get("required", [])]

    def one(key: str) -> Any:
        rests = heads.get(key)
        if (
            rests is None or "" in rests
        ):  # kept because required, or named whole
            return props[key]
        prop = props[key]
        holder = (
            prop.get("items", prop) if prop.get("type") == "array" else prop
        )
        name = ref_name(holder)
        if not name or name not in pool:
            raise SchemaPathError(
                f"'{where}{key}' has no sub-fields ({holder.get('type', 'value')})."
            )
        inner = narrow(
            shape(pool[name], mode, enums),
            rests,
            pool,
            enums,
            mode,
            f"{where}{key}.",
        )
        inner = {
            **inner,
            **(
                {"description": prop["description"]}
                if prop.get("description")
                else {}
            ),
        }
        return (
            {**prop, "items": inner}
            if prop.get("type") == "array"
            else {k: v for k, v in prop.items() if k != "$ref"} | inner
        )

    kept_props = {k: one(k) for k in keep}
    groups = {
        g: [m for m in members if m in kept_props]
        for g, members in obj.get("x-oneof", {}).items()
    }
    rest = {k: v for k, v in obj.items() if k not in ("properties", "x-oneof")}
    return {**rest, "properties": kept_props, **_groups(groups)}


def bundle(
    root: Mapping,
    pool: Mapping,
    enums: Mapping,
    mode: Mode,
    brief: bool = False,
) -> dict:
    """A standalone schema: root + the $defs it reaches (shaped for the mode), nothing else."""
    root = inline_enums(root, enums)
    defs: dict[str, dict] = {}
    todo = sorted(refs_in(root))
    while todo:
        name, *todo = todo
        if name in defs or name not in pool:
            continue
        defs[name] = shape(pool[name], mode, enums)
        todo += sorted(refs_in(defs[name]) - defs.keys())
    body = {
        **root,
        **({"$defs": dict(sorted(defs.items()))} if defs else {}),
    }
    if brief:
        body = {
            **strip_descriptions(body),
            **(
                {"description": root["description"]}
                if root.get("description")
                else {}
            ),
        }
    return {"$schema": DIALECT, **expand_oneofs(body)}


def resource_schema(
    data: Mapping,
    gaql: str,
    fields: Iterable[str] = (),
    brief: bool = False,
) -> dict:
    """The resource as read (what search returns), with its resource name pattern (`x-pattern`)."""
    entry = data["index"]["resources"][gaql]
    root = shape(data["pool"][entry["message"]], "read", data["enums"])
    fields = list(fields)
    if fields:
        root = narrow(root, fields, data["pool"], data["enums"], "read")
    return bundle(root, data["pool"], data["enums"], "read", brief)


def operation_schema(
    data: Mapping,
    gaql: str,
    kind: OperationKind,
    fields: Iterable[str] = (),
    brief: bool = False,
) -> dict:
    """One entry of `operations` (or of a googleAds:mutate `<x>Operation`) for one operation kind."""
    entry = data["index"]["resources"][gaql]
    op = data["pool"][entry["mutate"]["operation"]]
    pool, enums = data["pool"], data["enums"]
    mode: Mode = "update" if kind == "update" else "create"
    others = {
        k: v
        for k, v in op["properties"].items()
        if k not in ("create", "update", "remove", "updateMask")
    }
    if kind == "remove":
        main = {"remove": op["properties"]["remove"]}
        required = ["remove"]
    else:
        resource = shape(pool[entry["message"]], mode, enums)
        if kind == "update":
            resource = {**resource, "required": ["resourceName"]}
        fields = list(fields)
        if fields:
            resource = narrow(resource, fields, pool, enums, mode)
        main = {
            kind: {
                **resource,
                "description": op["properties"][kind].get("description", ""),
            },
            **others,
        }
        required = [kind]
        if kind == "update":
            main["updateMask"] = op["properties"].get(
                "updateMask", {"type": "string"}
            )
            required.append("updateMask")
    root = {
        "type": "object",
        "description": op.get("description", ""),
        "properties": main,
        "required": required,
        "additionalProperties": False,
    }
    return bundle(root, pool, enums, mode, brief)


def enum_schema(data: Mapping, name: str) -> dict:
    return {"$schema": DIALECT, **data["enums"][name]}


MAX_ENUM_SHOWN = 12
MAX_SUBFIELDS = 15


def describe(prop: Mapping, pool: Mapping, mode: Mode) -> str:
    """One field as a short line: its type (enum values, message sub-fields, list of...)."""
    if prop.get("x-enum"):
        values = prop.get("enum", [])
        shown = "|".join(values[:MAX_ENUM_SHOWN])
        more = f"|...({len(values)})" if len(values) > MAX_ENUM_SHOWN else ""
        return f"enum {prop['x-enum']}: {shown}{more}"
    if prop.get("type") == "array":
        return "list of " + describe(prop.get("items", {}), pool, mode)
    name = ref_name(prop)
    if name and name in pool:
        subs = [
            k
            for k, v in pool[name].get("properties", {}).items()
            if mode == "read" or not v.get("readOnly")
        ]
        more = ", ..." if len(subs) > MAX_SUBFIELDS else ""
        return f"object {name} {{{', '.join(subs[:MAX_SUBFIELDS])}{more}}}"
    if prop.get("type") == "string" and prop.get("pattern"):
        return "int64 (digit string)"
    if prop.get("contentEncoding") == "base64":
        return "bytes (base64)"
    if prop.get("type") == "object" and "additionalProperties" in prop:
        return "map"
    return str(prop.get("type", "any"))


def _flags(key: str, prop: Mapping, required: set, groups: Mapping) -> str:
    flags = [
        "required" if key in required else "",
        "immutable" if prop.get("x-immutable") else "",
        f"oneof {groups[key]}" if key in groups else "",
        f"ref {prop['x-resource']}" if prop.get("x-resource") else "",
    ]
    return "".join(f"; {f}" for f in flags if f)


def operation_summary(data: Mapping, gaql: str, kind: OperationKind) -> dict:
    """The operation as one line per field: `{"<kind>.name": "string; required", ...}` (the small default)."""
    entry = data["index"]["resources"][gaql]
    pool, enums = data["pool"], data["enums"]
    op = pool[entry["mutate"]["operation"]]
    if kind == "remove":
        remove = op["properties"]["remove"]
        return {
            "remove": "resource name" + _flags("remove", remove, {"remove"}, {})
        }
    mode: Mode = "update" if kind == "update" else "create"
    resource = shape(pool[entry["message"]], mode, enums)
    required = (
        {"resourceName"}
        if kind == "update"
        else set(resource.get("required", []))
    )
    groups = {m: g for g, ms in resource.get("x-oneof", {}).items() for m in ms}
    lines = {
        f"{kind}.{k}": describe(v, pool, mode) + _flags(k, v, required, groups)
        for k, v in resource["properties"].items()
    }
    others = {
        k: describe(inline_enums(v, enums), pool, mode)
        for k, v in op["properties"].items()
        if k not in ("create", "update", "remove", "updateMask")
    }
    mask = (
        {"updateMask": "comma-separated field paths; required"}
        if kind == "update"
        else {}
    )
    return {**lines, **mask, **others}
