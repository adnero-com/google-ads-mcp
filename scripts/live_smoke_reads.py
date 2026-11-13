#!/usr/bin/env python3
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

"""Live, READ-ONLY smoke of the read tools against a real account. Never run in CI; it changes nothing.

    GOOGLE_ADS_ACCESS_TOKEN=<token with the adwords scope> \\
        python scripts/live_smoke_reads.py --customer 8396625493 --login 8868324827 --campaign 24454294245

Without GOOGLE_ADS_ACCESS_TOKEN it needs GOOGLE_APPLICATION_CREDENTIALS (ADC). The token is never printed or logged.
About 15 Google Ads operations. Expected on the owner's account: the App campaign with SOCIAL_CASINO_GAMES
(LIMITED), geo 2124 targeted and 20113 / 20121 excluded, all named.
"""

import argparse
import json
import os
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _use_token_from_env() -> None:
    token = os.environ.get("GOOGLE_ADS_ACCESS_TOKEN", "").strip()
    if not token:
        return
    from google.oauth2.credentials import Credentials

    import ads_mcp.utils as utils

    utils._create_credentials = lambda: Credentials(token=token)


def _show(label: str, call) -> bool:
    try:
        out = call()
    except (
        Exception
    ) as exc:  # report and go on: one failure must not hide the others
        print(f"FAIL {label}: {type(exc).__name__}: {str(exc)[:400]}")
        if os.environ.get("SMOKE_TRACEBACK"):
            traceback.print_exc()
        return False
    data = out.model_dump() if hasattr(out, "model_dump") else out
    text = json.dumps(data, ensure_ascii=False, default=str)
    print(f"ok   {label}: {len(text):,} chars")
    print("     " + text[:600] + ("..." if len(text) > 600 else ""))
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--customer", default="8396625493")
    parser.add_argument("--login", default="8868324827")
    parser.add_argument("--campaign", default="24454294245")
    args = parser.parse_args(argv)
    os.environ.pop(
        "ADNERO_MULTI_TENANT", None
    )  # in-process: credentials from the env token or ADC
    if not (
        os.environ.get("GOOGLE_ADS_ACCESS_TOKEN")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    ):
        print(
            "Set GOOGLE_ADS_ACCESS_TOKEN (adwords scope) or GOOGLE_APPLICATION_CREDENTIALS; nothing was called."
        )
        return 2
    _use_token_from_env()

    from ads_mcp.tools import adnero_lookup as lookup
    from ads_mcp.tools import adnero_reports as reports

    c, login, campaign = args.customer, args.login, args.campaign
    checks = [
        (
            "accounts_get",
            lambda: lookup.get(customer_id=c, login_customer_id=login),
        ),
        (
            "lookup_geo ids",
            lambda: lookup.geo(
                ids=["2124", "20113", "20121"], login_customer_id=login
            ),
        ),
        (
            "lookup_geo names",
            lambda: lookup.geo(
                names=["Ontario"], country="CA", login_customer_id=login
            ),
        ),
        (
            "lookup_language",
            lambda: lookup.language(
                customer_id=c, query="en", login_customer_id=login
            ),
        ),
        (
            "resolve",
            lambda: lookup.resolve(
                customer_id=c,
                kind="campaign",
                name="a",
                match="contains",
                login_customer_id=login,
            ),
        ),
        (
            "policy_report",
            lambda: reports.policy_report(
                customer_id=c, campaign_id=campaign, login_customer_id=login
            ),
        ),
    ] + [
        (
            f"app_report by {by}",
            lambda by=by: reports.app_report(
                customer_id=c,
                campaign_id=campaign,
                by=by,
                login_customer_id=login,
            ),
        )
        for by in (
            "day",
            "network",
            "asset",
            "combination",
            "conversion_action",
        )
    ]
    try:  # the schema layer, when this checkout has it
        from ads_mcp.tools import adnero_schema as schema

        checks.append(
            (
                "schema_check_query",
                lambda: schema.check_query(
                    "SELECT campaign.id, metrics.clicks FROM campaign WHERE segments.date DURING LAST_7_DAYS",
                    login,
                ),
            )
        )
    except ImportError:
        pass
    results = [_show(label, call) for label, call in checks]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
