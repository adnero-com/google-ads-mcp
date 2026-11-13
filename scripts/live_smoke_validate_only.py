#!/usr/bin/env python
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

"""Live smoke of the v2 tools: validate_only ONLY, one fixed account. NOT run in CI.

    GOOGLE_ADS_ACCESS_TOKEN=<oauth access token with the adwords scope> \
    SMOKE_APP_ID=com.example.app \
    [SMOKE_IMAGE_ASSET_IDS=1,2] [SMOKE_VIDEO_ASSET_IDS=3] [SMOKE_CONVERSION_ACTION_IDS=4] \
    python scripts/live_smoke_validate_only.py

Customer 8396625493 through manager 8868324827, hard-coded: the script refuses any other account. Every
write is a preview (Google's validate_only): the approval gate is replaced by one that raises if anything
tries to apply, so nothing can change in the account. The token is read from the environment and is never
printed or logged. Output: one line per step with its status and Google's error codes.
"""

import json
import os
import sys
import traceback

CUSTOMER = "8396625493"
LOGIN = "8868324827"


def main() -> int:
    token = os.environ.get("GOOGLE_ADS_ACCESS_TOKEN", "")
    if not token:
        print("GOOGLE_ADS_ACCESS_TOKEN is not set; nothing ran.")
        return 2
    os.environ.pop("ADNERO_MULTI_TENANT", None)
    os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = LOGIN

    from google.oauth2.credentials import Credentials

    from ads_mcp import adnero_mutate, utils

    utils._create_credentials = lambda: Credentials(token=token)
    real_gate = adnero_mutate.gate

    def preview_only(**kwargs):
        if kwargs.get("validate_only") is not True:
            raise SystemExit(
                "refused: the smoke script only previews (validate_only=true)"
            )
        return real_gate(**kwargs)

    adnero_mutate.gate = preview_only

    from ads_mcp.tools import (
        adnero_app,
        adnero_app_create,
        adnero_assets_list,
        search,
    )

    ids = lambda name: [
        v for v in os.environ.get(name, "").split(",") if v
    ]  # noqa: E731
    app_id = os.environ.get("SMOKE_APP_ID", "com.example.app")
    common = dict(
        customer_id=CUSTOMER,
        login_customer_id=LOGIN,
        kind="install",
        app_id=app_id,
        store="GOOGLE_APP_STORE",
        goal="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
        target_cpa_micros=1_000_000,
        conversion_action_ids=ids("SMOKE_CONVERSION_ACTION_IDS"),
        geo_target_ids=["2124"],
        language_ids=["1000"],
        headlines=["Smoke test only", "Never applied"],
        descriptions=["validate_only preview from the MCP smoke script."],
        image_asset_ids=ids("SMOKE_IMAGE_ASSET_IDS"),
        youtube_video_asset_ids=ids("SMOKE_VIDEO_ASSET_IDS"),
    )
    steps = [
        (
            "search (GAQL string, enums as names)",
            lambda: search.search(
                customer_id=CUSTOMER,
                login_customer_id=LOGIN,
                query="SELECT customer.id, customer.currency_code, customer.status FROM customer",
                max_rows=1,
            ).rows,
        ),
        (
            "search (bad field -> structured error)",
            lambda: search.search(
                customer_id=CUSTOMER,
                login_customer_id=LOGIN,
                query="SELECT campaign.start_date FROM campaign",
            ),
        ),
        (
            "assets_list",
            lambda: [
                (a.id, a.type, a.ratio, len(a.used_in))
                for a in adnero_assets_list.list_assets(
                    customer_id=CUSTOMER, login_customer_id=LOGIN, max_rows=5
                ).assets
            ],
        ),
        (
            "app_check",
            lambda: adnero_app.check(**{k: v for k, v in common.items()}),
        ),
        (
            "app_campaign_create (validate_only)",
            lambda: adnero_app_create.campaign_create(
                **common,
                name="SMOKE validate_only (never applied)",
                daily_budget_micros=1_000_000,
                validate_only=True,
            ).status,
        ),
    ]
    failures = 0
    for name, run in steps:
        try:
            out = run()
            text = (
                out.model_dump_json()
                if hasattr(out, "model_dump_json")
                else json.dumps(out, default=str)
            )
            print(f"OK    {name}: {text[:400]}")
        except SystemExit:
            raise
        except (
            Exception
        ) as exc:  # report and go on: a refusal with codes is a result too
            failures += 1
            first = str(exc).splitlines()[:3]
            print(
                f"ERROR {name}: {type(exc).__name__}: {' | '.join(first)[:600]}"
            )
            if os.environ.get("SMOKE_DEBUG"):
                traceback.print_exc()
    return 1 if failures > 1 else 0  # the bad-field step is expected to error


if __name__ == "__main__":
    sys.exit(main())
