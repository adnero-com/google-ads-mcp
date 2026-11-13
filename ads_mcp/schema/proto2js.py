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

"""googleapis .proto descriptors -> JSON Schema fragments (pure; build time only).

Input: a FileDescriptorSet compiled with source info (comments) from the googleapis Google Ads protos. Output:
plain dicts, one JSON Schema fragment per message and per enum, plus an index of resources and services.

Mapping (draft 2020-12, the proto3 JSON names, i.e. lowerCamelCase, as the REST API takes them):
- REQUIRED -> the parent's `required`; OUTPUT_ONLY -> `readOnly: true`; IMMUTABLE -> `x-immutable: true`.
- A oneof (never proto3's synthetic `optional` ones) -> `x-oneof: {group: [members]}` on the message; the
  assembler (ads_mcp.schema.assemble) turns it into standard `oneOf` for the schemas we serve.
- int64 family -> string of digits; bytes -> base64 string; enums -> `$ref` to the enum (names, never numbers).
- `google.api.resource_reference` -> `x-resource` (the resource type it points to); a resource message gets
  `x-pattern` (its resource name pattern). Comments -> `description` (behaviour prefixes dropped: the flags say it).
"""

import re
from typing import Any, Iterable, Mapping

from google.api import annotations_pb2, field_behavior_pb2, resource_pb2
from google.protobuf import descriptor_pb2

D = descriptor_pb2.FieldDescriptorProto

KINDS = ("resources", "common", "services", "enums")
REQUIRED, OUTPUT_ONLY, IMMUTABLE = (
    field_behavior_pb2.REQUIRED,
    field_behavior_pb2.OUTPUT_ONLY,
    field_behavior_pb2.IMMUTABLE,
)
_INT64 = (D.TYPE_INT64, D.TYPE_SINT64, D.TYPE_SFIXED64)
_UINT64 = (D.TYPE_UINT64, D.TYPE_FIXED64)
_INT32 = (
    D.TYPE_INT32,
    D.TYPE_SINT32,
    D.TYPE_SFIXED32,
    D.TYPE_UINT32,
    D.TYPE_FIXED32,
)
_SCALARS: Mapping[int, Mapping[str, Any]] = {
    **{t: {"type": "string", "pattern": "^-?[0-9]+$"} for t in _INT64},
    **{t: {"type": "string", "pattern": "^[0-9]+$"} for t in _UINT64},
    **{t: {"type": "integer"} for t in _INT32},
    D.TYPE_DOUBLE: {"type": "number"},
    D.TYPE_FLOAT: {"type": "number"},
    D.TYPE_BOOL: {"type": "boolean"},
    D.TYPE_STRING: {"type": "string"},
    D.TYPE_BYTES: {"type": "string", "contentEncoding": "base64"},
}
_WELL_KNOWN: Mapping[str, Mapping[str, Any]] = {
    ".google.protobuf.FieldMask": {
        "type": "string",
        "description": "Comma-separated lowerCamelCase field paths, e.g. 'name,status,campaignBudget'.",
    },
    ".google.protobuf.Timestamp": {"type": "string", "format": "date-time"},
    ".google.protobuf.Duration": {"type": "string"},
}
_PREFIX = re.compile(
    r"^(?:(?:Output only|Immutable|Required|Optional)\.\s*)+", re.I
)
_RESOURCE_TYPE = "googleads.googleapis.com/"
# Source-info path tags (descriptor.proto field numbers).
_FILE_MESSAGE, _FILE_ENUM, _FILE_SERVICE = 4, 5, 6
_MSG_FIELD, _MSG_NESTED, _MSG_ENUM = 2, 3, 4
_ENUM_VALUE, _SERVICE_METHOD = 2, 2


def clean_comment(text: str) -> str:
    """One-line description: whitespace collapsed, the behaviour prefixes the flags already carry dropped."""
    return _PREFIX.sub("", " ".join(str(text or "").split()))


def first_sentence(text: str) -> str:
    match = re.match(r"(.+?\.)(\s|$)", text)
    return match.group(1) if match else text


def snake(name: str) -> str:
    """AdGroupAdAssetView -> ad_group_ad_asset_view (the GAQL resource name)."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def camel(name: str) -> str:
    """campaign_bidding_strategy -> campaignBiddingStrategy (proto3 JSON naming)."""
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _comments(fd: descriptor_pb2.FileDescriptorProto) -> dict:
    return {
        tuple(loc.path): clean_comment(loc.leading_comments)
        for loc in fd.source_code_info.location
        if loc.leading_comments.strip()
    }


def _walk(
    fd: descriptor_pb2.FileDescriptorProto,
) -> Iterable[tuple[str, str, Any, tuple]]:
    """(kind 'message'|'enum', full name with leading dot, descriptor, source path) for every type in the file."""

    def messages(prefix: str, items, path: tuple):
        for i, msg in enumerate(items):
            full, here = f"{prefix}.{msg.name}", path + (i,)
            yield "message", full, msg, here
            yield from messages(full, msg.nested_type, here + (_MSG_NESTED,))
            for j, enum in enumerate(msg.enum_type):
                yield "enum", f"{full}.{enum.name}", enum, here + (
                    _MSG_ENUM,
                    j,
                )

    package = "." + fd.package
    yield from messages(package, fd.message_type, (_FILE_MESSAGE,))
    for i, enum in enumerate(fd.enum_type):
        yield "enum", f"{package}.{enum.name}", enum, (_FILE_ENUM, i)


def _kind_of(full: str, root: str) -> str | None:
    rest = full[len(root) + 1 :] if full.startswith(root + ".") else ""
    kind = rest.split(".", 1)[0]
    return kind if kind in KINDS else None


def def_names(fds: descriptor_pb2.FileDescriptorSet, root: str) -> dict:
    """Full proto name -> $defs name: the short name when unique in the set, else kind-qualified.

    Messages: name inside their kind ('Campaign.AppCampaignSetting', 'AppAdInfo'). Enums: the enum's own name
    ('CampaignStatus', not 'CampaignStatusEnum.CampaignStatus').
    """
    entries = [
        (full, kind, _kind_of(full, root))
        for fd in fds.file
        for kind, full, _, _ in _walk(fd)
        if _kind_of(full, root)
    ]

    def short(full: str, kind: str, k: str) -> str:
        inside = full[len(root) + len(k) + 2 :]
        return inside.rsplit(".", 1)[-1] if kind == "enum" else inside

    counts: dict[str, int] = {}
    for full, kind, k in entries:
        counts[short(full, kind, k)] = counts.get(short(full, kind, k), 0) + 1
    return {
        full: (
            short(full, kind, k)
            if counts[short(full, kind, k)] == 1
            else f"{k}.{short(full, kind, k)}"
        )
        for full, kind, k in entries
    }


def _behaviors(field: descriptor_pb2.FieldDescriptorProto) -> frozenset:
    return frozenset(
        field.options.Extensions[field_behavior_pb2.field_behavior]
    )


def _type_schema(
    field: descriptor_pb2.FieldDescriptorProto, names: Mapping, maps: Mapping
) -> dict:
    if field.type in (D.TYPE_MESSAGE, D.TYPE_GROUP, D.TYPE_ENUM):
        if field.type_name in maps:
            return {
                "type": "object",
                "additionalProperties": maps[field.type_name],
            }
        if field.type_name in _WELL_KNOWN:
            return dict(_WELL_KNOWN[field.type_name])
        if field.type_name in names:
            return {"$ref": f"#/$defs/{names[field.type_name]}"}
        return {
            "type": "object"
        }  # outside the four kinds (errors, rpc.Status...): opaque
    return dict(_SCALARS[field.type])


def field_schema(
    field: descriptor_pb2.FieldDescriptorProto,
    names: Mapping,
    maps: Mapping,
    comment: str,
) -> dict:
    base = _type_schema(field, names, maps)
    repeated = field.label == D.LABEL_REPEATED and not (field.type_name in maps)
    out = {"type": "array", "items": base} if repeated else base
    behaviors = _behaviors(field)
    ref = field.options.Extensions[resource_pb2.resource_reference]
    target = ref.type or ref.child_type
    extra = {
        **({"description": comment} if comment else {}),
        **({"readOnly": True} if OUTPUT_ONLY in behaviors else {}),
        **({"x-immutable": True} if IMMUTABLE in behaviors else {}),
        **(
            {"x-resource": target.removeprefix(_RESOURCE_TYPE)}
            if target and target != "*"
            else {}
        ),
    }
    return {**out, **extra}


def message_schema(
    msg: descriptor_pb2.DescriptorProto,
    path: tuple,
    comments: Mapping,
    names: Mapping,
    maps: Mapping,
) -> dict:
    fields = list(msg.field)
    props = {
        f.json_name: field_schema(
            f, names, maps, comments.get(path + (_MSG_FIELD, i), "")
        )
        for i, f in enumerate(fields)
    }
    required = [f.json_name for f in fields if REQUIRED in _behaviors(f)]
    groups = {
        camel(o.name): [
            f.json_name
            for f in fields
            if f.HasField("oneof_index")
            and f.oneof_index == i
            and not f.proto3_optional
        ]
        for i, o in enumerate(msg.oneof_decl)
    }
    groups = {k: v for k, v in groups.items() if len(v) > 1}
    resource = msg.options.Extensions[resource_pb2.resource]
    return {
        "type": "object",
        **({"description": comments[path]} if comments.get(path) else {}),
        **({"x-pattern": resource.pattern[0]} if resource.pattern else {}),
        "properties": props,
        **({"required": required} if required else {}),
        **({"x-oneof": groups} if groups else {}),
        "additionalProperties": False,
    }


def enum_schema(
    enum: descriptor_pb2.EnumDescriptorProto, path: tuple, comments: Mapping
) -> dict:
    values = [
        (v.name, comments.get(path + (_ENUM_VALUE, i), ""))
        for i, v in enumerate(enum.value)
        if v.name not in ("UNSPECIFIED", "UNKNOWN")
    ]
    return {
        "type": "string",
        **({"description": comments[path]} if comments.get(path) else {}),
        "enum": [n for n, _ in values],
        "x-enumDescriptions": {n: d for n, d in values if d},
    }


def _map_entries(fds: descriptor_pb2.FileDescriptorSet, names: Mapping) -> dict:
    """Map-entry message full name -> the schema of its values (maps become objects)."""
    out = {}
    for fd in fds.file:
        for kind, full, desc, _ in _walk(fd):
            if kind == "message" and desc.options.map_entry:
                value = next(f for f in desc.field if f.name == "value")
                out[full] = _type_schema(value, names, {})
    return out


def _http(method: descriptor_pb2.MethodDescriptorProto) -> str:
    rule = method.options.Extensions[annotations_pb2.http]
    verb = rule.WhichOneof("pattern")
    return f"{verb.upper()} {getattr(rule, verb)}" if verb else ""


_MUTATE_HTTP = re.compile(
    r"^POST /v\d+/customers/\{customer_id=\*\}(?:/(\w+))?:(mutate\w*)$"
)


def convert(fds: descriptor_pb2.FileDescriptorSet, version: str) -> dict:
    """{'resources': {...}, 'common': {...}, 'services': {...}, 'enums': {...}, 'index': {...}}."""
    root = f".google.ads.googleads.{version}"
    names = def_names(fds, root)
    maps = _map_entries(fds, names)
    out: dict[str, dict] = {k: {} for k in KINDS}
    resources: dict[str, dict] = {}
    services: dict[str, dict] = {}
    for fd in fds.file:
        comments = _comments(fd)
        for kind, full, desc, path in _walk(fd):
            k = _kind_of(full, root)
            if not k or (kind == "message" and desc.options.map_entry):
                continue
            if kind == "enum":
                out["enums"][names[full]] = enum_schema(desc, path, comments)
                continue
            out[k][names[full]] = message_schema(
                desc, path, comments, names, maps
            )
            resource = desc.options.Extensions[resource_pb2.resource]
            if (
                k == "resources"
                and resource.type
                and full.count(".") == root.count(".") + 2
            ):
                resources[snake(desc.name)] = {
                    "message": names[full],
                    "type": resource.type.removeprefix(_RESOURCE_TYPE),
                    "description": first_sentence(comments.get(path, "")),
                }
        if fd.package != f"google.ads.googleads.{version}.services":
            continue
        for i, svc in enumerate(fd.service):
            services[svc.name] = {
                "description": first_sentence(
                    comments.get((_FILE_SERVICE, i), "")
                ),
                "methods": {
                    m.name: {
                        "http": _http(m),
                        "request": names.get(m.input_type, m.input_type),
                        "response": names.get(m.output_type, m.output_type),
                    }
                    for m in svc.method
                },
            }
    mutates = _mutates(services, out["services"], resources)
    for gaql, m in mutates.items():
        resources[gaql]["mutate"] = m
    return {
        **out,
        "index": {
            "version": version,
            "resources": dict(sorted(resources.items())),
            "services": dict(sorted(services.items())),
            "googleAdsMutate": _google_ads_mutate(out["services"]),
        },
    }


def _mutates(services: Mapping, messages: Mapping, resources: Mapping) -> dict:
    """gaql resource -> how to mutate it: REST collection, service, method, request and operation defs."""
    by_message = {r["message"]: gaql for gaql, r in resources.items()}
    found = {}
    for svc_name, svc in services.items():
        for method_name, method in svc["methods"].items():
            match = _MUTATE_HTTP.match(method["http"])
            props = messages.get(method["request"], {}).get("properties", {})
            key = next(
                (k for k in ("operations", "operation") if k in props), ""
            )
            ref = (
                props.get(key, {})
                .get("items", props.get(key, {}))
                .get("$ref", "")
            )
            if not match or not ref:
                continue
            op_name = ref.rsplit("/", 1)[-1]
            op = messages.get(op_name, {})
            kinds = [
                m
                for m in ("create", "update", "remove")
                if m in op.get("properties", {})
            ]
            target = next(
                (
                    op["properties"][m].get("$ref", "").rsplit("/", 1)[-1]
                    for m in ("create", "update")
                    if m in op.get("properties", {})
                ),
                "",
            )
            gaql = by_message.get(target) or _by_remove(op, resources)
            if gaql:
                collection, verb = match.group(1) or "", match.group(2)
                found[gaql] = {
                    "path": "customers/{customer_id}"
                    + (f"/{collection}" if collection else "")
                    + f":{verb}",
                    "collection": collection,
                    "operations_key": key,
                    "service": svc_name,
                    "method": method_name,
                    "request": method["request"],
                    "operation": op_name,
                    "operations": kinds,
                }
    return found


def _by_remove(op: Mapping, resources: Mapping) -> str | None:
    target = op.get("properties", {}).get("remove", {}).get("x-resource")
    return next((g for g, r in resources.items() if r["type"] == target), None)


def _google_ads_mutate(messages: Mapping) -> dict:
    """googleAds:mutate wrapper key ('campaignOperation') -> operation def ('CampaignOperation')."""
    props = messages.get("MutateOperation", {}).get("properties", {})
    return {
        key: value["$ref"].rsplit("/", 1)[-1]
        for key, value in props.items()
        if "$ref" in value
    }
