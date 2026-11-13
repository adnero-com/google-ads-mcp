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

"""Tests for the discovery-document resource."""

import unittest
import urllib.request
from unittest import mock

from ads_mcp.resources import discovery


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        discovery._cache.clear()

    @mock.patch("urllib.request.urlopen")
    def test_get_discovery_document(self, mock_urlopen):
        # Setup mock response
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"mock": "discovery content"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        # Call function
        result = discovery.get_discovery_document()

        # Assertions
        self.assertEqual(result, '{"mock": "discovery content"}')

        # Verify urlopen was called correctly
        mock_urlopen.assert_called_once()
        args, _ = mock_urlopen.call_args
        request_obj = args[0]

        self.assertIsInstance(request_obj, urllib.request.Request)
        self.assertEqual(
            request_obj.full_url,
            "https://googleads.googleapis.com/$discovery/rest?version=v26",
        )
        self.assertEqual(request_obj.headers.get("User-agent"), "Mozilla/5.0")


class DiscoveryCacheTest(unittest.TestCase):
    def setUp(self):
        discovery._cache.clear()

    def test_revision_of(self):
        cases = [
            ('{"revision": "20261110"}', "20261110"),
            ("{}", ""),
            ("not json", ""),
            ("[]", ""),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(discovery.revision_of(text), want)

    def test_cached_by_revision_and_refreshed_daily(self):
        answers = iter(
            [
                '{"revision": "1", "a": 1}',
                '{"revision": "1", "a": 2}',  # same revision: the cached text stays
                '{"revision": "2"}',
            ]
        )
        first = '{"revision": "1", "a": 1}'
        day = discovery.REFRESH_S
        with mock.patch.object(
            discovery, "_fetch", side_effect=lambda v: next(answers)
        ) as fetch:
            self.assertEqual(discovery.document("v26", now=0), first)
            self.assertEqual(discovery.document("v26", now=100), first)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(discovery.document("v26", now=day + 1), first)
            self.assertEqual(
                discovery.document("v26", now=2 * day + 2), '{"revision": "2"}'
            )
            self.assertEqual(fetch.call_count, 3)

    def test_a_failed_refresh_serves_the_cache(self):
        discovery._cache["v26"] = ("1", 0.0, "cached")
        down = OSError("down")
        with mock.patch.object(discovery, "_fetch", side_effect=down):
            later = discovery.REFRESH_S + 1
            self.assertEqual(discovery.document("v26", now=later), "cached")
            discovery._cache.clear()
            with self.assertRaises(OSError):
                discovery.document("v26", now=0)
