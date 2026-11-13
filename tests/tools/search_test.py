# Copyright 2026 Google LLC.
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

"""Test cases for the search tool (adnero: GAQL or parts, bounded pages, enum names)."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v26.services.types.google_ads_service import (
    GoogleAdsRow,
    SearchGoogleAdsResponse,
)
from google.protobuf.field_mask_pb2 import FieldMask

from ads_mcp.core import gaql
from ads_mcp.tools import search


def _row(i):
    row = GoogleAdsRow()
    row.campaign.id = i
    row.campaign.name = f"C{i}"
    row.campaign.status = 2  # ENABLED
    return row


class FakeGoogleAds:
    """GoogleAdsService.search over canned pages: page_token -> (row ids, next token)."""

    def __init__(self, pages, error=None):
        self.pages = pages
        self.error = error
        self.requests = []

    def search(self, request):
        self.requests.append(dict(request))
        if self.error:
            raise self.error
        ids, nxt = self.pages[request.get("page_token", "")]
        response = SearchGoogleAdsResponse(
            results=[_row(i) for i in ids], next_page_token=nxt
        )
        type(response).pb(response).field_mask.CopyFrom(
            FieldMask(paths=["campaign.id", "campaign.name", "campaign.status"])
        )
        return SimpleNamespace(pages=iter([response]))


@pytest.fixture
def fake(monkeypatch):
    def install(pages, error=None):
        service = FakeGoogleAds(pages, error)
        monkeypatch.setattr(
            "ads_mcp.utils.get_googleads_service",
            lambda name, login_customer_id=None: service,
        )
        return service

    return install


@pytest.mark.parametrize(
    "kwargs, sent",
    [
        (
            {"query": "SELECT campaign.id FROM campaign"},
            "SELECT campaign.id FROM campaign ORDER BY campaign.id",
        ),
        (
            {
                "fields": ["campaign.id", "campaign.name"],
                "resource": "campaign",
                "conditions": ["campaign.status = 'ENABLED'"],
                "orderings": ["campaign.name ASC"],
                "limit": 10,
            },
            "SELECT campaign.id,campaign.name FROM campaign WHERE campaign.status = 'ENABLED' "
            "ORDER BY campaign.name ASC LIMIT 10 PARAMETERS omit_unselected_resource_names=true",
        ),
    ],
)
def test_both_query_forms(fake, kwargs, sent):
    service = fake({"": ([1, 2], "")})
    out = search.search(customer_id="123-456-7890", **kwargs)
    assert service.requests == [{"customer_id": "1234567890", "query": sent}]
    assert out.query == sent
    assert out.rows == [
        {"campaign.id": 1, "campaign.name": "C1", "campaign.status": "ENABLED"},
        {"campaign.id": 2, "campaign.name": "C2", "campaign.status": "ENABLED"},
    ]
    assert (out.stopped_by, out.next_page_token) == ("END", None)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"query": "SELECT campaign.id FROM campaign", "resource": "campaign"},
        {"query": "SELECT campaign.id FROM campaign", "limit": 5},
        {"query": "DELETE campaign"},
        {"resource": "campaign"},
        {"query": "SELECT campaign.id FROM campaign", "max_rows": 0},
        {"query": "SELECT campaign.id FROM campaign", "max_rows": 10_001},
        {"query": "SELECT campaign.id FROM campaign", "page_token": "garbage"},
    ],
)
def test_refused_before_google(fake, kwargs):
    service = fake({"": ([1], "")})
    with pytest.raises(ToolError):
        search.search(customer_id="1", **kwargs)
    assert service.requests == []


def test_paging(fake):
    service = fake({"": (list(range(1, 6)), "G2"), "G2": ([6, 7], "")})
    q = "SELECT campaign.id FROM campaign"
    seen, token, stops = [], None, []
    for _ in range(5):
        out = search.search(
            customer_id="1", query=q, max_rows=2, page_token=token
        )
        seen += [r["campaign.id"] for r in out.rows]
        stops.append(out.stopped_by)
        token = out.next_page_token
        if not token:
            break
    assert seen == [1, 2, 3, 4, 5, 6, 7]
    assert stops == ["MAX_ROWS", "MAX_ROWS", "END", "END"]
    assert [r.get("page_token", "") for r in service.requests] == [
        "",
        "",
        "",
        "G2",
    ]


def test_a_token_from_another_query_is_refused(fake):
    fake({"": (list(range(1, 6)), "")})
    out = search.search(
        customer_id="1", query="SELECT campaign.id FROM campaign", max_rows=1
    )
    with pytest.raises(ToolError, match="another query"):
        search.search(
            customer_id="1",
            query="SELECT campaign.name FROM campaign",
            page_token=out.next_page_token,
        )
    with pytest.raises(ToolError, match="another query"):
        search.search(
            customer_id="2",
            query="SELECT campaign.id FROM campaign",
            page_token=out.next_page_token,
        )


def test_byte_cap(fake, monkeypatch):
    fake({"": (list(range(1, 50)), "")})
    monkeypatch.setattr(gaql, "BYTE_CAP", 150)
    out = search.search(
        customer_id="1", query="SELECT campaign.id FROM campaign"
    )
    assert out.stopped_by == "BYTE_CAP"
    assert 1 <= out.row_count < 49
    assert out.next_page_token


def test_google_errors_are_structured(fake):
    failure = MagicMock()
    failure.errors = [SimpleNamespace(message="Unrecognized field")]
    ex = GoogleAdsException(MagicMock(), MagicMock(), failure, "req-123")
    fake({}, error=ex)
    with pytest.raises(ToolError) as info:
        search.search(
            customer_id="1", query="SELECT campaign.nope FROM campaign"
        )
    text = str(info.value)
    assert "Request ID: req-123" in text
    assert "Unrecognized field" in text


class TestSearchDescription(unittest.TestCase):
    def test_search_tool_description(self):
        """Tests that the tool description is generated correctly."""
        # Mocking open as if the file exists
        m = mock_open(read_data="resource1: field1, field2")
        with patch("builtins.open", m):
            with patch(
                "ads_mcp.utils.get_gaql_resources_filepath",
                return_value="/fake/path",
            ):
                description = search._search_tool_description()
                self.assertIn("resource1: field1, field2", description)
                self.assertIn("Language Grammar", description)
                self.assertIn("Hints for historical metrics", description)

    def test_search_tool_description_file_not_found(self):
        """Tests that the tool description handles missing file correctly."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch(
                "ads_mcp.utils.get_gaql_resources_filepath",
                return_value="/fake/path",
            ):
                with patch("ads_mcp.utils.logger.error") as mock_log_error:
                    description = search._search_tool_description()
                    self.assertIn(
                        "WARNING: The list of valid resources is missing.",
                        description,
                    )
                    mock_log_error.assert_called_once()


def test_unordered_queries_get_no_mid_page_token(fake):
    fake({"": ([1, 2, 3], "G2")})
    out = search.search(
        customer_id="1", query="SELECT campaign.name FROM campaign", max_rows=2
    )
    assert out.stopped_by == "MAX_ROWS"
    assert out.next_page_token is None and "ORDER BY" in out.note
    # a whole Google page read: Google's own token continues it
    whole = search.search(
        customer_id="1", query="SELECT campaign.name FROM campaign"
    )
    assert whole.next_page_token and whole.note is None


def test_segments_get_no_mid_page_token(fake):
    fake({"": ([1, 2, 3], "")})
    out = search.search(
        customer_id="1",
        query="SELECT campaign.id, segments.date FROM campaign",
        max_rows=2,
    )
    assert out.next_page_token is None and "segments" in out.note
