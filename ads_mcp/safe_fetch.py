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

"""Fetch customer media from a public https URL without reaching private hosts (edge).

The host is resolved once; every address must be public (core/net.py); the TCP connection
goes to that address (pinned) while TLS verifies the original host name. Redirects are
followed by hand (at most 3), each one re-checked. The body is read under a byte cap and a
deadline. No cookies or credentials are sent. Failures are returned as Err, never raised.
"""

from collections.abc import Callable
from dataclasses import dataclass
import http.client
import socket
import ssl
import time
from urllib.parse import urljoin

from ads_mcp.core.net import Target, judge_addresses, parse_https_url
from ads_mcp.core.result import Err, Ok, Result

MAX_REDIRECTS = 3
TIMEOUT_S = 15.0
_CHUNK = 64 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str, int], tuple[str, ...]]


@dataclass(frozen=True)
class Fetched:
    url: str
    final_url: str
    data: bytes
    content_type: str


def resolve(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return ()
    return tuple(dict.fromkeys(str(i[4][0]) for i in infos))


class _Pinned(http.client.HTTPSConnection):
    def __init__(self, target: Target, ip: str, timeout: float):
        super().__init__(
            target.host,
            target.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._ip = ip
        self._ctx = ssl.create_default_context()

    def connect(self) -> None:
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            self.sock = self._ctx.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _connect(
    target: Target, ip: str, timeout: float
) -> http.client.HTTPConnection:
    return _Pinned(target, ip, timeout)


def fetch(
    url: str,
    max_bytes: int,
    resolver: Resolver = resolve,
    connect: Callable[
        [Target, str, float], http.client.HTTPConnection
    ] = _connect,
    timeout: float = TIMEOUT_S,
) -> Result[Fetched, str]:
    deadline = time.monotonic() + timeout
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = parse_https_url(current)
        if isinstance(parsed, Err):
            return parsed
        target = parsed.value
        ip = judge_addresses(target.host, resolver(target.host, target.port))
        if isinstance(ip, Err):
            return ip
        conn = connect(target, ip.value, max(1.0, deadline - time.monotonic()))
        try:
            conn.request(
                "GET",
                target.path,
                headers={
                    "User-Agent": "adnero-google-ads-mcp/1 (media fetch)",
                    "Accept": "*/*",
                },
            )
            resp = conn.getresponse()
            if resp.status in _REDIRECTS:
                location = resp.getheader("Location") or ""
                if not location:
                    return Err(f"{resp.status} redirect without a Location.")
                current = urljoin(current, location)
                continue
            if resp.status != 200:
                return Err(f"The URL answered HTTP {resp.status}.")
            declared = resp.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                return Err(
                    f"The file is {declared} bytes; the limit is {max_bytes}."
                )
            body = _read_capped(resp, max_bytes, deadline)
            if isinstance(body, Err):
                return body
            return Ok(
                Fetched(
                    url,
                    current,
                    body.value,
                    resp.getheader("Content-Type") or "",
                )
            )
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            return Err(f"Could not fetch the URL: {type(exc).__name__}.")
        finally:
            conn.close()
    return Err(f"More than {MAX_REDIRECTS} redirects.")


def _read_capped(resp, max_bytes: int, deadline: float) -> Result[bytes, str]:
    # Local buffer; nothing escapes but the bytes (streaming read under a cap).
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            return Err("The download took too long.")
        chunk = resp.read(_CHUNK)
        if not chunk:
            return Ok(b"".join(chunks))
        total += len(chunk)
        if total > max_bytes:
            return Err(f"The file is over the {max_bytes}-byte limit.")
        chunks.append(chunk)
