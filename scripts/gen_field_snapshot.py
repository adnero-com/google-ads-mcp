#!/usr/bin/env python3
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

"""Snapshot of GoogleAdsFieldService metadata for the resources our read tools query, for offline tests.

GoogleAdsFieldService needs a Google token; Google publishes the same metadata (category, data type, selectable,
filterable, sortable, repeated, selectable with, attributed resources) on its public field reference pages,
https://developers.google.com/google-ads/api/fields/<version>/<resource>. This reads those pages and writes
ads_mcp/schemas/<API_VERSION>/fields.json.gz:

    {"resources": {name: {"attribute_resources": [...], "metrics": [...], "segments": [...]}},
     "fields": {name: [category, data_type, selectable, filterable, sortable, repeated]}}

tests/reads/fields_test.py checks every query the read tools build against it (Google refuses a query that selects
a field that is not selectable: UNRECOGNIZED_FIELD). Add a resource here when a read tool starts querying it.

    python scripts/gen_field_snapshot.py            # fetch and write
    python scripts/gen_field_snapshot.py --check    # exit 1 when the checked-in snapshot differs
"""

import argparse
import gzip
import html
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ads_mcp.api_version import API_VERSION  # noqa: E402

RESOURCES = (
    "ad_group",
    "ad_group_ad",
    "ad_group_ad_asset_view",
    "app_top_combination_view",
    "asset",
    "campaign",
    "campaign_budget",
    "campaign_criterion",
    "conversion_action",
    "customer",
    "geo_target_constant",
    "label",
    "language_constant",
    "user_list",
)
URL = "https://developers.google.com/google-ads/api/fields/{version}/{resource}"
_FIELD_ANCHOR = re.compile(r'id="((?:[a-z_]+)(?:\.[a-z0-9_]+)+)"')


def text_of(page: str) -> str:
    """The page as text: tags become '|', entities decoded, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " | ", page)))


def _value(block: str, label: str) -> str:
    match = re.search(rf"\b{label}(?: \|)+ ([A-Za-z_0-9.]+)", block)
    return match.group(1) if match else ""


def _list_after(block: str, label: str, until: str) -> list[str]:
    i = block.find(label)
    if i < 0:
        return []
    j = block.find(until, i)
    part = block[i + len(label) : j if j > 0 else None]
    return re.findall(r"\| ([a-z_][a-z0-9_.]*) \|", part)


def parse(resource: str, page: str) -> tuple[dict, dict]:
    """(the resource entry, {field: [category, data_type, selectable, filterable, sortable, repeated]}) (pure)."""
    starts = [(m.start(), m.group(1)) for m in _FIELD_ANCHOR.finditer(page)]
    fields, usable = {}, {"METRIC": [], "SEGMENT": []}
    for (start, name), (end, _) in zip(starts, starts[1:] + [(len(page), "")]):
        block = text_of(page[start:end])
        category = _value(block, "Category")
        if not category:
            continue
        flag = lambda label: _value(block, label) == "True"  # noqa: E731
        fields[name] = [
            category,
            _value(block, "Data Type"),
            flag("Selectable"),
            flag("Filterable"),
            flag("Sortable"),
            flag("Repeated"),
        ]
        if category in usable:
            with_ = _list_after(
                block, "can be selected with this field:", "Field description"
            )
            if resource in with_:
                usable[category].append(name)
    whole = text_of(page)
    attributed = _list_after(
        whole, "Attributed resources", "Fields from the above"
    )
    entry = {
        "attribute_resources": sorted(set(attributed) - {resource}),
        "metrics": sorted(usable["METRIC"]),
        "segments": sorted(usable["SEGMENT"]),
    }
    return entry, fields


def fetch(resource: str, version: str) -> str:
    request = urllib.request.Request(
        URL.format(version=version, resource=resource),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def encode(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def build(version: str) -> dict:
    resources, fields = {}, {}
    for resource in RESOURCES:
        entry, found = parse(resource, fetch(resource, version))
        resources[resource] = entry
        fields.update(found)
    return {"version": version, "resources": resources, "fields": fields}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    out = ROOT / "ads_mcp" / "schemas" / API_VERSION / "fields.json.gz"
    data = build(API_VERSION)
    raw = encode(data)
    if args.check:
        same = out.exists() and gzip.decompress(out.read_bytes()) == raw
        print("up to date" if same else "differs")
        return 0 if same else 1
    out.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    print(
        f"{len(data['resources'])} resources, {len(data['fields'])} fields, {out.stat().st_size:,} B gzip"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
