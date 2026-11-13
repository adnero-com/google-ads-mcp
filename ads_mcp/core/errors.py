"""Google Ads errors as structured data (pure).

Input is the plain dict of a GoogleAdsFailure (core.plain.to_plain: enum names, API keys).
Output is one GoogleAdsErrorDetail per error: the code name, message, field path, trigger,
operation index, whether a policy finding can be exempted, the policy topics, and a fix hint
for the errors we know. Nothing here raises.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# (error type or None for any type, code) -> what to do. One source of truth for fix hints.
_SCHEMA_HINT = (
    "Look the resource's fields up first (metadata_get_resource_metadata, or the schema_* tools) and use full "
    "v26 field names such as 'campaign.start_date_time' (campaign.start_date does not exist since v23)."
)
FIX_HINTS: Mapping[tuple[str | None, str], str] = {
    (None, "ASPECT_RATIO_NOT_ALLOWED"): (
        "App campaign images must be 1:1 (min 200x200), 1.91:1 (min 600x314) or 4:5 (min 320x400); Google refuses "
        "even 0.8% off. Crop the image to one of these ratios and upload it again; 9:16 and 16:9 creative must be a YouTube video."
    ),
    ("ad_group_ad_error", "AD_TYPE_CANNOT_BE_PAUSED"): (
        "App ads (app_ad, app_engagement_ad, app_pre_registration_ad) cannot be paused. Pause the ad group or the "
        "campaign instead (campaigns_set_status with level AD_GROUP or CAMPAIGN)."
    ),
    ("ad_group_ad_error", "AD_TYPE_CANNOT_BE_REMOVED"): (
        "This ad type cannot be removed. Pause or remove its ad group instead, or replace its assets."
    ),
    ("identity_verification_error", "BILLING_NOT_ON_MONTHLY_INVOICING"): (
        "Not a broken call: Google answers identity verification through the API only for accounts billed by "
        "monthly invoicing. Check it in the Google Ads UI (Billing > Advertiser verification)."
    ),
    ("identity_verification_error", "NO_EFFECTIVE_BILLING"): (
        "Not a broken call: the account has no active billing setup, so Google has no identity verification to "
        "report. Set up billing in the Google Ads UI first."
    ),
    (None, "MISSING_EU_POLITICAL_ADVERTISING_SELF_DECLARATION"): (
        "A targeted location is in the EU: the campaign must state contains_eu_political_advertising. Ask the "
        "person, then pass it explicitly (app_campaign_create takes it)."
    ),
    (None, "EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED"): (
        "A targeted location is in the EU: the campaign must state contains_eu_political_advertising. Ask the "
        "person, then pass it explicitly."
    ),
    ("query_error", "UNRECOGNIZED_FIELD"): _SCHEMA_HINT,
    ("query_error", "BAD_FIELD_NAME"): _SCHEMA_HINT,
    ("query_error", "BAD_RESOURCE_TYPE_IN_FROM_CLAUSE"): (
        "The FROM resource does not exist in v26. Pick one from the search tool's list of valid resources."
    ),
    ("query_error", "PROHIBITED_FIELD_COMBINATION_IN_SELECT_CLAUSE"): (
        "These fields cannot be selected together. Check selectable_with in metadata_get_resource_metadata."
    ),
    ("ad_error", "STORE_MISMATCH"): (
        "The ad's app store differs from the campaign's app_campaign_setting.app_store; use the campaign's store."
    ),
    ("ad_error", "MISMATCHED_ASPECT_RATIOS"): (
        "The assets' aspect ratios do not fit together; use images of the allowed ratios (1:1, 1.91:1, 4:5)."
    ),
    ("campaign_error", "INSUFFICIENT_APP_INSTALLS_COUNT"): (
        "An App engagement campaign needs an app with enough installs; run an install campaign first."
    ),
    (
        "campaign_error",
        "CONVERSION_ACTION_WITH_DOWNLOAD_CATEGORY_NOT_ALLOWED",
    ): (
        "Selective optimization cannot use a DOWNLOAD-category conversion action; pick in-app actions."
    ),
    ("date_range_error", "CANNOT_MODIFY_START_DATE_IF_ALREADY_STARTED"): (
        "The campaign has started: its start date can no longer change. Leave start_date_time out."
    ),
    ("authorization_error", "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"): (
        "The Google Cloud project only has test-account access. Apply for Explorer/Basic access, or use a test "
        "account."
    ),
    ("mutate_error", "RESOURCE_NOT_FOUND"): (
        "An id in the request does not exist in this account; read it back with search first."
    ),
    (None, "CONCURRENT_MODIFICATION"): (
        "Someone else changed the same object at the same time; read it again and retry once."
    ),
    ("quota_error", "RESOURCE_EXHAUSTED"): (
        "Google's rate limit was hit; wait a minute before retrying."
    ),
    ("quota_error", "RESOURCE_TEMPORARILY_EXHAUSTED"): (
        "Google's short-term rate limit was hit; wait a few seconds before retrying."
    ),
}

_POLICY_FIX = (
    "A policy finding. Do not request an exemption on your own: show the topics to the person; if they decide "
    "to, the change can be retried with an exemption for the exemptible topics only."
)


class GoogleAdsErrorDetail(BaseModel):
    """One error Google returned, in the shape tools hand back."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(
        description="The error code name, e.g. ASPECT_RATIO_NOT_ALLOWED."
    )
    error_type: str = Field(
        description="Which error enum it belongs to, e.g. image_error."
    )
    message: str = ""
    field_path: str = Field(
        default="",
        description="Where in the request: 'mutate_operations[2].ad_group_ad_operation.create.ad.app_ad.images[0]'.",
    )
    operation_index: int | None = Field(
        default=None,
        description="The index of the failing operation in the request, when known.",
    )
    trigger: str | None = Field(
        default=None, description="The value that triggered it."
    )
    exemptible: bool = Field(
        default=False,
        description="True when a person may request a policy exemption for it. Never request one on your own.",
    )
    policy_topics: tuple[str, ...] = Field(default=())
    fix: str | None = Field(
        default=None, description="What to do, when we know."
    )


class GoogleAdsErrorReport(BaseModel):
    """Every error of one refused request."""

    model_config = ConfigDict(frozen=True)

    request_id: str = ""
    errors: tuple[GoogleAdsErrorDetail, ...] = ()


def _code_of(error_code: Any) -> tuple[str, str]:
    """{'image_error': 'ASPECT_RATIO_NOT_ALLOWED'} -> ('image_error', 'ASPECT_RATIO_NOT_ALLOWED')."""
    if not isinstance(error_code, Mapping):
        return ("unknown", "UNKNOWN")
    named = [
        (str(k), str(v))
        for k, v in error_code.items()
        if v not in (None, "", "UNSPECIFIED", 0)
    ]
    return named[0] if named else ("unknown", "UNKNOWN")


def _path_of(location: Any) -> tuple[str, int | None]:
    elements = (
        location.get("field_path_elements", [])
        if isinstance(location, Mapping)
        else []
    )
    parts = [
        f"{e.get('field_name', '')}"
        + (f"[{e['index']}]" if e.get("index") is not None else "")
        for e in elements
        if isinstance(e, Mapping)
    ]
    first = elements[0] if elements and isinstance(elements[0], Mapping) else {}
    index = (
        int(first["index"])
        if first.get("field_name") in ("mutate_operations", "operations")
        and first.get("index") is not None
        else None
    )
    return ".".join(parts), index


def _trigger_of(trigger: Any) -> str | None:
    if not isinstance(trigger, Mapping):
        return None
    values = [str(v) for v in trigger.values() if v not in (None, "")]
    return values[0] if values else None


def _policy_of(details: Any) -> tuple[bool, tuple[str, ...]]:
    """(exemptible, topics) from policy_violation_details or policy_finding_details."""
    if not isinstance(details, Mapping):
        return False, ()
    violation = details.get("policy_violation_details")
    if isinstance(violation, Mapping):
        key = violation.get("key") or {}
        name = key.get("policy_name") or violation.get(
            "external_policy_name", ""
        )
        return bool(violation.get("is_exemptible")), (
            (str(name),) if name else ()
        )
    finding = details.get("policy_finding_details")
    if isinstance(finding, Mapping):
        entries = [
            e
            for e in finding.get("policy_topic_entries", [])
            if isinstance(e, Mapping)
        ]
        topics = tuple(
            str(e.get("topic", "")) for e in entries if e.get("topic")
        )
        # A PROHIBITED topic can never be exempted; the others may be, by a person.
        exemptible = bool(entries) and all(
            e.get("type") != "PROHIBITED" for e in entries
        )
        return exemptible, topics
    return False, ()


def fix_for(error_type: str, code: str) -> str | None:
    return FIX_HINTS.get((error_type, code)) or FIX_HINTS.get((None, code))


def detail_of(error: Mapping[str, Any]) -> GoogleAdsErrorDetail:
    error_type, code = _code_of(error.get("error_code"))
    path, index = _path_of(error.get("location"))
    exemptible, topics = _policy_of(error.get("details"))
    policy_fix = _POLICY_FIX if topics else None
    return GoogleAdsErrorDetail(
        code=code,
        error_type=error_type,
        message=str(error.get("message", "")),
        field_path=path,
        operation_index=index,
        trigger=_trigger_of(error.get("trigger")),
        exemptible=exemptible,
        policy_topics=topics,
        fix=fix_for(error_type, code) or policy_fix,
    )


def report_of(
    failure: Mapping[str, Any] | None, request_id: str = ""
) -> GoogleAdsErrorReport:
    errors: Sequence[Any] = (failure or {}).get("errors", []) or []
    return GoogleAdsErrorReport(
        request_id=request_id or "",
        errors=tuple(detail_of(e) for e in errors if isinstance(e, Mapping)),
    )


def render(report: GoogleAdsErrorReport) -> str:
    """Text for a ToolError: one line per error a person can read, then the JSON (with the fix hints)."""
    lines = [
        f"- {e.error_type}.{e.code}: {e.message}"
        + (f" (at {e.field_path})" if e.field_path else "")
        for e in report.errors
    ]
    head = f"Google Ads refused the request (Request ID: {report.request_id})"
    body = "\n".join(lines)
    return (
        f"{head}\n{body}\nerrors_json: "
        f"{report.model_dump_json(exclude_defaults=False)}"
    )
