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
- New tools, each in its own module and namespace, using propose and confirm (`validate_only` + a signed
  `plan_id`, see "Plans" below). Everything new is created PAUSED, except App ads (they cannot be paused).
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
      the operations is refused. The preview sends `"validateOnly": true` and returns a plan bound to
      `{customer_id, path, body}`, the body without the caller's `validateOnly`/`partialFailure` (they are never
      sent either: a write is atomic). Apply needs that plan (`plan_id`, or `checksum` for older clients);
      the SEP-2322 elicitation path works as for the other writes. Google's errors come back as its own message,
      status and first errorCode (e.g. `AuthorizationError.CUSTOMER_NOT_ENABLED`).
- API version: ONE constant, `ads_mcp/api_version.py` `API_VERSION = "v26"`. Every `GoogleAdsClient`, every
  `get_service` / `get_type` call (`version=API_VERSION`, guarded by `tests/core/version_test.py`), the REST
  passthrough and the discovery resource use it; `adnero_auth.api_version()` returns it. There is no env override
  any more: a `GOOGLE_ADS_API_VERSION` naming another version stops the server at startup
  (`adnero_auth.check_api_version_env`). Minor releases (v26.2) are served on the major's
  endpoint. Bump the constant, the `google-ads>=35.1.0,<36` pin and the checked-in schemas together.
- Pure core (`ads_mcp/core/`): no I/O, failures as `Result` values (`core/result.py`).
  - `core/plain.py`: every value a tool returns is plain data with enum NAMES at every depth (nested messages too:
    `policy_summary.policy_topic_entries[].type` is `LIMITED`, not 4) and API keys (`type`, never proto-plus's
    `type_`). int64 values (ids, micros) are JSON numbers at every depth, like a row's top-level fields, except
    when |value| >= 2**53: then they are digit strings, so no JSON client rounds them (a summed cost_micros in a
    weak currency such as VND can cross it). Clients read number-or-string. Only set fields appear: no made-up defaults. `utils.format_output_value` delegates to it.
  - `core/errors.py`: every `GoogleAdsException` becomes `{request_id, errors[]: {code, error_type, message,
    field_path, operation_index, trigger, exemptible, policy_topics, fix}}`. `utils.format_google_ads_error` renders
    one readable line per error, then `errors_json: <that JSON>`. Fix hints live in one table (`FIX_HINTS`):
    ASPECT_RATIO_NOT_ALLOWED, AD_TYPE_CANNOT_BE_PAUSED, BILLING_NOT_ON_MONTHLY_INVOICING, NO_EFFECTIVE_BILLING,
    UNRECOGNIZED_FIELD / BAD_FIELD_NAME and more. Policy findings are never auto-exempted: `exemptible` tells a
    person they may ask.
  - `core/gaql.py`: `search_search` takes a GAQL `query` OR `resource` + `fields` (+ `conditions`, `orderings`,
    `limit`), and answers one bounded page: `max_rows` (default 500, max 10,000), a ~200 KB byte cap, and
    `next_page_token` (our cursor over Google's 10,000-row pages, bound to the customer and query). A cursor
    that resumes inside a Google page needs a stable order: `ORDER BY <resource>.id` is added when that field is
    selected and nothing is ordered (quoted literals are ignored when looking for ORDER BY / LIMIT). A query
    that is unordered or selects `segments.*` (rows share an id) gets no token inside a page, only a `note`;
    Google's own page tokens still continue it page by page. One call = one
    Google page = one operation. The answer is `{rows, row_count, next_page_token, stopped_by, query}` (was a bare
    list).
- App campaign rules layer (`ads_mcp/rules/`, pure data + checks; DESIGN.md principle 1):
  - `rules/app.py`: the limits as data (2-5 headlines of 30 chars, 1-5 descriptions of 90; images JPG/PNG <=5 MB,
    1:1 (min 200x200), 1.91:1 (min 600x314), 4:5 (min 320x400); <=20 images, videos, HTML5 bundles; HTML5 zip <=5 MB
    and 512 files), the goal -> bidding table, the ad info per sub-type, the immutables, "app ads cannot be paused".
    The ratio tolerance is 0.5%, not the Help Center's 1%: Google refused 0.8% off live.
  - `rules/app_creative.py`, `app_goal.py`, `app_targeting.py`, `app_check.py`: the checks; `app_spec.py` the spec.
  - `rules/__init__.py` `rules_for(resource)`: the rules as data for the schema layer (`ad_group_ad`, `campaign`,
    `asset`, `ad_group_criterion`).
- `app_check` (namespace `app`): the pre-launch check. It reads conversion actions, the named assets and user
  lists (1-3 operations), then runs the pure check; it returns `{ok, problems[{code, field, message, fix, severity}],
  campaign_sub_type, ad_field, bidding}`. It changes nothing.
- Assets (namespace `assets`, next to upstream's):
  - `upload_image`: https URL (or base64). The fetch is SSRF-safe (`safe_fetch.py` + `core/net.py`): https only,
    the host is resolved once and every address must be public (IPv4-in-IPv6 forms judged too), the connection is
    pinned to that address with TLS verifying the host, at most 3 redirects (each re-checked), a 5 MB cap and a
    wall-clock deadline for the whole fetch (a watchdog shuts the socket down, so a drip cannot hold it). The type comes from the magic bytes (PNG/JPEG), and the size and ratio are checked before Google
    sees it. The name gets the content sha256 (8 hex) appended, so it is unique. Preview -> apply; apply fetches
    again, and the checksum binds the sha256, so a file that changed in between is refused.
  - `upload_html5`: zip checks (size, file count, an .html primary entry, `ad.orientation` meta, no http://).
  - `add_youtube`: a video id or any youtube.com / youtu.be / shorts URL.
  - `list`: sizes, ratio class, file size, and where each asset is used (ad_group_ad_asset_view, campaign_asset,
    ad_group_asset; 4 operations per page), with `unused`.
  - `upload_video_asset` (upstream) stays off in this build: it reads a local file path in stdio mode.
- Tests: `pytest` (nox runs it; it also runs the unittest classes). CI runs on PRs into `adnero/multi-tenant`
  too, without upstream's Gemini LLM job.
- `GOOGLE_ADS_REST_BASE`: the REST host for the `api` tools (default `https://googleads.googleapis.com`; tests and
  e2e point it at a stub).
- Our tools follow MCP 2026-07-28. Each one has:
  - a title and all four annotations;
  - a Pydantic output (an `outputSchema`, plus `structuredContent` with its JSON text);
  - errors that say what to fix;
  - cursors on lists.
  Writes return `MutationResult`: PROPOSED with a `plan_id`, or APPLIED with the resource names (plus `read_back`
  for App creates, and `notes`). Called with `validate_only=false` and no plan, a client that declared form
  elicitation is asked through an `input_required` result (SEP-2322). Google validates the change first, and the
  retry applies it only with the person's acceptance and a plan of ours as `requestState`. Every other client gets
  the two-step flow.
- **Plans (changed in v2; was an unkeyed sha256 "checksum").** `plan_id = p1.<expires_at>.<nonce>.<HMAC-SHA256>`,
  keyed by the server secret, over the expiry, a random nonce and the digest of `{tool, customer_id, args,
  sha256 of the serialized request actually sent}` (`core/plan.py`, `plans.py`). A model cannot mint one without
  the preview, any changed argument or request voids it, and it expires after 15 minutes. **A plan applies once**:
  the second apply of the same plan is refused before Google (creates can never be sent twice with one
  approval); each preview is a new plan. A plan Google refused on apply is released and may be retried within
  its 15 minutes; an apply with an unknown outcome (timeout) stays used. The record of applied plans is
  in-process memory, so: a plan issued before this process started is refused (a restart cannot replay one),
  and **the server refuses to start with more than one worker or replica** (`WEB_CONCURRENCY`,
  `UVICORN_WORKERS` or `GOOGLE_ADS_MCP_REPLICAS` > 1) until a shared plan store exists (none does yet). Deployers
  that scale by replicas must set `GOOGLE_ADS_MCP_REPLICAS`. `api_mutate` keeps a plan used on any failed apply
  (its REST errors are not told apart from timeouts yet). Every write goes through one gate (`adnero_mutate.gate`): the typed tools, `api_mutate`, and
  upstream's `propose_and_confirm` (labels, assets_create_asset, upload_video_asset). Compatibility: the
  answer carries the plan in both `plan_id` and `checksum`; our tools take `plan_id` and still accept `checksum`;
  upstream's tools keep their `checksum` argument, which now carries the plan. A stored old sha256 checksum is
  refused (re-preview). Secret: `GOOGLE_ADS_MCP_PLAN_SECRET` (32+ characters, the same on every worker); unset, it is derived
  from `GOOGLE_ADS_MCP_SHARED_SECRET` (HMAC with a fixed label); neither: **writes are refused** (previews too).
- App writes (namespace `app`):
  - `campaign_create`: app_check's checks (errors refuse before Google, warnings become `notes`), then ONE
    GoogleAdsService.Mutate with temp ids: budget (-1, not shared), campaign (-2, PAUSED, MULTI_CHANNEL + sub-type,
    app setting, the goal's bidding, selective optimization, pre-registration goal type, EU declaration, day
    dates), geo (+negative) and language criteria, ad group (-3, ENABLED: the campaign is the one switch), user list
    criteria, App ad (ENABLED, the sub-type's ad info). Apply asks for MUTABLE_RESOURCE and reads back
    primary_status and the ad's policy summary into `read_back`.
    EU self-declaration (`rules/eu.py`, `eu_read.py`; also Search `campaigns_create`): with no statement from
    the caller, `geo_target_constant.require_eu_political_advertising_declaration` is read for every targeted
    location. One requires it, or no location is set: refused before Google, with the fix (ask the person). None
    does: DOES_NOT_CONTAIN. The read fails: the field is left unset, so Google enforces it. app_check checks it too.
  - app_check / creates read their facts as exact lookups (`WHERE id IN (...)`, each paged to the end; whether the
    app is tracked is its own `LIMIT 1` query). A read that could not finish turns "not found" into a warning:
    nothing is refused on a cut read.
  - `ad_group_create`: ad group PAUSED + its App ad (type from the campaign), checked first.
  - `ad_update`: add/remove texts and assets of an App ad in place (ad_operation, the AdService update inside
    the one Mutate), update mask only on changed fields, before/after in change_items; the plan binds the ad's
    current content, so an ad changed since the preview is refused.
  - `campaign_update`: budget, targets, conversion actions (replace), dates, add/remove targeting; refuses
    app id, store, kind, a started start date, a target the goal does not use, pre-registration conversion actions,
    shared budgets, each naming the rule (`rules/app_update.py`).
  - `campaigns_set_status` level AD with PAUSED reads the ad types first (1 operation) and refuses App ads with
    the reason (pause the ad group or campaign).
- `scripts/live_smoke_validate_only.py`: a live smoke, NOT in CI, previews only, account 8396625493 through
  manager 8868324827 (hard-coded); it needs `GOOGLE_ADS_ACCESS_TOKEN`, never prints it, and replaces the gate with
  one that refuses any apply.
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

## Schemas (`ads_mcp/schemas/v26/`, generated)

- `ads_mcp/api_version.py` holds `API_VERSION = "v26"`, the one version constant.
- `scripts/gen_schemas.py` compiles the googleapis Google Ads protos (`resources`, `common`, `services`, `enums`) at a
  pinned commit (`GOOGLEAPIS_COMMIT`) with `grpc_tools.protoc` (`pip install .[schemas]`, build time only). It writes
  one gzip JSON file per kind plus `index.json.gz` (resources, services, mutate paths) and `manifest.json`
  (sha256 and sizes). `--check` compares without writing.
- Why the protos and not the installed package: proto-plus descriptors in `google-ads` carry no
  `google.api.field_behavior` (every FieldOptions in v26 is empty), so REQUIRED / IMMUTABLE / OUTPUT_ONLY exist only
  in googleapis.
- Each message/enum is a JSON Schema (draft 2020-12) fragment, keyed by its short name (`Campaign`,
  `Campaign.AppCampaignSetting`, `AppAdInfo`, `CampaignOperation`, enums `CampaignStatus`; kind-qualified on a
  clash: `common.Metrics`). The properties use proto3 JSON names (lowerCamelCase, as REST takes them). REQUIRED → `required`,
  OUTPUT_ONLY → `readOnly`, IMMUTABLE → `x-immutable`, oneofs → `x-oneof` (the served schemas expand them to
  `oneOf`), int64 → digit string, bytes → base64, enums → `$ref` to names (UNSPECIFIED/UNKNOWN dropped), resource
  references → `x-resource`, resource patterns → `x-pattern`, comments → `description`.
- Tests: `tests/schema/` (converter on a hand-built descriptor; data facts; `GADS_SCHEMA_REGEN=1` regenerates from
  googleapis and must match the checked-in data).

## Schema tools (namespace `schema`; `ads_mcp/tools/adnero_schema.py`)

Read only. The answers come from the checked-in schemas: one resource or operation at a time, `$defs` pruned to what
the root reaches, enums inlined as names (`x-enum` names the enum), `fields` narrows big resources to dotted
lowerCamelCase paths (oneof branches not named are pruned), `brief` drops descriptions.
- `schema_list_resources` {filter?, mutable_only?, include_services?}: one line per resource (GAQL name, description,
  REST mutate path, operations), optionally every service and its methods. No Google call.
- `schema_get_resource` {resource, fields?, brief?, query: fields|all|none}: the read schema + GoogleAdsFieldService
  metadata (`ads_mcp/schema/fieldmeta.py`: data_type, enum_values, is_repeated, selectable/filterable/sortable,
  selectable_with, attribute_resources, metrics, segments), cached per process. 1-2 Google requests the first time a
  resource is asked for, then none. Google failing leaves `query_error`, never fails the schema.
- `schema_get_mutate` {resource, operation: create|update|remove, fields?, full?, brief?}: by default a summary, one line
  per writable field (type, enum values, sub-fields; required / immutable / oneof / ref flags) plus `rules` from
  `ads_mcp/rules.rules_for(resource)` (campaign ~7 KB, ad_group_ad ~1 KB, asset ~5 KB). `fields` (dotted paths) or
  `full` gives the JSON Schema of one operation body as api_mutate takes it (output-only dropped; update:
  resourceName + updateMask required, immutables marked), `x-rules` included. No Google call.
- `schema_get_enum` {enum}: values + descriptions. No Google call.
- `schema_check_query` {query}: GAQL checked against field metadata without running it (`ads_mcp/schema/gaql.py`).
  1 Google request when a named field is not cached yet.
- `api_mutate` first puts its body in canonical REST form (`ads_mcp/schema/normalize.py`), guided by the operation's
  schema and accepting only what Google's own JSON parser accepts: snake_case keys whose camelCase name is a field
  there are renamed, int64 integers become digit strings. The answer's `next_step` starts with one line saying what
  was normalized; the checksum covers the normalized body (the snake_case and camelCase spellings of one change get
  the same checksum).
- `api_mutate` then checks the body against these schemas before Google sees it (`ads_mcp/schema/validate.py`): problems come
  back as a ToolError holding `{"error": "SCHEMA_INVALID", "problems": [{path (JSON pointer), code, message}]}`,
  codes REQUIRED, UNKNOWN_FIELD (with the camelCase name when that is the fix), ONE_OF, IMMUTABLE (in an update),
  BAD_ENUM, BAD_TYPE (int64 is a digit string), BAD_OPERATION, UNKNOWN_RESOURCE, plus:
  - FOREIGN_CUSTOMER (`schema/scope.py`): every `resourceName`, `remove`, `x-resource` reference to a customer-owned
    resource and any other `customers/<id>/...` string must start with `customers/{customer_id}/` of the call;
  - BAD_MASK / IMMUTABLE (`schema/mask.py`): every updateMask path must exist (snake_case or camelCase segments, not
    past a repeated field); a path naming an immutable field, or a message whose body sets one, is refused even when
    the body omits it. Fields in the body that the mask does not name are ignored, as Google does.
- The Discovery resource follows `API_VERSION` and is cached per process by its `revision` (re-checked daily, a
  failed re-check serves the cache).
