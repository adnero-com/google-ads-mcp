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

"""Multi-tenant mode for the AdHive gateway (adnero fork; see ADNERO.md).

With ``ADNERO_MULTI_TENANT=1`` the server serves Streamable HTTP behind our MCP
gateway only. Every request carries the caller's credential as headers the
gateway sets from its authorization answer:

- ``x-google-ads-mcp-secret``: the gateway's shared key
  (``GOOGLE_ADS_MCP_SHARED_SECRET``). Missing or wrong: 401. Unset in the
  environment: every request is refused (fails closed).
- ``x-google-ads-access-token``: a short-lived OAuth access token for the
  workspace's Google login (minted by the AdHive api; never stored here).
- ``x-google-ads-login-customer-id``: the manager (MCC) account the login
  chose, when one. It wins over any ``login_customer_id`` argument.
- ``x-google-ads-ops-left``: the Cloud project's operations left today, as
  ``<left>/<limit>``; echoed at the end of every tool result.

No Application Default Credentials, no developer token, no OAuth proxy, no
token storage: the container holds no tenant secret.
"""

import hmac
import logging
import os
import re

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from mcp.types import TextContent

MULTI_TENANT_ENV = "ADNERO_MULTI_TENANT"
SHARED_SECRET_ENV = "GOOGLE_ADS_MCP_SHARED_SECRET"
API_ENDPOINT_ENV = "GOOGLE_ADS_API_ENDPOINT"

SECRET_HEADER = "x-google-ads-mcp-secret"
TOKEN_HEADER = "x-google-ads-access-token"
LOGIN_CUSTOMER_HEADER = "x-google-ads-login-customer-id"
OPS_LEFT_HEADER = "x-google-ads-ops-left"

HEALTH_PATH = "/healthz"

_OPS_LEFT = re.compile(r"^(\d+)/(\d+)$")


def enabled() -> bool:
    """True when the server runs behind the AdHive gateway."""
    return os.environ.get(MULTI_TENANT_ENV) == "1"


def api_endpoint() -> str | None:
    """A stub Google Ads API (tests, e2e); unset: the real API."""
    value = os.environ.get(API_ENDPOINT_ENV, "").strip()
    return value or None


def _header(name: str) -> str:
    return get_http_headers(include={name}).get(name, "").strip()


def request_credentials():
    """The caller's credential for this request; refuses a call without one."""
    from google.oauth2.credentials import Credentials

    token = _header(TOKEN_HEADER)
    if not token:
        raise ToolError(
            "No Google login reaches this call. Connect Google in AdHive "
            "(Connections) and pick the ads account, then try again."
        )
    return Credentials(token=token)


def login_customer_id() -> str | None:
    """The manager account the gateway set for this request, digits only."""
    value = re.sub(r"\D", "", _header(LOGIN_CUSTOMER_HEADER))
    return value or None


def ops_left_note() -> str | None:
    """'Google Ads operations left today: N of M', from the gateway's header."""
    match = _OPS_LEFT.match(_header(OPS_LEFT_HEADER))
    if not match:
        return None
    left, limit = int(match.group(1)), int(match.group(2))
    return f"Google Ads operations left today: {left:,} of {limit:,}."


def secret_ok(provided: str | None) -> bool:
    """Constant-time check of the gateway's key; no key configured: never."""
    expected = os.environ.get(SHARED_SECRET_ENV, "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    )


class SharedSecretMiddleware:
    """ASGI: only the gateway may call (health checks excepted)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        if secret_ok(headers.get(SECRET_HEADER)):
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":"unauthorized"}',
            }
        )


class OpsLeftMiddleware(Middleware):
    """Ends every tool result with the operations left today, when known."""

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        note = ops_left_note()
        # An input_required leg carries no content by contract (SEP-2322).
        if note is None or type(result).__name__ == "InputRequiredToolResult":
            return result
        return result.model_copy(
            update={
                "content": [
                    *result.content,
                    TextContent(type="text", text=note),
                ]
            }
        )


INSTRUCTIONS = """Google Ads, through the person's Google login in AdHive (their manager account, MCC).

Start with accounts_list_clients: it lists the accounts under the manager; every other tool takes one of them as
customer_id. Money is in micros of the account currency (10 EUR = 10000000).

Read with search_search (GAQL; metadata_get_resource_metadata lists a resource's fields). One search is ONE
operation however many rows it returns, and this app has a daily operations budget (each result ends with what is
left): prefer one search over many small ones. Ready queries:
- campaigns: SELECT campaign.id, campaign.name, campaign.status, campaign_budget.amount_micros, metrics.cost_micros,
  metrics.conversions FROM campaign WHERE segments.date DURING LAST_7_DAYS
- ad groups: SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.cpc_bid_micros, campaign.id FROM ad_group
- ads: SELECT ad_group_ad.ad.id, ad_group_ad.status, ad_group.id, ad_group_ad.ad.final_urls FROM ad_group_ad
- keywords: SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text,
  ad_group_criterion.keyword.match_type, ad_group_criterion.status, ad_group.id FROM keyword_view
- assets: SELECT asset.id, asset.name, asset.type FROM asset
- conversions: SELECT conversion_action.id, conversion_action.name, conversion_action.status FROM conversion_action

Change with the campaigns_* and keywords_* tools. Every change is previewed first (validate_only=true: Google
checks it, nothing changes) and applied only after the person approves (validate_only=false with the checksum).
New campaigns, ad groups and ads are always created PAUSED; turning them on is campaigns_set_status. Ads and
keywords are named 'adGroupId~id'."""


def build_app(server):
    """The ASGI app: stateless Streamable HTTP at /mcp, /healthz, the key check."""
    from starlette.middleware import Middleware as AsgiMiddleware
    from starlette.responses import JSONResponse

    @server.custom_route(HEALTH_PATH, methods=["GET"])
    async def _healthz(_request):
        # Healthy either way, so a stack without the key still deploys; "configured" false means every
        # tool call is refused until GOOGLE_ADS_MCP_SHARED_SECRET is set (the gateway never offers the
        # target to a workspace without a Google login anyway).
        return JSONResponse(
            {"ok": True, "configured": bool(os.environ.get(SHARED_SECRET_ENV))}
        )

    if not getattr(server, "instructions", None):
        server.instructions = INSTRUCTIONS
    server.add_middleware(OpsLeftMiddleware())
    return server.http_app(
        path="/mcp",
        stateless_http=True,
        middleware=[AsgiMiddleware(SharedSecretMiddleware)],
    )


def serve(server) -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    if not os.environ.get(SHARED_SECRET_ENV):
        logging.getLogger(__name__).warning(
            "%s is not set: not configured, every tool call is refused",
            SHARED_SECRET_ENV,
        )
    uvicorn.run(build_app(server), host="0.0.0.0", port=port, access_log=False)
