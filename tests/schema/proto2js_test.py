"""proto -> JSON Schema converter (pure), on a small hand-built descriptor set."""

import unittest

from google.api import annotations_pb2, field_behavior_pb2, resource_pb2
from google.protobuf import descriptor_pb2

from ads_mcp.schema import proto2js

D = descriptor_pb2.FieldDescriptorProto
ROOT = "google.ads.googleads.v99"


def field(
    name,
    number,
    ftype,
    type_name="",
    repeated=False,
    behaviors=(),
    ref="",
    oneof=None,
    optional=False,
):
    f = D(
        name=name,
        number=number,
        type=ftype,
        type_name=type_name,
        json_name=proto2js.camel(name),
        label=D.LABEL_REPEATED if repeated else D.LABEL_OPTIONAL,
    )
    f.options.Extensions[field_behavior_pb2.field_behavior].extend(behaviors)
    if ref:
        f.options.Extensions[resource_pb2.resource_reference].type = ref
    if oneof is not None:
        f.oneof_index = oneof
    if optional:
        f.proto3_optional = True
    return f


def comment(fd, path, text):
    fd.source_code_info.location.add(path=list(path), leading_comments=text)


def descriptor_set():
    enums = descriptor_pb2.FileDescriptorProto(
        name="e.proto", package=f"{ROOT}.enums"
    )
    wrapper = enums.message_type.add(name="ThingStatusEnum")
    status = wrapper.enum_type.add(name="ThingStatus")
    for i, n in enumerate(("UNSPECIFIED", "UNKNOWN", "ENABLED", "PAUSED")):
        status.value.add(name=n, number=i)
    comment(enums, (4, 0, 4, 0), " Status of a thing.\n")
    comment(enums, (4, 0, 4, 0, 2, 2), " It runs.\n")

    res = descriptor_pb2.FileDescriptorProto(
        name="r.proto", package=f"{ROOT}.resources"
    )
    thing = res.message_type.add(name="Thing")
    thing.options.Extensions[resource_pb2.resource].type = (
        "googleads.googleapis.com/Thing"
    )
    thing.options.Extensions[resource_pb2.resource].pattern.append(
        "customers/{customer_id}/things/{thing_id}"
    )
    thing.field.extend(
        [
            field(
                "resource_name",
                1,
                D.TYPE_STRING,
                behaviors=[field_behavior_pb2.IMMUTABLE],
                ref="googleads.googleapis.com/Thing",
            ),
            field(
                "id",
                2,
                D.TYPE_INT64,
                behaviors=[field_behavior_pb2.OUTPUT_ONLY],
                oneof=0,
                optional=True,
            ),
            field(
                "name",
                3,
                D.TYPE_STRING,
                behaviors=[field_behavior_pb2.REQUIRED],
            ),
            field(
                "status",
                4,
                D.TYPE_ENUM,
                type_name=f".{ROOT}.enums.ThingStatusEnum.ThingStatus",
            ),
            field(
                "manual",
                5,
                D.TYPE_MESSAGE,
                type_name=f".{ROOT}.resources.Thing.Manual",
                oneof=1,
            ),
            field(
                "auto",
                6,
                D.TYPE_MESSAGE,
                type_name=f".{ROOT}.resources.Thing.Manual",
                oneof=1,
            ),
            field("blob", 7, D.TYPE_BYTES),
            field("labels", 8, D.TYPE_STRING, repeated=True),
            field(
                "tags",
                9,
                D.TYPE_MESSAGE,
                type_name=f".{ROOT}.resources.Thing.TagsEntry",
                repeated=True,
            ),
            field("count", 10, D.TYPE_UINT64),
        ]
    )
    thing.oneof_decl.add(name="_id")
    thing.oneof_decl.add(name="bidding_scheme")
    manual = thing.nested_type.add(name="Manual")
    manual.field.append(field("cpc_micros", 1, D.TYPE_INT64))
    entry = thing.nested_type.add(name="TagsEntry")
    entry.options.map_entry = True
    entry.field.extend(
        [field("key", 1, D.TYPE_STRING), field("value", 2, D.TYPE_INT32)]
    )
    comment(res, (4, 0), " A thing.  Second sentence.\n")
    comment(res, (4, 0, 2, 1), " Output only. The ID.\n")
    comment(res, (4, 0, 2, 2), " Required. The   name\n of it.\n")

    svc = descriptor_pb2.FileDescriptorProto(
        name="s.proto", package=f"{ROOT}.services"
    )
    op = svc.message_type.add(name="ThingOperation")
    op.field.extend(
        [
            field(
                "update_mask",
                4,
                D.TYPE_MESSAGE,
                type_name=".google.protobuf.FieldMask",
            ),
            field(
                "create",
                1,
                D.TYPE_MESSAGE,
                type_name=f".{ROOT}.resources.Thing",
                oneof=0,
            ),
            field(
                "update",
                2,
                D.TYPE_MESSAGE,
                type_name=f".{ROOT}.resources.Thing",
                oneof=0,
            ),
            field(
                "remove",
                3,
                D.TYPE_STRING,
                oneof=0,
                ref="googleads.googleapis.com/Thing",
            ),
        ]
    )
    op.oneof_decl.add(name="operation")
    req = svc.message_type.add(name="MutateThingsRequest")
    req.field.extend(
        [
            field(
                "customer_id",
                1,
                D.TYPE_STRING,
                behaviors=[field_behavior_pb2.REQUIRED],
            ),
            field(
                "operations",
                2,
                D.TYPE_MESSAGE,
                type_name=f".{ROOT}.services.ThingOperation",
                repeated=True,
                behaviors=[field_behavior_pb2.REQUIRED],
            ),
        ]
    )
    svc.message_type.add(name="MutateThingsResponse")
    mo = svc.message_type.add(name="MutateOperation")
    mo.field.append(
        field(
            "thing_operation",
            1,
            D.TYPE_MESSAGE,
            type_name=f".{ROOT}.services.ThingOperation",
            oneof=0,
        )
    )
    mo.oneof_decl.add(name="operation")
    service = svc.service.add(name="ThingService")
    method = service.method.add(
        name="MutateThings",
        input_type=f".{ROOT}.services.MutateThingsRequest",
        output_type=f".{ROOT}.services.MutateThingsResponse",
    )
    method.options.Extensions[annotations_pb2.http].post = (
        "/v99/customers/{customer_id=*}/things:mutate"
    )
    comment(svc, (6, 0), " Manages things. More.\n")
    return descriptor_pb2.FileDescriptorSet(file=[enums, res, svc])


class HelpersTest(unittest.TestCase):
    def test_table(self):
        cases = [
            (proto2js.snake, "AdGroupAdAssetView", "ad_group_ad_asset_view"),
            (proto2js.snake, "Customer", "customer"),
            (
                proto2js.camel,
                "campaign_bidding_strategy",
                "campaignBiddingStrategy",
            ),
            (proto2js.camel, "id", "id"),
            (
                proto2js.clean_comment,
                " Output only. Immutable. The\n  id.",
                "The id.",
            ),
            (proto2js.clean_comment, "Required. Name.", "Name."),
            (
                proto2js.clean_comment,
                "Immutability is fine.",
                "Immutability is fine.",
            ),
            (proto2js.first_sentence, "A campaign. More text.", "A campaign."),
            (proto2js.first_sentence, "No period", "No period"),
            (
                proto2js.first_sentence,
                "Version 1.2 is here. Next.",
                "Version 1.2 is here.",
            ),
        ]
        for fn, arg, want in cases:
            with self.subTest(fn=fn.__name__, arg=arg):
                self.assertEqual(fn(arg), want)


class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = proto2js.convert(descriptor_set(), "v99")
        cls.thing = cls.out["resources"]["Thing"]
        cls.props = cls.thing["properties"]

    def test_fields(self):
        cases = [
            (
                "resourceName",
                {"type": "string", "x-immutable": True, "x-resource": "Thing"},
            ),
            (
                "id",
                {
                    "type": "string",
                    "pattern": "^-?[0-9]+$",
                    "readOnly": True,
                    "description": "The ID.",
                },
            ),
            ("name", {"type": "string", "description": "The name of it."}),
            ("status", {"$ref": "#/$defs/ThingStatus"}),
            ("manual", {"$ref": "#/$defs/Thing.Manual"}),
            ("blob", {"type": "string", "contentEncoding": "base64"}),
            ("labels", {"type": "array", "items": {"type": "string"}}),
            (
                "tags",
                {"type": "object", "additionalProperties": {"type": "integer"}},
            ),
            ("count", {"type": "string", "pattern": "^[0-9]+$"}),
        ]
        for name, want in cases:
            with self.subTest(field=name):
                self.assertEqual(self.props[name], want)

    def test_message(self):
        self.assertEqual(self.thing["required"], ["name"])
        self.assertEqual(
            self.thing["x-oneof"], {"biddingScheme": ["manual", "auto"]}
        )  # never the synthetic _id
        self.assertEqual(
            self.thing["x-pattern"], "customers/{customer_id}/things/{thing_id}"
        )
        self.assertEqual(self.thing["description"], "A thing. Second sentence.")
        self.assertIs(self.thing["additionalProperties"], False)
        self.assertNotIn(
            "Thing.TagsEntry", self.out["resources"]
        )  # map entries fold into objects

    def test_enum(self):
        self.assertEqual(
            self.out["enums"]["ThingStatus"],
            {
                "type": "string",
                "description": "Status of a thing.",
                "enum": ["ENABLED", "PAUSED"],
                "x-enumDescriptions": {"ENABLED": "It runs."},
            },
        )

    def test_operation_and_index(self):
        op = self.out["services"]["ThingOperation"]
        self.assertEqual(op["properties"]["updateMask"]["type"], "string")
        self.assertEqual(
            op["x-oneof"], {"operation": ["create", "update", "remove"]}
        )
        index = self.out["index"]
        self.assertEqual(
            index["resources"]["thing"],
            {
                "message": "Thing",
                "type": "Thing",
                "description": "A thing.",
                "mutate": {
                    "path": "customers/{customer_id}/things:mutate",
                    "collection": "things",
                    "operations_key": "operations",
                    "service": "ThingService",
                    "method": "MutateThings",
                    "request": "MutateThingsRequest",
                    "operation": "ThingOperation",
                    "operations": ["create", "update", "remove"],
                },
            },
        )
        self.assertEqual(
            index["services"]["ThingService"]["description"], "Manages things."
        )
        self.assertEqual(
            index["googleAdsMutate"], {"thingOperation": "ThingOperation"}
        )

    def test_name_collisions_are_qualified(self):
        fds = descriptor_set()
        dup = fds.file[2].message_type.add(
            name="Thing"
        )  # services.Thing next to resources.Thing
        dup.field.append(field("x", 1, D.TYPE_STRING))
        out = proto2js.convert(fds, "v99")
        self.assertIn("resources.Thing", out["resources"])
        self.assertIn("services.Thing", out["services"])
        self.assertEqual(
            out["index"]["resources"]["thing"]["message"], "resources.Thing"
        )


if __name__ == "__main__":
    unittest.main()
