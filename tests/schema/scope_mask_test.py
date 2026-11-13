"""Account scoping of every resource name, and updateMask rules (table-driven, real v26 schemas)."""

import unittest

from ads_mcp.schema import store
from ads_mcp.schema import validate as v

C = "1234567890"
OWN = f"customers/{C}"


def campaign_update(update, mask):
    return {
        "operations": [
            {
                "update": {"resourceName": f"{OWN}/campaigns/1", **update},
                "updateMask": mask,
            }
        ]
    }


class ScopeTest(unittest.TestCase):
    def test_table(self):
        cases = [
            (
                "own names and temp ids",
                "campaigns",
                {
                    "operations": [
                        {
                            "create": {
                                "name": "x",
                                "campaignBudget": f"{OWN}/campaignBudgets/-1",
                            }
                        }
                    ]
                },
                [],
            ),
            (
                "update of another account",
                "campaigns",
                campaign_update({}, "name")
                | {
                    "operations": [
                        {
                            "update": {
                                "resourceName": "customers/999/campaigns/1"
                            },
                            "updateMask": "name",
                        }
                    ]
                },
                [("/operations/0/update/resourceName", "FOREIGN_CUSTOMER")],
            ),
            (
                "foreign conversion action",
                "campaigns",
                {
                    "operations": [
                        {
                            "create": {
                                "name": "x",
                                "selectiveOptimization": {
                                    "conversionActions": [
                                        f"{OWN}/conversionActions/1",
                                        "customers/999/conversionActions/5",
                                    ]
                                },
                            }
                        }
                    ]
                },
                [
                    (
                        "/operations/0/create/selectiveOptimization/conversionActions/1",
                        "FOREIGN_CUSTOMER",
                    )
                ],
            ),
            (
                "foreign video asset",
                "adGroupAds",
                {
                    "operations": [
                        {
                            "create": {
                                "adGroup": f"{OWN}/adGroups/1",
                                "ad": {
                                    "appAd": {
                                        "youtubeVideos": [
                                            {"asset": "customers/999/assets/7"}
                                        ]
                                    }
                                },
                            }
                        }
                    ]
                },
                [
                    (
                        "/operations/0/create/ad/appAd/youtubeVideos/0/asset",
                        "FOREIGN_CUSTOMER",
                    )
                ],
            ),
            (
                "remove outside the account",
                "campaigns",
                {"operations": [{"remove": "customers/999/campaigns/1"}]},
                [("/operations/0/remove", "FOREIGN_CUSTOMER")],
            ),
            (
                "a reference that is no resource name",
                "adGroups",
                {
                    "operations": [
                        {"create": {"name": "x", "campaign": "campaigns/1"}}
                    ]
                },
                [("/operations/0/create/campaign", "FOREIGN_CUSTOMER")],
            ),
            (
                "constants are not under a customer",
                "campaignCriteria",
                {
                    "operations": [
                        {
                            "create": {
                                "campaign": f"{OWN}/campaigns/1",
                                "language": {
                                    "languageConstant": "languageConstants/1000"
                                },
                            }
                        }
                    ]
                },
                [],
            ),
            (
                "googleAds: every entry",
                "googleAds",
                {
                    "mutateOperations": [
                        {
                            "adGroupOperation": {
                                "create": {
                                    "name": "x",
                                    "campaign": "customers/999/campaigns/-2",
                                }
                            }
                        }
                    ]
                },
                [
                    (
                        "/mutateOperations/0/adGroupOperation/create/campaign",
                        "FOREIGN_CUSTOMER",
                    )
                ],
            ),
        ]
        for name, collection, body, want in cases:
            with self.subTest(name):
                got = [
                    (p.path, p.code)
                    for p in v.check_body(store.load(), collection, body, C)
                ]
                self.assertEqual(got, want)

    def test_without_customer_no_scoping(self):
        body = {"operations": [{"remove": "customers/999/campaigns/1"}]}
        self.assertEqual(v.check_body(store.load(), "campaigns", body), [])


class MaskTest(unittest.TestCase):
    def test_table(self):
        cases = [
            ("plain field", {"name": "x"}, "name", []),
            (
                "snake path",
                {"targetCpa": {"targetCpaMicros": "1"}},
                "target_cpa.target_cpa_micros",
                [],
            ),
            (
                "immutable named, value absent",
                {},
                "app_campaign_setting.app_id",
                ["IMMUTABLE"],
            ),
            (
                "immutable in body, not in mask: Google ignores it",
                {"name": "x", "appCampaignSetting": {"appId": "y"}},
                "name",
                [],
            ),
            (
                "message mask carrying an immutable",
                {"appCampaignSetting": {"appId": "y"}},
                "appCampaignSetting",
                ["IMMUTABLE"],
            ),
            (
                "message mask, mutable part only",
                {
                    "appCampaignSetting": {
                        "biddingStrategyGoalType": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST"
                    }
                },
                "appCampaignSetting",
                [],
            ),
            (
                "immutable top field",
                {},
                "advertising_channel_type",
                ["IMMUTABLE"],
            ),
            ("unknown path", {}, "name,nope", ["BAD_MASK"]),
            ("unknown sub-field", {}, "target_cpa.nope", ["BAD_MASK"]),
            ("output-only field is not writable", {}, "id", ["BAD_MASK"]),
            ("past a repeated field", {}, "frequency_caps.key", ["BAD_MASK"]),
        ]
        for name, update, mask, want in cases:
            with self.subTest(name):
                got = [
                    p.code
                    for p in v.check_body(
                        store.load(),
                        "campaigns",
                        campaign_update(update, mask),
                        C,
                    )
                ]
                self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
