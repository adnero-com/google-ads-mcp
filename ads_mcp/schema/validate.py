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

"""Mutate bodies checked against the generated schemas before Google sees them (pure).

`check_body(data, collection, body)` -> problems, each with a JSON pointer into the body (RFC 6901), a code and
a message that says what to fix. Codes: REQUIRED, UNKNOWN_FIELD, ONE_OF, IMMUTABLE, BAD_ENUM, BAD_TYPE,
BAD_OPERATION, UNKNOWN_RESOURCE. An update may not carry an IMMUTABLE field (other than its resourceName).
"""

import functools
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError, validators

from ads_mcp.schema import assemble

MAX_PROBLEMS = 20
KINDS = ("create", "update", "remove")


@dataclass(frozen=True)
class Problem:
    path: str  # JSON pointer into the body, e.g. /operations/0/create/appCampaignSetting/appStore
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"path": self.path, "code": self.code, "message": self.message}


def pointer(parts) -> str:
    return "".join(
        "/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts
    )


def _immutable(validator, value, instance, schema):
    if value:
        yield ValidationError(
            "is immutable: it is set at creation and cannot change in an update"
        )


# An update's validator: also refuses fields marked x-immutable.
UpdateValidator = validators.extend(
    Draft202012Validator, {"x-immutable": _immutable}
)


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _problem(error: ValidationError, prefix: tuple) -> Problem:
    path = prefix + tuple(error.absolute_path)
    kind = error.validator
    if kind == "required":
        missing = (
            str(error.message).split("'")[1] if "'" in error.message else ""
        )
        return Problem(
            pointer(path + (missing,)),
            "REQUIRED",
            f"'{missing}' is required here.",
        )
    if kind == "additionalProperties":
        return _unknown(error, path)[0]
    if kind == "oneOf":
        members = [
            b["required"][0] for b in error.validator_value if "required" in b
        ]
        present = [
            m
            for m in members
            if isinstance(error.instance, dict) and m in error.instance
        ]
        return Problem(
            pointer(path),
            "ONE_OF",
            f"Set only one of {', '.join(members)}; got {', '.join(present)}.",
        )
    if kind == "x-immutable":
        return Problem(
            pointer(path),
            "IMMUTABLE",
            f"'{path[-1]}' {error.message}; drop it from the update.",
        )
    if kind == "enum":
        allowed = list(error.validator_value)
        shown = ", ".join(allowed[:25]) + (" ..." if len(allowed) > 25 else "")
        return Problem(
            pointer(path),
            "BAD_ENUM",
            f"{error.instance!r} is not a value of {error.schema.get('x-enum', 'this enum')}: {shown}.",
        )
    if kind in ("type", "pattern"):
        want = error.schema.get("type")
        hint = (
            ' (int64: a string of digits, e.g. "5000000")'
            if error.schema.get("pattern")
            else ""
        )
        return Problem(
            pointer(path),
            "BAD_TYPE",
            f"{error.instance!r} is not a valid {want}{hint}.",
        )
    return Problem(pointer(path), "INVALID", error.message)


def _unknown(error: ValidationError, path: tuple) -> list[Problem]:
    """One problem per unknown key, with the camelCase name when that is the fix."""
    allowed = set(error.schema.get("properties", {}))
    extra = sorted(set(error.instance) - allowed) or [""]

    def hint(key: str) -> str:
        if _camel(key) in allowed and _camel(key) != key:
            return f" Use the camelCase name '{_camel(key)}'."
        return " It is misspelled, output-only (Google sets it), or not part of this message."

    return [
        Problem(
            pointer(path + (key,)),
            "UNKNOWN_FIELD",
            f"'{key}' is not a writable field here.{hint(key)}",
        )
        for key in extra
    ]


def _problems(error: ValidationError, prefix: tuple) -> list[Problem]:
    if error.validator == "additionalProperties":
        return _unknown(error, prefix + tuple(error.absolute_path))
    return [_problem(error, prefix)]


def _resources_by(data: Mapping, key: str) -> dict:
    return {
        r["mutate"][key]: gaql
        for gaql, r in data["index"]["resources"].items()
        if "mutate" in r
    }


@functools.lru_cache(maxsize=256)
def _validator(gaql: str, kind: str, data_ref: "_Ref") -> Any:
    """One compiled validator per (resource, kind, loaded data): compiling is the slow part."""
    schema = assemble.operation_schema(data_ref.data, gaql, kind)
    if kind == "update":
        props = schema["properties"]["update"]["properties"]
        if (
            "resourceName" in props
        ):  # names the target: never refused as immutable
            props["resourceName"] = {
                k: v
                for k, v in props["resourceName"].items()
                if k != "x-immutable"
            }
        return UpdateValidator(schema)
    return Draft202012Validator(schema)


class _Ref:
    """Hashable holder so the validator cache keys on the loaded data's identity."""

    def __init__(self, data):
        self.data = data

    def __hash__(self):
        return id(self.data)

    def __eq__(self, other):
        return isinstance(other, _Ref) and other.data is self.data


def operation_schema(data: Mapping, gaql: str, kind: str) -> Mapping:
    """The served operation schema (the compiled validator's, so it is built once per process)."""
    return _validator(gaql, kind, _Ref(data)).schema


def check_operation(
    data: Mapping, gaql: str, operation: Any, prefix: tuple
) -> list[Problem]:
    entry = data["index"]["resources"].get(gaql, {}).get("mutate")
    if not isinstance(operation, dict):
        return [
            Problem(
                pointer(prefix),
                "BAD_OPERATION",
                "An operation is a JSON object such as {'create': {...}}.",
            )
        ]
    kinds = [k for k in KINDS if k in operation]
    allowed = entry["operations"] if entry else []
    if len(kinds) != 1 or kinds[0] not in allowed:
        return [
            Problem(
                pointer(prefix),
                "BAD_OPERATION",
                f"Each operation has exactly one of {', '.join(allowed)} for {gaql}; got {', '.join(kinds) or 'none'}.",
            )
        ]
    validator = _validator(gaql, kinds[0], _Ref(data))
    errors = sorted(
        validator.iter_errors(operation),
        key=lambda e: list(map(str, e.absolute_path)),
    )
    return [p for e in errors for p in _problems(e, prefix)]


def check_body(data: Mapping, collection: str, body: Mapping) -> list[Problem]:
    """Problems in a `<collection>:mutate` body ('campaigns', 'adGroupAds' ... or 'googleAds'), at most 20."""
    if collection == "googleAds":
        wrappers = data["index"]["googleAdsMutate"]
        by_op = _resources_by(data, "operation")
        found: list[Problem] = []
        for i, op in enumerate(body.get("mutateOperations") or []):
            keys = list(op) if isinstance(op, dict) else []
            if len(keys) != 1 or keys[0] not in wrappers:
                found.append(
                    Problem(
                        pointer(("mutateOperations", i)),
                        "BAD_OPERATION",
                        "Each mutateOperations entry has one key such as campaignOperation, adGroupOperation, "
                        "campaignBudgetOperation (see schema_list_resources).",
                    )
                )
                continue
            gaql = by_op.get(wrappers[keys[0]])
            found += check_operation(
                data, gaql or "", op[keys[0]], ("mutateOperations", i, keys[0])
            )
        return found[:MAX_PROBLEMS]
    gaql = _resources_by(data, "collection").get(collection)
    if not gaql:
        return [
            Problem(
                "",
                "UNKNOWN_RESOURCE",
                f"No mutable resource is served at '{collection}:mutate'.",
            )
        ]
    key = data["index"]["resources"][gaql]["mutate"]["operations_key"]
    ops = body.get(key) if key == "operations" else [body.get(key)]
    found = [
        p
        for i, op in enumerate(ops or [])
        for p in check_operation(
            data, gaql, op, (key, i) if key == "operations" else (key,)
        )
    ]
    return found[:MAX_PROBLEMS]
