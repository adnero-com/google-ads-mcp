"""Google refusing a read comes back as the structured error (utils.format_google_ads_error), never as the raw
gRPC exception text (live on v26: "Error calling tool 'report': (<_InactiveRpcError ...").
"""

import unittest
from unittest.mock import patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v26.errors.types import errors, query_error

from ads_mcp.tools import adnero_accounts, adnero_lookup, adnero_reports

QE = query_error.QueryErrorEnum.QueryError


class RawRpc(Exception):
    def __str__(self):
        return "<_InactiveRpcError of RPC that terminated with: status = StatusCode.INVALID_ARGUMENT>"


def refused():
    failure = errors.GoogleAdsFailure(
        errors=[
            errors.GoogleAdsError(
                error_code=errors.ErrorCode(query_error=QE.UNRECOGNIZED_FIELD),
                message="Unrecognized field in the query: 'ad_group_ad_asset_view.policy_summary.approval_status'.",
            )
        ]
    )
    return GoogleAdsException(RawRpc(), RawRpc(), failure, "req-1")


class StructuredErrorsTest(unittest.TestCase):
    def test_read_tools(self):
        calls = [
            (
                "policy_report",
                lambda: adnero_reports.policy_report(customer_id="8396625493"),
            ),
            (
                "app_report",
                lambda: adnero_reports.app_report(
                    customer_id="1", campaign_id="2"
                ),
            ),
            ("accounts_get", lambda: adnero_lookup.get(customer_id="1")),
            (
                "resolve",
                lambda: adnero_lookup.resolve(
                    customer_id="1", kind="campaign", name="x"
                ),
            ),
            (
                "lookup_language",
                lambda: (
                    adnero_lookup._languages.clear(),
                    adnero_lookup.language(customer_id="1"),
                ),
            ),
            (
                "accounts_list_clients",
                lambda: adnero_accounts.list_clients(manager_id="1"),
            ),
        ]
        for name, call in calls:
            with (
                self.subTest(name),
                patch(
                    "ads_mcp.adnero_mutate.search_rows", side_effect=refused()
                ),
            ):
                with self.assertRaises(ToolError) as caught:
                    call()
                text = str(caught.exception)
                self.assertIn("UNRECOGNIZED_FIELD", text)
                self.assertIn("policy_summary.approval_status", text)
                self.assertNotIn("_InactiveRpcError", text)

    def test_lookup_geo(self):
        service = unittest.mock.MagicMock()
        service.suggest_geo_target_constants.side_effect = refused()
        with (
            patch("ads_mcp.utils.get_googleads_service", return_value=service),
            patch(
                "ads_mcp.utils.get_googleads_type",
                return_value=unittest.mock.MagicMock(),
            ),
        ):
            with self.assertRaises(ToolError) as caught:
                adnero_lookup.geo(ids=[2124])
        self.assertIn("UNRECOGNIZED_FIELD", str(caught.exception))
        self.assertNotIn("_InactiveRpcError", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
