"""Bearer mode (a remote MCP server with standard MCP OAuth), login_customer_id and accounts_list_accessible."""

import hashlib
import http.server
import json
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from ads_mcp import adnero_auth, utils
from ads_mcp.tools import adnero_accounts

ADWORDS = "https://www.googleapis.com/auth/adwords"
PUBLIC = "https://h.example/mcp-hosted/google-ads/mcp"
BODY = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
ACCEPT = {"accept": "application/json, text/event-stream"}


class StubTokeninfo:
    """A local tokeninfo: token -> (status, body); counts the calls."""

    def __init__(self):
        self.answers = {}
        self.calls = []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                token = parse_qs(urlsplit(self.path).query)["access_token"][0]
                stub.calls.append(token)
                status, body = stub.answers.get(token, (400, {}))
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/tokeninfo"

    def good(self, token, aud="310132552575-abc.apps.googleusercontent.com"):
        self.answers[token] = (
            200,
            {"scope": f"openid {ADWORDS}", "aud": aud, "exp": str(int(time.time()) + 3000)},
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class BearerBase(unittest.TestCase):
    def setUp(self):
        self.stub = StubTokeninfo()
        self.addCleanup(self.stub.close)
        env = patch.dict(
            os.environ,
            {
                "ADNERO_MULTI_TENANT": "1",
                "GOOGLE_ADS_MCP_SHARED_SECRET": "k" * 32,
                "GOOGLE_ADS_MCP_PUBLIC_URL": PUBLIC,
                "GOOGLE_ADS_TOKENINFO_URL": self.stub.url,
            },
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GOOGLE_ADS_MCP_ALLOWED_PROJECTS", None)
        adnero_auth._CACHE.clear()
        self.addCleanup(adnero_auth._CACHE.clear)

    def post(self, http, token=None, **extra):
        headers = {**ACCEPT, **extra}
        if token:
            headers["authorization"] = f"Bearer {token}"
        return http.post("/mcp", json=BODY, headers=headers)


class TokenOkTest(BearerBase):
    def test_accepts_the_adwords_scope(self):
        self.stub.good("t1")
        self.assertTrue(adnero_auth.token_ok("t1"))

    def test_refuses_a_missing_scope(self):
        self.stub.answers["t2"] = (
            200,
            {"scope": "openid email", "aud": "1-x", "exp": str(int(time.time()) + 99)},
        )
        self.assertFalse(adnero_auth.token_ok("t2"))

    def test_refuses_a_token_google_rejects(self):
        self.assertFalse(adnero_auth.token_ok("unknown"))

    def test_project_allow_list(self):
        self.stub.good("t3", aud="310132552575-abc.apps.googleusercontent.com")
        self.stub.good("t4", aud="999999999999-abc.apps.googleusercontent.com")
        self.stub.good("t5", aud="3101325525750-abc.apps.googleusercontent.com")
        with patch.dict(
            os.environ,
            {"GOOGLE_ADS_MCP_ALLOWED_PROJECTS": "111, 310132552575"},
        ):
            self.assertTrue(adnero_auth.token_ok("t3"))
            self.assertFalse(adnero_auth.token_ok("t4"))
            # The project number must be followed by '-', not just be a prefix.
            self.assertFalse(adnero_auth.token_ok("t5"))

    def test_positive_answers_are_cached_by_hash(self):
        self.stub.good("t6")
        self.assertTrue(adnero_auth.token_ok("t6"))
        self.assertTrue(adnero_auth.token_ok("t6"))
        self.assertEqual(len(self.stub.calls), 1)
        self.assertEqual(
            list(adnero_auth._CACHE), [hashlib.sha256(b"t6").hexdigest()]
        )

    def test_positive_answer_ends_with_the_token(self):
        self.stub.good("t7")
        self.assertTrue(adnero_auth.token_ok("t7"))
        later = time.time() + 2900
        with patch("ads_mcp.adnero_auth._now", return_value=later):
            self.assertTrue(adnero_auth.token_ok("t7"))
        self.assertEqual(len(self.stub.calls), 1)
        with patch("ads_mcp.adnero_auth._now", return_value=time.time() + 3100):
            self.stub.answers["t7"] = (400, {})
            self.assertFalse(adnero_auth.token_ok("t7"))
        self.assertEqual(len(self.stub.calls), 2)

    def test_negative_answers_are_cached_for_a_minute(self):
        self.assertFalse(adnero_auth.token_ok("bad"))
        self.assertFalse(adnero_auth.token_ok("bad"))
        self.assertEqual(len(self.stub.calls), 1)
        with patch("ads_mcp.adnero_auth._now", return_value=time.time() + 61):
            self.stub.good("bad")
            self.assertTrue(adnero_auth.token_ok("bad"))
        self.assertEqual(len(self.stub.calls), 2)

    def test_cache_is_bounded(self):
        with patch.object(adnero_auth, "CACHE_MAX", 3):
            for i in range(5):
                adnero_auth.token_ok(f"nope{i}")
        self.assertEqual(len(adnero_auth._CACHE), 3)

    def test_unreachable_tokeninfo_refuses_and_is_not_remembered(self):
        with patch.dict(
            os.environ, {"GOOGLE_ADS_TOKENINFO_URL": "http://127.0.0.1:9/x"}
        ):
            self.assertFalse(adnero_auth.token_ok("t8"))
        self.assertEqual(len(adnero_auth._CACHE), 0)


class HttpTest(BearerBase):
    def app(self):
        return TestClient(adnero_auth.build_app(FastMCP("t")))

    def test_bearer_is_accepted_and_cached_across_requests(self):
        self.stub.good("t9")
        with self.app() as http:
            self.assertNotEqual(self.post(http, "t9").status_code, 401)
            self.assertNotEqual(self.post(http, "t9").status_code, 401)
        self.assertEqual(len(self.stub.calls), 1)

    def test_bad_bearer_gets_the_challenge(self):
        with self.app() as http:
            for response in (self.post(http, "nope"), self.post(http)):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.headers["www-authenticate"],
                    'Bearer resource_metadata="https://h.example/mcp-hosted/google-ads/.well-known/oauth-protected-resource"',
                )

    def test_secret_still_works_without_a_bearer(self):
        with self.app() as http:
            ok = self.post(http, **{"x-google-ads-mcp-secret": "k" * 32})
            self.assertNotEqual(ok.status_code, 401)
            wrong = self.post(http, **{"x-google-ads-mcp-secret": "z" * 32})
            self.assertEqual(wrong.status_code, 401)
        self.assertEqual(self.stub.calls, [])

    def test_well_known_is_public(self):
        want = {
            "resource": PUBLIC,
            "authorization_servers": ["https://accounts.google.com"],
            "scopes_supported": [ADWORDS],
            "bearer_methods_supported": ["header"],
        }
        with self.app() as http:
            for path in (
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-protected-resource/mcp",
            ):
                response = http.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), want)

    def test_healthz_reports_bearer(self):
        with self.app() as http:
            self.assertEqual(
                http.get("/healthz").json(),
                {"ok": True, "configured": True, "bearer": True},
            )


class SecretModeUnchangedTest(unittest.TestCase):
    """No PUBLIC_URL: only the key works, no challenge header, no well-known routes."""

    def setUp(self):
        env = patch.dict(
            os.environ,
            {"ADNERO_MULTI_TENANT": "1", "GOOGLE_ADS_MCP_SHARED_SECRET": "k" * 32},
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GOOGLE_ADS_MCP_PUBLIC_URL", None)

    def test_bearer_is_off(self):
        stub = StubTokeninfo()
        self.addCleanup(stub.close)
        stub.good("t10")
        adnero_auth._CACHE.clear()
        with patch.dict(os.environ, {"GOOGLE_ADS_TOKENINFO_URL": stub.url}):
            with TestClient(adnero_auth.build_app(FastMCP("t"))) as http:
                response = http.post(
                    "/mcp",
                    json=BODY,
                    headers={**ACCEPT, "authorization": "Bearer t10"},
                )
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("www-authenticate", response.headers)
                self.assertEqual(
                    http.get("/.well-known/oauth-protected-resource").status_code,
                    401,
                )
                self.assertEqual(
                    http.get("/healthz").json(),
                    {"ok": True, "configured": True, "bearer": False},
                )
        self.assertEqual(stub.calls, [])


def headers(**values):
    return patch(
        "ads_mcp.adnero_auth.get_http_headers",
        return_value={k.replace("_", "-"): v for k, v in values.items()},
    )


class CredentialsTest(BearerBase):
    def test_key_makes_the_header_token_the_google_token(self):
        with headers(
            x_google_ads_mcp_secret="k" * 32,
            x_google_ads_access_token="ya29.header",
            authorization="Bearer other",
        ):
            self.assertEqual(utils._create_credentials().token, "ya29.header")

    def test_without_the_key_the_bearer_is_the_google_token(self):
        with headers(
            x_google_ads_access_token="ya29.header",
            authorization="Bearer ya29.bearer",
        ):
            self.assertEqual(utils._create_credentials().token, "ya29.bearer")

    def test_no_credential_refuses(self):
        with headers():
            with self.assertRaises(ToolError):
                utils._create_credentials()


class LoginCustomerIdTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"ADNERO_MULTI_TENANT": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_header_then_argument_then_none(self):
        with headers(x_google_ads_login_customer_id="111-222-3333"):
            self.assertEqual(utils._get_login_customer_id("999"), "1112223333")
        with headers():
            self.assertEqual(utils._get_login_customer_id("999-1"), "9991")
            self.assertEqual(utils._get_login_customer_id(555), "555")
            self.assertIsNone(utils._get_login_customer_id(None))
            self.assertIsNone(utils._get_login_customer_id("abc"))

    def test_a_tool_threads_its_argument_into_the_client(self):
        from ads_mcp import adnero_mutate as m

        m.use_login("123-456")
        with patch("ads_mcp.utils._get_googleads_client") as get:
            m.client()
        self.assertEqual(get.call_args.kwargs["login_customer_id"], "123-456")
        m.use_login(None)

    def test_list_clients_defaults_the_manager_to_login_customer_id(self):
        with headers():
            with patch(
                "ads_mcp.adnero_mutate.search_rows", return_value=[]
            ) as s:
                out = adnero_accounts.list_clients(login_customer_id="12-34")
        self.assertEqual(s.call_args.args[0], "1234")
        self.assertEqual(out.manager_id, "1234")

    def test_list_clients_without_any_manager_says_how_to_fix_it(self):
        with headers():
            with self.assertRaises(ToolError) as e:
                adnero_accounts.list_clients()
        self.assertIn("login_customer_id", str(e.exception))


class AuthorizationError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.failure = SimpleNamespace(errors=[SimpleNamespace(message=message)])


class ListAccessibleTest(unittest.TestCase):
    def test_marks_a_not_enabled_account_unusable(self):
        service = MagicMock()
        service.list_accessible_customers.return_value = SimpleNamespace(
            resource_names=["customers/111", "customers/222", "customers/333"]
        )
        customer = SimpleNamespace(
            descriptive_name="Main",
            manager=True,
            currency_code="EUR",
            time_zone="Europe/Berlin",
            status=SimpleNamespace(name="ENABLED"),
        )
        refusal = "The customer account can't be accessed because it is not yet enabled or has been deactivated."

        def search(customer_id, query, login=None):
            self.assertEqual(login, customer_id)
            if customer_id == "222":
                raise AuthorizationError(refusal)
            if customer_id == "333":
                raise RuntimeError("PERMISSION_DENIED")
            return [SimpleNamespace(customer=customer)]

        with patch("ads_mcp.utils.get_googleads_service", return_value=service):
            with patch("ads_mcp.adnero_mutate.search_rows", side_effect=search):
                out = adnero_accounts.list_accessible()
        by_id = {a.customer_id: a for a in out.accounts}
        self.assertTrue(by_id["111"].usable)
        self.assertEqual(by_id["111"].name, "Main")
        self.assertTrue(by_id["111"].manager)
        self.assertIsNone(by_id["111"].why)
        self.assertFalse(by_id["222"].usable)
        self.assertEqual(by_id["222"].why, refusal)
        self.assertFalse(by_id["333"].usable)
        self.assertEqual(by_id["333"].why, "PERMISSION_DENIED")
        self.assertFalse(out.truncated)

    def test_caps_at_fifty(self):
        service = MagicMock()
        service.list_accessible_customers.return_value = SimpleNamespace(
            resource_names=[f"customers/{i}" for i in range(60)]
        )
        with patch("ads_mcp.utils.get_googleads_service", return_value=service):
            with patch(
                "ads_mcp.adnero_mutate.search_rows",
                side_effect=RuntimeError("x"),
            ):
                out = adnero_accounts.list_accessible()
        self.assertEqual(len(out.accounts), 50)
        self.assertTrue(out.truncated)


if __name__ == "__main__":
    unittest.main()
