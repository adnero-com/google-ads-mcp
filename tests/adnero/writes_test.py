"""The adnero write tools: operations built, propose and confirm, PAUSED creates."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp.tools import adnero_campaigns as camp
from ads_mcp.tools import adnero_keywords as kw


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = GoogleAdsClient(
            credentials=AnonymousCredentials(), use_proto_plus=True
        )

    def setUp(self):
        self.service = MagicMock()
        self.service.mutate.return_value = SimpleNamespace(
            mutate_operation_responses=[]
        )
        self.requests = []
        self.service.mutate.side_effect = self._record
        patches = [
            patch("ads_mcp.adnero_mutate.client", return_value=self.client),
            patch(
                "ads_mcp.utils.get_googleads_service_from_client",
                return_value=self.service,
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _record(self, request):
        self.requests.append(
            (request.validate_only, list(request.mutate_operations))
        )
        return SimpleNamespace(mutate_operation_responses=[])

    def propose_then_confirm(self, fn, **kwargs):
        proposal = fn(**kwargs).model_dump()
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertTrue(self.requests[-1][0], "the preview validates only")
        applied = fn(
            **kwargs, validate_only=False, checksum=proposal["checksum"]
        )
        self.assertEqual(applied.status, "APPLIED")
        self.assertFalse(self.requests[-1][0], "the confirm applies")
        return proposal, self.requests[-1][1]


class SetStatusTest(Harness):
    def test_pauses_campaigns(self):
        _, ops = self.propose_then_confirm(
            camp.set_status,
            customer_id="123-456-7890",
            level="CAMPAIGN",
            ids=["11", "12"],
            status="PAUSED",
        )
        self.assertEqual(len(ops), 2)
        update = ops[0].campaign_operation.update
        self.assertEqual(
            update.resource_name, "customers/1234567890/campaigns/11"
        )
        self.assertEqual(update.status.name, "PAUSED")
        self.assertEqual(
            list(ops[0].campaign_operation.update_mask.paths), ["status"]
        )

    def test_ads_need_composite_ids(self):
        with self.assertRaises(ToolError):
            camp.set_status(
                customer_id="1", level="AD", ids=["55"], status="ENABLED"
            )

    def test_ad_status(self):
        _, ops = self.propose_then_confirm(
            camp.set_status,
            customer_id="1",
            level="AD",
            ids=["5~6"],
            status="ENABLED",
        )
        update = ops[0].ad_group_ad_operation.update
        self.assertEqual(update.resource_name, "customers/1/adGroupAds/5~6")
        self.assertEqual(update.status.name, "ENABLED")

    def test_confirm_needs_the_preview_checksum(self):
        with self.assertRaises(ToolError):
            camp.set_status(
                customer_id="1",
                level="CAMPAIGN",
                ids=["11"],
                status="ENABLED",
                validate_only=False,
                checksum="0" * 64,
            )
        self.assertEqual(self.requests, [], "nothing reached Google")

    def test_checksum_binds_the_arguments(self):
        p = camp.set_status(
            customer_id="1", level="CAMPAIGN", ids=["11"], status="PAUSED"
        ).model_dump()
        with self.assertRaises(ToolError):
            camp.set_status(
                customer_id="1",
                level="CAMPAIGN",
                ids=["11"],
                status="ENABLED",
                validate_only=False,
                checksum=p["checksum"],
            )


class BudgetTest(Harness):
    def budget_row(self, shared):
        return [
            SimpleNamespace(
                campaign_budget=SimpleNamespace(
                    resource_name="customers/1/campaignBudgets/9",
                    amount_micros=5_000_000,
                    explicitly_shared=shared,
                )
            )
        ]

    def test_sets_the_campaign_budget(self):
        with patch(
            "ads_mcp.adnero_mutate.search_rows",
            return_value=self.budget_row(False),
        ):
            proposal, ops = self.propose_then_confirm(
                camp.set_budget,
                customer_id="1",
                campaign_id="11",
                daily_budget_micros=7_500_000,
            )
        update = ops[0].campaign_budget_operation.update
        self.assertEqual(update.resource_name, "customers/1/campaignBudgets/9")
        self.assertEqual(update.amount_micros, 7_500_000)
        item = proposal["change_items"][0]
        self.assertEqual(item["before"], "5000000")

    def test_refuses_a_shared_budget(self):
        with patch(
            "ads_mcp.adnero_mutate.search_rows",
            return_value=self.budget_row(True),
        ):
            with self.assertRaises(ToolError):
                camp.set_budget(
                    customer_id="1", campaign_id="11", daily_budget_micros=1
                )

    def test_refuses_negative_or_fractional_micros(self):
        for bad in (-1, 1.5, True):
            with self.assertRaises(ToolError):
                camp.set_budget(
                    customer_id="1", campaign_id="11", daily_budget_micros=bad
                )


class BidsTest(Harness):
    def test_ad_group_bids(self):
        _, ops = self.propose_then_confirm(
            camp.set_bids,
            customer_id="1",
            level="AD_GROUP",
            id="7",
            cpc_bid_micros=1_200_000,
            target_roas=3.5,
        )
        op = ops[0].ad_group_operation
        self.assertEqual(op.update.cpc_bid_micros, 1_200_000)
        self.assertEqual(op.update.target_roas, 3.5)
        self.assertEqual(
            sorted(op.update_mask.paths), ["cpc_bid_micros", "target_roas"]
        )

    def test_keyword_takes_only_cpc(self):
        with self.assertRaises(ToolError):
            camp.set_bids(
                customer_id="1", level="KEYWORD", id="7~8", target_roas=2.0
            )

    def test_campaign_level_is_refused(self):
        with self.assertRaises(ToolError):
            camp.set_bids(
                customer_id="1", level="CAMPAIGN", id="7", cpc_bid_micros=1
            )


class CreateTest(Harness):
    def setUp(self):
        super().setUp()
        # Google's answer to "which of these locations need the EU declaration": none, unless a test says so.
        self.eu = patch("ads_mcp.eu_read.eu_requiring", return_value=())
        self.eu_read = self.eu.start()
        self.addCleanup(self.eu.stop)

    def test_eu_rule(self):
        for requiring, declared, sent in (
            (("2276",), True, "CONTAINS_EU_POLITICAL_ADVERTISING"),
            (("2276",), False, "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"),
            (
                None,
                None,
                "UNSPECIFIED",
            ),  # the read failed: left for Google to enforce
        ):
            self.eu_read.return_value = requiring
            camp.create(
                customer_id="1",
                name="X",
                daily_budget_micros=1,
                location_ids=[2276],
                contains_eu_political_advertising=declared,
            )
            sent_now = self.requests[-1][1][1].campaign_operation.create
            self.assertEqual(
                sent_now.contains_eu_political_advertising.name, sent
            )
        self.eu_read.return_value = ("2276",)
        before = len(self.requests)
        for geos in ([2276], []):
            with self.assertRaises(ToolError) as e:
                camp.create(
                    customer_id="1",
                    name="X",
                    daily_budget_micros=1,
                    location_ids=geos,
                )
            self.assertIn(
                "EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED",
                str(e.exception),
            )
        self.assertEqual(len(self.requests), before, "refused before Google")

    def test_campaign_is_created_paused_with_its_budget(self):
        _, ops = self.propose_then_confirm(
            camp.create,
            customer_id="1",
            name="Brand US",
            daily_budget_micros=20_000_000,
            bidding="MAXIMIZE_CONVERSIONS",
            target_cpa_micros=4_000_000,
            location_ids=[2840],
            language_ids=[1000],
        )
        self.assertEqual(len(ops), 4)
        budget = ops[0].campaign_budget_operation.create
        campaign = ops[1].campaign_operation.create
        self.assertEqual(budget.amount_micros, 20_000_000)
        self.assertFalse(budget.explicitly_shared)
        self.assertEqual(campaign.status.name, "PAUSED")
        self.assertEqual(campaign.campaign_budget, budget.resource_name)
        self.assertEqual(
            campaign.maximize_conversions.target_cpa_micros, 4_000_000
        )
        self.assertEqual(
            campaign.contains_eu_political_advertising.name,
            "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        )
        geo = ops[2].campaign_criterion_operation.create
        self.assertEqual(
            geo.location.geo_target_constant, "geoTargetConstants/2840"
        )
        self.assertEqual(geo.campaign, campaign.resource_name)

    def test_cpa_needs_maximize_conversions(self):
        with self.assertRaises(ToolError):
            camp.create(
                customer_id="1",
                name="x",
                daily_budget_micros=1,
                bidding="MANUAL_CPC",
                target_cpa_micros=1,
            )

    def test_ad_group_and_ad_are_paused(self):
        _, ops = self.propose_then_confirm(
            camp.create_ad_group, customer_id="1", campaign_id="11", name="G"
        )
        self.assertEqual(ops[0].ad_group_operation.create.status.name, "PAUSED")
        _, ops = self.propose_then_confirm(
            camp.create_ad,
            customer_id="1",
            ad_group_id="7",
            headlines=["a", "b", "c"],
            descriptions=["d", "e"],
            final_urls=["https://example.com"],
        )
        ad = ops[0].ad_group_ad_operation.create
        self.assertEqual(ad.status.name, "PAUSED")
        self.assertEqual(len(ad.ad.responsive_search_ad.headlines), 3)

    def test_ad_needs_enough_headlines(self):
        with self.assertRaises(ToolError):
            camp.create_ad(
                customer_id="1",
                ad_group_id="7",
                headlines=["a"],
                descriptions=["d", "e"],
                final_urls=["https://example.com"],
            )


class KeywordsTest(Harness):
    def test_campaign_negatives(self):
        _, ops = self.propose_then_confirm(
            kw.add,
            customer_id="1",
            campaign_id="11",
            negative=True,
            keywords=[{"text": "free", "match_type": "BROAD"}],
        )
        crit = ops[0].campaign_criterion_operation.create
        self.assertTrue(crit.negative)
        self.assertEqual(crit.keyword.text, "free")
        self.assertEqual(crit.keyword.match_type.name, "BROAD")

    def test_positive_keywords_need_an_ad_group(self):
        with self.assertRaises(ToolError):
            kw.add(customer_id="1", campaign_id="11", keywords=[{"text": "x"}])

    def test_remove(self):
        _, ops = self.propose_then_confirm(
            kw.remove, customer_id="1", level="AD_GROUP", ids=["7~8"]
        )
        self.assertEqual(
            ops[0].ad_group_criterion_operation.remove,
            "customers/1/adGroupCriteria/7~8",
        )


class ElicitationTest(Harness):
    """validate_only=false without a checksum: a 2026-07-28 client with form elicitation is asked; others refused."""

    def ctx(self, *, modern=True, elicit=True, responses=None, state=None):
        meta = {
            "io.modelcontextprotocol/clientCapabilities": (
                {"elicitation": {"form": {}}} if elicit else {}
            )
        }
        rc = SimpleNamespace(meta=meta)
        return SimpleNamespace(
            request_context=rc,
            _is_modern_protocol=lambda: modern,
            input_responses=responses,
            request_state=state,
        )

    def call(self, ctx):
        return camp.set_status(
            customer_id="1",
            level="CAMPAIGN",
            ids=["11"],
            status="ENABLED",
            validate_only=False,
            ctx=ctx,
        )

    def test_asks_in_the_client_after_google_validates(self):
        from fastmcp.tools import InputRequiredToolResult

        asked = self.call(self.ctx())
        self.assertIsInstance(asked, InputRequiredToolResult)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.requests[0][0], "Google validated only")
        req = asked.input_required.input_requests["confirm"]
        self.assertIn("ENABLED", req.params.message)
        self.assertTrue(asked.input_required.request_state)

    def test_the_accepted_retry_applies(self):
        asked = self.call(self.ctx())
        state = asked.input_required.request_state
        answer = SimpleNamespace(action="accept", content={"confirm": True})
        done = self.call(self.ctx(responses={"confirm": answer}, state=state))
        self.assertEqual(done.status, "APPLIED")
        self.assertFalse(self.requests[-1][0])

    def test_a_declined_or_foreign_answer_never_applies(self):
        for answer, state in (
            (SimpleNamespace(action="decline", content=None), None),
            (
                SimpleNamespace(action="accept", content={"confirm": True}),
                "0" * 64,
            ),
        ):
            before = len(self.requests)
            result = self.call(
                self.ctx(responses={"confirm": answer}, state=state)
            )
            self.assertNotEqual(getattr(result, "status", None), "APPLIED")
            self.assertTrue(all(r[0] for r in self.requests[before:]))

    def test_without_elicitation_the_two_step_is_required(self):
        for ctx in (None, self.ctx(elicit=False), self.ctx(modern=False)):
            with self.assertRaises(ToolError):
                self.call(ctx)


if __name__ == "__main__":
    unittest.main()
