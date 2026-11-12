# adnero fork of googleads/google-ads-mcp

Branch `adnero/multi-tenant`. AdHive (adnero-com/adplatform) builds it from a pinned
commit as the `google-ads-mcp` compose service, gateway target `google-ads`
(see ADR 0014 there). Upstream: https://github.com/googleads/google-ads-mcp (Apache-2.0).

## What differs from upstream

- `ads_mcp/adnero_auth.py`: with `ADNERO_MULTI_TENANT=1` the server serves
  stateless Streamable HTTP at `/mcp`, plus `/healthz`.
  - Only the gateway may call: `x-google-ads-mcp-secret` must equal
    `GOOGLE_ADS_MCP_SHARED_SECRET`. If that variable is unset, every call is refused.
  - The caller's Google access token and manager id arrive with each request
    (`x-google-ads-access-token`, `x-google-ads-login-customer-id`).
  - There is no ADC, no OAuth proxy, no token store and no developer token
    (developer tokens were sunset on 2026-09-09).
  - Tool results end with the ops left today (`x-google-ads-ops-left`).
- Bearer mode, a second way in as a remote MCP server (standard MCP OAuth). It is off unless
  `GOOGLE_ADS_MCP_PUBLIC_URL` is set; the gateway mode above is unchanged.
  - A request is accepted with the gateway key, or with `Authorization: Bearer <Google access token>`. The Google
    token the tools use is `x-google-ads-access-token` when the key was valid, otherwise the bearer token.
  - The bearer token is checked against Google's tokeninfo (5 s timeout): HTTP 200 and the
    `https://www.googleapis.com/auth/adwords` scope. Answers are cached by sha256 of the token (never the token):
    a good one until its `exp` (at most 1 h), a refused one for 60 s, at most 10k entries. A tokeninfo outage
    refuses the request and is not cached. Tokens are never logged.
  - A refused request gets a 401 with `WWW-Authenticate: Bearer resource_metadata="<origin + prefix +
    /.well-known/oauth-protected-resource>"` (the public URL with its last path segment replaced). The app itself
    sees `/mcp` and `/.well-known/...`: the reverse proxy strips the public prefix.
  - `GET /.well-known/oauth-protected-resource` (and `.../mcp`) is public and returns the resource, Google as
    authorization server and the adwords scope. `/healthz` also reports `"bearer": bool`.
  - Env: `GOOGLE_ADS_MCP_PUBLIC_URL` (e.g. `https://xhfti.com/mcp-hosted/google-ads/mcp`; unset: bearer off, no
    well-known routes), `GOOGLE_ADS_MCP_ALLOWED_PROJECTS` (REQUIRED in bearer mode: comma-separated Cloud project
    numbers; the token's `aud` client id must start with one of them followed by `-`; unset or empty: every bearer
    token is refused without asking Google, and a warning is logged at start), `GOOGLE_ADS_TOKENINFO_URL` (overrides
    `https://oauth2.googleapis.com/tokeninfo`, for tests and e2e).
- `login_customer_id`: in multi-tenant mode the header `x-google-ads-login-customer-id` wins, else the tool's
  `login_customer_id` argument (digits only), else none. Every `accounts_*`, `campaigns_*` and `keywords_*` tool
  takes it (`adnero_mutate.use_login` sets it per call).
- Hook lines in upstream files, to keep when rebasing:
  `utils._create_credentials`, `utils._get_login_customer_id`,
  `utils._get_googleads_client` (endpoint, version), `utils.get_googleads_service_from_client` (version),
  `resources/discovery.py` (version) and `server.run_server`.
- New tools, each in its own module and namespace, using upstream's propose and
  confirm (`validate_only` + `checksum`). Everything new is created PAUSED.
  - `accounts`: `list_accessible` (every account the login can reach, each marked usable or not; 1 operation per
    account, at most 50), `list_clients`
  - `campaigns`: `set_status`, `set_budget`, `set_bids`, `create`,
    `create_ad_group`, `create_ad`
  - `keywords`: `add`, `remove`
  - `api`: the raw Google Ads REST API, for fields no typed tool covers (`ads_mcp/tools/adnero_api.py`).
    Both take `customer_id` and `login_customer_id`, call
    `{GOOGLE_ADS_REST_BASE}/{GOOGLE_ADS_API_VERSION}/{path}` with `Authorization: Bearer <the request's Google
    token>` and `login-customer-id` when one resolves (header > argument), 30 s timeout, no developer token; the
    token and bodies are never logged. `path` is relative and must be `customers/{customer_id}` or under it for the
    SAME customer_id: no URL, leading slash, version segment, `..`, `?`, `#`, `%`, at most 300 characters.
    - `read` (`path`, `method` GET|POST, `body`): GET any path under the customer; POST only to
      `googleAds:search` / `googleAds:searchStream`. Returns `{status, data}`; an answer over 200,000 characters of
      JSON is cut (`truncated: true`, a hint to page or narrow). 1 operation.
    - `mutate` (`path` = `customers/{id}/<camelCaseResource>:mutate`, `body` up to 60,000 characters of JSON,
      `validate_only`, `checksum`): at most 100 operations; any `remove` key (or a status set to REMOVED) inside
      the operations is refused. The preview sends `"validateOnly": true`; the checksum is sha256 of the canonical
      JSON (sorted keys, `,`/`:` separators, UTF-8) of `{customer_id, path, body}`, the body without the caller's
      `validateOnly`/`partialFailure` (they are never sent either: a write is atomic). Apply needs that checksum;
      the SEP-2322 elicitation path works as for the other writes. Google's errors come back as its own message,
      status and first errorCode (e.g. `AuthorizationError.CUSTOMER_NOT_ENABLED`).
- API version: `GOOGLE_ADS_API_VERSION` (default `v26`, must match `^v\d{2}$`, else the default with a warning),
  read by `adnero_auth.api_version()`; every `GoogleAdsClient`/`get_service`, the REST passthrough and the
  discovery resource use it. Minor releases (v26.2) are served on the major's endpoint. `google-ads>=35.1.0,<36`.
- `GOOGLE_ADS_REST_BASE`: the REST host for the `api` tools (default `https://googleads.googleapis.com`; tests and
  e2e point it at a stub).
- Our tools follow MCP 2026-07-28. Each one has:
  - a title and all four annotations;
  - a Pydantic output (an `outputSchema`, plus `structuredContent` with its JSON text);
  - errors that say what to fix;
  - cursors on lists.
  Writes return `MutationResult`: PROPOSED with a checksum, or APPLIED with the resource names. Called with
  `validate_only=false` and no checksum, a client that declared form elicitation is asked through an
  `input_required` result (SEP-2322). Google validates the change first, and the retry applies it only with the
  person's acceptance and our checksum as `requestState`. Every other client gets the two-step flow.
- The server instructions are set in multi-tenant mode: where to start (`accounts_list_accessible` first), ready GAQL queries, the ops budget and the
  approval flow.
- `ads_mcp/adnero_tools_config.yaml` (the tool set) and `Dockerfile.adnero`.
- Tests: `tests/adnero/`.

## Staying current with upstream

    git remote add upstream https://github.com/googleads/google-ads-mcp.git
    git fetch upstream
    git checkout adnero/multi-tenant
    git rebase upstream/main          # conflicts only in the hook lines above
    python -m unittest discover -s tests -p '*_test.py'
    git push --force-with-lease origin adnero/multi-tenant

Then open a PR in adplatform that bumps the pinned sha (`docker-compose.yml`,
`docker-bake.hcl`). Its CI checks the tool list against the gateway's
classification table, so a new upstream tool is never exposed unclassified.
Rebase monthly, and on every upstream release.
