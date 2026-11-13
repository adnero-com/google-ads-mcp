"""ads_mcp.core.errors: every Google error as structured data with fix hints (table-driven)."""

import json

import pytest

from ads_mcp.core import errors


def _err(kind, code, **extra):
    return {"error_code": {kind: code}, "message": f"{code} msg", **extra}


@pytest.mark.parametrize(
    "error, code, kind, fix_has",
    [
        (
            _err("media_upload_error", "ASPECT_RATIO_NOT_ALLOWED"),
            "ASPECT_RATIO_NOT_ALLOWED",
            "media_upload_error",
            "1.91:1",
        ),
        (
            _err("image_error", "ASPECT_RATIO_NOT_ALLOWED"),
            "ASPECT_RATIO_NOT_ALLOWED",
            "image_error",
            "4:5",
        ),
        (
            _err("ad_group_ad_error", "AD_TYPE_CANNOT_BE_PAUSED"),
            "AD_TYPE_CANNOT_BE_PAUSED",
            "ad_group_ad_error",
            "ad group",
        ),
        (
            _err(
                "identity_verification_error",
                "BILLING_NOT_ON_MONTHLY_INVOICING",
            ),
            "BILLING_NOT_ON_MONTHLY_INVOICING",
            "identity_verification_error",
            "monthly invoicing",
        ),
        (
            _err("identity_verification_error", "NO_EFFECTIVE_BILLING"),
            "NO_EFFECTIVE_BILLING",
            "identity_verification_error",
            "billing",
        ),
        (
            _err("query_error", "UNRECOGNIZED_FIELD"),
            "UNRECOGNIZED_FIELD",
            "query_error",
            "start_date_time",
        ),
        (
            _err("query_error", "BAD_FIELD_NAME"),
            "BAD_FIELD_NAME",
            "query_error",
            "metadata",
        ),
        (
            _err("request_error", "UNKNOWN_THING"),
            "UNKNOWN_THING",
            "request_error",
            None,
        ),
        ({"message": "no code"}, "UNKNOWN", "unknown", None),
        ({"error_code": "garbage"}, "UNKNOWN", "unknown", None),
    ],
)
def test_codes_and_fixes(error, code, kind, fix_has):
    d = errors.detail_of(error)
    assert (d.code, d.error_type) == (code, kind)
    if fix_has is None:
        assert d.fix is None
    else:
        assert fix_has in d.fix


@pytest.mark.parametrize(
    "elements, path, index",
    [
        ([], "", None),
        (
            [
                {"field_name": "mutate_operations", "index": 2},
                {"field_name": "ad_group_ad_operation"},
                {"field_name": "create"},
            ],
            "mutate_operations[2].ad_group_ad_operation.create",
            2,
        ),
        (
            [
                {"field_name": "operations", "index": 0},
                {"field_name": "create"},
            ],
            "operations[0].create",
            0,
        ),
        ([{"field_name": "query"}], "query", None),
    ],
)
def test_field_path_and_index(elements, path, index):
    d = errors.detail_of({"location": {"field_path_elements": elements}})
    assert (d.field_path, d.operation_index) == (path, index)


@pytest.mark.parametrize(
    "trigger, expected",
    [
        ({"string_value": "2048x2002"}, "2048x2002"),
        ({"int64_value": "5"}, "5"),
        ({}, None),
        (None, None),
    ],
)
def test_trigger(trigger, expected):
    assert errors.detail_of({"trigger": trigger}).trigger == expected


@pytest.mark.parametrize(
    "details, exemptible, topics",
    [
        (None, False, ()),
        (
            {
                "policy_finding_details": {
                    "policy_topic_entries": [
                        {"topic": "GAMBLING", "type": "LIMITED"}
                    ]
                }
            },
            True,
            ("GAMBLING",),
        ),
        (
            {
                "policy_finding_details": {
                    "policy_topic_entries": [
                        {"topic": "A", "type": "LIMITED"},
                        {"topic": "B", "type": "PROHIBITED"},
                    ]
                }
            },
            False,
            ("A", "B"),
        ),
        (
            {
                "policy_violation_details": {
                    "is_exemptible": True,
                    "key": {"policy_name": "TRADEMARK"},
                }
            },
            True,
            ("TRADEMARK",),
        ),
        (
            {
                "policy_violation_details": {
                    "is_exemptible": False,
                    "external_policy_name": "X",
                }
            },
            False,
            ("X",),
        ),
    ],
)
def test_policy(details, exemptible, topics):
    d = errors.detail_of(
        {
            "error_code": {"policy_finding_error": "POLICY_FINDING"},
            "details": details,
        }
    )
    assert (d.exemptible, d.policy_topics) == (exemptible, topics)
    if topics:
        assert "exemption" in d.fix


def test_report_and_render():
    report = errors.report_of(
        {"errors": [_err("image_error", "ASPECT_RATIO_NOT_ALLOWED"), "junk"]},
        "req-9",
    )
    assert len(report.errors) == 1
    text = errors.render(report)
    assert "Request ID: req-9" in text
    assert "image_error.ASPECT_RATIO_NOT_ALLOWED" in text
    parsed = json.loads(text.split("errors_json: ", 1)[1])
    assert parsed["errors"][0]["code"] == "ASPECT_RATIO_NOT_ALLOWED"
    assert errors.report_of(None).errors == ()
