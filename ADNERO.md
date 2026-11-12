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
- Hook lines in upstream files, to keep when rebasing:
  `utils._create_credentials`, `utils._get_login_customer_id`,
  `utils._get_googleads_client` (endpoint) and `server.run_server`.
- New tools, each in its own module and namespace, using upstream's propose and
  confirm (`validate_only` + `checksum`). Everything new is created PAUSED.
  - `accounts`: `list_clients`
  - `campaigns`: `set_status`, `set_budget`, `set_bids`, `create`,
    `create_ad_group`, `create_ad`
  - `keywords`: `add`, `remove`
- Our tools follow MCP 2026-07-28. Each one has:
  - a title and all four annotations;
  - a Pydantic output (an `outputSchema`, plus `structuredContent` with its JSON text);
  - errors that say what to fix;
  - cursors on lists.
  Writes return `MutationResult`: PROPOSED with a checksum, or APPLIED with the resource names. Called with
  `validate_only=false` and no checksum, a client that declared form elicitation is asked through an
  `input_required` result (SEP-2322). Google validates the change first, and the retry applies it only with the
  person's acceptance and our checksum as `requestState`. Every other client gets the two-step flow.
- The server instructions are set in multi-tenant mode: where to start, ready GAQL queries, the ops budget and the
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
