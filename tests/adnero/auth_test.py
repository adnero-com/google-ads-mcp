"""The adnero multi-tenant mode: the gateway's key, per-request credentials, the tool set."""

import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from ads_mcp import adnero_auth, utils
from ads_mcp.tools import adnero_accounts

CONFIG = os.path.join(
    os.path.dirname(adnero_auth.__file__), "adnero_tools_config.yaml"
)

# Every tool the adnero build lists. AdHive's gateway table must classify each
# (packages/mcp-gateway); change both together.
EXPECTED_TOOLS = sorted(
    [
        "accounts_get",
        "accounts_list_accessible",
        "accounts_list_clients",
        "api_mutate",
        "api_read",
        "app_ad_group_create",
        "app_ad_update",
        "app_campaign_create",
        "app_campaign_update",
        "app_check",
        "app_report",
        "assets_add_youtube",
        "assets_list",
        "assets_upload_html5",
        "assets_upload_image",
        "assets_create_asset",
        "campaigns_create",
        "campaigns_create_ad",
        "campaigns_create_ad_group",
        "campaigns_set_bids",
        "campaigns_set_budget",
        "campaigns_set_status",
        "customers_list_accessible_customers",
        "keywords_add",
        "keywords_remove",
        "labels_manage_label_associations",
        "labels_manage_labels",
        "lookup_geo",
        "lookup_language",
        "metadata_get_resource_metadata",
        "policy_report",
        "resolve",
        "schema_check_query",
        "schema_get_enum",
        "schema_get_mutate",
        "schema_get_resource",
        "schema_list_resources",
        "search_search",
    ]
)


def headers(**values):
    return patch(
        "ads_mcp.adnero_auth.get_http_headers",
        return_value={k.replace("_", "-"): v for k, v in values.items()},
    )


class MultiTenantTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {
                "ADNERO_MULTI_TENANT": "1",
                "GOOGLE_ADS_MCP_SHARED_SECRET": "k" * 32,
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def test_secret(self):
        self.assertTrue(adnero_auth.secret_ok("k" * 32))
        self.assertFalse(adnero_auth.secret_ok("k" * 31))
        self.assertFalse(adnero_auth.secret_ok(None))
        with patch.dict(os.environ, {"GOOGLE_ADS_MCP_SHARED_SECRET": ""}):
            self.assertFalse(adnero_auth.secret_ok(""))

    def test_credentials_come_from_the_request(self):
        with headers(
            x_google_ads_mcp_secret="k" * 32,
            x_google_ads_access_token="ya29.abc",
        ):
            creds = utils._create_credentials()
        self.assertEqual(creds.token, "ya29.abc")

    def test_no_token_refuses_never_adc(self):
        with headers():
            with patch("google.auth.default") as adc:
                with self.assertRaises(ToolError):
                    utils._create_credentials()
                adc.assert_not_called()

    def test_gateway_manager_wins_over_the_argument(self):
        with headers(x_google_ads_login_customer_id="111-222-3333"):
            self.assertEqual(utils._get_login_customer_id("999"), "1112223333")
        with headers():
            self.assertEqual(utils._get_login_customer_id("999"), "999")
            self.assertIsNone(utils._get_login_customer_id(None))

    def test_ops_left_note(self):
        with headers(x_google_ads_ops_left="2700/2880"):
            self.assertEqual(
                adnero_auth.ops_left_note(),
                "Google Ads operations left today: 2,700 of 2,880.",
            )
        with headers(x_google_ads_ops_left="lots"):
            self.assertIsNone(adnero_auth.ops_left_note())

    def test_http_needs_the_gateway_key(self):
        app = adnero_auth.build_app(FastMCP("t"))
        with TestClient(app) as http:
            health = http.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(
                health.json(),
                {"ok": True, "configured": True, "bearer": False},
            )
            body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            accept = {"accept": "application/json, text/event-stream"}
            self.assertEqual(
                http.post("/mcp", json=body, headers=accept).status_code, 401
            )
            ok = http.post(
                "/mcp",
                json=body,
                headers={**accept, "x-google-ads-mcp-secret": "k" * 32},
            )
            self.assertNotEqual(ok.status_code, 401)

    def test_list_clients_uses_the_connection_manager(self):
        rows = [
            SimpleNamespace(
                customer_client=SimpleNamespace(
                    id=level * 10 + 1,
                    descriptive_name=f"acct {level}",
                    currency_code="EUR",
                    time_zone="Europe/Berlin",
                    manager=False,
                    status=SimpleNamespace(name="ENABLED"),
                    level=level,
                    hidden=hidden,
                )
            )
            for level, hidden in ((0, False), (1, False), (1, True))
        ]
        with headers(x_google_ads_login_customer_id="5550001111"):
            with patch(
                "ads_mcp.adnero_mutate.search_rows", return_value=rows
            ) as s:
                out = adnero_accounts.list_clients()
        self.assertEqual(s.call_args.args[0], "5550001111")
        self.assertEqual([a.customer_id for a in out.accounts], ["11"])
        self.assertIsNone(out.next_page_token)


class ToolSetTest(unittest.TestCase):
    def test_the_adnero_tool_set(self):
        # A fresh interpreter: mounting removes disabled tools from the shared
        # sub-servers, which upstream's own tests must not see.
        script = (
            "import asyncio, json; from ads_mcp.coordinator import mcp; "
            "print(json.dumps([{'name': t.name, 'title': t.title, "
            "'annotations': t.annotations.model_dump(exclude_none=True, by_alias=True) if t.annotations else {}, "
            "'outputSchema': t.output_schema, 'inputSchema': t.parameters} "
            "for t in asyncio.run(mcp.list_tools())]))"
        )
        env = {**os.environ, "GOOGLE_ADS_MCP_TOOLS_CONFIG": CONFIG}
        env.pop("ADNERO_MULTI_TENANT", None)
        out = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        tools = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(sorted(t["name"] for t in tools), EXPECTED_TOOLS)
        ours = [
            t
            for t in tools
            if t["name"].split("_")[0]
            in (
                "accounts",
                "api",
                "app",
                "campaigns",
                "keywords",
                "lookup",
                "policy",
                "resolve",
                "schema",
            )
            or t["name"].startswith(
                ("assets_upload_", "assets_add_", "assets_list")
            )
        ]
        for t in ours:
            # The quality bar (ADR 0014): a title, all four hints, an output schema.
            self.assertTrue(t["title"], t["name"])
            hints = t["annotations"]
            for h in (
                "readOnlyHint",
                "destructiveHint",
                "idempotentHint",
                "openWorldHint",
            ):
                self.assertIn(h, hints, t["name"])
            self.assertTrue(t["outputSchema"], t["name"])
            self.assertNotIn("ctx", t["inputSchema"]["properties"], t["name"])


if __name__ == "__main__":
    unittest.main()
