"""GAQL parse + check against field metadata (pure, table-driven)."""

import unittest

from ads_mcp.schema import gaql
from ads_mcp.schema.gaql import FieldMeta as F

STATUS = ("UNSPECIFIED", "UNKNOWN", "ENABLED", "PAUSED", "REMOVED")
META = {
    m.name: m
    for m in (
        F(
            "campaign",
            "RESOURCE",
            attribute_resources=("customer", "bidding_strategy"),
            metrics=("metrics.clicks", "metrics.conversions"),
            segments=("segments.date", "segments.ad_network_type"),
        ),
        F("ad_group", "RESOURCE", attribute_resources=("campaign", "customer")),
        F(
            "campaign.id",
            "ATTRIBUTE",
            "INT64",
            selectable=True,
            filterable=True,
            sortable=True,
        ),
        F(
            "campaign.name",
            "ATTRIBUTE",
            "STRING",
            selectable=True,
            filterable=True,
            sortable=True,
        ),
        F(
            "campaign.status",
            "ATTRIBUTE",
            "ENUM",
            STATUS,
            selectable=True,
            filterable=True,
            sortable=True,
        ),
        F(
            "campaign.labels",
            "ATTRIBUTE",
            "RESOURCE_NAME",
            is_repeated=True,
            selectable=True,
            filterable=True,
        ),
        F(
            "customer.id",
            "ATTRIBUTE",
            "INT64",
            selectable=True,
            filterable=True,
            sortable=True,
        ),
        F(
            "ad_group.id",
            "ATTRIBUTE",
            "INT64",
            selectable=True,
            filterable=True,
            sortable=True,
        ),
        F(
            "metrics.clicks",
            "METRIC",
            "INT64",
            selectable=True,
            filterable=True,
            sortable=True,
            selectable_with=("campaign", "segments.date"),
        ),
        F(
            "metrics.conversions",
            "METRIC",
            "DOUBLE",
            selectable=True,
            filterable=True,
            sortable=True,
            selectable_with=("campaign", "segments.date"),
        ),
        F(
            "segments.date",
            "SEGMENT",
            "DATE",
            selectable=True,
            filterable=True,
            sortable=True,
            selectable_with=(
                "campaign",
                "metrics.clicks",
                "metrics.conversions",
            ),
        ),
        F(
            "segments.ad_network_type",
            "SEGMENT",
            "ENUM",
            selectable=True,
            filterable=True,
            sortable=True,
            selectable_with=("campaign", "metrics.clicks"),
        ),
    )
}


def codes(query):
    parsed = gaql.parse(query)
    if isinstance(parsed, gaql.QueryProblem):
        return [(parsed.clause, parsed.code)]
    return [(p.clause, p.field, p.code) for p in gaql.check(parsed, META)]


class ParseTest(unittest.TestCase):
    def test_parse(self):
        q = gaql.parse(
            "SELECT campaign.id, metrics.clicks FROM campaign "
            "WHERE campaign.status IN ('ENABLED','PAUSED') AND campaign.name LIKE '%a.b%' "
            "AND segments.date BETWEEN '2026-01-01' AND '2026-01-31' "
            "ORDER BY metrics.clicks DESC, campaign.id LIMIT 10"
        )
        self.assertEqual(q.select, ("campaign.id", "metrics.clicks"))
        self.assertEqual(q.resource, "campaign")
        self.assertEqual(
            q.where, ("campaign.status", "campaign.name", "segments.date")
        )  # never 'a.b' in a string
        self.assertEqual(q.order_by, ("metrics.clicks", "campaign.id"))
        self.assertEqual(q.limit, "10")
        self.assertEqual(
            q.enum_literals, (("campaign.status", ("ENABLED", "PAUSED")),)
        )
        self.assertEqual(gaql.names_in(q)[0], "campaign")


class CheckTest(unittest.TestCase):
    def test_table(self):
        cases = [
            ("SELECT campaign.id FROM campaign", []),
            ("select campaign.id, customer.id from campaign limit 5", []),
            (
                "SELECT campaign.id FROM nope",
                [("FROM", "nope", "UNKNOWN_RESOURCE")],
            ),
            ("campaign.id FROM campaign", [("SYNTAX", "SYNTAX")]),
            (
                "SELECT campaign.nope FROM campaign",
                [("SELECT", "campaign.nope", "UNKNOWN_FIELD")],
            ),
            (
                "SELECT ad_group.id FROM campaign",
                [("SELECT", "ad_group.id", "NOT_WITH_RESOURCE")],
            ),
            ("SELECT campaign.id FROM ad_group", []),
            (
                "SELECT campaign.id FROM campaign ORDER BY campaign.labels",
                [("ORDER BY", "campaign.labels", "NOT_SORTABLE")],
            ),
            (
                "SELECT campaign.id FROM campaign WHERE campaign.status = 'ACTIVE'",
                [("WHERE", "campaign.status", "BAD_ENUM")],
            ),
            (
                "SELECT campaign.id FROM campaign WHERE campaign.status = ENABLED",
                [],
            ),
            (
                "SELECT campaign.id FROM campaign WHERE campaign.status NOT IN ('REMOVED', 'GONE')",
                [("WHERE", "campaign.status", "BAD_ENUM")],
            ),
            (
                "SELECT segments.date, metrics.clicks FROM campaign",
                [("WHERE", "segments.date", "DATE_FILTER_NEEDED")],
            ),
            (
                "SELECT segments.date, metrics.clicks FROM campaign WHERE segments.date DURING LAST_7_DAYS",
                [],
            ),
            (
                "SELECT segments.ad_network_type, metrics.conversions FROM campaign",
                [("SELECT", "segments.ad_network_type", "INCOMPATIBLE")],
            ),
            (
                "SELECT metrics.clicks FROM ad_group",
                [("SELECT", "metrics.clicks", "NOT_WITH_RESOURCE")],
            ),
            (
                "SELECT campaign.id FROM campaign LIMIT 0",
                [("LIMIT", "", "BAD_LIMIT")],
            ),
            (
                "SELECT campaign FROM campaign",
                [("SELECT", "campaign", "NOT_WITH_RESOURCE")],
            ),
        ]
        for query, want in cases:
            with self.subTest(query=query):
                self.assertEqual(codes(query), want)

    def test_compact(self):
        self.assertEqual(
            META["campaign.status"].compact(),
            {
                "type": "ENUM",
                "can": "select filter sort",
                "enum": ["ENABLED", "PAUSED", "REMOVED"],
            },
        )
        self.assertEqual(
            META["campaign.labels"].compact(),
            {"type": "RESOURCE_NAME", "can": "select filter", "repeated": True},
        )


if __name__ == "__main__":
    unittest.main()
