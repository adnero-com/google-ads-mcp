"""utils.format_google_ads_error on a real v26 GoogleAdsFailure (the edge of core.errors)."""

import json
from unittest.mock import MagicMock

from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v26.errors.types.errors import (
    ErrorCode,
    ErrorLocation,
    GoogleAdsError,
    GoogleAdsFailure,
)
from google.ads.googleads.v26.errors.types.media_upload_error import (
    MediaUploadErrorEnum,
)
from google.ads.googleads.v26.errors.types.policy_finding_error import (
    PolicyFindingErrorEnum,
)
from google.ads.googleads.v26.common.types.policy import PolicyTopicEntry

from ads_mcp import utils


def test_real_failure_is_structured():
    image = GoogleAdsError(
        error_code=ErrorCode(
            media_upload_error=MediaUploadErrorEnum.MediaUploadError.ASPECT_RATIO_NOT_ALLOWED
        ),
        message="The aspect ratio of the image is not allowed.",
        location=ErrorLocation(
            field_path_elements=[
                ErrorLocation.FieldPathElement(
                    field_name="mutate_operations", index=2
                ),
                ErrorLocation.FieldPathElement(field_name="asset_operation"),
            ]
        ),
    )
    image.trigger.string_value = "2048x2002"
    policy = GoogleAdsError(
        error_code=ErrorCode(
            policy_finding_error=PolicyFindingErrorEnum.PolicyFindingError.POLICY_FINDING
        ),
        message="policy",
    )
    policy.details.policy_finding_details.policy_topic_entries.append(
        PolicyTopicEntry(topic="SOCIAL_CASINO_GAMES", type_=4)
    )
    failure = GoogleAdsFailure(errors=[image, policy])
    ex = GoogleAdsException(
        MagicMock(), MagicMock(), type(failure).pb(failure), "req-1"
    )

    report = utils.google_ads_error_report(ex)
    first, second = report.errors
    assert (
        first.error_type,
        first.code,
        first.trigger,
        first.operation_index,
    ) == (
        "media_upload_error",
        "ASPECT_RATIO_NOT_ALLOWED",
        "2048x2002",
        2,
    )
    assert first.field_path == "mutate_operations[2].asset_operation"
    assert (second.code, second.exemptible, second.policy_topics) == (
        "POLICY_FINDING",
        True,
        ("SOCIAL_CASINO_GAMES",),
    )
    text = utils.format_google_ads_error(ex)
    assert (
        json.loads(text.split("errors_json: ", 1)[1])["request_id"] == "req-1"
    )
