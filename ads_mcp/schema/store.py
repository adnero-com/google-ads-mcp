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

"""The checked-in schemas of API_VERSION, read once per process (the only file I/O of the schema layer)."""

import functools
import gzip
import importlib.resources
import json
from types import MappingProxyType
from typing import Mapping

from ads_mcp.api_version import API_VERSION


def _read(version: str, kind: str) -> dict:
    path = importlib.resources.files("ads_mcp").joinpath(
        "schemas", version, f"{kind}.json.gz"
    )
    return json.loads(gzip.decompress(path.read_bytes()))


@functools.lru_cache(maxsize=2)
def load(version: str = API_VERSION) -> Mapping:
    """{'pool': every message fragment, 'enums': every enum, 'index': resources/services/googleAdsMutate}."""
    pool = {
        **_read(version, "resources"),
        **_read(version, "common"),
        **_read(version, "services"),
    }
    return MappingProxyType(
        {
            "pool": pool,
            "enums": _read(version, "enums"),
            "index": _read(version, "index"),
        }
    )


def available(version: str) -> bool:
    """True when schemas for `version` are checked in (a version switch without them must fail at start)."""
    return (
        importlib.resources.files("ads_mcp")
        .joinpath("schemas", version, "index.json.gz")
        .is_file()
    )
