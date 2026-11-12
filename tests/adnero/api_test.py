"""The raw REST passthrough (api_read, api_mutate) and the pinned API version."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from ads_mcp import adnero_auth, utils
from ads_mcp.tools import adnero_api as api

CID = "1234567890"
TOKEN = "ya29.stub-token"


class _Handler(BaseHTTPRequestHandler):
    def _serve(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.seen.append(
            SimpleNamespace(
                method=self.command,
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=json.loads(raw) if raw else None,
            )
        )
        status, payload = self.server.reply(self.server.seen[-1])
        data = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode()
        )
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("request-id", "req-42")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _serve
    do_POST = _serve

    def log_message(self, *args):
        pass


class StubGoogle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.seen = []
        cls.server.reply = lambda req: (200, {})
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.seen.clear()
        self.server.reply = lambda req: (200, {})
        env = patch.dict(
            os.environ,
            {
                "ADNERO_MULTI_TENANT": "1",
                "GOOGLE_ADS_MCP_SHARED_SECRET": "k" * 32,
                "GOOGLE_ADS_REST_BASE": self.base,
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.env_headers = {
            "x-google-ads-mcp-secret": "k" * 32,
            "x-google-ads-access-token": TOKEN,
        }
        hdr = patch(
            "ads_mcp.adnero_auth.get_http_headers",
            side_effect=lambda include=None: dict(self.env_headers),
        )
        hdr.start()
        self.addCleanup(hdr.stop)

    def reply(self, fn):
        self.server.reply = fn

    def assert_nothing_sent(self):
        self.assertEqual(self.server.seen, [], "nothing reached Google")


class ContainmentTest(unittest.TestCase):
    def test_paths_under_the_same_customer_pass(self):
        for p in (
            f"customers/{CID}",
            f"customers/{CID}/googleAds:search",
            f"customers/{CID}/adGroupCriteria/7~8",
        ):
            self.assertEqual(api.contained_path(CID, p), p)

    def test_refused(self):
        for bad in (
            "customers/999/campaigns/1",  # another customer
            f"customers/{CID}0/campaigns/1",  # a longer id, same prefix
            f"customers/{CID}/../999/campaigns",  # ..
            f"customers/{CID}/./campaigns",
            f"https://googleads.googleapis.com/v26/customers/{CID}",  # URL
            f"//evil.example/customers/{CID}",
            f"/customers/{CID}/campaigns",  # leading slash
            f"v26/customers/{CID}/campaigns",  # version segment
            f"customers/{CID}/v25/campaigns",
            f"customers/{CID}/campaigns?alt=json",
            f"customers/{CID}/campaigns#x",
            f"customers/{CID}/campaigns%2F..",
            f"customers/{CID}//campaigns",
            f"customers/{CID}/" + "a" * 300,
            "",
        ):
            with self.assertRaises(ToolError, msg=bad):
                api.contained_path(CID, bad)

    def test_other_customer_says_what_to_do(self):
        with self.assertRaises(ToolError) as e:
            api.contained_path(CID, "customers/42/campaigns")
        self.assertIn(f"customers/{CID}", str(e.exception))

    def test_remove_found_at_any_depth(self):
        self.assertTrue(
            api.has_remove([{"campaignOperation": {"remove": "x"}}])
        )
        self.assertTrue(
            api.has_remove(
                [{"update": {"resourceName": "r", "status": "REMOVED"}}]
            )
        )
        self.assertFalse(api.has_remove([{"update": {"status": "PAUSED"}}]))

    def test_checksum_ignores_control_keys_and_key_order(self):
        a = api.clean_body(
            {
                "operations": [{"update": {"b": 1, "a": 2}}],
                "validateOnly": False,
            }
        )
        b = api.clean_body(
            {
                "partialFailure": True,
                "operations": [{"update": {"a": 2, "b": 1}}],
            }
        )
        self.assertEqual(
            api.checksum_of(CID, "p", a), api.checksum_of(CID, "p", b)
        )
        self.assertNotEqual(
            api.checksum_of(CID, "p", a), api.checksum_of("1", "p", a)
        )


class ReadTest(StubGoogle):
    def test_get_any_path_under_the_customer(self):
        self.reply(lambda req: (200, {"resourceName": f"customers/{CID}"}))
        out = api.read(customer_id="123-456-7890", path=f"customers/{CID}")
        self.assertEqual(out.status, 200)
        self.assertEqual(out.data, {"resourceName": f"customers/{CID}"})
        self.assertFalse(out.truncated)
        req = self.server.seen[-1]
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.path, f"/v26/customers/{CID}")
        self.assertEqual(req.headers["authorization"], f"Bearer {TOKEN}")
        self.assertNotIn("developer-token", req.headers)
        self.assertNotIn("login-customer-id", req.headers)

    def test_search_post_is_allowed(self):
        self.reply(lambda req: (200, {"results": [{"campaign": {"id": "1"}}]}))
        out = api.read(
            customer_id=CID,
            path=f"customers/{CID}/googleAds:search",
            method="POST",
            body={"query": "SELECT campaign.id FROM campaign"},
        )
        self.assertEqual(out.data["results"][0]["campaign"]["id"], "1")
        req = self.server.seen[-1]
        self.assertEqual(req.method, "POST")
        self.assertEqual(
            req.body, {"query": "SELECT campaign.id FROM campaign"}
        )

    def test_other_posts_are_refused(self):
        for path in (
            f"customers/{CID}/campaigns:mutate",
            f"customers/{CID}/googleAds:mutate",
            f"customers/{CID}",
        ):
            with self.assertRaises(ToolError) as e:
                api.read(customer_id=CID, path=path, method="POST", body={})
            self.assertIn("changes go through api_mutate", str(e.exception))
        self.assert_nothing_sent()

    def test_other_customer_never_sent(self):
        with self.assertRaises(ToolError):
            api.read(customer_id=CID, path="customers/42")
        self.assert_nothing_sent()

    def test_truncates_a_large_answer(self):
        big = {"results": [{"x": "y" * 1000} for _ in range(300)]}
        self.reply(lambda req: (200, big))
        out = api.read(customer_id=CID, path=f"customers/{CID}")
        self.assertTrue(out.truncated)
        self.assertEqual(len(out.data), api.MAX_RESPONSE_CHARS)
        self.assertIn("page", out.hint)

    def test_login_customer_id_header_then_argument(self):
        api.read(
            customer_id=CID,
            path=f"customers/{CID}",
            login_customer_id="555-000-1111",
        )
        self.assertEqual(
            self.server.seen[-1].headers["login-customer-id"], "5550001111"
        )
        self.env_headers["x-google-ads-login-customer-id"] = "999"
        api.read(
            customer_id=CID,
            path=f"customers/{CID}",
            login_customer_id="5550001111",
        )
        self.assertEqual(
            self.server.seen[-1].headers["login-customer-id"], "999"
        )

    def test_google_errors_are_mapped(self):
        error = {
            "error": {
                "code": 403,
                "message": "The caller does not have permission",
                "status": "PERMISSION_DENIED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure",
                        "errors": [
                            {
                                "errorCode": {
                                    "authorizationError": "CUSTOMER_NOT_ENABLED"
                                },
                                "message": "The customer account can't be accessed because it is not yet enabled or has been deactivated.",
                            }
                        ],
                    }
                ],
            }
        }
        self.reply(lambda req: (403, error))
        with self.assertRaises(ToolError) as e:
            api.read(customer_id=CID, path=f"customers/{CID}")
        text = str(e.exception)
        self.assertIn("403 PERMISSION_DENIED", text)
        self.assertIn("AuthorizationError.CUSTOMER_NOT_ENABLED", text)
        self.assertIn("not yet enabled", text)
        self.assertIn("req-42", text)
        self.assertNotIn(TOKEN, text)

    def test_plain_google_error(self):
        self.reply(
            lambda req: (
                404,
                {
                    "error": {
                        "code": 404,
                        "message": "Not found",
                        "status": "NOT_FOUND",
                    }
                },
            )
        )
        with self.assertRaises(ToolError) as e:
            api.read(customer_id=CID, path=f"customers/{CID}/nothing")
        self.assertIn("404 NOT_FOUND", str(e.exception))
        self.assertIn("Not found", str(e.exception))


class MutateTest(StubGoogle):
    PATH = f"customers/{CID}/campaigns:mutate"
    BODY = {
        "operations": [
            {
                "update": {
                    "resourceName": f"customers/{CID}/campaigns/11",
                    "status": "PAUSED",
                },
                "updateMask": "status",
            }
        ]
    }

    def setUp(self):
        super().setUp()
        self.reply(
            lambda req: (
                200,
                (
                    {}
                    if req.body.get("validateOnly")
                    else {
                        "results": [
                            {"resourceName": f"customers/{CID}/campaigns/11"}
                        ]
                    }
                ),
            )
        )

    def test_preview_sends_validate_only(self):
        out = api.mutate(customer_id=CID, path=self.PATH, body=self.BODY)
        self.assertEqual(out.status, "PROPOSED")
        self.assertEqual(len(out.checksum), 64)
        self.assertEqual(out.change_items[0].operation, "UPDATE")
        req = self.server.seen[-1]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.path, f"/v26/{self.PATH}")
        self.assertIs(req.body["validateOnly"], True)
        self.assertEqual(req.headers["authorization"], f"Bearer {TOKEN}")

    def test_apply_with_the_preview_checksum(self):
        proposal = api.mutate(customer_id=CID, path=self.PATH, body=self.BODY)
        done = api.mutate(
            customer_id=CID,
            path=self.PATH,
            body={**self.BODY, "partialFailure": True},  # ours to set: dropped
            validate_only=False,
            checksum=proposal.checksum,
        )
        self.assertEqual(done.status, "APPLIED")
        self.assertEqual(done.resource_names, [f"customers/{CID}/campaigns/11"])
        sent = self.server.seen[-1].body
        self.assertNotIn("validateOnly", sent)
        self.assertNotIn("partialFailure", sent)
        self.assertEqual(sent["operations"], self.BODY["operations"])

    def test_wrong_or_missing_checksum_is_refused(self):
        proposal = api.mutate(customer_id=CID, path=self.PATH, body=self.BODY)
        self.server.seen.clear()
        changed = {
            "operations": [{**self.BODY["operations"][0], "updateMask": "name"}]
        }
        with self.assertRaises(ToolError) as e:
            api.mutate(
                customer_id=CID,
                path=self.PATH,
                body=changed,
                validate_only=False,
                checksum=proposal.checksum,
            )
        self.assertIn("validate_only=true first", str(e.exception))
        with self.assertRaises(ToolError):
            api.mutate(
                customer_id=CID,
                path=self.PATH,
                body=self.BODY,
                validate_only=False,
                checksum="0" * 64,
            )
        with self.assertRaises(ToolError) as e:
            api.mutate(
                customer_id=CID,
                path=self.PATH,
                body=self.BODY,
                validate_only=False,
            )
        self.assertIn("validate_only=true first", str(e.exception))
        self.assert_nothing_sent()

    def test_remove_is_refused(self):
        for body in (
            {"operations": [{"remove": f"customers/{CID}/campaigns/11"}]},
            {
                "mutateOperations": [
                    {
                        "campaignOperation": {
                            "remove": f"customers/{CID}/campaigns/11"
                        }
                    }
                ]
            },
        ):
            path = (
                self.PATH
                if "operations" in body
                else f"customers/{CID}/googleAds:mutate"
            )
            with self.assertRaises(ToolError) as e:
                api.mutate(customer_id=CID, path=path, body=body)
            self.assertIn("never remove", str(e.exception))
        self.assert_nothing_sent()

    def test_paths_and_sizes_refused(self):
        bad = (
            (f"customers/{CID}/campaigns", self.BODY),
            (f"customers/{CID}/Campaigns:mutate", self.BODY),
            ("customers/42/campaigns:mutate", self.BODY),
            (self.PATH, {"operations": [self.BODY["operations"][0]] * 101}),
            (self.PATH, {"operations": []}),
            (self.PATH, {"operations": [{"create": {"name": "x" * 61_000}}]}),
        )
        for path, body in bad:
            with self.assertRaises(ToolError, msg=path):
                api.mutate(customer_id=CID, path=path, body=body)
        self.assert_nothing_sent()

    def test_google_ads_mutate_resource_names(self):
        self.reply(
            lambda req: (
                200,
                (
                    {}
                    if req.body.get("validateOnly")
                    else {
                        "mutateOperationResponses": [
                            {
                                "campaignResult": {
                                    "resourceName": f"customers/{CID}/campaigns/9"
                                }
                            }
                        ]
                    }
                ),
            )
        )
        path = f"customers/{CID}/googleAds:mutate"
        body = {
            "mutateOperations": [
                {
                    "campaignOperation": {
                        "create": {"name": "x", "status": "PAUSED"}
                    }
                }
            ]
        }
        p = api.mutate(customer_id=CID, path=path, body=body)
        self.assertEqual(p.change_items[0].entity, "campaign")
        done = api.mutate(
            customer_id=CID,
            path=path,
            body=body,
            validate_only=False,
            checksum=p.checksum,
        )
        self.assertEqual(done.resource_names, [f"customers/{CID}/campaigns/9"])

    def test_google_validation_errors_reach_the_caller(self):
        self.reply(
            lambda req: (
                400,
                {
                    "error": {
                        "code": 400,
                        "message": "Request contains an invalid argument.",
                        "status": "INVALID_ARGUMENT",
                        "details": [
                            {
                                "errors": [
                                    {
                                        "errorCode": {"fieldError": "REQUIRED"},
                                        "message": "The required field was not present.",
                                    }
                                ]
                            }
                        ],
                    }
                },
            )
        )
        with self.assertRaises(ToolError) as e:
            api.mutate(customer_id=CID, path=self.PATH, body=self.BODY)
        self.assertIn("FieldError.REQUIRED", str(e.exception))


class ApiVersionTest(unittest.TestCase):
    def test_default_override_and_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(adnero_auth.api_version(), "v26")
        with patch.dict(os.environ, {"GOOGLE_ADS_API_VERSION": "v27"}):
            self.assertEqual(adnero_auth.api_version(), "v27")
        for bad in ("26", "v2", "v26_2", "v26/../x", "latest"):
            with patch.dict(os.environ, {"GOOGLE_ADS_API_VERSION": bad}):
                with self.assertLogs("ads_mcp.adnero_auth", "WARNING"):
                    self.assertEqual(adnero_auth.api_version(), "v26")

    def test_the_client_is_built_with_it(self):
        with patch.dict(
            os.environ, {"GOOGLE_ADS_API_VERSION": "v25"}, clear=True
        ):
            with patch.object(
                utils, "_create_credentials", return_value=object()
            ):
                with patch("ads_mcp.utils.GoogleAdsClient") as client:
                    utils._get_googleads_client()
                    self.assertEqual(client.call_args.kwargs["version"], "v25")
                    service = utils.get_googleads_service_from_client(
                        client.return_value, "GoogleAdsService"
                    )
        self.assertEqual(
            client.return_value.get_service.call_args.kwargs["version"], "v25"
        )
        self.assertIs(service, client.return_value.get_service.return_value)
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                utils, "_create_credentials", return_value=object()
            ):
                with patch("ads_mcp.utils.GoogleAdsClient") as client:
                    utils._get_googleads_client()
        self.assertEqual(client.call_args.kwargs["version"], "v26")

    def test_the_real_library_serves_v26(self):
        from google.ads.googleads.client import GoogleAdsClient
        from google.auth.credentials import AnonymousCredentials

        c = GoogleAdsClient(
            credentials=AnonymousCredentials(),
            use_proto_plus=True,
            version="v26",
        )
        self.assertIn(".v26.", type(c.get_type("MutateOperation")).__module__)


if __name__ == "__main__":
    unittest.main()
