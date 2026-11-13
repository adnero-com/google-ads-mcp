# Copyright 2026 Adnero.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""accounts_get, pure part: the customer row and the identity verification answer, shaped."""

from typing import Any

from ads_mcp.reads.common import google_errors, name, names, why

ACCOUNT_QUERY = (
    "SELECT customer.id, customer.descriptive_name, customer.status, customer.currency_code, "
    "customer.time_zone, customer.auto_tagging_enabled, customer.manager, customer.test_account, "
    "customer.contains_eu_political_advertising, customer.has_partners_badge, "
    "customer.pay_per_conversion_eligibility_failure_reasons, "
    "customer.conversion_tracking_setting.conversion_tracking_id, "
    "customer.conversion_tracking_setting.cross_account_conversion_tracking_id, "
    "customer.conversion_tracking_setting.conversion_tracking_status, "
    "customer.conversion_tracking_setting.accepted_customer_data_terms, "
    "customer.conversion_tracking_setting.google_ads_conversion_customer "
    "FROM customer LIMIT 1"
)
# Google refuses to read verification for accounts that are not invoiced monthly: that says nothing about the
# account's verification, so it is never reported as "not verified".
NOT_READABLE = ("BILLING_NOT_ON_MONTHLY_INVOICING", "NO_EFFECTIVE_BILLING")


def account(row: Any) -> dict:
    c = row.customer
    t = c.conversion_tracking_setting
    return {
        "customer_id": str(c.id),
        "name": c.descriptive_name,
        "status": name(c.status),
        "currency": c.currency_code,
        "time_zone": c.time_zone,
        "auto_tagging": bool(c.auto_tagging_enabled),
        "manager": bool(c.manager),
        "test_account": bool(c.test_account),
        "eu_political_advertising": name(c.contains_eu_political_advertising),
        "conversion_tracking": {
            "status": name(t.conversion_tracking_status),
            "conversion_tracking_id": str(t.conversion_tracking_id or "")
            or None,
            "cross_account_conversion_tracking_id": str(
                t.cross_account_conversion_tracking_id or ""
            )
            or None,
            "google_ads_conversion_customer": t.google_ads_conversion_customer
            or None,
            "accepted_customer_data_terms": bool(
                t.accepted_customer_data_terms
            ),
        },
        "pay_per_conversion_ineligible_because": names(
            c.pay_per_conversion_eligibility_failure_reasons
        ),
    }


def verification(response: Any) -> dict:
    """GetIdentityVerification's answer: the program's status, deadlines and the link to act on."""
    entries = list(getattr(response, "identity_verification", None) or [])
    if not entries:
        return {
            "verification": "not_enrolled",
            "reason": "Google returned no verification program for this account (nothing is required now).",
        }
    programs = [
        {
            "program": name(e.verification_program),
            "status": name(e.verification_progress.program_status),
            "action_url": e.verification_progress.action_url or None,
            "link_expires": e.verification_progress.invitation_link_expiration_time
            or None,
            "start_deadline": e.identity_verification_requirement.verification_start_deadline_time
            or None,
            "completion_deadline": e.identity_verification_requirement.verification_completion_deadline_time
            or None,
        }
        for e in entries
    ]
    return {
        "verification": programs[0]["status"] or "UNKNOWN",
        "programs": programs,
    }


def verification_error(exc: Exception) -> dict:
    """A refused read: `not_readable_via_api` with Google's reason for billing refusals, else `unknown`."""
    codes = [e for e in google_errors(exc) if e["code"] in NOT_READABLE]
    if codes:
        return {
            "verification": "not_readable_via_api",
            "reason": f"{codes[0]['code']}: Google only exposes identity verification through the API for accounts "
            "on monthly invoicing. This says nothing about whether the account is verified: check Google Ads "
            "(Billing → Advertiser verification).",
        }
    return {"verification": "unknown", "reason": why(exc)}
