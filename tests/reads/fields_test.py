"""Every GAQL query the read tools build, checked against the field metadata snapshot (ads_mcp/schemas/<v>/
fields.json.gz, scripts/gen_field_snapshot.py): each field exists and is selectable / filterable / sortable as
its clause needs, and fits the FROM resource. Google answers anything else with UNRECOGNIZED_FIELD.
"""

import gzip
import json
import pathlib
import unittest

from ads_mcp.api_version import API_VERSION
from ads_mcp.reads import account, app_report, lookup, policy
from ads_mcp.schema import gaql

SNAPSHOT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "ads_mcp"
    / "schemas"
    / API_VERSION
    / "fields.json.gz"
)


def metadata() -> dict:
    data = json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
    fields = {
        name: gaql.FieldMeta(
            name=name,
            category=row[0],
            data_type=row[1],
            selectable=row[2],
            filterable=row[3],
            sortable=row[4],
            is_repeated=row[5],
        )
        for name, row in data["fields"].items()
    }
    resources = {
        name: gaql.FieldMeta(
            name=name,
            category="RESOURCE",
            attribute_resources=tuple(r["attribute_resources"]),
            metrics=tuple(r["metrics"]),
            segments=tuple(r["segments"]),
        )
        for name, r in data["resources"].items()
    }
    return {**fields, **resources}


def queries() -> dict:
    out = {
        "accounts_get": account.ACCOUNT_QUERY,
        "lookup_language": lookup.LANGUAGE_QUERY,
    }
    out["policy_report geo"] = lookup.geo_by_id_queries(["2124"])[0]
    for scope in (policy.Scope(), policy.Scope("1", "2")):
        for level, q in policy.queries(scope).items():
            out[f"policy_report {level} {scope}"] = q
    dates = "segments.date DURING LAST_30_DAYS"
    for by in ("day", "network", "asset", "combination", "conversion_action"):
        for target in (
            app_report.Target(campaign_id="1"),
            app_report.Target(ad_group_id="2"),
        ):
            out[f"app_report {by} {target.level}"] = app_report.query(
                by, target, dates
            )
    out["app_report combination assets"] = app_report.ASSETS_BY_ID.format(
        ids="1, 2"
    )
    for kind in lookup.KINDS:
        for match in ("exact", "contains"):
            out[f"resolve {kind} {match}"] = lookup.resolve_query(
                kind, "x", match, False
            )
    return out


class FieldsTest(unittest.TestCase):
    def test_every_query_fits_the_field_metadata(self):
        meta = metadata()
        for name, query in queries().items():
            with self.subTest(name):
                parsed = gaql.parse(query)
                self.assertIsInstance(parsed, gaql.Query, query)
                problems = [p.as_dict() for p in gaql.check(parsed, meta)]
                self.assertEqual(problems, [], query)

    def test_the_snapshot_knows_the_v26_rule_that_broke_policy_report(self):
        meta = metadata()
        self.assertTrue(
            meta["ad_group_ad_asset_view.policy_summary"].selectable
        )
        self.assertNotIn(
            "ad_group_ad_asset_view.policy_summary.approval_status", meta
        )


if __name__ == "__main__":
    unittest.main()
