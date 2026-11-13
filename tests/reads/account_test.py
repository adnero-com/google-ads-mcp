"""accounts_get: the account shaped, identity verification read or explained (never "not verified")."""

import unittest
from unittest.mock import patch

from google.ads.googleads.v26.errors.types import (
    errors,
    identity_verification_error,
)

from ads_mcp.reads import account as acc
from ads_mcp.tools import adnero_lookup as tools
from tests.reads.fakes import E, GoogleError, obj

IVE = (
    identity_verification_error.IdentityVerificationErrorEnum.IdentityVerificationError
)

ROW = obj(
    {
        "customer": {
            "id": 8396625493,
            "descriptive_name": "Owner account",
            "status": E("ENABLED"),
            "currency_code": "CAD",
            "time_zone": "America/Toronto",
            "auto_tagging_enabled": True,
            "manager": False,
            "test_account": False,
            "contains_eu_political_advertising": E(
                "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
            ),
            "pay_per_conversion_eligibility_failure_reasons": [
                E("NOT_ENOUGH_CONVERSIONS")
            ],
            "conversion_tracking_setting": {
                "conversion_tracking_id": 123,
                "cross_account_conversion_tracking_id": 0,
                "conversion_tracking_status": E(
                    "CONVERSION_TRACKING_MANAGED_BY_SELF"
                ),
                "accepted_customer_data_terms": True,
                "google_ads_conversion_customer": "customers/8396625493",
            },
        }
    }
)


def verification_entry(status):
    return obj(
        {
            "verification_program": E("ADVERTISER_IDENTITY_VERIFICATION"),
            "verification_progress": {
                "program_status": E(status),
                "action_url": "https://ads.google.com/verify",
                "invitation_link_expiration_time": "2026-12-01 00:00:00",
            },
            "identity_verification_requirement": {
                "verification_start_deadline_time": "2026-11-20 00:00:00",
                "verification_completion_deadline_time": "",
            },
        }
    )


class RealFailure(Exception):
    """A GoogleAdsException's shape with a real v26 GoogleAdsFailure (the error_code oneof)."""

    def __init__(self, code):
        super().__init__("refused")
        self.failure = errors.GoogleAdsFailure(
            errors=[
                errors.GoogleAdsError(
                    error_code=errors.ErrorCode(
                        identity_verification_error=code
                    ),
                    message="Billing is not on monthly invoicing.",
                )
            ]
        )


class AccountTest(unittest.TestCase):
    def test_account(self):
        a = acc.account(ROW)
        self.assertEqual(a["customer_id"], "8396625493")
        self.assertEqual(a["currency"], "CAD")
        self.assertTrue(a["auto_tagging"])
        self.assertEqual(
            a["conversion_tracking"]["status"],
            "CONVERSION_TRACKING_MANAGED_BY_SELF",
        )
        self.assertIsNone(
            a["conversion_tracking"]["cross_account_conversion_tracking_id"]
        )
        self.assertEqual(
            a["pay_per_conversion_ineligible_because"],
            ["NOT_ENOUGH_CONVERSIONS"],
        )

    def test_verification(self):
        cases = [
            (obj({"identity_verification": []}), "not_enrolled"),
            (
                obj({"identity_verification": [verification_entry("SUCCESS")]}),
                "SUCCESS",
            ),
            (
                obj(
                    {
                        "identity_verification": [
                            verification_entry("PENDING_USER_ACTION")
                        ]
                    }
                ),
                "PENDING_USER_ACTION",
            ),
        ]
        for response, want in cases:
            with self.subTest(want=want):
                self.assertEqual(
                    acc.verification(response)["verification"], want
                )
        pending = acc.verification(
            obj(
                {
                    "identity_verification": [
                        verification_entry("PENDING_USER_ACTION")
                    ]
                }
            )
        )
        self.assertEqual(
            pending["programs"][0]["start_deadline"], "2026-11-20 00:00:00"
        )
        self.assertIsNone(pending["programs"][0]["completion_deadline"])

    def test_refusals(self):
        cases = [
            (
                GoogleError(
                    "identity_verification_error",
                    "BILLING_NOT_ON_MONTHLY_INVOICING",
                ),
                "not_readable_via_api",
            ),
            (
                GoogleError(
                    "identity_verification_error", "NO_EFFECTIVE_BILLING"
                ),
                "not_readable_via_api",
            ),
            (
                RealFailure(IVE.BILLING_NOT_ON_MONTHLY_INVOICING),
                "not_readable_via_api",
            ),
            (
                GoogleError("authorization_error", "USER_PERMISSION_DENIED"),
                "unknown",
            ),
            (TimeoutError("slow"), "unknown"),
        ]
        for exc, want in cases:
            with self.subTest(exc=repr(exc)):
                out = acc.verification_error(exc)
                self.assertEqual(out["verification"], want)
        billing = acc.verification_error(
            RealFailure(IVE.BILLING_NOT_ON_MONTHLY_INVOICING)
        )
        self.assertTrue(
            billing["reason"].startswith("BILLING_NOT_ON_MONTHLY_INVOICING")
        )
        self.assertIn(
            "says nothing about whether the account is verified",
            billing["reason"],
        )


class AccountsGetToolTest(unittest.TestCase):
    def test_tool(self):
        with (
            patch.object(tools, "_search", return_value=[ROW]) as search,
            patch.object(
                tools,
                "_identity",
                side_effect=GoogleError(
                    "identity_verification_error",
                    "BILLING_NOT_ON_MONTHLY_INVOICING",
                ),
            ),
        ):
            out = tools.get(
                customer_id="839-662-5493", login_customer_id="8868324827"
            )
        self.assertEqual(search.call_args.args[0], "8396625493")
        self.assertEqual(out.account["time_zone"], "America/Toronto")
        self.assertEqual(
            out.identity_verification["verification"], "not_readable_via_api"
        )

    def test_without_verification(self):
        with (
            patch.object(tools, "_search", return_value=[ROW]),
            patch.object(tools, "_identity") as identity,
        ):
            out = tools.get(
                customer_id="8396625493", identity_verification=False
            )
        identity.assert_not_called()
        self.assertIsNone(out.identity_verification)


if __name__ == "__main__":
    unittest.main()
