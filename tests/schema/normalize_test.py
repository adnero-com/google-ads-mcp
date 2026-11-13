"""Bodies put in canonical REST form: only what Google itself accepts (table-driven)."""

import unittest

from ads_mcp.schema import normalize as n
from ads_mcp.schema import store

BUDGET = "customers/1/campaignBudgets/2"


class NormalizeTest(unittest.TestCase):
    def setUp(self):
        self.data = store.load()

    def test_table(self):
        cases = [
            (
                "snake keys and an int64 number",
                "campaigns",
                {
                    "operations": [
                        {
                            "create": {
                                "campaign_budget": BUDGET,
                                "target_cpa": {"target_cpa_micros": 5},
                            }
                        }
                    ]
                },
                {
                    "operations": [
                        {
                            "create": {
                                "campaignBudget": BUDGET,
                                "targetCpa": {"targetCpaMicros": "5"},
                            }
                        }
                    ]
                },
                ["key", "key", "key", "int64"],
            ),
            (
                "canonical: untouched",
                "campaigns",
                {
                    "operations": [
                        {"create": {"campaignBudget": BUDGET, "name": "x"}}
                    ]
                },
                {
                    "operations": [
                        {"create": {"campaignBudget": BUDGET, "name": "x"}}
                    ]
                },
                [],
            ),
            (
                "unknown keys stay for the validator",
                "campaigns",
                {"operations": [{"create": {"bogus_field": 1}}]},
                {"operations": [{"create": {"bogus_field": 1}}]},
                [],
            ),
            (
                "int32 and booleans untouched, repeated messages walked",
                "adGroupAds",
                {
                    "operations": [
                        {
                            "create": {
                                "ad": {
                                    "app_ad": {"headlines": [{"text": "a"}]}
                                },
                                "status": "ENABLED",
                            }
                        }
                    ]
                },
                {
                    "operations": [
                        {
                            "create": {
                                "ad": {"appAd": {"headlines": [{"text": "a"}]}},
                                "status": "ENABLED",
                            }
                        }
                    ]
                },
                ["key"],
            ),
            (
                "both spellings present: never merged",
                "campaigns",
                {
                    "operations": [
                        {
                            "create": {
                                "campaignBudget": BUDGET,
                                "campaign_budget": BUDGET,
                            }
                        }
                    ]
                },
                {
                    "operations": [
                        {
                            "create": {
                                "campaignBudget": BUDGET,
                                "campaign_budget": BUDGET,
                            }
                        }
                    ]
                },
                [],
            ),
            (
                "googleAds wrappers",
                "googleAds",
                {
                    "mutate_operations": [
                        {
                            "campaign_budget_operation": {
                                "create": {"explicitly_shared": False}
                            }
                        }
                    ]
                },
                {
                    "mutateOperations": [
                        {
                            "campaignBudgetOperation": {
                                "create": {"explicitlyShared": False}
                            }
                        }
                    ]
                },
                ["key", "key", "key"],
            ),
            (
                "unknown collection",
                "things",
                {"operations": [{"a_b": 1}]},
                {"operations": [{"a_b": 1}]},
                [],
            ),
        ]
        for name, collection, body, want, kinds in cases:
            with self.subTest(name):
                got, notes = n.normalize_body(self.data, collection, body)
                self.assertEqual(got, want)
                self.assertEqual([x.kind for x in notes], kinds)

    def test_summary(self):
        self.assertIsNone(n.summary(()))
        notes = tuple(
            n.Note("key", f"/p/{i}", f"a_{i}→a{i}") for i in range(5)
        ) + (
            n.Note("int64", "/x", "5"),
        )
        line = n.summary(notes)
        self.assertEqual(
            line,
            "Normalized: 5 snake_case key(s) → camelCase (a_0→a0, a_1→a1, a_2→a2, ...); "
            "1 int64 number(s) → digit strings (/x).",
        )


if __name__ == "__main__":
    unittest.main()
