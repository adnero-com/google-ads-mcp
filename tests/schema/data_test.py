"""The checked-in v26 schemas: intact, and what regenerating from googleapis gives."""

import gzip
import hashlib
import importlib.util
import json
import os
import pathlib
import unittest

from ads_mcp.api_version import API_VERSION

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "ads_mcp" / "schemas" / API_VERSION
KINDS = ("resources", "common", "services", "enums", "index")


def load(kind):
    return json.loads(gzip.decompress((DATA / f"{kind}.json.gz").read_bytes()))


def gen_module():
    spec = importlib.util.spec_from_file_location(
        "gen_schemas", ROOT / "scripts" / "gen_schemas.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckedInTest(unittest.TestCase):
    def test_manifest_matches_the_files(self):
        manifest = json.loads((DATA / "manifest.json").read_text())
        for kind in KINDS:
            with self.subTest(kind=kind):
                raw = gzip.decompress((DATA / f"{kind}.json.gz").read_bytes())
                self.assertEqual(
                    hashlib.sha256(raw).hexdigest(), manifest[kind]["sha256"]
                )

    def test_known_v26_facts(self):
        r, s, e, i = (
            load("resources"),
            load("services"),
            load("enums"),
            load("index"),
        )
        campaign = r["Campaign"]["properties"]
        setting = r["Campaign.AppCampaignSetting"]["properties"]
        cases = [
            ("appId immutable", setting["appId"].get("x-immutable"), True),
            (
                "appStore enum",
                setting["appStore"]["$ref"],
                "#/$defs/AppCampaignAppStore",
            ),
            ("id output only", campaign["id"].get("readOnly"), True),
            ("id int64 string", campaign["id"]["type"], "string"),
            (
                "budget reference",
                campaign["campaignBudget"].get("x-resource"),
                "CampaignBudget",
            ),
            ("no startDate since v23", "startDate" in campaign, False),
            ("startDateTime", "startDateTime" in campaign, True),
            (
                "bidding oneof",
                "targetCpa"
                in r["Campaign"]["x-oneof"]["campaignBiddingStrategy"],
                True,
            ),
            (
                "ad oneof has appAd",
                "appAd" in r["Ad"]["x-oneof"]["adData"],
                True,
            ),
            (
                "status enum",
                e["CampaignStatus"]["enum"],
                ["ENABLED", "PAUSED", "REMOVED"],
            ),
            (
                "operation oneof",
                s["CampaignOperation"]["x-oneof"]["operation"],
                ["create", "update", "remove"],
            ),
            (
                "campaign path",
                i["resources"]["campaign"]["mutate"]["path"],
                "customers/{customer_id}/campaigns:mutate",
            ),
            (
                "criteria collection",
                i["resources"]["campaign_criterion"]["mutate"]["collection"],
                "campaignCriteria",
            ),
            (
                "view not mutable",
                "mutate" in i["resources"]["app_top_combination_view"],
                False,
            ),
            (
                "googleAds wrapper",
                i["googleAdsMutate"]["adGroupAdOperation"],
                "AdGroupAdOperation",
            ),
            ("pinned version", i["version"], API_VERSION),
        ]
        for name, got, want in cases:
            with self.subTest(name):
                self.assertEqual(got, want)

    def test_every_ref_resolves(self):
        pool = {
            **load("resources"),
            **load("common"),
            **load("services"),
            **load("enums"),
        }
        text = json.dumps(pool)
        refs = {
            part.split('"', 1)[0]
            for part in text.split('"$ref": "#/$defs/')[1:]
        }
        self.assertEqual(sorted(refs - pool.keys()), [])


@unittest.skipUnless(
    os.environ.get("GADS_SCHEMA_REGEN")
    or os.environ.get("GADS_GOOGLEAPIS_DIR"),
    "set GADS_SCHEMA_REGEN=1 (fetches googleapis, needs grpcio-tools) or GADS_GOOGLEAPIS_DIR=<checkout>",
)
class RegenerateTest(unittest.TestCase):
    def test_regenerating_gives_the_checked_in_data(self):
        gen = gen_module()
        checkout = os.environ.get("GADS_GOOGLEAPIS_DIR")
        googleapis = (
            pathlib.Path(checkout)
            if checkout
            else gen.fetch(
                pathlib.Path(
                    os.path.expanduser("~/.cache/gads-mcp-googleapis")
                ),
                gen.GOOGLEAPIS_COMMIT,
                API_VERSION,
            )
        )
        data = gen.generate(googleapis, API_VERSION, gen.GOOGLEAPIS_COMMIT)
        self.assertEqual(gen.differences(DATA, data), [])


if __name__ == "__main__":
    unittest.main()
