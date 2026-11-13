"""Pure helpers of the read tools (table-driven)."""

import unittest

from ads_mcp.reads import common as c
from tests.reads.fakes import E, GoogleError


class CommonTest(unittest.TestCase):
    def test_table(self):
        cases = [
            (c.quote, ("it's",), "'it\\'s'"),
            (c.quote, ("a\\b",), "'a\\\\b'"),
            (c.re2_escape, ("a.b (c)",), "a\\.b \\(c\\)"),
            (c.digits, ("123-456-7890",), "1234567890"),
            (c.digits, ("12a",), None),
            (c.digits, (None,), None),
            (c.id_list, ([1, "2", "x", 1],), ("1", "2")),
            (c.name, (E("PAUSED"),), "PAUSED"),
            (c.name, (E("UNSPECIFIED"),), None),
            (c.name, (None,), None),
            (c.names, ([E("A"), E("UNSPECIFIED"), E("B")],), ["A", "B"]),
            (c.micros_to_units, (1_500_000,), 1.5),
            (c.ratio, (3, 0), None),
            (c.ratio, (1, 3), 0.33),
            (c.tail_id, ("geoTargetConstants/2124",), "2124"),
            (
                c.date_condition,
                (None, None, "last_7_days"),
                "segments.date DURING LAST_7_DAYS",
            ),
            (
                c.date_condition,
                ("2026-11-01", "2026-11-12", None),
                "segments.date BETWEEN '2026-11-01' AND '2026-11-12'",
            ),
        ]
        for fn, args, want in cases:
            with self.subTest(fn=fn.__name__, args=args):
                self.assertEqual(fn(*args), want)

    def test_bad_dates(self):
        for args in (
            (None, None, "FOREVER"),
            ("2026-11-12", "2026-11-01", None),
            ("2024-01-01", "2026-01-01", None),
            ("yesterday", "today", None),
        ):
            with self.subTest(args=args):
                self.assertIsInstance(c.date_condition(*args), ValueError)

    def test_google_errors(self):
        exc = GoogleError(
            "identity_verification_error", "NO_EFFECTIVE_BILLING", "no billing"
        )
        self.assertEqual(
            c.google_errors(exc),
            [
                {
                    "kind": "identity_verification_error",
                    "code": "NO_EFFECTIVE_BILLING",
                    "message": "no billing",
                }
            ],
        )
        self.assertEqual(c.why(exc), "NO_EFFECTIVE_BILLING: no billing")
        self.assertEqual(c.why(ValueError("plain")), "plain")


if __name__ == "__main__":
    unittest.main()
