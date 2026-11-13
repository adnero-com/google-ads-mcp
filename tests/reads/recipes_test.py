"""The App recipes: well-formed, the right ad per sub-type, and (with the schema layer) valid bodies."""

import json
import unittest

from ads_mcp.resources import recipes


def filled(value, key=""):
    """Placeholders replaced the way an agent would (micros digits, real-looking ids and dates)."""
    if isinstance(value, dict):
        return {k: filled(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [filled(v, key) for v in value]
    if isinstance(value, str):
        text = value.replace("{customer_id}", "8396625493")
        if key.endswith("Micros"):
            return "2000000"
        if key == "startDateTime":
            return "2026-12-01 00:00:00"
        return text.replace("<", "").replace(">", "").replace(" ", "")
    return value


class RecipesTest(unittest.TestCase):
    def test_shape(self):
        cases = [
            ("app-install", "APP_CAMPAIGN", "appAd"),
            (
                "app-engagement",
                "APP_CAMPAIGN_FOR_ENGAGEMENT",
                "appEngagementAd",
            ),
            (
                "app-pre-registration",
                "APP_CAMPAIGN_FOR_PRE_REGISTRATION",
                "appPreRegistrationAd",
            ),
        ]
        for slug, sub_type, ad_kind in cases:
            with self.subTest(slug=slug):
                recipe = json.loads(recipes.recipe_text(slug))
                ops = recipe["body"]["mutateOperations"]
                campaign = next(
                    o["campaignOperation"]["create"]
                    for o in ops
                    if "campaignOperation" in o
                )
                self.assertEqual(
                    campaign["advertisingChannelSubType"], sub_type
                )
                self.assertEqual(campaign["status"], "PAUSED")
                ad = next(
                    o["adGroupAdOperation"]["create"]
                    for o in ops
                    if "adGroupAdOperation" in o
                )
                self.assertEqual(list(ad["ad"]), [ad_kind])
                self.assertEqual(
                    ad["status"], "ENABLED"
                )  # App ads cannot be paused
                self.assertTrue(recipe["steps"])

    def test_engagement_targets_a_user_list(self):
        ops = recipes.RECIPES["app-engagement"]["body"]["mutateOperations"]
        self.assertTrue(
            any(
                "userList"
                in o.get("adGroupCriterionOperation", {}).get("create", {})
                for o in ops
            )
        )

    def test_bodies_match_the_schemas(self):
        try:
            from ads_mcp.schema import store, validate
        except ImportError:
            self.skipTest(
                "the schema layer (ads_mcp/schema) is not on this branch"
            )
        for slug, recipe in recipes.RECIPES.items():
            with self.subTest(slug=slug):
                problems = validate.check_body(
                    store.load(), "googleAds", filled(recipe["body"])
                )
                self.assertEqual([p.as_dict() for p in problems], [])


if __name__ == "__main__":
    unittest.main()
