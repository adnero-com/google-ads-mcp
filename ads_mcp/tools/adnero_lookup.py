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

"""Account, geo, language and name lookups (adnero fork; see ADNERO.md): accounts_get, lookup_geo,
lookup_language, resolve. The pure parts live in ads_mcp/reads/."""

import threading
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import adnero_auth
from ads_mcp import adnero_mutate as m
from ads_mcp.reads import account as acc
from ads_mcp.reads import google
from ads_mcp.reads import lookup as lk
from ads_mcp.reads.common import id_list
from ads_mcp.tools.adnero_accounts import accounts_mcp
import ads_mcp.utils as utils

lookup_mcp = FastMCP("lookup")
# Mounted without a prefix (adnero_tools_config.yaml: resolve: {prefix: ""}): the tool is just `resolve`.
resolve_mcp = FastMCP("resolve")

READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

# ---------------------------------------------------------------- edges (patched in tests)


def _search(customer_id: str, query: str) -> list:
    with google.refusals():  # Google's refusals come back structured
        return m.search_rows(customer_id, query)


def _identity(customer_id: str) -> Any:
    service = utils.get_googleads_service("IdentityVerificationService")
    return service.get_identity_verification(customer_id=customer_id)


def _suggest(
    names: list[str], ids: list[str], country: str | None, locale: str
) -> Any:
    service = utils.get_googleads_service("GeoTargetConstantService")
    request = utils.get_googleads_type("SuggestGeoTargetConstantsRequest")
    request.locale = locale
    if country:
        request.country_code = country
    if names:
        request.location_names.names.extend(names)
    else:
        request.geo_targets.geo_target_constants.extend(
            f"geoTargetConstants/{i}" for i in ids
        )
    with google.refusals():
        return service.suggest_geo_target_constants(request=request)


# ---------------------------------------------------------------- accounts_get


class Account(BaseModel):
    account: dict = Field(
        description="customer_id, name, status, currency (ISO 4217: money is micros of it), time_zone, auto_tagging, "
        "manager, test_account, eu_political_advertising, conversion_tracking {status, ids, accepted terms}."
    )
    identity_verification: dict | None = Field(
        default=None,
        description="verification: SUCCESS / PENDING_USER_ACTION / PENDING_REVIEW / FAILURE (with deadlines and "
        "action_url), not_enrolled, not_readable_via_api (Google refuses the read for this billing setup: it does NOT "
        "mean unverified) or unknown; reason.",
    )


@accounts_mcp.tool(
    title="One Google Ads account: status, currency, time zone, tracking, identity verification",
    annotations=READ,
)
def get(
    customer_id: str | int,
    identity_verification: bool = True,
    login_customer_id: str | int | None = None,
) -> Account:
    """Reads one account's settings: status, currency, time zone, auto-tagging, conversion tracking, EU political
    advertising declaration, and its advertiser identity verification (IdentityVerificationService).

    When Google refuses the verification read because the account is not on monthly invoicing, the answer says
    `not_readable_via_api` with the reason: never report that as "not verified". Costs 2 Google Ads operations (1
    with identity_verification=false).

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        identity_verification: Also read the identity verification (default true).
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    rows = _search(cid, acc.ACCOUNT_QUERY)
    if not rows:
        raise ToolError(f"Google returned no row for account {cid}.")
    out = Account(account=acc.account(rows[0]))
    if not identity_verification:
        return out
    try:
        verification = acc.verification(_identity(cid))
    except Exception as exc:  # a refused read never fails the account read
        verification = acc.verification_error(exc)
    return out.model_copy(update={"identity_verification": verification})


# ---------------------------------------------------------------- lookup_geo / lookup_language


class GeoTargets(BaseModel):
    geo_targets: list[dict] = Field(
        description="id (use it in location criteria: geoTargetConstants/<id>), name, canonical_name, type, "
        "country_code, status, eu_political_declaration_required, reach (name lookups), query."
    )


@lookup_mcp.tool(
    title="Find geo targets by name, or name geo target ids", annotations=READ
)
def geo(
    names: list[str] | None = None,
    ids: list[str | int] | None = None,
    country: str | None = None,
    locale: str = "en",
    login_customer_id: str | int | None = None,
) -> GeoTargets:
    """Finds Google Ads geo targets (countries, regions, cities...) by name, or turns ids into names, canonical
    names and types (GeoTargetConstantService.SuggestGeoTargetConstants).

    Pass `names` (e.g. ['Ontario', 'Toronto']) or `ids` (e.g. [2124, 20113]), at most 25. Costs 1 Google Ads
    operation.

    Args:
        names: Place names to look up (best matches first, with reach).
        ids: Geo target constant ids to name.
        country: ISO 3166-1 alpha-2 code to narrow name lookups (e.g. 'CA').
        locale: Language of the names returned (default 'en').
        login_customer_id: The manager (MCC) account to call through, if your login needs one.
    """
    m.use_login(login_customer_id)
    clean_names = [n.strip() for n in names or [] if n and n.strip()]
    clean_ids = list(id_list(ids or []))
    if bool(clean_names) == bool(clean_ids):
        raise ToolError("Error: pass either names or ids (not both).")
    if len(clean_names) > lk.MAX_GEO or len(clean_ids) > lk.MAX_GEO:
        raise ToolError(f"Error: at most {lk.MAX_GEO} names or ids per call.")
    if country and not (
        len(country.strip()) == 2 and country.strip().isalpha()
    ):
        raise ToolError("Error: country is a 2-letter code such as 'CA'.")
    response = _suggest(
        clean_names, clean_ids, (country or "").strip().upper() or None, locale
    )
    return GeoTargets(geo_targets=lk.suggestions(response))


_languages: dict[str, list[dict]] = {}
_languages_lock = threading.Lock()


class Languages(BaseModel):
    languages: list[dict] = Field(
        description="id (use it in language criteria: languageConstants/<id>), code, name, targetable."
    )


@lookup_mcp.tool(title="Find Google Ads language ids", annotations=READ)
def language(
    customer_id: str | int,
    query: str | None = None,
    login_customer_id: str | int | None = None,
) -> Languages:
    """Finds language constants by name or code ('German', 'de', 'Portuguese'); no query lists them all.

    The list is read once per server process, then cached. Costs 1 Google Ads operation the first time, then none.

    Args:
        customer_id: Any account this login can read (the language list is the same for all).
        query: A language name, code or id; empty lists every language.
        login_customer_id: The manager (MCC) account to call through, if your login needs one.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    version = adnero_auth.api_version()
    languages = _languages.get(version)
    if not languages:
        languages = sorted(
            (lk.language(r) for r in _search(cid, lk.LANGUAGE_QUERY)),
            key=lambda l: l["name"],
        )
        if languages:  # never cache an empty answer
            with _languages_lock:
                _languages[version] = languages
    return Languages(languages=lk.match_languages(languages, query or ""))


# ---------------------------------------------------------------- resolve


class Resolved(BaseModel):
    kind: str
    matches: list[dict] = Field(
        description="id, name, resource_name, status, and the kind's context (campaign of an ad group, asset type...)."
    )
    ambiguous: bool = Field(
        description="More than one match: ask which one, never guess."
    )
    truncated: bool = Field(
        default=False,
        description=f"More than {lk.MAX_MATCHES} matches: narrow the name.",
    )


@resolve_mcp.tool(
    title="Find the id of a campaign, ad group, asset... by its name",
    annotations=READ,
)
def resolve(
    customer_id: str | int,
    kind: Literal[
        "campaign",
        "ad_group",
        "asset",
        "conversion_action",
        "user_list",
        "label",
        "campaign_budget",
    ],
    name: str,
    match: Literal["exact", "contains"] = "exact",
    include_removed: bool = False,
    login_customer_id: str | int | None = None,
) -> Resolved:
    """Turns a name into ids: the campaign, ad group, asset, conversion action, user list, label or budget of one
    account with that name. Several matches come back as `ambiguous`: ask the person which one.

    Costs 1 Google Ads operation.

    Args:
        customer_id: The Google Ads account (digits; hyphens are dropped).
        kind: What the name is.
        name: The name ('exact': equal, case-sensitive; 'contains': a case-insensitive substring).
        match: 'exact' (default) or 'contains'.
        include_removed: Also match removed ones.
        login_customer_id: The manager (MCC) account to call through; needed when the account is reached through a manager.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    text = str(name or "").strip()
    if not text or len(text) > 255:
        raise ToolError("Error: 'name' is 1 to 255 characters.")
    rows = _search(cid, lk.resolve_query(kind, text, match, include_removed))
    found = [lk.resolved(kind, r) for r in rows]
    return Resolved(
        kind=kind,
        matches=found[: lk.MAX_MATCHES],
        ambiguous=len(found) > 1,
        truncated=len(found) > lk.MAX_MATCHES,
    )
