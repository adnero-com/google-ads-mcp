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

Bearer mode (second way in, ``GOOGLE_ADS_MCP_PUBLIC_URL`` set): a remote MCP server with standard MCP OAuth.
A request without the gateway's key may carry ``Authorization: Bearer <Google access token>``; the token is
checked with Google's tokeninfo (the adwords scope, and a Cloud project listed in
``GOOGLE_ADS_MCP_ALLOWED_PROJECTS``, required: unset, every token is refused) and is the token the tools use. A refused request gets a 401 with
``WWW-Authenticate: Bearer resource_metadata=...`` pointing at ``/.well-known/oauth-protected-resource``.

No Application Default Credentials, no developer token, no OAuth proxy, no
token storage: the container holds no tenant secret.
"""

import asyncio
import collections
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from mcp.types import TextContent

from ads_mcp.api_version import API_VERSION  # noqa: E402

MULTI_TENANT_ENV = "ADNERO_MULTI_TENANT"
SHARED_SECRET_ENV = "GOOGLE_ADS_MCP_SHARED_SECRET"
API_ENDPOINT_ENV = "GOOGLE_ADS_API_ENDPOINT"
PUBLIC_URL_ENV = "GOOGLE_ADS_MCP_PUBLIC_URL"
ALLOWED_PROJECTS_ENV = "GOOGLE_ADS_MCP_ALLOWED_PROJECTS"
TOKENINFO_URL_ENV = "GOOGLE_ADS_TOKENINFO_URL"
API_VERSION_ENV = "GOOGLE_ADS_API_VERSION"

# Kept for callers that read it; the one source is ads_mcp.api_version.API_VERSION.
DEFAULT_API_VERSION = API_VERSION

DEFAULT_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"
TOKENINFO_TIMEOUT_S = 5
POSITIVE_TTL_MAX_S = 3600
NEGATIVE_TTL_S = 60
CACHE_MAX = 10_000

SECRET_HEADER = "x-google-ads-mcp-secret"
TOKEN_HEADER = "x-google-ads-access-token"
LOGIN_CUSTOMER_HEADER = "x-google-ads-login-customer-id"
OPS_LEFT_HEADER = "x-google-ads-ops-left"

HEALTH_PATH = "/healthz"
RESOURCE_METADATA_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
)

_OPS_LEFT = re.compile(r"^(\d+)/(\d+)$")


def enabled() -> bool:
    """True when the server runs behind the AdHive gateway."""
    return os.environ.get(MULTI_TENANT_ENV) == "1"


def api_endpoint() -> str | None:
    """A stub Google Ads API (tests, e2e); unset: the real API."""
    value = os.environ.get(API_ENDPOINT_ENV, "").strip()
    return value or None


def api_version() -> str:
    """The Google Ads API version every client, REST call and schema uses: ads_mcp.api_version.API_VERSION.

    There is no env override: the code, the client lib pin and the checked-in schemas move together. A
    GOOGLE_ADS_API_VERSION naming another version is ignored with a warning (it used to switch versions).
    """
    value = os.environ.get(API_VERSION_ENV, "").strip()
    if value and value != API_VERSION:
        logging.getLogger(__name__).warning(
            "%s=%r is ignored: this build speaks %s only",
            API_VERSION_ENV,
            value[:20],
            API_VERSION,
        )
    return API_VERSION


def check_api_version_env() -> None:
    """Startup: a GOOGLE_ADS_API_VERSION naming another version than this build speaks stops the server.

    The code, the client lib pin and the checked-in schemas move together, so an env override can only be a
    mistake (a deployment still expecting the old switch).
    """
    value = os.environ.get(API_VERSION_ENV, "").strip()
    if value and value != API_VERSION:
        raise RuntimeError(
            f"{API_VERSION_ENV}={value[:20]!r} but this build speaks {API_VERSION} only: unset it, or deploy a "
            "build for that version."
        )


def _header(name: str) -> str:
    return get_http_headers(include={name}).get(name, "").strip()


def _bearer_of(authorization: str | None) -> str:
    """The token of an 'Authorization: Bearer <token>' value, else ''."""
    parts = (authorization or "").strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def request_credentials():
    """The caller's credential for this request; refuses a call without one.

    A valid gateway key makes the access-token header the Google token; otherwise it is the bearer
    token (already checked by the middleware).
    """
    from google.oauth2.credentials import Credentials

    if secret_ok(_header(SECRET_HEADER)):
        token = _header(TOKEN_HEADER)
    else:
        token = _bearer_of(_header("authorization")) if bearer_enabled() else ""
    if not token:
        raise ToolError(
            "No Google login reaches this call. Connect Google in AdHive "
            "(Connections) and pick the ads account, or sign in with Google "
            "when you connect to this server, then try again."
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


def public_url() -> str | None:
    """The public https URL of /mcp; set: bearer mode is on."""
    value = os.environ.get(PUBLIC_URL_ENV, "").strip()
    return value or None


def bearer_enabled() -> bool:
    return public_url() is not None


def resource_metadata_url(url: str) -> str:
    """origin + prefix + /.well-known/oauth-protected-resource (the last path segment is replaced)."""
    parts = urllib.parse.urlsplit(url)
    prefix = parts.path.rstrip("/").rsplit("/", 1)[0]
    return (
        f"{parts.scheme}://{parts.netloc}{prefix}{RESOURCE_METADATA_PATHS[0]}"
    )


def resource_metadata() -> dict:
    return {
        "resource": public_url(),
        "authorization_servers": ["https://accounts.google.com"],
        "scopes_supported": [ADWORDS_SCOPE],
        "bearer_methods_supported": ["header"],
    }


def _allowed_projects() -> list[str]:
    raw = os.environ.get(ALLOWED_PROJECTS_ENV, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _now() -> float:
    return time.time()


# sha256(token) -> (valid, expires at). Never the token itself. Oldest entry out when full.
_CACHE: "collections.OrderedDict[str, tuple[bool, float]]" = (
    collections.OrderedDict()
)
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> bool | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if entry[1] <= _now():
            del _CACHE[key]
            return None
        return entry[0]


def _cache_put(key: str, valid: bool, ttl: float) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        _CACHE[key] = (valid, _now() + ttl)
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)


def _tokeninfo(token: str) -> tuple[int, dict]:
    """(HTTP status, JSON body) of Google's tokeninfo; status 0 when unreachable. Never logs the token."""
    base = (
        os.environ.get(TOKENINFO_URL_ENV, "").strip() or DEFAULT_TOKENINFO_URL
    )
    url = f"{base}?{urllib.parse.urlencode({'access_token': token})}"
    try:
        with urllib.request.urlopen(url, timeout=TOKENINFO_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, b""
    except (
        Exception
    ):  # unreachable, timeout: the text could echo the URL, so say nothing of it
        logging.getLogger(__name__).warning("tokeninfo unreachable")
        return 0, {}
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}


def _judge(status: int, body: dict) -> bool:
    if status != 200:
        return False
    if ADWORDS_SCOPE not in str(body.get("scope", "")).split():
        return False
    # Required: only our Cloud projects' tokens (none listed: none accepted).
    aud = str(body.get("aud", ""))
    return any(aud.startswith(f"{p}-") for p in _allowed_projects())


def token_ok(token: str | None) -> bool:
    """A Google access token with the adwords scope, from one of the allowed Cloud projects (required: unset, none)."""
    if not token or not _allowed_projects():
        return False
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached
    status, body = _tokeninfo(token)
    if status == 0 or status >= 500:
        return False  # Google unreachable or failing: refuse, but do not remember it
    valid = _judge(status, body)
    if valid:
        try:
            left = float(body.get("exp", 0)) - _now()
        except (TypeError, ValueError):
            left = 0.0
        ttl = min(left, POSITIVE_TTL_MAX_S)
        if ttl > 0:
            _cache_put(key, True, ttl)
    else:
        _cache_put(key, False, NEGATIVE_TTL_S)
    return valid


class SharedSecretMiddleware:
    """ASGI: the gateway's key, or (bearer mode) a valid Google bearer token (health checks excepted)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path")
        if (
            scope["type"] != "http"
            or path == HEALTH_PATH
            or (bearer_enabled() and path in RESOURCE_METADATA_PATHS)
        ):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        if secret_ok(headers.get(SECRET_HEADER)):
            await self.app(scope, receive, send)
            return
        if bearer_enabled():
            token = _bearer_of(headers.get("authorization"))
            if token and await asyncio.to_thread(token_ok, token):
                await self.app(scope, receive, send)
                return
        response_headers = [(b"content-type", b"application/json")]
        url = public_url()
        if url:
            challenge = (
                f'Bearer resource_metadata="{resource_metadata_url(url)}"'
            )
            response_headers.append(
                (b"www-authenticate", challenge.encode("latin-1"))
            )
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": response_headers,
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

Start with accounts_list_accessible when no manager account is known: it lists every account this login can reach,
and which ones work. Then accounts_list_clients lists the accounts under a manager; every other tool takes one of
them as customer_id. When an account is reached through a manager, pass that manager as login_customer_id (every
tool takes it; the connection's own manager wins when it has one). Money is in micros of the account currency (10 EUR = 10000000).

Read with search_search: pass `query` (a GAQL string) or `resource` + `fields` (+ `conditions`);
metadata_get_resource_metadata lists a resource's fields. One search is ONE operation; an answer holds at most
`max_rows` rows (default 500) and about 200 KB, and `next_page_token` fetches the rest. This app has a daily
operations budget (each result ends with what is left): prefer one search over many small ones. Enums come back as
names. Ready queries (pass each as `query`):
- campaigns: SELECT campaign.id, campaign.name, campaign.status, campaign_budget.amount_micros, metrics.cost_micros,
  metrics.conversions FROM campaign WHERE segments.date DURING LAST_7_DAYS
- ad groups: SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.cpc_bid_micros, campaign.id FROM ad_group
- ads: SELECT ad_group_ad.ad.id, ad_group_ad.status, ad_group.id, ad_group_ad.ad.final_urls FROM ad_group_ad
- keywords: SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text,
  ad_group_criterion.keyword.match_type, ad_group_criterion.status, ad_group.id FROM keyword_view
- assets: SELECT asset.id, asset.name, asset.type FROM asset
- conversions: SELECT conversion_action.id, conversion_action.name, conversion_action.status FROM conversion_action

Errors come back with Google's code name, field path, trigger and a fix hint when one is known (errors_json).
Policy findings list their topics and whether a person may request an exemption: never request one on your own.

App campaigns: check the plan with app_check first (goal, conversion actions, texts, assets: problems with
fixes; changes nothing). Images go up with assets_upload_image (an https URL; it must be 1:1, 1.91:1 or 4:5: other
ratios are refused before Google), HTML5 zips with assets_upload_html5, YouTube videos with assets_add_youtube;
assets_list shows sizes, ratio class and where each asset is used. app_campaign_create builds the whole campaign
in one atomic request (budget, PAUSED campaign, targeting, ad group, App ad); app_ad_group_create adds an ad group
(PAUSED) with its ad; app_ad_update changes an ad's texts and assets in place; app_campaign_update changes budget,
targets, conversion actions, dates and targeting (app, store and kind never change). App ads cannot be paused:
pause the ad group or campaign.

Change with the campaigns_*, keywords_* and app_* tools. Every change is previewed first (validate_only=true:
Google checks it, nothing changes, the answer has a plan_id) and applied only after the person approves
(validate_only=false, the SAME arguments and that plan_id, within 15 minutes; the server signs plans, so they
cannot be made up, and any changed argument voids one). New campaigns and ad groups are always created PAUSED;
turning them on is campaigns_set_status. Ads and keywords are named 'adGroupId~id'.

When no typed tool covers a field, use the raw Google Ads REST API: api_read reads any resource of the account (GET
under customers/{id}, or a googleAds:search POST), and api_mutate previews a raw '<resource>:mutate' body
(validate_only=true, a plan_id), then applies it with validate_only=false and that plan_id after the person
approves. It never removes: pause instead."""


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
            {
                "ok": True,
                "configured": bool(os.environ.get(SHARED_SECRET_ENV)),
                "bearer": bearer_enabled(),
            }
        )

    if bearer_enabled():

        async def _resource_metadata(_request):
            return JSONResponse(resource_metadata())

        for path in RESOURCE_METADATA_PATHS:
            server.custom_route(path, methods=["GET"])(_resource_metadata)

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
    if not os.environ.get(SHARED_SECRET_ENV) and not bearer_enabled():
        logging.getLogger(__name__).warning(
            "%s is not set: not configured, every tool call is refused",
            SHARED_SECRET_ENV,
        )
    if bearer_enabled() and not _allowed_projects():
        logging.getLogger(__name__).warning(
            "%s is not set: bearer mode refuses every token (fails closed)",
            ALLOWED_PROJECTS_ENV,
        )
    uvicorn.run(build_app(server), host="0.0.0.0", port=port, access_log=False)
