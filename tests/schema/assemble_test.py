"""Served schemas: modes, narrowing, pruning, oneOf, on a small pool and on the real v26 data."""

import json
import unittest

from jsonschema import Draft202012Validator

from ads_mcp.schema import assemble as a
from ads_mcp.schema import store

ENUMS = {
    "Status": {
        "type": "string",
        "enum": ["ON", "OFF"],
        "x-enumDescriptions": {"ON": "on"},
    }
}
POOL = {
    "Thing": {
        "type": "object",
        "x-pattern": "customers/{customer_id}/things/{thing_id}",
        "properties": {
            "resourceName": {"type": "string", "x-immutable": True},
            "id": {"type": "string", "readOnly": True},
            "name": {"type": "string", "description": "Name."},
            "status": {"$ref": "#/$defs/Status"},
            "manual": {"$ref": "#/$defs/Bid"},
            "auto": {"$ref": "#/$defs/Bid"},
            "computed": {"$ref": "#/$defs/Bid", "readOnly": True},
            "parts": {"type": "array", "items": {"$ref": "#/$defs/Part"}},
        },
        "required": ["name", "id"],
        "x-oneof": {"scheme": ["manual", "auto", "computed"]},
        "additionalProperties": False,
    },
    "Bid": {
        "type": "object",
        "properties": {"micros": {"type": "string"}},
        "additionalProperties": False,
    },
    "Part": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "bid": {"$ref": "#/$defs/Bid"},
        },
        "additionalProperties": False,
    },
    "Unused": {"type": "object"},
    "ThingOperation": {
        "type": "object",
        "description": "An op.",
        "properties": {
            "updateMask": {"type": "string"},
            "create": {"$ref": "#/$defs/Thing", "description": "Create."},
            "update": {"$ref": "#/$defs/Thing", "description": "Update."},
            "remove": {"type": "string", "x-resource": "Thing"},
            "extra": {"type": "boolean"},
        },
        "x-oneof": {"operation": ["create", "update", "remove"]},
    },
}
DATA = {
    "pool": POOL,
    "enums": ENUMS,
    "index": {
        "resources": {
            "thing": {
                "message": "Thing",
                "mutate": {
                    "operation": "ThingOperation",
                    "operations": ["create", "update", "remove"],
                },
            }
        }
    },
}


class ShapeTest(unittest.TestCase):
    def test_modes(self):
        cases = [
            # mode, has id, required, oneof members
            ("read", True, ["name", "id"], ["manual", "auto", "computed"]),
            ("create", False, ["name"], ["manual", "auto"]),
            ("update", False, None, ["manual", "auto"]),
        ]
        for mode, has_id, required, members in cases:
            with self.subTest(mode=mode):
                out = a.shape(POOL["Thing"], mode, ENUMS)
                self.assertEqual("id" in out["properties"], has_id)
                self.assertEqual(out.get("required"), required)
                self.assertEqual(out["x-oneof"], {"scheme": members})
                self.assertEqual(
                    out["properties"]["status"],
                    {
                        "type": "string",
                        "enum": ["ON", "OFF"],
                        "x-enum": "Status",
                    },
                )

    def test_one_of_semantics(self):
        schema = {"type": "object", **a.one_of({"g": ["a", "b"]})}
        cases = [
            ({}, True),
            ({"a": 1}, True),
            ({"b": 1}, True),
            ({"a": 1, "b": 1}, False),
        ]
        for instance, ok in cases:
            with self.subTest(instance=instance):
                self.assertEqual(
                    Draft202012Validator(schema).is_valid(instance), ok
                )
        self.assertEqual(a.one_of({}), {})
        self.assertIn("allOf", a.one_of({"g": ["a", "b"], "h": ["c", "d"]}))


class BundleTest(unittest.TestCase):
    def test_resource_prunes_defs(self):
        out = a.resource_schema(DATA, "thing")
        self.assertEqual(
            sorted(out["$defs"]), ["Bid", "Part"]
        )  # never Unused, enums inlined
        self.assertEqual(out["$schema"], a.DIALECT)
        self.assertNotIn("x-oneof", json.dumps(out))
        self.assertEqual(len(out["oneOf"]), 4)

    def test_narrow(self):
        cases = [
            (["status"], {"name", "id", "status"}),  # required ones always stay
            (["parts.text"], {"name", "id", "parts"}),
            (["manual"], {"name", "id", "manual"}),
        ]
        for fields, keys in cases:
            with self.subTest(fields=fields):
                out = a.resource_schema(DATA, "thing", fields)
                self.assertEqual(set(out["properties"]), keys)
        part = a.resource_schema(DATA, "thing", ["parts.text"])["properties"][
            "parts"
        ]["items"]
        self.assertEqual(set(part["properties"]), {"text"})
        self.assertNotIn(
            "$defs", a.resource_schema(DATA, "thing", ["parts.text"])
        )
        only_manual = a.resource_schema(DATA, "thing", ["manual"])
        self.assertNotIn("oneOf", only_manual)  # a group of one is no group

    def test_narrow_errors(self):
        for fields in (["nope"], ["parts.nope"], ["name.sub"]):
            with (
                self.subTest(fields=fields),
                self.assertRaises(a.SchemaPathError),
            ):
                a.resource_schema(DATA, "thing", fields)

    def test_operations(self):
        create = a.operation_schema(DATA, "thing", "create")
        self.assertEqual(create["required"], ["create"])
        self.assertEqual(set(create["properties"]), {"create", "extra"})
        self.assertEqual(create["properties"]["create"]["required"], ["name"])
        update = a.operation_schema(DATA, "thing", "update")
        self.assertEqual(update["required"], ["update", "updateMask"])
        self.assertEqual(
            update["properties"]["update"]["required"], ["resourceName"]
        )
        remove = a.operation_schema(DATA, "thing", "remove")
        self.assertEqual(set(remove["properties"]), {"remove"})
        brief = a.operation_schema(DATA, "thing", "create", brief=True)
        self.assertEqual(brief["description"], "An op.")
        self.assertNotIn("Name.", json.dumps(brief))


class RealDataTest(unittest.TestCase):
    def test_sizes_and_validity(self):
        data = store.load()
        cases = [
            ("campaign", "create", [], 90_000),
            (
                "ad_group_ad",
                "create",
                ["ad.appAd", "status", "adGroup"],
                25_000,
            ),
            ("campaign_budget", "create", [], 12_000),
            ("campaign", "update", ["appCampaignSetting", "targetCpa"], 12_000),
        ]
        for gaql, kind, fields, cap in cases:
            with self.subTest(gaql=gaql, kind=kind, fields=fields):
                out = a.operation_schema(data, gaql, kind, fields)
                Draft202012Validator.check_schema(out)
                self.assertLess(
                    len(json.dumps(out, separators=(",", ":"))), cap
                )

    def test_app_campaign_create_body_is_valid(self):
        schema = a.operation_schema(store.load(), "campaign", "create")
        body = {
            "create": {
                "name": "App install",
                "status": "PAUSED",
                "campaignBudget": "customers/1/campaignBudgets/-1",
                "advertisingChannelType": "MULTI_CHANNEL",
                "advertisingChannelSubType": "APP_CAMPAIGN",
                "appCampaignSetting": {
                    "appId": "com.example",
                    "appStore": "GOOGLE_APP_STORE",
                    "biddingStrategyGoalType": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
                },
                "targetCpa": {"targetCpaMicros": "2000000"},
                "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            }
        }
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(body)), []
        )


if __name__ == "__main__":
    unittest.main()
