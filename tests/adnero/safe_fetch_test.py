"""safe_fetch: pinned, https-only, redirect re-checks, size caps (fake DNS and connections)."""

import pytest

from ads_mcp import safe_fetch
from ads_mcp.core.result import Err, Ok


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        self._headers = headers or {}

    def getheader(self, name):
        return self._headers.get(name)

    def read(self, n):
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk


class FakeConnection:
    def __init__(self, routes, log, target, ip):
        self.routes, self.log, self.target, self.ip = routes, log, target, ip

    def request(self, method, path, headers):
        self.log.append((self.target.host, self.ip, path))
        assert "Authorization" not in headers and "Cookie" not in headers

    def getresponse(self):
        return self.routes[(self.target.host, self.target.path)]

    def close(self):
        pass


def run(url, routes, dns, cap=1000):
    log = []
    got = safe_fetch.fetch(
        url,
        cap,
        resolver=lambda host, port: dns.get(host, ()),
        connect=lambda t, ip, timeout: FakeConnection(routes, log, t, ip),
    )
    return got, log


DNS = {
    "cdn.example.com": ("93.184.216.34",),
    "evil.example.com": ("169.254.169.254",),
    "mixed.example.com": ("8.8.8.8", "10.0.0.1"),
}


@pytest.mark.parametrize(
    "url, routes, error",
    [
        ("http://cdn.example.com/a.png", {}, "https"),
        ("https://evil.example.com/a.png", {}, "non-public"),
        ("https://mixed.example.com/a.png", {}, "non-public"),
        ("https://nowhere.example.com/a.png", {}, "does not resolve"),
        (
            "https://cdn.example.com/a.png",
            {("cdn.example.com", "/a.png"): FakeResponse(404)},
            "HTTP 404",
        ),
        (
            "https://cdn.example.com/a.png",
            {
                ("cdn.example.com", "/a.png"): FakeResponse(
                    200, b"x" * 10, {"Content-Length": "5000"}
                )
            },
            "limit",
        ),
        (
            "https://cdn.example.com/a.png",
            {("cdn.example.com", "/a.png"): FakeResponse(200, b"x" * 2000)},
            "limit",
        ),
        (
            "https://cdn.example.com/a.png",
            {
                ("cdn.example.com", "/a.png"): FakeResponse(
                    302, headers={"Location": "https://evil.example.com/x"}
                )
            },
            "non-public",
        ),
        (
            "https://cdn.example.com/a.png",
            {
                ("cdn.example.com", "/a.png"): FakeResponse(
                    302, headers={"Location": "http://cdn.example.com/b"}
                )
            },
            "https",
        ),
    ],
)
def test_refused(url, routes, error):
    got, _ = run(url, routes, DNS)
    assert isinstance(got, Err) and error in got.error


def test_fetches_pinned_and_follows_a_safe_redirect():
    routes = {
        ("cdn.example.com", "/a.png"): FakeResponse(
            301, headers={"Location": "/b.png?v=1"}
        ),
        ("cdn.example.com", "/b.png?v=1"): FakeResponse(
            200, b"PNGDATA", {"Content-Type": "image/png"}
        ),
    }
    got, log = run("https://cdn.example.com/a.png", routes, DNS)
    assert isinstance(got, Ok)
    assert got.value.data == b"PNGDATA"
    assert got.value.final_url == "https://cdn.example.com/b.png?v=1"
    assert [ip for _, ip, _ in log] == ["93.184.216.34", "93.184.216.34"]


def test_redirect_loop_stops():
    routes = {
        ("cdn.example.com", "/a"): FakeResponse(302, headers={"Location": "/a"})
    }

    def fresh(t, ip, timeout):
        return FakeConnection(
            {k: FakeResponse(302, headers={"Location": "/a"}) for k in routes},
            [],
            t,
            ip,
        )

    got = safe_fetch.fetch(
        "https://cdn.example.com/a",
        100,
        resolver=lambda h, p: DNS.get(h, ()),
        connect=fresh,
    )
    assert isinstance(got, Err) and "redirects" in got.error


def _drip_server(head: bytes, body_drip: bool):
    """A local server that sends `head` (one byte per 0.2 s when body_drip is False) then drips the body."""
    import socket
    import threading
    import time

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        try:
            if body_drip:
                c.sendall(head)
                for _ in range(1000):
                    c.sendall(b"x")
                    time.sleep(0.2)
            else:
                for b in head:
                    c.sendall(bytes([b]))
                    time.sleep(0.2)
        except OSError:
            pass
        finally:
            c.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


@pytest.mark.parametrize(
    "head, body_drip",
    [
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n",
            True,
        ),  # slow body
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n"
            + b"X-Pad: y\r\n" * 50
            + b"\r\nx",
            False,
        ),  # slow headers
    ],
)
def test_wall_clock_deadline_holds_against_a_drip(head, body_drip):
    import http.client
    import time

    port = _drip_server(head, body_drip)
    start = time.monotonic()
    got = safe_fetch.fetch(
        "https://cdn.example.com/a.png",
        5_000_000,
        resolver=lambda h, p: ("93.184.216.34",),
        connect=lambda t, ip, to: http.client.HTTPConnection(
            "127.0.0.1", port, timeout=to
        ),
        timeout=1.0,
    )
    elapsed = time.monotonic() - start
    assert isinstance(got, Err) and "too long" in got.error
    assert elapsed < 3.0, f"the 1 s deadline took {elapsed:.1f} s"
