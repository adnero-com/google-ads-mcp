"""api_mutate bodies against the v26 schemas: problems with JSON pointers (table-driven)."""

import unittest

from ads_mcp.schema import store
from ads_mcp.schema import validate as v

BUDGET = "customers/1/campaignBudgets/-1"


class PointerTest(unittest.TestCase):
    def test_escaping(self):
        cases = [
            ((), ""),
            (("operations", 0, "create"), "/operations/0/create"),
            (("a/b", "c~d"), "/a~1b/c~0d"),
        ]
        for parts, want in cases:
            with self.subTest(parts=parts):
                self.assertEqual(v.pointer(parts), want)


class CheckBodyTest(unittest.TestCase):
    def setUp(self):
        self.data = store.load()

    def codes(self, collection, body):
        return [
            (p.path, p.code) for p in v.check_body(self.data, collection, body)
        ]

    def test_table(self):
        cases = [
            (
                "valid budget",
                "campaignBudgets",
                {
                    "operations": [
                        {
                            "create": {
                                "amountMicros": "5000000",
                                "explicitlyShared": False,
                            }
                        }
                    ]
                },
                [],
            ),
            (
                "valid update",
                "campaigns",
                {
                    "operations": [
                        {
                            "update": {
                                "resourceName": "customers/1/campaigns/2",
                                "status": "PAUSED",
                            },
                            "updateMask": "status",
                        }
                    ]
                },
                [],
            ),
            (
                "snake_case key",
                "campaigns",
                {
                    "operations": [
                        {"create": {"name": "x", "campaign_budget": BUDGET}}
                    ]
                },
                [("/operations/0/create/campaign_budget", "UNKNOWN_FIELD")],
            ),
            (
                "output-only field",
                "campaigns",
                {"operations": [{"create": {"name": "x", "id": "1"}}]},
                [("/operations/0/create/id", "UNKNOWN_FIELD")],
            ),
            (
                "bad enum",
                "campaigns",
                {"operations": [{"create": {"name": "x", "status": "ON"}}]},
                [("/operations/0/create/status", "BAD_ENUM")],
            ),
            (
                "int64 as number",
                "campaignBudgets",
                {"operations": [{"create": {"amountMicros": 5}}]},
                [("/operations/0/create/amountMicros", "BAD_TYPE")],
            ),
            (
                "two bidding strategies",
                "campaigns",
                {
                    "operations": [
                        {
                            "create": {
                                "name": "x",
                                "targetCpa": {},
                                "targetRoas": {},
                            }
                        }
                    ]
                },
                [("/operations/0/create", "ONE_OF")],
            ),
            (
                "immutable in update",
                "campaigns",
                {
                    "operations": [
                        {
                            "update": {
                                "resourceName": "customers/1/campaigns/2",
                                "appCampaignSetting": {"appId": "x"},
                            },
                            "updateMask": "app_campaign_setting.app_id",
                        }
                    ]
                },
                [
                    (
                        "/operations/0/updateMask",
                        "IMMUTABLE",
                    )
                ],
            ),
            (
                "update without mask and name",
                "campaigns",
                {"operations": [{"update": {"name": "x"}}]},
                [
                    ("/operations/0/update/resourceName", "REQUIRED"),
                    ("/operations/0/updateMask", "REQUIRED"),
                ],
            ),
            (
                "two kinds in one op",
                "campaigns",
                {"operations": [{"create": {}, "update": {}}]},
                [("/operations/0", "BAD_OPERATION")],
            ),
            (
                "kind the resource lacks",
                "campaignLabels",
                {
                    "operations": [
                        {"update": {"resourceName": "x"}, "updateMask": "x"}
                    ]
                },
                [("/operations/0", "BAD_OPERATION")],
            ),
            (
                "unknown collection",
                "things",
                {"operations": []},
                [("", "UNKNOWN_RESOURCE")],
            ),
            (
                "googleAds mutate",
                "googleAds",
                {
                    "mutateOperations": [
                        {
                            "campaignBudgetOperation": {
                                "create": {
                                    "resourceName": BUDGET,
                                    "amountMicros": "1000000",
                                }
                            }
                        },
                        {
                            "campaignOperation": {
                                "create": {
                                    "name": "x",
                                    "campaignBudget": BUDGET,
                                    "bogus": 1,
                                }
                            }
                        },
                        {"nopeOperation": {}},
                    ]
                },
                [
                    (
                        "/mutateOperations/1/campaignOperation/create/bogus",
                        "UNKNOWN_FIELD",
                    ),
                    ("/mutateOperations/2", "BAD_OPERATION"),
                ],
            ),
        ]
        for name, collection, body, want in cases:
            with self.subTest(name):
                self.assertEqual(
                    sorted(self.codes(collection, body)), sorted(want)
                )

    def test_messages_say_the_fix(self):
        (p,) = v.check_body(
            self.data,
            "campaigns",
            {
                "operations": [
                    {"create": {"campaign_budget": BUDGET, "name": "x"}}
                ]
            },
        )
        self.assertIn("campaignBudget", p.message)
        self.assertEqual(set(p.as_dict()), {"path", "code", "message"})

    def test_capped(self):
        body = {"operations": [{"create": {"name": "x", "bad": 1}}] * 30}
        self.assertEqual(
            len(v.check_body(self.data, "campaigns", body)), v.MAX_PROBLEMS
        )


if __name__ == "__main__":
    unittest.main()
