"""lookup_geo, lookup_language, resolve: pure parts table-driven, tools with Google faked."""

import unittest
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from ads_mcp.reads import lookup as lk
from ads_mcp.tools import adnero_lookup as tools
from tests.reads.fakes import E, obj


def constant(id_, name, canonical, type_, cc="CA"):
    return {
        "id": id_,
        "name": name,
        "canonical_name": canonical,
        "target_type": type_,
        "country_code": cc,
        "status": E("ENABLED"),
        "require_eu_political_advertising_declaration": False,
    }


SUGGEST = obj(
    {
        "geo_target_constant_suggestions": [
            {
                "geo_target_constant": constant(
                    2124, "Canada", "Canada", "Country"
                ),
                "reach": 0,
                "search_term": "",
            },
            {
                "geo_target_constant": constant(
                    20113, "Quebec", "Quebec,Canada", "Province"
                ),
                "reach": 5000000,
                "search_term": "Quebec",
            },
        ]
    }
)
LANGS = [
    obj(
        {
            "language_constant": {
                "id": i,
                "code": c,
                "name": n,
                "targetable": True,
            }
        }
    )
    for i, c, n in (
        (1000, "en", "English"),
        (1001, "de", "German"),
        (1002, "fr", "French"),
    )
]


class PureTest(unittest.TestCase):
    def test_suggestions(self):
        out = lk.suggestions(SUGGEST)
        self.assertEqual(
            out[1],
            {
                "id": "20113",
                "name": "Quebec",
                "canonical_name": "Quebec,Canada",
                "type": "Province",
                "country_code": "CA",
                "status": "ENABLED",
                "eu_political_declaration_required": False,
                "reach": 5000000,
                "query": "Quebec",
            },
        )
        self.assertNotIn("reach", out[0])

    def test_match_languages(self):
        langs = [lk.language(r) for r in LANGS]
        cases = [
            ("", 3),
            ("de", 1),
            ("GERMAN", 1),
            ("1002", 1),
            ("en", 1),
            ("ren", 1),
            ("xx", 0),
        ]
        for text, count in cases:
            with self.subTest(text=text):
                self.assertEqual(len(lk.match_languages(langs, text)), count)

    def test_resolve_query(self):
        cases = [
            (
                "campaign",
                "App #1",
                "exact",
                False,
                "WHERE campaign.name = 'App #1' AND campaign.status != 'REMOVED'",
            ),
            (
                "ad_group",
                "it's",
                "exact",
                True,
                "WHERE ad_group.name = 'it\\'s' ORDER BY",
            ),
            (
                "asset",
                "a.b",
                "contains",
                False,
                "WHERE asset.name REGEXP_MATCH '(?i).*a\\\\.b.*' ORDER BY",
            ),
            (
                "conversion_action",
                "first_open",
                "exact",
                False,
                "conversion_action.app_id",
            ),
        ]
        for kind, text, match, removed, fragment in cases:
            with self.subTest(kind=kind, text=text):
                self.assertIn(
                    fragment, lk.resolve_query(kind, text, match, removed)
                )
        self.assertTrue(
            lk.resolve_query("label", "x", "exact", False).endswith(
                f"LIMIT {lk.MAX_MATCHES + 1}"
            )
        )

    def test_resolved(self):
        row = obj(
            {
                "ad_group": {
                    "id": 7,
                    "name": "AG",
                    "resource_name": "customers/1/adGroups/7",
                    "status": E("ENABLED"),
                },
                "campaign": {"id": 24454294245, "name": "App"},
            }
        )
        self.assertEqual(
            lk.resolved("ad_group", row),
            {
                "id": "7",
                "name": "AG",
                "resource_name": "customers/1/adGroups/7",
                "status": "ENABLED",
                "campaign_id": "24454294245",
                "campaign_name": "App",
            },
        )


class ToolsTest(unittest.TestCase):
    def test_geo(self):
        with patch.object(tools, "_suggest", return_value=SUGGEST) as suggest:
            out = tools.geo(ids=[2124, "20113", "x"])
        self.assertEqual(
            suggest.call_args.args, ([], ["2124", "20113"], None, "en")
        )
        self.assertEqual(
            [g["name"] for g in out.geo_targets], ["Canada", "Quebec"]
        )
        with patch.object(tools, "_suggest", return_value=SUGGEST) as suggest:
            tools.geo(names=["Quebec"], country="ca")
        self.assertEqual(suggest.call_args.args, (["Quebec"], [], "CA", "en"))
        for kwargs in (
            {},
            {"names": ["a"], "ids": [1]},
            {"names": ["a"] * 26},
            {"names": ["a"], "country": "CAN"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ToolError):
                tools.geo(**kwargs)

    def test_language_is_cached(self):
        tools._languages.clear()
        with patch.object(tools, "_search", return_value=LANGS) as search:
            self.assertEqual(
                tools.language(customer_id="1", query="de").languages[0]["id"],
                "1001",
            )
            self.assertEqual(len(tools.language(customer_id="1").languages), 3)
        self.assertEqual(search.call_count, 1)

    def test_resolve(self):
        rows = [
            obj(
                {
                    "campaign": {
                        "id": i,
                        "name": "App",
                        "resource_name": f"customers/1/campaigns/{i}",
                        "status": E("PAUSED"),
                        "advertising_channel_type": E("MULTI_CHANNEL"),
                        "advertising_channel_sub_type": E("APP_CAMPAIGN"),
                    }
                }
            )
            for i in (1, 2)
        ]
        with patch.object(tools, "_search", return_value=rows):
            out = tools.resolve(customer_id="1", kind="campaign", name="App")
        self.assertTrue(out.ambiguous)
        self.assertEqual(
            out.matches[0]["advertising_channel_sub_type"], "APP_CAMPAIGN"
        )
        with self.assertRaises(ToolError):
            tools.resolve(customer_id="1", kind="campaign", name=" ")


if __name__ == "__main__":
    unittest.main()
