"""app_report: one query per breakdown, rows with CPA / ROAS, combinations named."""

import unittest
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from ads_mcp.reads import app_report as ar
from ads_mcp.tools import adnero_reports as tools
from tests.reads.fakes import E, FakeSearch, obj

CAMPAIGN = "24454294245"
DATES = "segments.date DURING LAST_30_DAYS"


def metrics(cost, conv, value, impressions=1000, clicks=50):
    return {
        "impressions": impressions,
        "clicks": clicks,
        "cost_micros": cost,
        "conversions": conv,
        "conversions_value": value,
        "all_conversions": conv,
    }


class QueryTest(unittest.TestCase):
    def test_queries(self):
        campaign, group = ar.Target(campaign_id=CAMPAIGN), ar.Target(
            ad_group_id="7"
        )
        cases = [
            (
                "day",
                campaign,
                [
                    "SELECT segments.date",
                    "FROM campaign",
                    f"campaign.id = {CAMPAIGN}",
                    DATES,
                ],
            ),
            (
                "network",
                group,
                [
                    "segments.ad_network_type",
                    "FROM ad_group",
                    "ad_group.id = 7",
                ],
            ),
            (
                "conversion_action",
                campaign,
                ["segments.conversion_action_name", "FROM campaign"],
            ),
            (
                "asset",
                campaign,
                ["FROM ad_group_ad_asset_view", "performance_label", DATES],
            ),
            (
                "combination",
                campaign,
                ["FROM app_top_combination_view", f"campaign.id = {CAMPAIGN}"],
            ),
        ]
        for by, target, fragments in cases:
            with self.subTest(by=by):
                q = ar.query(by, target, DATES)
                for f in fragments:
                    self.assertIn(f, q)
        self.assertNotIn(
            "segments.date", ar.query("combination", campaign, DATES)
        )

    def test_metrics(self):
        cases = [
            (
                metrics(10_000_000, 4, 20),
                {"cost": 10.0, "cpa": 2.5, "roas": 2.0},
            ),
            (metrics(0, 0, 0), {"cost": 0.0, "cpa": None, "roas": None}),
        ]
        for m, want in cases:
            with self.subTest(m=m):
                got = ar.metrics(obj(m))
                self.assertEqual({k: got[k] for k in want}, want)

    def test_totals(self):
        rows = [
            ar.metrics(obj(metrics(10_000_000, 4, 20))),
            ar.metrics(obj(metrics(5_000_000, 1, 0))),
        ]
        self.assertEqual(ar.totals("day", rows)["cost"], 15.0)
        self.assertEqual(ar.totals("day", rows)["cpa"], 3.0)
        self.assertIsNone(ar.totals("asset", rows))
        self.assertIsNone(ar.totals("day", []))


class ToolTest(unittest.TestCase):
    def test_by_day(self):
        fake = FakeSearch(
            {
                "campaign": [
                    obj(
                        {
                            "segments": {"date": "2026-11-01"},
                            "metrics": metrics(2_000_000, 1, 0),
                        }
                    )
                ]
            }
        )
        with patch.object(tools, "_search", fake):
            out = tools.app_report(
                customer_id="8396625493",
                campaign_id=CAMPAIGN,
                start="2026-11-01",
                end="2026-11-02",
            )
        self.assertEqual(out.rows[0]["date"], "2026-11-01")
        self.assertEqual(out.totals["cpa"], 2.0)
        self.assertIn("BETWEEN '2026-11-01' AND '2026-11-02'", fake.queries[0])
        self.assertEqual(out.operations, 1)

    def test_by_asset(self):
        row = obj(
            {
                "asset": {
                    "id": 501,
                    "name": "",
                    "type_": E("TEXT"),
                    "text_asset": {"text": "Spin now!"},
                    "youtube_video_asset": {"youtube_video_id": ""},
                },
                "ad_group": {"id": 7},
                "ad_group_ad_asset_view": {
                    "field_type": E("HEADLINE"),
                    "performance_label": E("BEST"),
                    "enabled": True,
                },
                "metrics": metrics(1_000_000, 1, 0),
            }
        )
        with patch.object(
            tools, "_search", FakeSearch({"ad_group_ad_asset_view": [row]})
        ):
            out = tools.app_report(
                customer_id="1", campaign_id=CAMPAIGN, by="asset"
            )
        self.assertEqual(out.rows[0]["performance_label"], "BEST")
        self.assertEqual(out.rows[0]["text"], "Spin now!")
        self.assertIsNone(out.totals)

    def test_by_combination_names_assets(self):
        combo = obj(
            {
                "ad_group": {"id": 7, "name": "AG"},
                "app_top_combination_view": {
                    "resource_name": "customers/1/appTopCombinationViews/7~8~ALL",
                    "ad_group_top_combinations": [
                        {
                            "asset_combination_served_assets": [
                                {
                                    "asset": "customers/1/assets/501",
                                    "served_asset_field_type": E("HEADLINE"),
                                }
                            ]
                        }
                    ],
                },
            }
        )
        label = obj(
            {
                "asset": {
                    "id": 501,
                    "name": "",
                    "type_": E("TEXT"),
                    "text_asset": {"text": "Spin now!"},
                    "youtube_video_asset": {"youtube_video_id": ""},
                }
            }
        )
        fake = FakeSearch(
            {"app_top_combination_view": [combo], "asset": [label]}
        )
        with patch.object(tools, "_search", fake):
            out = tools.app_report(
                customer_id="1", campaign_id=CAMPAIGN, by="combination"
            )
        self.assertEqual(out.operations, 2)
        self.assertEqual(out.rows[0]["category"], "ALL")
        self.assertEqual(
            out.rows[0]["combinations"][0][0],
            {
                "asset_id": "501",
                "field_type": "HEADLINE",
                "type": "TEXT",
                "text": "Spin now!",
            },
        )
        self.assertIn("asset.id IN (501)", fake.queries[1])
        self.assertEqual(out.dates, "")

    def test_refusals(self):
        cases = [
            {},
            {"campaign_id": CAMPAIGN, "ad_group_id": "7"},
            {"campaign_id": "abc"},
            {"campaign_id": CAMPAIGN, "date_preset": "ALL_TIME"},
            {
                "campaign_id": CAMPAIGN,
                "start": "2026-11-02",
                "end": "2026-11-01",
            },
        ]
        for kwargs in cases:
            with (
                self.subTest(kwargs=kwargs),
                patch.object(tools, "_search") as search,
                self.assertRaises(ToolError),
            ):
                tools.app_report(customer_id="1", **kwargs)
            search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
