"""policy_report on the owner's account shape: App campaign 24454294245, SOCIAL_CASINO_GAMES (LIMITED),
geo 2124 targeted, 20113 and 20121 excluded."""

import unittest
from unittest.mock import patch

from ads_mcp.reads import policy as pol
from ads_mcp.tools import adnero_reports as tools
from tests.reads.fakes import E, FakeSearch, obj

CAMPAIGN = "24454294245"
GROUP = "190000000001"
AD = "770000000001"


def summary(approval, topics):
    return {
        "approval_status": E(approval),
        "review_status": E("REVIEWED"),
        "policy_topic_entries": [
            {
                "topic": topic,
                "type_": E(kind),
                "constraints": (
                    [
                        {
                            "country_constraint_list": {
                                "countries": [
                                    {
                                        "country_criterion": f"geoTargetConstants/{c}"
                                    }
                                    for c in countries
                                ]
                            }
                        }
                    ]
                    if countries
                    else []
                ),
                "evidences": [],
            }
            for topic, kind, countries in topics
        ],
    }


ANSWERS = {
    "campaign": [
        obj(
            {
                "customer": {"descriptive_name": "Owner account"},
                "campaign": {
                    "id": int(CAMPAIGN),
                    "name": "App install CA",
                    "status": E("ENABLED"),
                    "primary_status": E("LIMITED"),
                    "primary_status_reasons": [E("MOST_ADS_UNDER_REVIEW")],
                    "advertising_channel_type": E("MULTI_CHANNEL"),
                    "advertising_channel_sub_type": E("APP_CAMPAIGN"),
                },
            }
        )
    ],
    "ad_group": [
        obj(
            {
                "campaign": {"id": int(CAMPAIGN)},
                "ad_group": {
                    "id": int(GROUP),
                    "name": "Ad group 1",
                    "status": E("ENABLED"),
                    "primary_status": E("ELIGIBLE"),
                    "primary_status_reasons": [],
                },
            }
        )
    ],
    "ad_group_ad": [
        obj(
            {
                "ad_group": {"id": int(GROUP)},
                "ad_group_ad": {
                    "ad": {"id": int(AD), "name": "", "type_": E("APP_AD")},
                    "status": E("ENABLED"),
                    "primary_status": E("LIMITED"),
                    "primary_status_reasons": [
                        E("AD_GROUP_AD_APPROVED_LABELED")
                    ],
                    "policy_summary": summary(
                        "APPROVED_LIMITED",
                        [("SOCIAL_CASINO_GAMES", "LIMITED", ["2124"])],
                    ),
                },
            }
        )
    ],
    "ad_group_ad_asset_view": [
        obj(
            {
                "ad_group": {"id": int(GROUP)},
                "asset": {"id": 501, "name": "", "type_": E("TEXT")},
                "ad_group_ad_asset_view": {
                    "ad_group_ad": f"customers/8396625493/adGroupAds/{GROUP}~{AD}",
                    "field_type": E("HEADLINE"),
                    "policy_summary": summary(
                        "APPROVED", [("TRADEMARKS", "DESCRIPTIVE", [])]
                    ),
                },
            }
        ),
        obj(
            {
                "ad_group": {"id": int(GROUP)},
                "asset": {"id": 502, "name": "hero.png", "type_": E("IMAGE")},
                "ad_group_ad_asset_view": {
                    "ad_group_ad": f"customers/8396625493/adGroupAds/{GROUP}~{AD}",
                    "field_type": E("MARKETING_IMAGE"),
                    "policy_summary": summary("APPROVED", []),
                },
            }
        ),
    ],
    "campaign_criterion": [
        obj(
            {
                "campaign": {"id": int(CAMPAIGN)},
                "campaign_criterion": {
                    "location": {
                        "geo_target_constant": f"geoTargetConstants/{g}"
                    },
                    "negative": neg,
                },
            }
        )
        for g, neg in (("2124", False), ("20113", True), ("20121", True))
    ],
    "geo_target_constant": [
        obj(
            {
                "geo_target_constant": {
                    "id": i,
                    "name": n,
                    "canonical_name": n,
                    "target_type": t,
                    "country_code": "CA",
                    "status": E("ENABLED"),
                }
            }
        )
        for i, n, t in (
            (2124, "Canada", "Country"),
            (20113, "Quebec", "Province"),
            (20121, "Nunavut", "Territory"),
        )
    ],
}


class PolicyReportTest(unittest.TestCase):
    def run_report(self, **kwargs):
        fake = FakeSearch(ANSWERS)
        with patch.object(tools, "_search", fake):
            out = tools.policy_report(
                customer_id="8396625493",
                login_customer_id="8868324827",
                **kwargs,
            )
        return out, fake

    def test_owner_account(self):
        out, fake = self.run_report()
        self.assertEqual(out.operations, 6)
        self.assertIn("Google decides at submit time", out.exemption_note)
        self.assertEqual(len(fake.queries), 6)
        report = out.report
        self.assertEqual(report["name"], "Owner account")
        (campaign,) = report["campaigns"]
        self.assertEqual(campaign["id"], CAMPAIGN)
        self.assertEqual(campaign["primary_status"], "LIMITED")
        self.assertEqual(
            campaign["geo"],
            {
                "targeted": [
                    {
                        "id": "2124",
                        "name": "Canada",
                        "type": "Country",
                        "country_code": "CA",
                    }
                ],
                "excluded": [
                    {
                        "id": "20113",
                        "name": "Quebec",
                        "type": "Province",
                        "country_code": "CA",
                    },
                    {
                        "id": "20121",
                        "name": "Nunavut",
                        "type": "Territory",
                        "country_code": "CA",
                    },
                ],
            },
        )
        ad = campaign["ad_groups"][0]["ads"][0]
        self.assertEqual(ad["approval"], "APPROVED_LIMITED")
        self.assertEqual(ad["policy_topics"][0]["topic"], "SOCIAL_CASINO_GAMES")
        self.assertEqual(ad["policy_topics"][0]["type"], "LIMITED")
        self.assertEqual(
            ad["policy_topics"][0]["countries"][0]["name"], "Canada"
        )
        self.assertEqual(
            ad["exemption_candidates"],
            {"ignorable_policy_topics": ["SOCIAL_CASINO_GAMES"]},
        )
        self.assertEqual(
            [a["field_type"] for a in ad["assets"]],
            ["HEADLINE", "MARKETING_IMAGE"],
        )
        self.assertNotIn(
            "exemption_candidates", ad["assets"][0]
        )  # DESCRIPTIVE only

    def test_problems_only(self):
        out, _ = self.run_report(problems_only=True)
        ad = out.report["campaigns"][0]["ad_groups"][0]["ads"][0]
        self.assertEqual(ad["assets"], [])  # approved, no restricting finding
        self.assertEqual(ad["id"], AD)

    def test_scope(self):
        _, fake = self.run_report(campaign_id=CAMPAIGN)
        self.assertTrue(
            all(f"campaign.id = {CAMPAIGN}" in q for q in fake.queries[:5])
        )
        _, fake = self.run_report(ad_group_id=GROUP)
        self.assertNotIn("ad_group.id", fake.queries[0])  # FROM campaign
        self.assertIn(f"ad_group.id = {GROUP}", fake.queries[2])

    def test_where(self):
        cases = [
            (pol.Scope(), "campaign", ""),
            (pol.Scope("1"), "ad_group", " AND campaign.id = 1"),
            (pol.Scope("1", "2"), "campaign", " AND campaign.id = 1"),
            (pol.Scope(None, "2"), "ad_group_ad", " AND ad_group.id = 2"),
        ]
        for scope, resource, want in cases:
            with self.subTest(scope=scope, resource=resource):
                self.assertEqual(scope.where(resource), want)


if __name__ == "__main__":
    unittest.main()


class BoundsTest(unittest.TestCase):
    def test_every_level_is_limited(self):
        for level, query in pol.queries(pol.Scope()).items():
            with self.subTest(level=level):
                self.assertTrue(query.endswith(f"LIMIT {pol.CAPS[level] + 1}"))

    def test_cut(self):
        rows, truncated = pol.cut(
            {"ads": list(range(pol.CAPS["ads"] + 1)), "campaigns": [1]}
        )
        self.assertEqual(len(rows["ads"]), pol.CAPS["ads"])
        self.assertEqual(truncated, ["ads"])

    def test_geo_names_are_read_in_chunks(self):
        from ads_mcp.reads import lookup as lk

        queries = lk.geo_by_id_queries([str(i) for i in range(250)])
        self.assertEqual(len(queries), 3)
        self.assertEqual(queries[2].count("geoTargetConstants/"), 50)
        self.assertEqual(lk.geo_by_id_queries([]), [])

    def test_truncated_levels_are_reported(self):
        many = {
            **ANSWERS,
            "ad_group": ANSWERS["ad_group"] * (pol.CAPS["ad_groups"] + 1),
        }
        with patch.object(tools, "_search", FakeSearch(many)):
            out = tools.policy_report(customer_id="8396625493")
        self.assertEqual(out.truncated, ["ad_groups"])
