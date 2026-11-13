"""schema_* tools and api_mutate's schema check, with Google faked."""

import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from ads_mcp.schema import fieldmeta
from ads_mcp.tools import adnero_api as api
from ads_mcp.tools import adnero_schema as tools


def row(name, category, data_type="", **kw):
    base = dict(
        enum_values=[],
        is_repeated=False,
        selectable=False,
        filterable=False,
        sortable=False,
        selectable_with=[],
        attribute_resources=[],
        metrics=[],
        segments=[],
        type_url="",
    )
    return NS(
        name=name,
        category=NS(name=category),
        data_type=NS(name=data_type),
        **{**base, **kw},
    )


ROWS = [
    row(
        "campaign",
        "RESOURCE",
        "MESSAGE",
        attribute_resources=["customer"],
        metrics=["metrics.clicks"],
        segments=["segments.date"],
    ),
    row(
        "campaign.status",
        "ATTRIBUTE",
        "ENUM",
        enum_values=["UNSPECIFIED", "ENABLED", "PAUSED"],
        selectable=True,
        filterable=True,
        sortable=True,
    ),
    row(
        "campaign.app_campaign_setting.app_id",
        "ATTRIBUTE",
        "STRING",
        selectable=True,
        filterable=True,
    ),
    row(
        "metrics.clicks",
        "METRIC",
        "INT64",
        selectable=True,
        filterable=True,
        sortable=True,
    ),
]


class FakeFieldService:
    def __init__(self, fail=False):
        self.queries, self.fail = [], fail

    def __call__(self, query, login_customer_id=None):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("no Google token")
        if "LIKE 'campaign.%'" in query:
            return [r for r in ROWS if r.name.startswith("campaign.")]
        names = (
            query.split("IN (", 1)[1].rstrip(")").replace("'", "").split(", ")
        )
        return [r for r in ROWS if r.name in names]


class SchemaToolsTest(unittest.TestCase):
    def setUp(self):
        fieldmeta.clear()
        self.fake = FakeFieldService()
        p = patch("ads_mcp.schema.fieldmeta.google_search", self.fake)
        p.start()
        self.addCleanup(p.stop)

    def test_list_resources(self):
        out = tools.list_resources(filter="app_top_combination")
        self.assertEqual(
            [r["name"] for r in out.resources], ["app_top_combination_view"]
        )
        mutable = tools.list_resources(mutable_only=True, page_size=200)
        self.assertTrue(all("mutate" in r for r in mutable.resources))
        first = tools.list_resources(page_size=50)
        self.assertEqual(first.next_page_token, "50")
        self.assertIsNone(first.services)
        geo = tools.list_resources(
            filter="SuggestGeoTargetConstants", include_services=True
        )
        self.assertEqual(
            [s["name"] for s in geo.services], ["GeoTargetConstantService"]
        )
        self.assertEqual(self.fake.queries, [])  # free

    def test_get_resource_with_cached_query_metadata(self):
        out = tools.get_resource(
            "Campaign", fields=["status", "appCampaignSetting"]
        )
        self.assertEqual(out.resource, "campaign")
        self.assertEqual(
            out.mutate["path"], "customers/{customer_id}/campaigns:mutate"
        )
        self.assertIn("appCampaignSetting", out.json_schema["properties"])
        self.assertEqual(
            out.query["fields"],
            {
                "campaign.app_campaign_setting.app_id": {
                    "type": "STRING",
                    "can": "select filter",
                },
                "campaign.status": {
                    "type": "ENUM",
                    "can": "select filter sort",
                    "enum": ["ENABLED", "PAUSED"],
                },
            },
        )
        self.assertEqual(out.query["metrics"], 1)
        self.assertEqual(len(self.fake.queries), 2)
        again = tools.get_resource("campaigns", query="all")
        self.assertEqual(again.query["metrics"], ["metrics.clicks"])
        self.assertEqual(len(self.fake.queries), 2)  # cached per process

    def test_get_resource_without_google(self):
        fieldmeta.clear()
        with patch(
            "ads_mcp.schema.fieldmeta.google_search",
            FakeFieldService(fail=True),
        ):
            out = tools.get_resource("campaign_budget")
        self.assertIsNone(out.query)
        self.assertIn("no Google token", out.query_error)
        self.assertIsNone(
            tools.get_resource("campaign_budget", query="none").query_error
        )

    def test_get_mutate(self):
        out = tools.get_mutate(
            "ad_group_ad", "create", fields=["ad.appAd", "status"]
        )
        self.assertEqual(out.path, "customers/{customer_id}/adGroupAds:mutate")
        self.assertEqual(out.google_ads_key, "adGroupAdOperation")
        # The App rules layer (rules/__init__.rules_for) fills x-rules, and `rules` in the summary.
        ids = {r["id"] for r in out.json_schema["x-rules"]}
        self.assertIn("app_ad.headlines", ids)
        self.assertEqual(out.json_schema["x-rules"], out.rules)
        with patch("ads_mcp.rules.rules_for", return_value=[{"rule": "x"}]):
            self.assertEqual(
                tools.get_mutate(
                    "campaign", "create", fields=["name"]
                ).json_schema["x-rules"],
                [{"rule": "x"}],
            )
        summary = tools.get_mutate("campaign", "create")
        self.assertIsNone(summary.json_schema)
        lines = summary.fields_summary
        self.assertEqual(
            lines["create.appCampaignSetting"],
            "object Campaign.AppCampaignSetting {appId, appStore, biddingStrategyGoalType}",
        )
        self.assertIn("immutable", lines["create.advertisingChannelType"])
        self.assertIn(
            "oneof campaignBiddingStrategy", lines["create.targetCpa"]
        )
        self.assertNotIn("create.id", lines)  # output only
        update = tools.get_mutate("campaign", "update").fields_summary
        self.assertIn("required", update["update.resourceName"])
        self.assertIn("updateMask", update)
        self.assertIsNotNone(
            tools.get_mutate("campaign_budget", "create", full=True).json_schema
        )
        errors = [
            ("app_top_combination_view", "create", "read-only"),
            ("campaign_label", "update", "supports create, remove"),
            ("campagin", "create", "Close: campaign"),
            ("campaign", "create", "not a field"),
        ]
        for resource, op, text in errors:
            with (
                self.subTest(resource=resource),
                self.assertRaisesRegex(ToolError, text),
            ):
                tools.get_mutate(
                    resource,
                    op,
                    fields=["nope"] if text == "not a field" else None,
                )

    def test_get_enum(self):
        for name in (
            "CampaignStatus",
            "CampaignStatusEnum.CampaignStatus",
            "campaign_status",
        ):
            with self.subTest(name=name):
                out = tools.get_enum(name)
                self.assertEqual(out.enum, "CampaignStatus")
                self.assertIn("PAUSED", out.json_schema["x-enumDescriptions"])
        with self.assertRaisesRegex(ToolError, "Close"):
            tools.get_enum("CampaignStatuss")

    def test_check_query(self):
        ok = tools.check_query(
            "SELECT campaign.status, metrics.clicks FROM campaign WHERE campaign.status = 'PAUSED'"
        )
        self.assertTrue(ok.valid, ok.problems)
        self.assertEqual(set(ok.fields), {"campaign.status", "metrics.clicks"})
        bad = tools.check_query("SELECT campaign.nope FROM campaign")
        self.assertFalse(bad.valid)
        self.assertEqual(bad.problems[0]["code"], "UNKNOWN_FIELD")
        self.assertEqual(
            tools.check_query("nonsense").problems[0]["code"], "SYNTAX"
        )


class ApiMutateSchemaTest(unittest.TestCase):
    def test_invalid_body_never_reaches_google(self):
        body = {
            "operations": [
                {
                    "create": {
                        "name": "x",
                        "campaign_budgett": "customers/1/campaignBudgets/2",
                    }
                }
            ]
        }
        with patch.object(api, "_call") as call:
            with self.assertRaises(ToolError) as caught:
                api.mutate(
                    customer_id="1234567890",
                    path="customers/1234567890/campaigns:mutate",
                    body=body,
                )
        call.assert_not_called()
        answer = json.loads(str(caught.exception))
        self.assertEqual(answer["error"], "SCHEMA_INVALID")
        self.assertEqual(
            answer["problems"][0]["path"],
            "/operations/0/create/campaign_budgett",
        )

    def test_another_accounts_name_never_reaches_google(self):
        body = {
            "operations": [
                {
                    "update": {
                        "resourceName": "customers/999/campaigns/5",
                        "name": "y",
                    },
                    "updateMask": "name",
                }
            ]
        }
        with patch.object(api, "_call") as call:
            with self.assertRaises(ToolError) as caught:
                api.mutate(
                    customer_id="1234567890",
                    path="customers/1234567890/campaigns:mutate",
                    body=body,
                )
        call.assert_not_called()
        problem = json.loads(str(caught.exception))["problems"][0]
        self.assertEqual(problem["code"], "FOREIGN_CUSTOMER")
        self.assertEqual(problem["path"], "/operations/0/update/resourceName")

    def test_valid_body_is_previewed(self):
        body = {
            "operations": [
                {
                    "update": {
                        "resourceName": "customers/1234567890/campaigns/5",
                        "name": "y",
                    },
                    "updateMask": "name",
                }
            ]
        }
        with patch.object(api, "_call", return_value=(200, {})) as call:
            out = api.mutate(
                customer_id="1234567890",
                path="customers/1234567890/campaigns:mutate",
                body=body,
            )
        self.assertEqual(out.status, "PROPOSED")
        call.assert_called_once()

    def test_what_google_accepts_is_normalized_then_sent(self):
        snake = {
            "operations": [
                {
                    "update": {
                        "resource_name": "customers/1234567890/campaignBudgets/5",
                        "amount_micros": 5000000,
                    },
                    "update_mask": "amount_micros",
                }
            ]
        }
        canonical = {
            "operations": [
                {
                    "update": {
                        "resourceName": "customers/1234567890/campaignBudgets/5",
                        "amountMicros": "5000000",
                    },
                    "updateMask": "amount_micros",
                }
            ]
        }
        path = "customers/1234567890/campaignBudgets:mutate"
        with patch.object(api, "_call", return_value=(200, {})) as call:
            out = api.mutate(customer_id="1234567890", path=path, body=snake)
            same = api.mutate(
                customer_id="1234567890", path=path, body=canonical
            )
        sent = call.call_args_list[0].args[2]
        self.assertEqual(sent["operations"], canonical["operations"])
        (note,) = out.notes
        self.assertTrue(note.startswith("Normalized: 3 snake_case key(s)"))
        self.assertIn("1 int64 number(s)", note)
        self.assertEqual(same.notes, [])
        # One change, one digest: the snake_case preview's plan applies the camelCase body.
        with patch.object(api, "_call", return_value=(200, {})):
            done = api.mutate(
                customer_id="1234567890",
                path=path,
                body=canonical,
                validate_only=False,
                plan_id=out.plan_id,
            )
        self.assertEqual(done.status, "APPLIED")


if __name__ == "__main__":
    unittest.main()
