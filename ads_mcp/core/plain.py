"""Turns Google Ads protobuf values into plain JSON-ready data (pure).

Rules, everywhere and at every depth:
- enums are returned as their NAME ('LIMITED'), never their number;
- keys are the API's field names: proto-plus escapes Python keywords with a trailing
  underscore ('type_'), which is removed ('type');
- int64 values are JSON numbers at every depth (ids, micros, counts), like the top-level fields of a row:
  Google Ads ids and micros stay below 2**53, so no client loses precision;
- only fields that are set appear: no made-up defaults (an unset enum is absent, not "UNSPECIFIED").
"""

from collections.abc import Mapping
from enum import Enum
from typing import Any

from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as PbMessage
import proto


def api_key(key: str) -> str:
    """'type_' -> 'type'. proto-plus adds one trailing '_' to fields named like Python keywords."""
    return key[:-1] if key.endswith("_") and not key.endswith("__") else key


def rename_keys(value: Any) -> Any:
    """Recursively renames every mapping key with api_key; lists and scalars pass through."""
    if isinstance(value, Mapping):
        return {api_key(str(k)): rename_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_keys(v) for v in value]
    return value


_INT64 = frozenset(
    {
        FieldDescriptor.TYPE_INT64,
        FieldDescriptor.TYPE_UINT64,
        FieldDescriptor.TYPE_SINT64,
        FieldDescriptor.TYPE_FIXED64,
        FieldDescriptor.TYPE_SFIXED64,
    }
)


def _unquote(value: Any, field: FieldDescriptor) -> Any:
    if isinstance(value, list):
        return [_unquote(v, field) for v in value]
    if field.type in _INT64 and isinstance(value, str):
        return int(value)
    if field.type == FieldDescriptor.TYPE_MESSAGE and isinstance(value, dict):
        return _unquote_message(value, field.message_type)
    return value


def _unquote_message(data: dict, descriptor: Any) -> dict:
    """The JSON mapping quotes int64; turn those back into numbers, walking the descriptor."""
    fields = descriptor.fields_by_name
    return {
        k: (_unquote(v, fields[k]) if k in fields else v)
        for k, v in data.items()
    }


def _message_dict(pb: PbMessage) -> Any:
    data = MessageToDict(
        pb,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
    return rename_keys(
        _unquote_message(data, pb.DESCRIPTOR)
        if isinstance(data, dict)
        else data
    )


def to_plain(value: Any) -> Any:
    """Any value a GoogleAdsRow / response can hold, as plain data with enum names."""
    if isinstance(value, proto.Enum):
        return value.name
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, proto.Message):
        return _message_dict(type(value).pb(value))
    if isinstance(value, PbMessage):
        # Well-known wrappers (FieldMask...) serialize to scalars; keep the JSON mapping.
        return rename_keys(
            MessageToDict(
                value,
                preserving_proto_field_name=True,
                use_integers_for_enums=False,
            )
        )
    if isinstance(value, Mapping):
        return {api_key(str(k)): to_plain(v) for k, v in value.items()}
    if isinstance(value, (str, bytes, bytearray)):
        return value
    if hasattr(value, "__iter__"):
        return [to_plain(v) for v in value]
    return value
