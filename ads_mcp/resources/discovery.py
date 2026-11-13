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

"""Discovery document resource (adnero: pinned to API_VERSION, cached per process by `revision`)."""

import json
import threading
import time
import urllib.request

from ads_mcp.api_version import API_VERSION
from ads_mcp.coordinator import mcp

# The document is ~3.4 MB, served with no ETag or Cache-Control; it only changes with a minor release.
REFRESH_S = 24 * 3600
TIMEOUT_S = 30
_lock = threading.Lock()
# version -> (revision, fetched at (monotonic), text)
_cache: dict[str, tuple[str, float, str]] = {}


def discovery_url(version: str) -> str:
    return f"https://googleads.googleapis.com/$discovery/rest?version={version}"


def revision_of(text: str) -> str:
    """The document's `revision` (e.g. '20261110'); '' when it has none or is not JSON (pure)."""
    try:
        value = json.loads(text).get("revision")
    except (ValueError, AttributeError):
        return ""
    return str(value or "")


def _fetch(version: str) -> str:
    req = urllib.request.Request(
        discovery_url(version),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
        return response.read().decode("utf-8")


def document(version: str = API_VERSION, now: float | None = None) -> str:
    """The Discovery document of `version`: fetched once, re-checked daily; a new revision replaces the cached
    text, a failed re-check keeps serving the cached one."""
    clock = time.monotonic() if now is None else now
    cached = _cache.get(version)
    if cached and clock - cached[1] < REFRESH_S:
        return cached[2]
    try:
        text = _fetch(version)
    except Exception:
        if cached:
            return cached[2]
        raise
    revision = revision_of(text)
    with _lock:
        keep = cached and cached[0] == revision and revision
        _cache[version] = (revision, clock, cached[2] if keep else text)
    return _cache[version][2]


@mcp.resource(
    uri="resource://discovery-document",
    mime_type="application/json",
)
def get_discovery_document() -> str:
    """Retrieve the Google Ads API discovery document (the pinned API version, about 3.4 MB).

    Provides the discovery document for the Google Ads API, which
    describes the API surface, including resources, methods, and schemas.

    Prefer the schema tools: schema_list_resources, schema_get_resource (fields and GAQL metadata) and
    schema_get_mutate (the exact api_mutate body) answer one resource at a time.

    Returns:
        str: The discovery document in JSON format.
    """
    return document()
